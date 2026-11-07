"""Let Backspace go back on every PiDeck screen, like Escape and the controller's Back.

Every surface already treats Escape as Back, so Backspace is translated into
Escape for the focused widget. Text fields keep Backspace for deleting, and so
does any window whose ``BACKSPACE_EDITS_TEXT`` property is set, such as the
on-screen keyboard. Held keys do not repeat Back, so one press leaves one level.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

BACKSPACE_EDITS_TEXT = "backspace_edits_text"


def _edits_text(widget: QObject) -> bool:
    if not isinstance(widget, QWidget):
        return False
    if widget.window().property(BACKSPACE_EDITS_TEXT):
        return True
    if isinstance(widget, QLineEdit | QTextEdit | QPlainTextEdit):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    return isinstance(widget, QAbstractSpinBox)


class BackspaceAsBack(QObject):
    """Application-wide event filter that turns Backspace into Escape."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Replace a Backspace press outside text fields with an Escape press."""
        if (
            event.type() != QEvent.Type.KeyPress
            or not isinstance(event, QKeyEvent)
            or event.key() != Qt.Key.Key_Backspace
            or event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            or _edits_text(watched)
        ):
            return False
        if not event.isAutoRepeat():
            for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(watched, QKeyEvent(event_type, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        return True
