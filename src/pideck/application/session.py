"""Application launch session orchestration."""

from dataclasses import dataclass
from enum import StrEnum
import logging
import threading
from typing import Protocol
from uuid import uuid4

from pideck.application.ports.process import (
    ProcessCommand,
    ProcessExit,
    ProcessHandle,
    ProcessSupervisor,
)
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile
from pideck.domain.errors import PiDeckError

_LOGGER = logging.getLogger(__name__)


class SessionState(StrEnum):
    """Lifecycle states of the currently managed application session."""

    IDLE = "idle"
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

    def finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Handle an application returning control to PiDeck."""
        ...

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Handle a launch or supervision failure."""
        ...


class ApplicationSessionService:
    """Coordinate launch requests, process supervision, and launcher return."""

    def __init__(self, supervisor: ProcessSupervisor, observer: SessionObserver) -> None:
        """Create a session service with injected process and presentation ports."""
        self._supervisor = supervisor
        self._observer = observer
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
        self.stop()
        self._supervisor.close()

    def _start(
        self,
        application: ApplicationDefinition,
        profile: ApplicationProfile | None,
    ) -> LaunchResult:
        """Build a safe process command and start it under a unique session token."""
        token = f"{application.identifier}-{uuid4().hex}"
        self._snapshot = SessionSnapshot(
            state=SessionState.RUNNING,
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
            handle = self._supervisor.start(
                self._command_for(application, profile),
                token,
                handle_process_exit,
            )
        except PiDeckError as error:
            start_ready.set()
            self._snapshot = SessionSnapshot(state=SessionState.IDLE)
            message = str(error)
            self._observer.failed(application, message)
            return LaunchResult(LaunchStatus.FAILED, application, message=message)
        self._snapshot = SessionSnapshot(
            state=SessionState.RUNNING,
            application=application,
            profile=profile,
            handle=handle,
            session_token=token,
        )
        self._observer.started(application, profile)
        start_ready.set()
        return LaunchResult(LaunchStatus.STARTED, application)

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
        arguments = application.arguments + (profile.arguments if profile else ())
        return ProcessCommand(
            executable=application.executable,
            arguments=arguments,
            environment=profile.environment if profile else None,
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
        pending_launch = self._pending_launch
        self._pending_launch = None
        self._snapshot = SessionSnapshot(state=SessionState.IDLE)
        if pending_launch is not None:
            self._start(*pending_launch)
            return
        self._observer.finished(application, event.return_code)


_PROFILE_REQUIRED = object()
