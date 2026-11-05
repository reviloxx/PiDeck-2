"""Hide the mouse pointer while PiDeck is navigated without a mouse.

The policy is event-driven: keyboard presses and normalized gamepad or
HDMI-CEC input hide the pointer, and genuine pointer activity shows it again.
X11 also emits synthetic mouse-move events when windows appear beneath a
stationary pointer, so a move only counts once the global position changes.
"""

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QCursor, QGuiApplication

_POINTER_EVENTS = frozenset({
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.Wheel,
})


class CursorVisibility(QObject):
    """Application-wide event filter toggling a blank override cursor."""

    def __init__(self, parent: QObject | None = None) -> None:
        """Create the filter; install it with ``QApplication.installEventFilter``."""
        super().__init__(parent)
        self._hidden = False
        self._hidden_at = QPoint()

    @property
    def hidden(self) -> bool:
        """Return whether PiDeck currently hides the pointer."""
        return self._hidden

    def hide_cursor(self) -> None:
        """Hide the pointer until the mouse is used again."""
        if self._hidden:
            return
        self._hidden = True
        self._hidden_at = QCursor.pos()
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))

    def show_cursor(self) -> None:
        """Restore the normal pointer after mouse activity."""
        if not self._hidden:
            return
        self._hidden = False
        QGuiApplication.restoreOverrideCursor()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Observe input events without consuming them."""
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress:
            self.hide_cursor()
        elif event_type in _POINTER_EVENTS:
            self.show_cursor()
        elif event_type == QEvent.Type.MouseMove and self._hidden and QCursor.pos() != self._hidden_at:
            self.show_cursor()
        return False
