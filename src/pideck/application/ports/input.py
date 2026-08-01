"""Normalized input events consumed by PiDeck presentation targets."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class InputAction(StrEnum):
    """Actions shared by keyboard, gamepad, CEC, and future input adapters."""

    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    ACTIVATE = "activate"
    BACK = "back"
    HOME = "home"


@dataclass(frozen=True, slots=True)
class InputEvent:
    """A normalized input action from one physical source."""

    action: InputAction
    source: str
    pressed: bool = True


class InputSink(Protocol):
    """Receive normalized input events on the application/UI event loop."""

    def handle_input(self, event: InputEvent) -> None:
        """Handle one normalized input event."""
        ...


class InputAdapter(Protocol):
    """Lifecycle contract for a physical input adapter."""

    def start(self, sink: InputSink) -> None:
        """Begin delivering normalized events."""
        ...

    def close(self) -> None:
        """Stop the adapter and release device resources."""
        ...
