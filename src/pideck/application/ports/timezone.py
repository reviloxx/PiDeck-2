"""Host timezone models and operations independent of the presentation layer."""

from typing import Protocol


class TimezoneError(Exception):
    """Report an unavailable timezone backend or rejected timezone change."""


class TimezoneGateway(Protocol):
    """Inspect and change the host timezone through an injected platform adapter."""

    def current_timezone(self) -> str:
        """Return the active IANA timezone identifier."""
        ...

    def list_timezones(self) -> tuple[str, ...]:
        """Return sorted IANA timezone identifiers available on this host."""
        ...

    def set_timezone(self, timezone: str) -> None:
        """Apply ``timezone`` as the host timezone."""
        ...
