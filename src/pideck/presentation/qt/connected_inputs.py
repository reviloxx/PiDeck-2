"""Footer strip showing which physical input sources are currently connected."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .theme import icon_for_path

_ICON_SIZE = 24


@dataclass(frozen=True, slots=True)
class ConnectedInputs:
    """Snapshot of live CEC / gamepad / keyboard presence."""

    cec: bool = False
    gamepad: bool = False
    keyboard: bool = False


class ConnectedInputsStrip(QWidget):
    """Non-focusable row of theme input icons; hidden sources stay invisible."""

    def __init__(
        self,
        theme: ThemeDefinition,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        """Build empty icon slots; call ``set_inputs`` when presence changes."""
        super().__init__(parent)
        self._theme = theme
        self._language = language
        self.setObjectName("connected_inputs_strip")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cec = self._make_icon("input_cec", "input_hint_cec")
        self._gamepad = self._make_icon("input_gamepad", "input_hint_gamepad")
        self._keyboard = self._make_icon("input_keyboard", "input_hint_keyboard")
        for label in (self._cec, self._gamepad, self._keyboard):
            layout.addWidget(label)
        layout.addStretch(1)
        self.set_inputs(ConnectedInputs())

    def set_language(self, language: str) -> None:
        """Refresh tooltips when the UI language changes."""
        self._language = language if language in {"en", "de"} else "en"
        self._cec.setToolTip(tr(self._language, "input_hint_cec"))
        self._gamepad.setToolTip(tr(self._language, "input_hint_gamepad"))
        self._keyboard.setToolTip(tr(self._language, "input_hint_keyboard"))

    def set_theme(self, theme: ThemeDefinition) -> None:
        """Reload icon pixmaps after a theme change."""
        self._theme = theme
        size = QSize(_ICON_SIZE, _ICON_SIZE)
        self._cec.setPixmap(icon_for_path(theme.icons.get("input_cec")).pixmap(size))
        self._gamepad.setPixmap(icon_for_path(theme.icons.get("input_gamepad")).pixmap(size))
        self._keyboard.setPixmap(icon_for_path(theme.icons.get("input_keyboard")).pixmap(size))

    def set_inputs(self, inputs: ConnectedInputs) -> None:
        """Show only icons for sources that are currently connected."""
        self._cec.setVisible(inputs.cec)
        self._gamepad.setVisible(inputs.gamepad)
        self._keyboard.setVisible(inputs.keyboard)
        self.setVisible(inputs.cec or inputs.gamepad or inputs.keyboard)

    def _make_icon(self, theme_key: str, hint_key: str) -> QLabel:
        """Create one decorative input-source icon label."""
        label = QLabel(self)
        label.setObjectName("connected_input_icon")
        label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        label.setPixmap(icon_for_path(self._theme.icons.get(theme_key)).pixmap(QSize(_ICON_SIZE, _ICON_SIZE)))
        label.setToolTip(tr(self._language, hint_key))
        label.hide()
        return label
