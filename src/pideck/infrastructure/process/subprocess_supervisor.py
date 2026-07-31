"""Linux process-group supervision using Python subprocess primitives."""

from dataclasses import dataclass
import logging
import os
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
            environment.update(command.environment)
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
        managed = _ManagedProcess(process=process, handle=handle, callback=on_exit)
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

    @staticmethod
    def _terminate_group(handle: ProcessHandle, termination_signal: int = signal.SIGKILL) -> None:
        """Send a signal to the complete managed process group."""
        try:
            os.killpg(handle.process_group_id, termination_signal)
        except ProcessLookupError:
            return
        except OSError:
            _LOGGER.exception("Unable to signal process group token=%s", handle.token)
