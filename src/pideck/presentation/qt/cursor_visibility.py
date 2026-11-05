"""Keep a single highlighted control across mouse, keyboard, gamepad and CEC input.

The pointer is hidden while PiDeck is navigated without a mouse, and shown
again on genuine pointer activity. Instead of a separate hover highlight, the
mouse moves keyboard focus: hovering a focusable control focuses it, so the
focus highlight is the only highlight and two items can never glow at once.

X11 emits synthetic Enter and mouse-move events when windows appear or content
scrolls beneath a stationary pointer, so these only count once the pointer's
global position has changed.
"""

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

_POINTER_EVENTS = frozenset({
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.Wheel,
})


class CursorVisibility(QObject):
    """Application-wide event filter for pointer visibility and hover focus."""

    def __init__(self, parent: QObject | None = None) -> None:
        """Create the filter; install it with ``QApplication.installEventFilter``."""
        super().__init__(parent)
        self._hidden = False
        self._hidden_at = QPoint()
        # A pointer resting where it was at startup must not take focus from the first tile.
        self._last_hover_at = QCursor.pos()

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
        """Restore the normal pointer and focus the control beneath it."""
        if not self._hidden:
            return
        self._hidden = False
        QGuiApplication.restoreOverrideCursor()
        position = QCursor.pos()
        self._focus_from_pointer(QApplication.widgetAt(position), position)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Observe input events without consuming them."""
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress:
            self.hide_cursor()
        elif event_type in _POINTER_EVENTS:
            self.show_cursor()
        elif event_type == QEvent.Type.MouseMove and self._hidden and QCursor.pos() != self._hidden_at:
            self.show_cursor()
        elif event_type == QEvent.Type.Enter and not self._hidden and isinstance(watched, QWidget):
            self._focus_from_pointer(watched, QCursor.pos())
        return False

    def _focus_from_pointer(self, widget: QWidget | None, position: QPoint) -> None:
        """Focus the nearest focusable control under a pointer that really moved."""
        if position == self._last_hover_at:
            return
        target = _focusable_ancestor(widget)
        if target is None:
            return
        self._last_hover_at = QPoint(position)
        if not target.hasFocus():
            target.setFocus(Qt.FocusReason.MouseFocusReason)


def _focusable_ancestor(widget: QWidget | None) -> QWidget | None:
    """Return ``widget`` or its closest parent control that accepts click focus.

    Top-level windows are never returned: focusing a window itself would leave
    no control visibly selected.
    """
    while widget is not None and not widget.isWindow():
        if (
            widget.focusPolicy() & Qt.FocusPolicy.ClickFocus
            and widget.isEnabled()
            and widget.isVisible()
        ):
            return widget
        widget = widget.parentWidget()
    return None
