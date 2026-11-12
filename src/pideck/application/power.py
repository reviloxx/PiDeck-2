"""Coordinate appliance power-off, hibernate, and launcher cleanup."""

from collections.abc import Callable
from typing import Protocol


class PowerControl(Protocol):
    """Request power actions from the host operating system."""

    def power_off(self) -> None:
        """Request system power-off or raise an error if it fails."""
        ...

    def hibernate(self) -> None:
        """Request system hibernate or raise an error if it fails."""
        ...


def shutdown(power: PowerControl | None, cleanup: Callable[[], None]) -> None:
    """Power off when configured, then clean up only after an accepted request."""
    if power is not None:
        power.power_off()
    cleanup()


def hibernate(power: PowerControl | None, cleanup: Callable[[], None]) -> None:
    """Hibernate when configured, then clean up only after an accepted request."""
    if power is not None:
        power.hibernate()
    cleanup()
