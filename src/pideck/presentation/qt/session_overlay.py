"""Overlay shown above a running application for returning to PiDeck."""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import create_system_panel
from .theme import apply_theme
from .wallpaper import WallpaperDialog


class SessionOverlay(WallpaperDialog):
    """Stay-on-top overlay with a single action that stops the running application."""

    return_requested = Signal()
    dismissed = Signal()

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget | None = None) -> None:
        """Build the overlay; call ``open_overlay`` to show it above an application."""
        super().__init__(parent)
        self._language = language
        self.setModal(True)
        self.setWindowTitle(tr(language, "session_overlay_title"))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        layout = create_system_panel(self, max_width=760, max_height=320)
        title = QLabel(tr(language, "session_overlay_title"), self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        message = QLabel(tr(language, "session_overlay_message"), self)
        message.setObjectName("settings_label")
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addStretch()
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.return_button = QPushButton(tr(language, "session_overlay_return"), self)
        self.return_button.setObjectName("settings_footer")
        self.return_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.return_button.installEventFilter(self)
        buttons.addWidget(self.return_button)
        layout.addLayout(buttons)
        self.return_button.clicked.connect(self._request_return)
        apply_theme(self, theme)

    def open_overlay(self) -> None:
        """Raise the overlay above the running application and focus its action."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.return_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self) -> None:
        """Hide the overlay without stopping the application."""
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def handle_input(self, event: InputEvent) -> None:
        """Activate the return control; MENU dismisses; BACK does not quit the app."""
        if not event.pressed:
            return
        if event.action in (InputAction.MENU, InputAction.HOME):
            self.dismiss()
        elif event.action is InputAction.ACTIVATE:
            self.return_button.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Escape is not Back-to-PiDeck; Super/menu equivalents dismiss via input routing."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.return_button.click()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            # Back must not close the application from this surface.
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
        self.return_requested.emit()
