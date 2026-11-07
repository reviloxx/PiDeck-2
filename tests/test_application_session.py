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
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile, CecMode
from pideck.infrastructure.process import SubprocessLaunchError, SubprocessSupervisor


@dataclass
class RecordingObserver:
    """Collect session callbacks for deterministic assertions."""

    started_applications: list[str] = field(default_factory=list)
    finished_applications: list[tuple[str, int]] = field(default_factory=list)
    failed_applications: list[tuple[str, str]] = field(default_factory=list)
    visible_applications: list[str] = field(default_factory=list)
    timed_out_applications: list[str] = field(default_factory=list)
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

    def visible(self, application: ApplicationDefinition) -> None:
        """Record external-window readiness."""
        self.visible_applications.append(application.identifier)

    def visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Record a readiness timeout."""
        self.timed_out_applications.append(application.identifier)


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


class FakeVisibilityDetector:
    """Control readiness callbacks without requiring an X11 display."""

    def __init__(self) -> None:
        self.watches: dict[str, tuple[object, object]] = {}
        self.cancelled: list[str] = []

    def wait_until_visible(
        self, handle: ProcessHandle, on_visible: object, on_timeout: object, window_mode: object = None,
    ) -> None:
        """Store callbacks for a test-controlled process handle."""
        self.watches[handle.token] = (on_visible, on_timeout)

    def cancel(self, handle: ProcessHandle) -> None:
        """Record watcher cancellation."""
        self.cancelled.append(handle.token)
        self.watches.pop(handle.token, None)

    def close(self) -> None:
        """Clear all fake watchers."""
        self.watches.clear()

    def emit_visible(self, handle: ProcessHandle) -> None:
        """Publish a controlled visible-window event."""
        self.watches[handle.token][0](handle)

    def emit_timeout(self, handle: ProcessHandle) -> None:
        """Publish a controlled readiness timeout."""
        self.watches[handle.token][1](handle)


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


def test_session_waits_for_application_visibility() -> None:
    """A process start stays STARTING until its application window is visible."""
    supervisor = FakeSupervisor()
    detector = FakeVisibilityDetector()
    observer = RecordingObserver()
    service = ApplicationSessionService(supervisor, observer, detector)
    application = ApplicationDefinition("visible", "Visible", sys.executable)

    result = service.request_launch(application)
    handle = next(iter(supervisor.handles.values()))

    assert result.status is LaunchStatus.STARTED
    assert service.snapshot.state is SessionState.STARTING
    assert observer.visible_applications == []

    detector.emit_timeout(handle)
    assert service.snapshot.state is SessionState.STARTING
    assert observer.timed_out_applications == ["visible"]

    detector.emit_visible(handle)
    assert service.snapshot.state is SessionState.RUNNING
    assert observer.visible_applications == ["visible"]

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


def test_input_resource_handoff_and_replacement_order() -> None:
    """Release before each start and never reacquire between replacements."""
    events: list[str] = []

    class InputResource:
        def suspend(self) -> None:
            events.append("suspend")

        def resume(self) -> None:
            events.append("resume")

    class Supervisor(FakeSupervisor):
        def start(self, command: ProcessCommand, token: str, on_exit: object) -> ProcessHandle:
            events.append("start")
            return super().start(command, token, on_exit)

    supervisor = Supervisor()
    service = ApplicationSessionService(supervisor, RecordingObserver(), input_resource=InputResource())
    first = ApplicationDefinition("first", "First", sys.executable)
    second = ApplicationDefinition("second", "Second", sys.executable)
    service.request_launch(first)
    first_token = next(iter(supervisor.handles))
    service.replace_running(second)
    supervisor.emit(first_token)
    assert events == ["suspend", "start", "suspend", "start"]
    second_token = next(token for token in supervisor.handles if token != first_token)
    supervisor.emit(first_token)
    assert events == ["suspend", "start", "suspend", "start"]
    supervisor.emit(second_token)
    assert events == ["suspend", "start", "suspend", "start", "resume"]
    service.close()


def test_input_resource_resumes_after_failed_launch_but_not_shutdown() -> None:
    """Failure restores CEC; a late exit after shutdown does not restart it."""
    events: list[str] = []

    class InputResource:
        def suspend(self) -> None:
            events.append("suspend")

        def resume(self) -> None:
            events.append("resume")

    application = ApplicationDefinition("app", "App", "app")
    failed = ApplicationSessionService(FailingSupervisor(), RecordingObserver(), input_resource=InputResource())
    assert failed.request_launch(application).status is LaunchStatus.FAILED
    assert events == ["suspend", "resume"]
    supervisor = FakeSupervisor()
    service = ApplicationSessionService(supervisor, RecordingObserver(), input_resource=InputResource())
    service.request_launch(application)
    token = next(iter(supervisor.handles))
    service.close()
    supervisor.emit(token)
    assert events == ["suspend", "resume", "suspend"]


def test_input_resource_is_not_released_for_profile_selection() -> None:
    """A selection prompt does not transfer CEC ownership prematurely."""
    events: list[str] = []

    class InputResource:
        def suspend(self) -> None:
            events.append("suspend")

        def resume(self) -> None:
            events.append("resume")

    application = ApplicationDefinition(
        "profiles", "Profiles", sys.executable,
        profiles=(ApplicationProfile("one", "One"), ApplicationProfile("two", "Two")),
    )
    service = ApplicationSessionService(FakeSupervisor(), RecordingObserver(), input_resource=InputResource())
    assert service.request_launch(application).status is LaunchStatus.PROFILE_SELECTION_REQUIRED
    assert events == []
    service.close()


def test_input_resource_release_failure_prevents_launch() -> None:
    """Do not start a competing application if the CEC client cannot exit."""
    from pideck.domain.errors import ProcessError

    events: list[str] = []

    class InputResource:
        def suspend(self) -> None:
            events.append("suspend")
            raise ProcessError("CEC client could not be stopped")

        def resume(self) -> None:
            events.append("resume")

    supervisor = FakeSupervisor()
    service = ApplicationSessionService(supervisor, RecordingObserver(), input_resource=InputResource())
    result = service.request_launch(ApplicationDefinition("app", "App", "app"))
    assert result.status is LaunchStatus.FAILED
    assert not supervisor.handles
    assert events == ["suspend", "resume"]
    service.close()


def test_shutdown_waits_for_in_progress_handoff() -> None:
    """Shutdown cannot miss a process created during CEC release."""
    entered = threading.Event()
    release = threading.Event()

    class InputResource:
        def suspend(self) -> None:
            entered.set()
            assert release.wait(2)

        def resume(self) -> None:
            raise AssertionError("CEC must not resume during shutdown")

    supervisor = FakeSupervisor()
    service = ApplicationSessionService(supervisor, RecordingObserver(), input_resource=InputResource())
    application = ApplicationDefinition("app", "App", "app")
    launching = threading.Thread(target=service.request_launch, args=(application,))
    launching.start()
    assert entered.wait(2)
    closing = threading.Thread(target=service.close)
    closing.start()
    release.set()
    launching.join(2)
    closing.join(2)
    assert not launching.is_alive() and not closing.is_alive()
    assert supervisor.stopped == list(supervisor.handles)
    supervisor.emit(next(iter(supervisor.handles)))
    assert service.request_launch(application).status is LaunchStatus.FAILED
    assert len(supervisor.handles) == 1


def test_input_resource_is_kept_for_every_application_when_releases_input_is_false() -> None:
    """Always-on CEC never suspends the adapter across launches."""
    events: list[str] = []

    class InputResource:
        def suspend(self) -> None:
            events.append("suspend")

        def resume(self) -> None:
            events.append("resume")

    supervisor = FakeSupervisor()
    service = ApplicationSessionService(
        supervisor, RecordingObserver(), input_resource=InputResource(),
        releases_input=lambda _application: False,
    )
    youtube = ApplicationDefinition("youtube", "YouTube", sys.executable, cec_mode=CecMode.KEYBOARD)
    releasing = ApplicationDefinition("steam-link", "Steam Link", sys.executable)
    service.request_launch(youtube)
    youtube_token = next(iter(supervisor.handles))
    supervisor.emit(youtube_token)
    assert events == []
    service.request_launch(releasing)
    releasing_token = next(token for token in supervisor.handles if token != youtube_token)
    supervisor.emit(releasing_token)
    assert events == []
    service.close()

