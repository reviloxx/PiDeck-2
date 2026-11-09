"""Theme background rendering for appliance-sized Qt windows."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QHideEvent, QPaintEvent, QPainter, QPixmap, QShowEvent
from PySide6.QtWidgets import QDialog, QWidget

from .blurred_backdrop import capture_blurred_screen, compose_backdrop
from .fullscreen import (
    apply_fullscreen_geometry,
    cancel_fullscreen_reassert,
    present_fullscreen,
)

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

    @property
    def background(self) -> QColor:
        """Return the solid fallback color."""
        return self._background

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
    """Fullscreen dialog with a blurred capture of whatever is behind it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a dialog that captures a blurred backdrop before it appears."""
        super().__init__(parent)
        self._background_image = _Wallpaper()
        self._blurred_backdrop = QPixmap()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_wallpaper(self, path: Path | None, background: str) -> None:
        """Store the theme fallback color used when a screen grab is unusable."""
        self._background_image.set(path, background)
        self.update()

    def _prepare_blurred_backdrop(self) -> None:
        """Capture the current screen once before this dialog becomes visible."""
        fill = self._background_image.background
        if not fill.isValid():
            fill = QColor("#101214")
        self._blurred_backdrop = compose_backdrop(capture_blurred_screen(), fill)

    def showFullScreen(self) -> None:
        """Present as a frameless fullscreen window and reassert after map."""
        present_fullscreen(self)

    def exec(self) -> int:  # noqa: A003 - Qt API
        """Capture a blurred backdrop, then run the modal dialog loop fullscreen."""
        self._prepare_blurred_backdrop()
        present_fullscreen(self)
        return int(super().exec())

    def showEvent(self, event: QShowEvent) -> None:
        """Capture before the first paint and keep geometry when already fullscreen."""
        if self._blurred_backdrop.isNull():
            self._prepare_blurred_backdrop()
        super().showEvent(event)
        if self.windowState() & Qt.WindowState.WindowFullScreen:
            apply_fullscreen_geometry(self)
            if not self.isFullScreen():
                QWidget.showFullScreen(self)

    def hideEvent(self, event: QHideEvent) -> None:
        """Drop pending fullscreen reasserts when the dialog hides."""
        cancel_fullscreen_reassert(self)
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the blurred screen (or theme wallpaper) behind child controls."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._blurred_backdrop.isNull():
            painter.drawPixmap(self.rect(), self._blurred_backdrop)
            return
        self._background_image.paint(self)
