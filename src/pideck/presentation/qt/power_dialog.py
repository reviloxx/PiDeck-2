"""Explicit, controller-friendly confirmation for system power actions."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .theme import apply_theme
from .wallpaper import WallpaperDialog


class ConfirmationDialog(WallpaperDialog):
    """Confirm an action with keyboard- and controller-friendly controls."""

    def __init__(
        self, title: str, message: str, confirm: str, cancel: str,
        theme: ThemeDefinition, parent: QWidget,
    ) -> None:
        """Build a confirmation defaulting to cancellation."""
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        message_label = QLabel(message, self)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)
        buttons = QHBoxLayout()
        self.cancel = QPushButton(cancel, self)
        self.confirm = QPushButton(confirm, self)
        for button in (self.cancel, self.confirm):
            button.setObjectName("settings_footer")
            button.installEventFilter(self)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.cancel.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.accept)
        apply_theme(self, theme)
        self.cancel.setFocus(Qt.FocusReason.OtherFocusReason)

    def handle_input(self, event: InputEvent) -> None:
        """Route gamepad navigation and activation to the confirmation buttons."""
        if not event.pressed:
            return
        if event.action is InputAction.BACK:
            self.reject()
        elif event.action is InputAction.ACTIVATE:
            focus = self.focusWidget()
            if isinstance(focus, QPushButton):
                focus.click()
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            key = Qt.Key.Key_Left if event.action is InputAction.LEFT else Qt.Key.Key_Right
            focus = self.focusWidget()
            if focus is not None:
                QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Move between confirmation buttons with left and right."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if watched in (self.cancel, self.confirm) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.confirm if event.key() == Qt.Key.Key_Right else self.cancel
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)


class PowerDialog(ConfirmationDialog):
    """Confirm a system power action without implicit activation."""

    def __init__(self, action: str, theme: ThemeDefinition, language: str, parent: QWidget) -> None:
        """Create a power-action confirmation."""
        if action not in {"restart", "shutdown"}:
            raise ValueError("Invalid system power action")
        super().__init__(
            tr(language, action), tr(language, f"confirm_{action}"),
            tr(language, action), tr(language, "cancel"), theme, parent,
        )
