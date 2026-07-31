"""Logging setup port used by the composition root."""

from typing import Protocol


class LoggingConfigurator(Protocol):
    """Configure application logging for a runtime environment."""

    def configure(self, level: str) -> None:
        """Configure logging at the requested level."""
        ...
