"""Display modes and operations independent of the settings presentation."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class DisplayError(Exception):
    """Report an unavailable display backend or failed mode operation."""


@dataclass(frozen=True, slots=True)
class DisplayMode:
    """Identify one supported resolution and refresh rate."""

    resolution: str
    rate: str


@dataclass(frozen=True, slots=True)
class DisplayOutput:
    """Describe a connected, active output and its supported modes."""

    name: str
    current: DisplayMode
    modes: tuple[DisplayMode, ...]


@runtime_checkable
class DisplayChange(Protocol):
    """Represent a temporary display change guarded by a rollback watchdog."""

    @property
    def deadline(self) -> float:
        """Return the monotonic confirmation deadline."""
        ...

    def confirm(self) -> None:
        """Keep and persist the new mode, or restore it on failure."""
        ...

    def cancel(self) -> None:
        """Restore the prior mode and wait for the watchdog result."""
        ...


class DisplayGateway(Protocol):
    """Inspect X11 outputs and start a temporary, guarded mode change."""

    def inspect(self) -> tuple[DisplayOutput, ...]:
        """Return active outputs and their supported modes."""
        ...

    def begin(self, output: str, mode: DisplayMode) -> DisplayChange:
        """Temporarily apply a validated mode with an independent rollback."""
        ...
