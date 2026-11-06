"""Port for applying a keyboard layout to the graphical session."""

from typing import Protocol

from pideck.domain.keyboard_layout import KeyboardLayout


class KeyboardLayoutGateway(Protocol):
    """Apply a keyboard layout to every keyboard in the session."""

    def apply(self, layout: KeyboardLayout) -> bool:
        """Apply ``layout``; return whether it succeeded. Failures are logged, never raised."""
        ...
