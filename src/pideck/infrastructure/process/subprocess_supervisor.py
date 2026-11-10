"""Linux process-group supervision using Python subprocess primitives."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading

from pideck.application.ports.process import (
    ProcessCommand,
    ProcessExit,
    ProcessExitCallback,
    ProcessHandle,
)
from pideck.domain.errors import ProcessError

_LOGGER = logging.getLogger(__name__)


class SubprocessLaunchError(ProcessError):
    """Raised when an external application cannot be started."""


@dataclass(slots=True)
class _ManagedProcess:
    """Internal process state protected by the supervisor lock."""

    process: subprocess.Popen[bytes]
    handle: ProcessHandle
    callback: ProcessExitCallback
    command: ProcessCommand


class SubprocessSupervisor:
    """Run each application in a dedicated process group and monitor its exit."""

    def __init__(self) -> None:
        """Create an empty process supervisor."""
        self._lock = threading.RLock()
        self._processes: dict[str, _ManagedProcess] = {}
        self._closed = False

    def start(
        self,
        command: ProcessCommand,
        token: str,
        on_exit: ProcessExitCallback,
    ) -> ProcessHandle:
        """Start an executable without a shell and monitor it in a daemon waiter."""
        with self._lock:
            if self._closed:
                raise SubprocessLaunchError("Process supervisor is closed")
        environment = os.environ.copy()
        if command.environment is not None:
            for key, value in command.environment.items():
                if value == "":
                    environment.pop(key, None)
                else:
                    environment[key] = value
        try:
            process = subprocess.Popen(
                [command.executable, *command.arguments],
                cwd=command.working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise SubprocessLaunchError(
                f"Unable to start {command.executable!r}: {error}"
            ) from error
        handle = ProcessHandle(token=token, pid=process.pid, process_group_id=process.pid)
        managed = _ManagedProcess(
            process=process, handle=handle, callback=on_exit, command=command,
        )
        with self._lock:
            if self._closed:
                self._terminate_group(handle)
                raise SubprocessLaunchError("Process supervisor closed during startup")
            self._processes[token] = managed
        threading.Thread(
            target=self._wait_for_exit,
            args=(managed,),
            name=f"pideck-process-{token}",
            daemon=True,
        ).start()
        return handle

    def stop(self, handle: ProcessHandle, grace_period: float = 3.0) -> None:
        """Send SIGTERM to a process group and escalate to SIGKILL if needed."""
        with self._lock:
            managed = self._processes.get(handle.token)
        if managed is None:
            return
        self._terminate_group(handle, signal.SIGTERM)
        self._terminate_orphans(managed.command, signal.SIGTERM)
        threading.Thread(
            target=self._escalate_if_needed,
            args=(managed, grace_period),
            name=f"pideck-stop-{handle.token}",
            daemon=True,
        ).start()

    def close(self) -> None:
        """Terminate all managed groups and reject new process starts."""
        with self._lock:
            self._closed = True
            processes = list(self._processes.values())
        for managed in processes:
            self._terminate_group(managed.handle, signal.SIGTERM)
            self._terminate_orphans(managed.command, signal.SIGTERM)

    def _wait_for_exit(self, managed: _ManagedProcess) -> None:
        """Wait for one child and publish exactly one exit event."""
        return_code = managed.process.wait()
        with self._lock:
            self._processes.pop(managed.handle.token, None)
        try:
            managed.callback(ProcessExit(managed.handle, return_code))
        except Exception:
            _LOGGER.exception("Process exit callback failed token=%s", managed.handle.token)

    def _escalate_if_needed(self, managed: _ManagedProcess, grace_period: float) -> None:
        """Kill the group after the grace period if the process is still alive."""
        try:
            managed.process.wait(timeout=grace_period)
        except subprocess.TimeoutExpired:
            _LOGGER.warning("Escalating process termination token=%s", managed.handle.token)
            self._terminate_group(managed.handle, signal.SIGKILL)
            self._terminate_orphans(managed.command, signal.SIGKILL)

    @staticmethod
    def _terminate_group(handle: ProcessHandle, termination_signal: int = signal.SIGKILL) -> None:
        """Send a signal to the complete managed process group."""
        try:
            os.killpg(handle.process_group_id, termination_signal)
        except ProcessLookupError:
            return
        except OSError:
            _LOGGER.exception("Unable to signal process group token=%s", handle.token)

    @classmethod
    def _terminate_orphans(cls, command: ProcessCommand, termination_signal: int) -> None:
        """Stop Flatpak/steamlink processes that left the supervised process group."""
        flatpak_id = _flatpak_app_id(command)
        if flatpak_id is not None:
            cls._flatpak_kill(flatpak_id)
            return
        if Path(command.executable).name == "steamlink":
            cls._signal_matching_processes(_is_steamlink_orphan, termination_signal)

    @staticmethod
    def _flatpak_kill(app_id: str) -> None:
        """Ask Flatpak to end every instance of one application id."""
        try:
            subprocess.run(
                ["flatpak", "kill", app_id],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _LOGGER.warning("Unable to flatpak kill app_id=%s error=%s", app_id, error)

    @staticmethod
    def _signal_matching_processes(
        matches: Callable[[bytes], bool],
        termination_signal: int,
    ) -> None:
        """Signal every local process whose /proc cmdline matches ``matches``."""
        try:
            entries = list(Path("/proc").iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                pid = int(entry.name)
                cmdline = (entry / "cmdline").read_bytes()
            except (OSError, ValueError):
                continue
            if not matches(cmdline):
                continue
            try:
                os.kill(pid, termination_signal)
            except ProcessLookupError:
                continue
            except OSError:
                _LOGGER.debug("Unable to signal orphan pid=%s", pid)


def _flatpak_app_id(command: ProcessCommand) -> str | None:
    """Return the Flatpak application id for a ``flatpak run`` command."""
    if Path(command.executable).name != "flatpak":
        return None
    try:
        run_index = command.arguments.index("run")
    except ValueError:
        return None
    for argument in command.arguments[run_index + 1 :]:
        if argument.startswith("-"):
            continue
        if "." in argument:
            return argument
        break
    return None


def _is_steamlink_orphan(cmdline: bytes) -> bool:
    """Identify leftover Steam Link launcher or runtime processes."""
    if not cmdline:
        return False
    text = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="ignore").casefold()
    return "steamlink" in text or "steam-link" in text or "steamlink/" in text or "/steamlink" in text
