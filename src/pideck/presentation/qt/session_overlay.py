"""Console-style overlay shown above a running application for returning to PiDeck."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .blurred_backdrop import (
    blur_pixmap,
    capture_blurred_screen,
    compose_backdrop,
    grab_is_useful,
)
from .localization import tr
from .theme import apply_theme

# Re-export blur helpers for existing tests and callers.
__all__ = [
    "SessionOverlay",
    "blur_pixmap",
    "capture_blurred_screen",
    "compose_backdrop",
    "grab_is_useful",
]


class SessionOverlay(QDialog):
    """Stay-on-top quick menu with a blurred app backdrop and one return action."""

    return_requested = Signal()
    dismissed = Signal()

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget | None = None) -> None:
        """Build the overlay; call ``open_overlay`` to show it above an application."""
        super().__init__(parent)
        self._theme = theme
        self._language = language
        self._backdrop = QPixmap()
        self.setObjectName("session_overlay")
        self.setModal(True)
        self.setWindowTitle("PiDeck")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)
        self._backdrop_label = QLabel(self)
        self._backdrop_label.setObjectName("session_overlay_backdrop")
        self._backdrop_label.setScaledContents(True)
        self._backdrop_label.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._backdrop_label.lower()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._card = QFrame(self)
        self._card.setObjectName("session_overlay_card")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(36, 28, 36, 28)
        card_layout.setSpacing(0)
        self.return_button = QPushButton(tr(language, "session_overlay_return"), self._card)
        self.return_button.setObjectName("session_overlay_return")
        self.return_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.return_button.installEventFilter(self)
        card_layout.addWidget(self.return_button)
        row.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        self.return_button.clicked.connect(self._request_return)
        apply_theme(self, theme)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the backdrop label matched to the dialog size."""
        super().resizeEvent(event)
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()

    def open_overlay(self) -> None:
        """Capture the screen, raise the overlay, and focus the return action."""
        if self.isVisible():
            self.hide()
            QApplication.processEvents()
        fill = QColor(self._theme.colors["background"])
        self._backdrop = compose_backdrop(capture_blurred_screen(), fill)
        self._backdrop_label.setPixmap(self._backdrop)
        self.showFullScreen()
        self._backdrop_label.setGeometry(self.rect())
        self._backdrop_label.lower()
        self._backdrop_label.show()
        self.raise_()
        self.activateWindow()
        self.return_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self) -> None:
        """Hide the overlay without stopping the application."""
        if self.isVisible():
            self.hide()
            self._backdrop = QPixmap()
            self._backdrop_label.clear()
            self.dismissed.emit()

    def handle_input(self, event: InputEvent) -> None:
        """Activate the return control; MENU/BACK dismiss without quitting the app."""
        if not event.pressed:
            return
        if event.action in (InputAction.MENU, InputAction.HOME, InputAction.BACK):
            self.dismiss()
        elif event.action is InputAction.ACTIVATE:
            self.return_button.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Return activates the action; Escape must not quit the application."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.return_button.click()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keep Return/Enter on the button activating the return action."""
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and watched is self.return_button
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
        ):
            self.return_button.click()
            return True
        return super().eventFilter(watched, event)

    def _request_return(self) -> None:
        """Hide first so the launcher can restore without the overlay on top."""
        self.hide()
        self._backdrop = QPixmap()
        self._backdrop_label.clear()
        self.return_requested.emit()
