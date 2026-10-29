"""Coordinate appliance power-off and launcher cleanup."""

from collections.abc import Callable
from typing import Protocol


class PowerControl(Protocol):
    """Request power-off from the host operating system."""

    def power_off(self) -> None:
        """Request system power-off or raise an error if it fails."""
        ...


def shutdown(power: PowerControl | None, cleanup: Callable[[], None]) -> None:
    """Power off when configured, then clean up only after an accepted request."""
    if power is not None:
        power.power_off()
    cleanup()
