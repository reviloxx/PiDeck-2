"""Flatpak and apt update integration for configured applications."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
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

    def __init__(self) -> None:
        """Create an empty update operation registry."""
        self._lock = threading.RLock()
        self._running: dict[str, _RunningUpdate] = {}
        self._closed = False

    def check(self, application: ApplicationDefinition) -> UpdateInfo:
        """Check one configured application using its detected package manager."""
        executable, arguments = _command_parts(application)
        flatpak_id = _flatpak_target(application, executable, arguments)
        if flatpak_id is not None:
            arguments = (*arguments, flatpak_id)
            return self._check_flatpak(application, arguments)
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
            command = ["flatpak", "update", "--noninteractive", "--assumeyes", flatpak_id]
            stdin_text = None
        else:
            package = _apt_package(executable)
            if os.geteuid() != 0:
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

    def _check_flatpak(self, application, arguments) -> UpdateInfo:
        """Check Flatpak availability without downloading or changing anything."""
        app_id = _flatpak_id(arguments)
        installed = _run_lines(["flatpak", "info", "--show-commit", app_id])
        if installed is None:
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED, message="Flatpak application is not installed")
        remote_lines = _run_lines(
            ["flatpak", "remote-ls", "--updates", "--app", "--columns=application,version,commit"]
        )
        if remote_lines is None:
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED, message="Flatpak update metadata is unavailable")
        for line in remote_lines:
            fields = line.split()
            if len(fields) >= 3 and fields[0] == app_id and fields[2] != installed[0]:
                return UpdateInfo(application.identifier, application.name, UpdateStatus.AVAILABLE, installed[0], fields[1])
        return UpdateInfo(application.identifier, application.name, UpdateStatus.UP_TO_DATE, installed[0])

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
        if result.returncode != 0 or not candidate or candidate == "(none)" or not installed or installed == "(none)":
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED, message=f"No apt package metadata for {package}")
        newer = subprocess.run(["dpkg", "--compare-versions", candidate, "gt", installed], check=False).returncode == 0
        return UpdateInfo(application.identifier, application.name, UpdateStatus.AVAILABLE if newer else UpdateStatus.UP_TO_DATE, installed, candidate if newer else None)


def _command_parts(application: ApplicationDefinition) -> tuple[str, tuple[str, ...]]:
    parts = tuple(shlex.split(application.executable))
    if not parts:
        raise ValueError(f"Empty executable for {application.identifier}")
    return parts[0], parts[1:] + application.arguments


def _flatpak_id(arguments: tuple[str, ...]) -> str:
    candidates = [value for value in arguments if not value.startswith("-") and "." in value]
    if not candidates:
        raise ValueError("Flatpak application ID is missing")
    return candidates[-1]


def _flatpak_target(
    application: ApplicationDefinition,
    executable: str,
    arguments: tuple[str, ...],
) -> str | None:
    """Resolve an explicit or name-matched installed Flatpak application."""
    if Path(executable).name == "flatpak":
        return _flatpak_id(arguments)
    result = subprocess.run(
        ["flatpak", "list", "--app", "--columns=application,name"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        return None
    expected_name = " ".join(application.name.casefold().split())
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2 and " ".join(fields[1].casefold().split()) == expected_name:
            return fields[0]
    return None


def _apt_package(executable: str) -> str:
    package = Path(executable).name
    if not _PACKAGE_PATTERN.fullmatch(package):
        raise ValueError(f"Unsafe apt package name: {package!r}")
    return package


def _run_lines(command: list[str]) -> list[str] | None:
    result = subprocess.run(command, capture_output=True, text=True, check=False, env={**os.environ, "LANG": "C", "LC_ALL": "C"})
    if result.returncode != 0:
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
