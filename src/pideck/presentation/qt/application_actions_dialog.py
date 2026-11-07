"""Controller-friendly action menu for one application in Settings > Applications.

Choosing an action closes the menu; moving continues in the list itself.
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from pideck.application.application_items import ApplicationAction
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import create_system_panel
from .theme import apply_theme
from .wallpaper import WallpaperDialog


class ApplicationActionsDialog(WallpaperDialog):
    """List the actions available for one application as focusable buttons."""

    action_chosen = Signal(str)

    def __init__(
        self, name: str, actions: tuple[ApplicationAction, ...], theme: ThemeDefinition,
        language: str, parent: QWidget,
    ) -> None:
        """Build a fullscreen menu whose last entry closes it."""
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(name)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self._language = language
        layout = create_system_panel(self, max_width=620, max_height=640)
        title = QLabel(name, self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        self._buttons_layout = QVBoxLayout()
        self._buttons_layout.setSpacing(10)
        layout.addLayout(self._buttons_layout)
        layout.addStretch()
        self.buttons: list[QPushButton] = []
        self.back = self._button(tr(language, "back"), "settings_back")
        self.back.clicked.connect(self.reject)
        layout.addWidget(self.back)
        apply_theme(self, theme)
        self.set_actions(actions)

    def set_actions(self, actions: tuple[ApplicationAction, ...], focus: ApplicationAction | None = None) -> None:
        """Replace the offered actions, keeping focus on ``focus`` when still offered."""
        for button in self.buttons:
            self._buttons_layout.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        target: QPushButton | None = None
        for action in actions:
            button = self._button(tr(self._language, f"action_{action.value}"), "settings_footer")
            button.setProperty("action", action.value)
            button.clicked.connect(lambda _=False, chosen=action: self._choose(chosen))
            self._buttons_layout.addWidget(button)
            # Show now; layouts otherwise show new children later, which blocks moving focus to them.
            button.show()
            self.buttons.append(button)
            if action is focus:
                target = button
        (target or (self.buttons[0] if self.buttons else self.back)).setFocus(Qt.FocusReason.OtherFocusReason)

    def actions(self) -> tuple[str, ...]:
        """Return the offered action values in display order."""
        return tuple(str(button.property("action")) for button in self.buttons)

    def choose(self, action: ApplicationAction) -> None:
        """Trigger an offered action as if its button was activated."""
        self._choose(action)

    def handle_input(self, event: InputEvent) -> None:
        """Route controller and remote input to the menu buttons."""
        if not event.pressed:
            return
        if event.action is InputAction.BACK:
            self.reject()
        elif event.action is InputAction.ACTIVATE:
            focus = self.focusWidget()
            if isinstance(focus, QPushButton):
                focus.click()
        elif event.action in (InputAction.UP, InputAction.DOWN):
            self._move_focus(-1 if event.action is InputAction.UP else 1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Move between buttons with Up and Down."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._move_focus(-1 if event.key() == Qt.Key.Key_Up else 1)
                return True
        return super().eventFilter(watched, event)

    def _move_focus(self, step: int) -> None:
        order = [*self.buttons, self.back]
        current = self.focusWidget()
        index = order.index(current) if current in order else 0
        order[max(0, min(len(order) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)

    def _button(self, text: str, name: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(name)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.installEventFilter(self)
        return button

    def _choose(self, action: ApplicationAction) -> None:
        self.action_chosen.emit(action.value)
        self.accept()
