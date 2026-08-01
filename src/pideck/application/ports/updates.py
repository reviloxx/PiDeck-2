"""Ports and immutable models for application update workflows."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol

from pideck.domain.configuration import ApplicationDefinition


class UpdateStatus(StrEnum):
    """States shown by the settings Updates page."""

    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    UPDATING = "updating"
    UPDATED = "updated"
    PASSWORD_REQUIRED = "password_required"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Current update state for one configured application."""

    application_id: str
    application_name: str
    status: UpdateStatus
    current_version: str | None = None
    available_version: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateHandle:
    """Identify one cancellable update operation."""

    application_id: str
    token: str


UpdateCallback = Callable[[UpdateInfo], None]


class UpdateGateway(Protocol):
    """Inspect and update applications through their native package manager."""

    def check(self, application: ApplicationDefinition) -> UpdateInfo:
        """Return the current update availability."""
        ...

    def start(
        self,
        application: ApplicationDefinition,
        password: str | None,
        callback: UpdateCallback,
    ) -> UpdateHandle:
        """Start a cancellable update operation."""
        ...

    def cancel(self, handle: UpdateHandle) -> None:
        """Cancel an active update operation."""
        ...

    def cancel_by_application(self, application_id: str) -> None:
        """Cancel the active update for an application."""
        ...

    def close(self) -> None:
        """Cancel all active update operations."""
        ...
