"""Flatpak and apt update integration for configured applications."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from uuid import uuid4

from pideck.application.ports.updates import UpdateGateway, UpdateHandle, UpdateInfo, UpdateStatus
from pideck.domain.configuration import ApplicationDefinition

_LOGGER = logging.getLogger(__name__)
_PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_.:@/-]*$")


@dataclass(slots=True)
class _RunningUpdate:
    """Internal state for one cancellable package-manager process."""

    handle: UpdateHandle
    process: subprocess.Popen[str]


class NativeUpdateGateway(UpdateGateway):
    """Check and update Flatpak or apt applications without shell execution."""

    def __init__(self, *, image_mode: bool = False) -> None:
        """Create an empty update operation registry."""
        self._lock = threading.RLock()
        self._running: dict[str, _RunningUpdate] = {}
        self._closed = False
        self._image_mode = image_mode

    def check(self, application: ApplicationDefinition) -> UpdateInfo:
        """Check one configured application using its detected package manager."""
        executable, arguments = _command_parts(application)
        flatpak_id = _flatpak_target(application, executable, arguments)
        if flatpak_id is not None:
            return self._check_flatpak(application, executable, arguments, flatpak_id)
        return self._check_apt(application, executable)

    def start(
        self,
        application: ApplicationDefinition,
        password: str | None,
        callback,
    ) -> UpdateHandle:
        """Start one native package-manager update in a dedicated process group."""
        executable, arguments = _command_parts(application)
        flatpak_id = _flatpak_target(application, executable, arguments)
        if flatpak_id is not None:
            scope = _flatpak_scope(executable, arguments, flatpak_id)
            if self._image_mode and scope.startswith("--installation="):
                raise ValueError("Named Flatpak installations cannot be updated through the image service")
            if self._image_mode and scope == "--system":
                command = [sys.executable, "-m", "pideck.infrastructure.image_update_client", "flatpak", flatpak_id]
            else:
                command = ["flatpak", "update", scope, "--noninteractive", "--assumeyes", flatpak_id]
            stdin_text = None
        else:
            package = _apt_package(executable)
            if self._image_mode:
                command = [sys.executable, "-m", "pideck.infrastructure.image_update_client", "apt", package]
                stdin_text = None
            elif os.geteuid() != 0:
                if password is None and not _sudo_is_cached():
                    raise PermissionError("sudo password required")
                command = ["sudo", "-S", "-p", "", "apt-get", "install", "--only-upgrade", "-y", package]
                stdin_text = (password + "\n") if password is not None else None
            else:
                command = ["apt-get", "install", "--only-upgrade", "-y", package]
                stdin_text = None
        with self._lock:
            if self._closed:
                raise RuntimeError("Update gateway is closed")
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        if stdin_text is not None and process.stdin is not None:
            process.stdin.write(stdin_text)
            process.stdin.close()
        handle = UpdateHandle(application.identifier, uuid4().hex)
        with self._lock:
            self._running[handle.token] = _RunningUpdate(handle, process)
        threading.Thread(
            target=self._wait_for_update,
            args=(application, handle, process, callback),
            name=f"pideck-update-{application.identifier}",
            daemon=True,
        ).start()
        return handle

    def cancel(self, handle: UpdateHandle) -> None:
        """Terminate one package-manager process group."""
        with self._lock:
            running = self._running.get(handle.token)
        if running is None:
            return
        try:
            os.killpg(running.process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            running.process.terminate()

    def cancel_by_application(self, application_id: str) -> None:
        """Cancel the active update for one application."""
        with self._lock:
            handles = [
                running.handle
                for running in self._running.values()
                if running.handle.application_id == application_id
            ]
        for handle in handles:
            self.cancel(handle)

    def close(self) -> None:
        """Cancel all package-manager processes."""
        with self._lock:
            self._closed = True
            handles = [running.handle for running in self._running.values()]
        for handle in handles:
            self.cancel(handle)

    def _wait_for_update(self, application, handle, process, callback) -> None:
        """Publish a terminal update result after the process exits."""
        output, _ = process.communicate()
        with self._lock:
            self._running.pop(handle.token, None)
        if process.returncode == 0:
            callback(UpdateInfo(application.identifier, application.name, UpdateStatus.UPDATED))
        elif process.returncode < 0:
            callback(UpdateInfo(application.identifier, application.name, UpdateStatus.CANCELLED))
        else:
            callback(UpdateInfo(application.identifier, application.name, UpdateStatus.FAILED, message=_safe_output(output)))

    def _check_flatpak(self, application, executable, arguments, app_id) -> UpdateInfo:
        """Check Flatpak availability without downloading or changing anything."""
        scope = _flatpak_scope(executable, arguments, app_id)
        arch, branch = _flatpak_launcher_ref_options(executable, arguments, app_id)
        ref_options = [scope, *([f"--arch={arch}"] if arch else [])]
        target = [app_id, *([branch] if branch else [])]
        ref = _run_lines(["flatpak", "info", *ref_options, "--show-ref", *target])
        installed = _run_lines(["flatpak", "info", *ref_options, "--show-commit", *target])
        if not ref or not installed:
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED, message="Flatpak application is not installed or its metadata is unavailable")
        expected_ref = ref[0].removeprefix("app/")
        versions = _run_lines(["flatpak", "list", scope, "--app", "--columns=ref,version:full"])
        if versions is None:
            return UpdateInfo(
                application.identifier, application.name, UpdateStatus.UNSUPPORTED,
                message="Flatpak installed metadata is unavailable", installed_commit=installed[0],
            )
        current_version = None
        for line in versions:
            fields = line.split("\t")
            if fields[0] == expected_ref:
                if len(fields) > 1 and fields[1] not in ("", "-"):
                    current_version = fields[1]
                break
        else:
            return UpdateInfo(
                application.identifier, application.name, UpdateStatus.UNSUPPORTED,
                message="Flatpak installed reference was not found", installed_commit=installed[0],
            )
        commit = installed[0] if current_version is None else None
        remote_lines = _run_lines(
            ["flatpak", "remote-ls", scope, "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"]
        )
        if remote_lines is None:
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED, current_version, message="Flatpak update metadata is unavailable", installed_commit=commit)
        for line in remote_lines:
            fields = line.split("\t")
            if len(fields) >= 3 and fields[0] == expected_ref and fields[2] != installed[0]:
                available_version = fields[1] if fields[1] not in ("", "-") else None
                return UpdateInfo(application.identifier, application.name, UpdateStatus.AVAILABLE, current_version, available_version, installed_commit=commit)
        return UpdateInfo(application.identifier, application.name, UpdateStatus.UP_TO_DATE, current_version, installed_commit=commit)

    def _check_apt(self, application, executable) -> UpdateInfo:
        """Compare installed and candidate apt package versions."""
        package = _apt_package(executable)
        result = subprocess.run(
            ["apt-cache", "policy", package],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        installed = _policy_value(result.stdout, "Installed")
        candidate = _policy_value(result.stdout, "Candidate")
        current_version = installed if installed and installed != "(none)" else None
        if result.returncode != 0 or not candidate or candidate == "(none)" or not installed or installed == "(none)":
            return UpdateInfo(
                application.identifier, application.name, UpdateStatus.UNSUPPORTED, current_version,
                message=f"No apt package metadata for {package}",
            )
        comparison = subprocess.run(
            ["dpkg", "--compare-versions", candidate, "gt", installed],
            capture_output=True, text=True, check=False,
        )
        if comparison.returncode not in (0, 1):
            return UpdateInfo(
                application.identifier, application.name, UpdateStatus.FAILED, installed,
                message=f"Unable to compare apt package versions for {package}",
            )
        newer = comparison.returncode == 0
        return UpdateInfo(application.identifier, application.name, UpdateStatus.AVAILABLE if newer else UpdateStatus.UP_TO_DATE, installed, candidate if newer else None)


def _command_parts(application: ApplicationDefinition) -> tuple[str, tuple[str, ...]]:
    parts = tuple(shlex.split(application.executable))
    if not parts:
        raise ValueError(f"Empty executable for {application.identifier}")
    return parts[0], parts[1:] + application.arguments


def _flatpak_id(arguments: tuple[str, ...]) -> str:
    """Read the Flatpak run target, ignoring option values and app arguments."""
    try:
        run_index = arguments.index("run")
    except ValueError as error:
        raise ValueError("Flatpak run command is missing") from error
    value_options = {
        "--arch", "--branch", "--command", "--cwd", "--installation", "--runtime",
        "--runtime-version", "--commit", "--runtime-commit", "--parent-pid",
        "--instance-id-fd", "--app-path", "--usr-path", "--share", "--unshare",
        "--socket", "--nosocket", "--device", "--nodevice", "--allow", "--disallow",
        "--filesystem", "--nofilesystem", "--env", "--env-fd", "--unset-env",
        "--own-name", "--talk-name", "--no-talk-name", "--system-own-name",
        "--system-talk-name", "--system-no-talk-name", "--add-policy",
        "--remove-policy", "--persist",
    }
    skip_value = False
    for argument in arguments[run_index + 1:]:
        if skip_value:
            skip_value = False
            continue
        if argument in value_options:
            skip_value = True
        elif not argument.startswith("-"):
            if "." in argument:
                return argument
            break
    raise ValueError("Flatpak application ID is missing")


def _flatpak_target(
    application: ApplicationDefinition,
    executable: str,
    arguments: tuple[str, ...],
) -> str | None:
    """Resolve an explicit or name-matched installed Flatpak application."""
    if Path(executable).name == "flatpak":
        return _flatpak_id(arguments)
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application,name"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
    except FileNotFoundError:
        _LOGGER.debug("Flatpak is not installed; checking the native package instead")
        return None
    if result.returncode != 0:
        _LOGGER.warning("Unable to list Flatpak applications: %s", result.stderr.strip())
        return None
    expected_name = " ".join(application.name.casefold().split())
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2 and " ".join(fields[1].casefold().split()) == expected_name:
            return fields[0]
    return None


def _flatpak_uses_user_scope(executable: str, arguments: tuple[str, ...], application_id: str) -> bool:
    """Honor explicit Flatpak scope, otherwise prefer an installed user application."""
    if Path(executable).name == "flatpak" and application_id in arguments:
        launcher_arguments = arguments[:arguments.index(application_id)]
        if "--system" in launcher_arguments:
            return False
        if "--user" in launcher_arguments:
            return True
    arch, branch = _flatpak_launcher_ref_options(executable, arguments, application_id)
    result = subprocess.run(
        ["flatpak", "info", "--user", *([f"--arch={arch}"] if arch else []),
         "--show-ref", application_id, *([branch] if branch else [])],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LANG": "C", "LC_ALL": "C"},
    )
    return result.returncode == 0


def _flatpak_scope(executable: str, arguments: tuple[str, ...], application_id: str) -> str:
    """Choose the explicitly named installation or the user/system installation."""
    if Path(executable).name == "flatpak" and application_id in arguments:
        launcher_arguments = arguments[:arguments.index(application_id)]
        for index, value in enumerate(launcher_arguments):
            if value.startswith("--installation="):
                name = value.split("=", 1)[1]
                if not name:
                    raise ValueError("Flatpak installation name is missing")
                return f"--installation={name}"
            if value == "--installation":
                if index + 1 >= len(launcher_arguments) or launcher_arguments[index + 1].startswith("-"):
                    raise ValueError("Flatpak installation name is missing")
                return f"--installation={launcher_arguments[index + 1]}"
    return "--user" if _flatpak_uses_user_scope(executable, arguments, application_id) else "--system"


def _flatpak_launcher_ref_options(
    executable: str, arguments: tuple[str, ...], application_id: str,
) -> tuple[str | None, str | None]:
    """Extract architecture and branch options before the Flatpak app target."""
    if Path(executable).name != "flatpak" or application_id not in arguments:
        return None, None
    launcher_arguments = arguments[:arguments.index(application_id)]
    values: dict[str, str] = {}
    for index, argument in enumerate(launcher_arguments):
        for name in ("arch", "branch"):
            if argument.startswith(f"--{name}="):
                values[name] = argument.split("=", 1)[1]
            elif argument == f"--{name}" and index + 1 < len(launcher_arguments):
                values[name] = launcher_arguments[index + 1]
    return values.get("arch"), values.get("branch")


def _apt_package(executable: str) -> str:
    """Find the installed package owning an executable, or use its basename."""
    package = Path(executable).name
    if not _PACKAGE_PATTERN.fullmatch(package):
        raise ValueError(f"Unsafe apt package name: {package!r}")
    path = shutil.which(executable)
    if path:
        paths = (path, str(Path(path).resolve()))
        for candidate in dict.fromkeys(paths):
            result = subprocess.run(
                ["dpkg-query", "-S", "--", candidate], capture_output=True, text=True, check=False,
                env={**os.environ, "LANG": "C", "LC_ALL": "C"},
            )
            if result.returncode != 0:
                continue
            owners = {
                owner.strip()
                for line in result.stdout.splitlines()
                if ": " in line and not line.startswith("diversion by ")
                for owner in line.rsplit(": ", 1)[0].split(",")
            }
            if len(owners) > 1:
                raise ValueError(f"Multiple packages own executable {candidate}")
            if owners:
                owner = owners.pop()
                if not _PACKAGE_PATTERN.fullmatch(owner) or "/" in owner:
                    raise ValueError(f"Unsafe apt package name: {owner!r}")
                return owner
        _LOGGER.warning("Unable to find package owning %s; checking package %s", path, package)
    return package


def _run_lines(command: list[str]) -> list[str] | None:
    result = subprocess.run(command, capture_output=True, text=True, check=False, env={**os.environ, "LANG": "C", "LC_ALL": "C"})
    if result.returncode != 0:
        _LOGGER.warning("Command %s failed: %s", command, result.stderr.strip() or f"exit {result.returncode}")
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _policy_value(output: str, label: str) -> str | None:
    prefix = f"  {label}:"
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _sudo_is_cached() -> bool:
    result = subprocess.run(["sudo", "-n", "-v"], capture_output=True, check=False)
    return result.returncode == 0


def _safe_output(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:240] if lines else "Package manager update failed"
