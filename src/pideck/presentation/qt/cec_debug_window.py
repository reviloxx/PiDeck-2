"""TV-friendly live log of HDMI-CEC remote key codes."""

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition
from pideck.infrastructure.input_cec import CecKeyObservation

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme
from .wallpaper import WallpaperDialog

_SCROLL_STEP = 80
_MAX_VISIBLE = 40


def format_cec_observation(observation: CecKeyObservation, language: str) -> str:
    """Render one key press as a single readable debug line."""
    mapping = (
        observation.action.value.replace("_", " ").title()
        if observation.action is not None
        else tr(language, "cec_debug_unmapped")
    )
    return f"0x{observation.code:02x}  {observation.label}  →  {mapping}"


class CecDebugWindow(WallpaperDialog):
    """Show recent and live CEC key presses so unmapped remotes can be diagnosed."""

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget) -> None:
        """Create an empty log; call ``set_history`` after show to seed recent keys."""
        super().__init__(parent)
        self._language = language
        self._lines: list[str] = []
        self.setWindowTitle(tr(language, "cec_debug"))
        self.setModal(True)
        frame = build_system_window(
            self, tr(language, "cec_debug"), tr(language, "cec_debug_description"),
        )
        self.status = frame.status
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("settings_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame.content.addWidget(self.scroll, stretch=1)
        self.clear = add_action_button(self, frame.footer, tr(language, "cec_debug_clear"))
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        for button in (self.clear, self.back):
            button.installEventFilter(self)
        self.clear.clicked.connect(self._clear)
        self.back.clicked.connect(self.reject)
        apply_theme(self, theme)
        self.back.setFocus(Qt.FocusReason.OtherFocusReason)
        set_status(self.status, "")
        self._rebuild()

    def set_history(self, observations: Sequence[CecKeyObservation]) -> None:
        """Replace the on-screen list with ``observations`` (oldest first)."""
        self._lines = [
            format_cec_observation(item, self._language) for item in observations[-_MAX_VISIBLE:]
        ]
        self._rebuild()

    def append(self, observation: CecKeyObservation) -> None:
        """Prepend one live key press to the visible log."""
        self._lines.append(format_cec_observation(observation, self._language))
        del self._lines[:-_MAX_VISIBLE]
        self._rebuild()

    def _clear(self) -> None:
        """Clear the on-screen list without changing the adapter history."""
        self._lines.clear()
        self._rebuild()

    def _rebuild(self) -> None:
        page = QWidget(self.scroll)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 14, 0)
        layout.setSpacing(8)
        if not self._lines:
            empty = QLabel(tr(self._language, "cec_debug_empty"), page)
            empty.setObjectName("settings_hint")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        else:
            for line in reversed(self._lines):
                label = QLabel(line, page)
                label.setObjectName("system_info_value")
                label.setWordWrap(True)
                label.setProperty("cec_debug_line", True)
                layout.addWidget(label)
        layout.addStretch()
        self.scroll.setWidget(page)

    def _scroll_by(self, step: int) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + step)

    def handle_input(self, event: InputEvent) -> None:
        """Scroll with up/down, move footer focus with left/right, close with Back."""
        if not event.pressed:
            return
        if event.action is InputAction.BACK or event.action is InputAction.MENU:
            self.reject()
        elif event.action in (InputAction.UP, InputAction.DOWN):
            self._scroll_by(-_SCROLL_STEP if event.action is InputAction.UP else _SCROLL_STEP)
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            self._focus_button(left=event.action is InputAction.LEFT)
        elif event.action is InputAction.ACTIVATE:
            focus = self.focusWidget()
            if focus in (self.clear, self.back) and focus.isEnabled():
                focus.click()

    def _focus_button(self, *, left: bool) -> None:
        target = self.clear if left else self.back
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Scroll with vertical keys while focus stays on the footer."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            steps = {
                Qt.Key.Key_Up: -_SCROLL_STEP, Qt.Key.Key_Down: _SCROLL_STEP,
                Qt.Key.Key_PageUp: -self.scroll.viewport().height(),
                Qt.Key.Key_PageDown: self.scroll.viewport().height(),
            }
            if event.key() in steps:
                self._scroll_by(steps[Qt.Key(event.key())])
                return True
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self._focus_button(left=event.key() == Qt.Key.Key_Left)
                return True
        return super().eventFilter(watched, event)
