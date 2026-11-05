"""TV-friendly on/off switch that always states its current value."""

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPaintEvent, QPainter
from PySide6.QtWidgets import QCheckBox, QWidget

_TRACK_WIDTH = 52
_TRACK_HEIGHT = 28
_SPACING = 12


class ToggleSwitch(QCheckBox):
    """Checkbox-compatible switch rendering a track, knob and On/Off label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a switch with English labels until a language is applied."""
        super().__init__(parent)
        self._on_text = "On"
        self._off_text = "Off"
        self._colors = {
            "on": QColor("#63d6c5"),
            "off": QColor("#3a4148"),
            "knob": QColor("#f4f7f7"),
            "text": QColor("#f4f7f7"),
        }
        self.toggled.connect(self.update)

    def set_state_texts(self, on_text: str, off_text: str) -> None:
        """Set the localized labels shown for each state."""
        self._on_text = on_text
        self._off_text = off_text
        self.updateGeometry()
        self.update()

    def state_text(self) -> str:
        """Return the label describing the current state."""
        return self._on_text if self.isChecked() else self._off_text

    def set_colors(self, on: str, off: str, knob: str, text: str) -> None:
        """Apply theme colors for the track, knob and state label."""
        self._colors = {"on": QColor(on), "off": QColor(off), "knob": QColor(knob), "text": QColor(text)}
        self.update()

    def sizeHint(self) -> QSize:
        """Reserve space for the track and the longer state label."""
        metrics = QFontMetrics(self.font())
        text_width = max(metrics.horizontalAdvance(self._on_text), metrics.horizontalAdvance(self._off_text))
        return QSize(_TRACK_WIDTH + _SPACING + text_width, max(_TRACK_HEIGHT, metrics.height()) + 4)

    def minimumSizeHint(self) -> QSize:
        """Never shrink below the complete switch and label."""
        return self.sizeHint()

    def hitButton(self, pos: QPoint) -> bool:
        """Toggle when any part of the switch or label is clicked."""
        return self.rect().contains(pos)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the switch independently of the platform style."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - _TRACK_HEIGHT) / 2
        track = QRectF(0, top, _TRACK_WIDTH, _TRACK_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colors["on"] if self.isChecked() else self._colors["off"])
        painter.drawRoundedRect(track, _TRACK_HEIGHT / 2, _TRACK_HEIGHT / 2)
        knob_size = _TRACK_HEIGHT - 6
        knob_left = track.right() - knob_size - 3 if self.isChecked() else track.left() + 3
        painter.setBrush(self._colors["knob"])
        painter.drawEllipse(QRectF(knob_left, top + 3, knob_size, knob_size))
        painter.setPen(self._colors["text"])
        painter.setFont(self.font())
        text_rect = self.rect().adjusted(_TRACK_WIDTH + _SPACING, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.state_text())
