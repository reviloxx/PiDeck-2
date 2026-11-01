"""Theme background rendering for appliance-sized Qt windows."""

import logging
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QWidget

_LOGGER = logging.getLogger(__name__)


class _Wallpaper:
    """Keep the rendered image and its fallback color for one widget."""

    def __init__(self) -> None:
        """Create an initially empty background."""
        self._background = QColor()
        self._wallpaper = QPixmap()

    def set(self, path: Path | None, background: str) -> None:
        """Load a wallpaper and its fallback color."""
        self._background = QColor(background)
        self._wallpaper = QPixmap(str(path)) if path is not None else QPixmap()
        if path is not None and self._wallpaper.isNull():
            _LOGGER.warning("Unable to load wallpaper: %s", path)

    def paint(self, widget: QWidget) -> None:
        """Fill a widget with a centered, cropped version of the wallpaper."""
        painter = QPainter(widget)
        painter.fillRect(widget.rect(), self._background)
        if self._wallpaper.isNull() or widget.width() <= 0 or widget.height() <= 0:
            return
        image_width = self._wallpaper.width()
        image_height = self._wallpaper.height()
        scale = max(widget.width() / image_width, widget.height() / image_height)
        source_width = widget.width() / scale
        source_height = widget.height() / scale
        source = QRectF(
            (image_width - source_width) / 2,
            (image_height - source_height) / 2,
            source_width,
            source_height,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(widget.rect()), self._wallpaper, source)


class WallpaperWidget(QWidget):
    """Paint a theme wallpaper centered and cropped to cover the widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a background widget that can contain normal Qt layouts."""
        super().__init__(parent)
        self._background_image = _Wallpaper()

    def set_wallpaper(self, path: Path | None, background: str) -> None:
        """Set a wallpaper and fallback color without changing child widgets."""
        self._background_image.set(path, background)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the theme behind child controls."""
        self._background_image.paint(self)


class WallpaperDialog(QDialog):
    """Paint an aspect-preserving wallpaper behind a dialog's controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a dialog with a shared wallpaper renderer."""
        super().__init__(parent)
        self._background_image = _Wallpaper()

    def set_wallpaper(self, path: Path | None, background: str) -> None:
        """Set the dialog's wallpaper and its fallback color."""
        self._background_image.set(path, background)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the dialog background without covering its child controls."""
        super().paintEvent(event)
        self._background_image.paint(self)
