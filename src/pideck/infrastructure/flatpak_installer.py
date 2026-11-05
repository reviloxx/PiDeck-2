"""Install an explicitly selected Flatpak without granting PiDeck root privileges."""

import os
import re
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable

from pideck.application.ports.installation import (
    InstallProgress,
    InstallProgressCallback,
    InstallStage,
)
from pideck.domain.configuration import ApplicationDefinition
from pideck.infrastructure.flatpak_progress import FlatpakProgressParser

_APP_ID = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+)+")
_FLATHUB_URL = "https://dl.flathub.org/repo/"


class InstallationError(Exception):
    """Report a failed or cancelled application installation."""


class InstallationCancelled(InstallationError):
    """Report an installation stopped at the user's request."""


class FlatpakInstaller:
    """Install applications in the current user's Flatpak installation."""

    def __init__(self) -> None:
        """Create an independent cancellable operation."""
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._cancelled = False

    @staticmethod
    def needs_password() -> bool:
        """Return whether the Flatpak system package must first be installed."""
        return shutil.which("flatpak") is None

    def cancel(self) -> None:
        """Request cancellation of the current package-manager process."""
        with self._lock:
            self._cancelled = True
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def _run(
        self, command: list[str], password: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        """Execute one command without a shell, streaming its output lines."""
        with self._lock:
            if self._cancelled:
                raise InstallationCancelled("Installation cancelled")
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE if password is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", start_new_session=True,
                    env={**os.environ, "LANG": "C", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"},
                )
            except OSError as error:
                raise InstallationError(str(error)) from error
            self._process = process
        lines: list[str] = []
        try:
            if password is not None and process.stdin is not None:
                try:
                    process.stdin.write(password + "\n")
                    process.stdin.flush()
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
            if process.stdout is not None:
                for line in process.stdout:
                    lines.append(line)
                    if on_line is not None:
                        on_line(line)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            raise
        finally:
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
        output = "".join(lines)
        with self._lock:
            self._process = None
            cancelled = self._cancelled
        if cancelled:
            raise InstallationCancelled("Installation cancelled")
        if process.returncode:
            message = output.strip()[-2000:] or f"Command failed with exit code {process.returncode}"
            if password:
                message = message.replace(password, "******")
            raise InstallationError(message)
        return output

    def install(
        self, application: ApplicationDefinition, password: str | None = None,
        on_progress: InstallProgressCallback | None = None,
    ) -> None:
        """Install the selected application, reusing an existing installation.

        ``on_progress`` is called from the calling (worker) thread.
        """
        report = on_progress or (lambda _: None)
        if application.executable != "flatpak" or len(application.arguments) < 2:
            raise InstallationError("Unsupported application definition")
        app_id = application.arguments[1]
        if application.arguments[0] != "run" or _APP_ID.fullmatch(app_id) is None:
            raise InstallationError("Invalid Flatpak application definition")
        report(InstallProgress(InstallStage.PREPARING))
        if self.needs_password():
            if password is None:
                raise InstallationError("Administrator authentication is required to install Flatpak")
            report(InstallProgress(InstallStage.INSTALLING_FLATPAK))
            self._run(
                ["sudo", "-S", "-p", "", "apt-get", "install", "--no-remove", "-y", "--", "flatpak"],
                password,
            )
        installed = self._run(["flatpak", "list", "--app", "--columns=application"])
        if app_id in installed.splitlines():
            return
        remotes = self._run(["flatpak", "remotes", "--user", "--columns=name,url"])
        matching = [line.split("\t", 1) for line in remotes.splitlines() if line.split("\t", 1)[0] == "flathub"]
        if matching:
            if len(matching[0]) != 2 or matching[0][1].rstrip("/") != _FLATHUB_URL.rstrip("/"):
                raise InstallationError("The user Flathub remote points to an unexpected URL")
        else:
            self._run([
                "flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                "https://dl.flathub.org/repo/flathub.flatpakrepo",
            ])
        report(InstallProgress(InstallStage.DOWNLOADING))
        parser = FlatpakProgressParser()

        def forward(line: str) -> None:
            progress = parser.feed(line)
            if progress is not None:
                report(progress)

        # --noninteractive would select flatpak's quiet transaction, which prints no progress.
        # --assumeyes answers every prompt and stdin is /dev/null, so it can never block.
        self._run(["flatpak", "install", "--user", "--assumeyes", "flathub", app_id], on_line=forward)
        report(InstallProgress(InstallStage.FINISHING, 100))
        self._run(["flatpak", "info", "--user", "--show-ref", app_id])
