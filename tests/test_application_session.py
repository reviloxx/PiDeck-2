"""Tests for application session orchestration."""

from dataclasses import dataclass, field
import sys
import threading

from pideck.application.ports.process import ProcessCommand, ProcessExit, ProcessHandle
from pideck.application.session import (
    ApplicationSessionService,
    LaunchStatus,
    SessionState,
)
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile
from pideck.infrastructure.process import SubprocessLaunchError, SubprocessSupervisor


@dataclass
class RecordingObserver:
    """Collect session callbacks for deterministic assertions."""

    started_applications: list[str] = field(default_factory=list)
    finished_applications: list[tuple[str, int]] = field(default_factory=list)
    failed_applications: list[tuple[str, str]] = field(default_factory=list)
    completion_event: threading.Event = field(default_factory=threading.Event)

    def started(self, application: ApplicationDefinition, profile: ApplicationProfile | None) -> None:
        """Record a successful start."""
        self.started_applications.append(application.identifier)

    def finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Record a process exit."""
        self.finished_applications.append((application.identifier, return_code))
        self.completion_event.set()

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Record a failed launch."""
        self.failed_applications.append((application.identifier, message))
        self.completion_event.set()


class FakeSupervisor:
    """Minimal supervisor for stale-event and replacement state tests."""

    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.handles: dict[str, ProcessHandle] = {}
        self.stopped: list[str] = []
        self.commands: list[ProcessCommand] = []

    def start(self, command: ProcessCommand, token: str, on_exit: object) -> ProcessHandle:
        """Register a fake process handle."""
        handle = ProcessHandle(token=token, pid=len(self.handles) + 1, process_group_id=1)
        self.handles[token] = handle
        self.callbacks[token] = on_exit
        self.commands.append(command)
        return handle

    def stop(self, handle: ProcessHandle, grace_period: float = 3.0) -> None:
        """Record a stop request."""
        self.stopped.append(handle.token)

    def close(self) -> None:
        """Close the fake supervisor."""

    def emit(self, token: str, return_code: int = 0) -> None:
        """Publish a fake process exit."""
        callback = self.callbacks[token]
        callback(ProcessExit(self.handles[token], return_code))


class FailingSupervisor:
    """Supervisor double that exposes launch error handling."""

    def start(self, command: ProcessCommand, token: str, on_exit: object) -> ProcessHandle:
        """Raise the same error as an unavailable executable."""
        raise SubprocessLaunchError("executable is unavailable")

    def stop(self, handle: ProcessHandle, grace_period: float = 3.0) -> None:
        """Provide the required supervisor protocol method."""

    def close(self) -> None:
        """Provide the required supervisor protocol method."""


def test_session_starts_and_returns_to_idle() -> None:
    """A controlled child starts, exits, and restores the idle session state."""
    supervisor = SubprocessSupervisor()
    observer = RecordingObserver()
    service = ApplicationSessionService(supervisor, observer)
    application = ApplicationDefinition(
        identifier="short-lived",
        name="Short lived",
        executable=sys.executable,
        arguments=("-c", "raise SystemExit(0)"),
    )

    result = service.request_launch(application)

    assert result.status is LaunchStatus.STARTED
    assert observer.completion_event.wait(2)
    assert service.snapshot.state is SessionState.IDLE
    assert observer.finished_applications == [("short-lived", 0)]
    service.close()


def test_session_requires_profile_selection() -> None:
    """Multiple configured profiles require an explicit selection."""
    supervisor = FakeSupervisor()
    observer = RecordingObserver()
    service = ApplicationSessionService(supervisor, observer)
    application = ApplicationDefinition(
        identifier="profiles",
        name="Profiles",
        executable=sys.executable,
        profiles=(
            ApplicationProfile(identifier="one", name="One"),
            ApplicationProfile(identifier="two", name="Two"),
        ),
    )

    result = service.request_launch(application)

    assert result.status is LaunchStatus.PROFILE_SELECTION_REQUIRED
    assert [profile.identifier for profile in result.profiles] == ["one", "two"]
    assert service.snapshot.state is SessionState.IDLE
    service.close()


def test_stale_process_exit_is_ignored() -> None:
    """An old process event cannot finish a newer active session."""
    supervisor = FakeSupervisor()
    observer = RecordingObserver()
    service = ApplicationSessionService(supervisor, observer)
    first_application = ApplicationDefinition("first", "First", sys.executable)
    second_application = ApplicationDefinition("second", "Second", sys.executable)

    service.request_launch(first_application)
    first_token = next(iter(supervisor.handles))
    supervisor.emit(first_token)
    service.request_launch(second_application)
    second_token = next(token for token in supervisor.handles if token != first_token)

    supervisor.callbacks[first_token](ProcessExit(supervisor.handles[first_token], 1))

    assert service.snapshot.application == second_application
    assert service.snapshot.state is SessionState.RUNNING
    assert observer.finished_applications == [("first", 0)]
    supervisor.emit(second_token)
    service.close()


def test_replacement_starts_after_current_process_exits() -> None:
    """A confirmed replacement waits for the current process to finish."""
    supervisor = FakeSupervisor()
    observer = RecordingObserver()
    service = ApplicationSessionService(supervisor, observer)
    first_application = ApplicationDefinition("first", "First", sys.executable)
    second_application = ApplicationDefinition("second", "Second", sys.executable)

    service.request_launch(first_application)
    first_token = next(iter(supervisor.handles))
    result = service.replace_running(second_application)

    assert result.status is LaunchStatus.STOPPING
    assert service.snapshot.state is SessionState.STOPPING
    assert supervisor.stopped == [first_token]

    supervisor.emit(first_token)

    assert service.snapshot.application == second_application
    assert service.snapshot.state is SessionState.RUNNING
    service.close()


def test_launch_failure_returns_to_idle_and_notifies_observer() -> None:
    """A process launch error remains recoverable from the launcher."""
    observer = RecordingObserver()
    service = ApplicationSessionService(FailingSupervisor(), observer)
    application = ApplicationDefinition("missing", "Missing", "missing")

    result = service.request_launch(application)

    assert result.status is LaunchStatus.FAILED
    assert service.snapshot.state is SessionState.IDLE
    assert observer.failed_applications == [("missing", "executable is unavailable")]
