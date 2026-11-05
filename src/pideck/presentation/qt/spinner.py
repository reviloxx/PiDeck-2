"""Small painted busy indicator shared by launcher tiles and update rows."""

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

_STEP_DEGREES = 30
_INTERVAL_MS = 60


class BusySpinner(QWidget):
    """Rotating arc that animates only while it is visible and running."""

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        """Create a square spinner; its color follows the theme primary color."""
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = QColor(self.palette().highlight().color())
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    @property
    def running(self) -> bool:
        """Return whether the indicator is currently animating."""
        return self._timer.isActive()

    def set_color(self, color: str) -> None:
        """Use a theme color for the arc."""
        self._color = QColor(color)
        self.update()

    def set_running(self, running: bool) -> None:
        """Show and animate, or hide and stop, the indicator."""
        self.setVisible(running)
        if running:
            self._timer.start()
        else:
            self._timer.stop()

    def _advance(self) -> None:
        self._angle = (self._angle + _STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint a faint track with a brighter rotating arc."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(2.0, self.width() / 8)
        bounds = QRectF(self.rect()).adjusted(width, width, -width, -width)
        track = QColor(self._color)
        track.setAlphaF(0.25)
        painter.setPen(QPen(track, width))
        painter.drawEllipse(bounds)
        pen = QPen(self._color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(bounds, -self._angle * 16, 100 * 16)
