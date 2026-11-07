"""Port for synthesizing keyboard input into the focused external application."""

from enum import StrEnum
from typing import Protocol


class VirtualKey(StrEnum):
    """Keys PiDeck may synthesize for a running application."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    RETURN = "return"
    ESCAPE = "escape"
    BACKSPACE = "backspace"
    MEDIA_PLAY = "media_play"
    MEDIA_PAUSE = "media_pause"
    MEDIA_PLAY_PAUSE = "media_play_pause"
    MEDIA_STOP = "media_stop"
    MEDIA_REWIND = "media_rewind"
    MEDIA_FAST_FORWARD = "media_fast_forward"


class KeyInjector(Protocol):
    """Deliver one complete key press to whichever window has keyboard focus."""

    def tap(self, key: VirtualKey) -> None:
        """Press and release ``key``; failures are logged, never raised."""
        ...

    def close(self) -> None:
        """Release any display connection."""
        ...
