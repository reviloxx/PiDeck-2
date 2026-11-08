"""Application launch session orchestration."""

from dataclasses import dataclass
from enum import StrEnum
import logging
import threading
import shlex
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from pideck.application.ports.process import (
    ProcessCommand,
    ProcessExit,
    ProcessHandle,
    ProcessSupervisor,
)
from pideck.application.ports.visibility import ProcessVisibilityDetector
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile
from pideck.domain.errors import PiDeckError, ProcessError

_LOGGER = logging.getLogger(__name__)


class SessionState(StrEnum):
    """Lifecycle states of the currently managed application session."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class LaunchStatus(StrEnum):
    """Outcome categories returned to the presentation layer."""

    STARTED = "started"
    PROFILE_SELECTION_REQUIRED = "profile_selection_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Describe the result of an application launch request."""

    status: LaunchStatus
    application: ApplicationDefinition
    profiles: tuple[ApplicationProfile, ...] = ()
    message: str | None = None


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Immutable view of the active application session."""

    state: SessionState
    application: ApplicationDefinition | None = None
    profile: ApplicationProfile | None = None
    handle: ProcessHandle | None = None
    session_token: str | None = None


class SessionObserver(Protocol):
    """Receive application session lifecycle notifications."""

    def started(self, application: ApplicationDefinition, profile: ApplicationProfile | None) -> None:
        """Handle a successfully started application."""
        ...

    def visible(self, application: ApplicationDefinition) -> None:
        """Handle the application window becoming visible."""
        ...

    def visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Handle an application that started but never exposed a window."""
        ...

    def finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Handle an application returning control to PiDeck."""
        ...

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Handle a launch or supervision failure."""
        ...


class SessionInputResource(Protocol):
    """Exclusive input resource released while an application is running."""

    def suspend(self) -> None:
        """Release the resource before an application starts."""
        ...

    def resume(self) -> None:
        """Reacquire the resource when the launcher becomes idle."""
        ...


InputHandoffPolicy = Callable[[ApplicationDefinition], bool]
"""Return whether an application must own the exclusive input resource."""


class ApplicationSessionService:
    """Coordinate launch requests, process supervision, and launcher return."""

    def __init__(
        self,
        supervisor: ProcessSupervisor,
        observer: SessionObserver,
        visibility_detector: ProcessVisibilityDetector | None = None,
        input_resource: SessionInputResource | None = None,
        releases_input: InputHandoffPolicy | None = None,
    ) -> None:
        """Create a session service with injected process and presentation ports.

        ``releases_input`` decides per application whether the exclusive input
        resource is handed over; by default every application receives it.
        """
        self._supervisor = supervisor
        self._observer = observer
        self._visibility_detector = visibility_detector
        self._input_resource = input_resource
        self._releases_input = releases_input or (lambda _: True)
        self._input_suspended = False
        self._closed = False
        self._lifecycle_lock = threading.RLock()
        self._snapshot = SessionSnapshot(state=SessionState.IDLE)
        self._pending_launch: tuple[ApplicationDefinition, ApplicationProfile | None] | None = None

    @property
    def snapshot(self) -> SessionSnapshot:
        """Return the current session state."""
        return self._snapshot

    def request_launch(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None = None,
    ) -> LaunchResult:
        """Start an application or report the presentation decision it needs."""
        if self._snapshot.state is not SessionState.IDLE:
            return LaunchResult(
                status=LaunchStatus.CONFIRMATION_REQUIRED,
                application=application,
                message="Another application is already running.",
            )
        selected_profile = self._select_profile(application, profile)
        if selected_profile is _PROFILE_REQUIRED:
            return LaunchResult(
                status=LaunchStatus.PROFILE_SELECTION_REQUIRED,
                application=application,
                profiles=application.profiles,
            )
        return self._start(application, selected_profile)

    def replace_running(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None = None,
    ) -> LaunchResult:
        """Stop the active application and launch the replacement afterward."""
        if self._snapshot.state is SessionState.IDLE:
            return self.request_launch(application, profile)
        selected_profile = self._select_profile(application, profile)
        if selected_profile is _PROFILE_REQUIRED:
            return LaunchResult(
                status=LaunchStatus.PROFILE_SELECTION_REQUIRED,
                application=application,
                profiles=application.profiles,
            )
        self._pending_launch = (application, selected_profile)
        self._snapshot = SessionSnapshot(
            state=SessionState.STOPPING,
            application=self._snapshot.application,
            profile=self._snapshot.profile,
            handle=self._snapshot.handle,
            session_token=self._snapshot.session_token,
        )
        if self._snapshot.handle is not None:
            self._supervisor.stop(self._snapshot.handle)
        return LaunchResult(status=LaunchStatus.STOPPING, application=application)

    def stop(self) -> None:
        """Stop the active application, if one exists."""
        if self._snapshot.handle is None:
            return
        self._pending_launch = None
        self._snapshot = SessionSnapshot(
            state=SessionState.STOPPING,
            application=self._snapshot.application,
            profile=self._snapshot.profile,
            handle=self._snapshot.handle,
            session_token=self._snapshot.session_token,
        )
        self._supervisor.stop(self._snapshot.handle)

    def close(self) -> None:
        """Stop the active session and close the injected supervisor."""
        with self._lifecycle_lock:
            self._closed = True
            self.stop()
            if self._visibility_detector is not None:
                self._visibility_detector.close()
            self._supervisor.close()

    def _start(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> LaunchResult:
        """Build a safe process command and start it under a unique session token."""
        token = f"{application.identifier}-{uuid4().hex}"
        self._snapshot = SessionSnapshot(
            state=SessionState.STARTING,
            application=application,
            profile=profile,
            session_token=token,
        )
        start_ready = threading.Event()

        def handle_process_exit(event: ProcessExit) -> None:
            """Wait until the returned process handle is installed."""
            start_ready.wait()
            self._on_process_exit(event)

        try:
            with self._lifecycle_lock:
                if self._closed:
                    raise ProcessError("Application session is closed")
                self._hand_over_input(self._releases_input(application))
                handle = self._supervisor.start(
                    self._command_for(application, profile),
                    token,
                    handle_process_exit,
                )
                self._snapshot = SessionSnapshot(
                    state=SessionState.STARTING,
                    application=application,
                    profile=profile,
                    handle=handle,
                    session_token=token,
                )
        except PiDeckError as error:
            start_ready.set()
            self._snapshot = SessionSnapshot(state=SessionState.IDLE)
            with self._lifecycle_lock:
                if not self._closed:
                    self._hand_over_input(False)
            message = str(error)
            self._observer.failed(application, message)
            return LaunchResult(LaunchStatus.FAILED, application, message=message)
        self._observer.started(application, profile)
        start_ready.set()
        if self._visibility_detector is None:
            self._mark_visible(handle)
        else:
            self._visibility_detector.wait_until_visible(
                handle,
                self._mark_visible,
                self._visibility_timeout,
                application.window_mode,
            )
        return LaunchResult(LaunchStatus.STARTED, application)

    def _mark_visible(self, handle: ProcessHandle) -> None:
        """Transition an active starting session to running."""
        active = self._snapshot
        if active.handle is None or active.handle.token != handle.token:
            return
        if active.application is None or active.state is not SessionState.STARTING:
            return
        self._snapshot = SessionSnapshot(
            state=SessionState.RUNNING,
            application=active.application,
            profile=active.profile,
            handle=active.handle,
            session_token=active.session_token,
        )
        visible = getattr(self._observer, "visible", None)
        if visible is not None:
            visible(active.application)

    def _visibility_timeout(self, handle: ProcessHandle) -> None:
        """Keep the launcher visible when readiness cannot be verified."""
        active = self._snapshot
        if active.handle is None or active.handle.token != handle.token:
            return
        if active.application is None or active.state is not SessionState.STARTING:
            return
        timeout = getattr(self._observer, "visibility_timeout", None)
        if timeout is not None:
            timeout(active.application)

    @staticmethod
    def _select_profile(
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> ApplicationProfile | None | object:
        """Select the sole profile or request presentation selection for several profiles."""
        if profile is not None:
            if profile not in application.profiles:
                raise ValueError("Selected profile does not belong to the application")
            return profile
        if len(application.profiles) > 1:
            return _PROFILE_REQUIRED
        return application.profiles[0] if application.profiles else None

    @staticmethod
    def _command_for(
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> ProcessCommand:
        """Merge application and selected-profile launch parameters."""
        try:
            executable_path = Path(application.executable)
            executable_parts = (
                (application.executable,)
                if executable_path.is_file()
                else tuple(shlex.split(application.executable))
            )
        except ValueError as error:
            raise ProcessError(
                f"Invalid command syntax for {application.identifier!r}: {error}"
            ) from error
        if not executable_parts:
            raise ProcessError(f"Empty command for {application.identifier!r}")
        executable, command_arguments = executable_parts[0], executable_parts[1:]
        arguments = command_arguments + application.arguments + (
            profile.arguments if profile else ()
        )
        environment = dict(profile.environment) if profile else {}
        if application.identifier == "kodi":
            # Browser session exports NVIDIA VA-API vars; Kodi must use VDPAU instead.
            environment["LIBVA_DRIVER_NAME"] = ""
            environment["NVD_BACKEND"] = ""
        return ProcessCommand(
            executable=executable,
            arguments=arguments,
            environment=environment or None,
            working_directory=profile.working_directory if profile else None,
        )

    def _on_process_exit(self, event: ProcessExit) -> None:
        """Handle an exit event while rejecting stale sessions."""
        active = self._snapshot
        if active.session_token != event.handle.token:
            _LOGGER.warning("Ignoring stale process exit token=%s", event.handle.token)
            return
        if active.application is None:
            _LOGGER.warning("Ignoring process exit without an active application")
            return
        application = active.application
        if active.handle is not None and self._visibility_detector is not None:
            self._visibility_detector.cancel(active.handle)
        pending_launch = self._pending_launch
        self._pending_launch = None
        self._snapshot = SessionSnapshot(state=SessionState.IDLE)
        if pending_launch is not None and not self._closed:
            self._start(*pending_launch)
            return
        with self._lifecycle_lock:
            if not self._closed:
                self._hand_over_input(False)
        self._observer.finished(application, event.return_code)

    def _hand_over_input(self, release: bool) -> None:
        """Release the input resource before a launch, or reacquire it if it was released."""
        if self._input_resource is None:
            return
        if release:
            # Record first: a failed suspend may leave the resource half released,
            # and the launch failure path must then still resume it.
            self._input_suspended = True
            self._input_resource.suspend()
        elif self._input_suspended:
            self._input_suspended = False
            self._input_resource.resume()


_PROFILE_REQUIRED = object()
