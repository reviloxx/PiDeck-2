"""Read-only, controller-scrollable view of PiDeck release notes.

Notes are Markdown from the published GitHub releases. Links are not opened
and no external resources are loaded, so the view never leaves PiDeck.
"""

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QTextBrowser, QWidget

from pideck.application.pideck_updates import ReleaseNote

_SCROLL_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down)


def release_notes_markdown(notes: tuple[ReleaseNote, ...]) -> str:
    """Combine release notes, newest first, under one heading per version."""
    return "\n\n".join(f"### PiDeck {note.version}\n\n{note.text.strip()}" for note in notes)


class ReleaseNotesView(QTextBrowser):
    """Scroll with Up/Down; hand focus on at either end or with Left/Right."""

    navigation_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty, focusable view."""
        super().__init__(parent)
        self.setObjectName("release_notes")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setMinimumHeight(220)

    def set_notes(self, notes: tuple[ReleaseNote, ...]) -> None:
        """Render notes as Markdown and hide the view when there are none."""
        self.setMarkdown(release_notes_markdown(notes))
        self.verticalScrollBar().setValue(0)
        self.setVisible(bool(notes))

    def scroll(self, key: int) -> bool:
        """Scroll one step; return ``False`` when already at that end."""
        bar = self.verticalScrollBar()
        if key == Qt.Key.Key_Up and bar.value() > bar.minimum():
            bar.setValue(bar.value() - bar.singleStep() * 3)
            return True
        if key == Qt.Key.Key_Down and bar.value() < bar.maximum():
            bar.setValue(bar.value() + bar.singleStep() * 3)
            return True
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Scroll the notes before moving focus to neighbouring controls."""
        key = event.key()
        if key in _SCROLL_KEYS and self.scroll(key):
            event.accept()
            return
        if key in (*_SCROLL_KEYS, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.navigation_requested.emit(key)
            event.accept()
            return
        super().keyPressEvent(event)

    def loadResource(self, resource_type: int, name: QUrl) -> object:
        """Never load images or other resources referenced by release notes."""
        return None
