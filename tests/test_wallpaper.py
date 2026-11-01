"""Verify aspect-preserving wallpaper coverage and fallback rendering."""

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from pideck.presentation.qt.wallpaper import WallpaperWidget


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an application for offscreen rendering."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(("width", "height"), [(2560, 1440), (640, 480), (300, 600)])
def test_wallpaper_covers_all_edges(
    application: QApplication, tmp_path: Path, width: int, height: int,
) -> None:
    """A solid wallpaper reaches every edge at different aspect ratios."""
    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff0000"))
    path = tmp_path / "wallpaper.png"
    assert image.save(str(path))
    widget = WallpaperWidget()
    widget.resize(width, height)
    widget.set_wallpaper(path, "#000000")
    widget.show()
    application.processEvents()
    result = widget.grab().toImage()
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert result.pixelColor(x, y) == QColor("#ff0000")
    widget.close()


def test_wallpaper_crops_and_updates_on_resize(
    application: QApplication, tmp_path: Path,
) -> None:
    """A square crop centers the image, and resizing recomputes the crop."""
    image = QImage(300, 100, QImage.Format.Format_RGB32)
    for x in range(300):
        color = QColor("#ff0000") if x < 100 else QColor("#00ff00") if x < 200 else QColor("#0000ff")
        for y in range(100):
            image.setPixelColor(x, y, color)
    path = tmp_path / "stripes.png"
    assert image.save(str(path))
    widget = WallpaperWidget()
    widget.resize(100, 100)
    widget.set_wallpaper(path, "#000000")
    widget.show()
    application.processEvents()
    assert widget.grab().toImage().pixelColor(5, 50) == QColor("#00ff00")
    widget.resize(300, 100)
    application.processEvents()
    assert widget.grab().toImage().pixelColor(5, 50) == QColor("#ff0000")
    widget.set_wallpaper(None, "#123456")
    application.processEvents()
    assert widget.grab().toImage().pixelColor(5, 50) == QColor("#123456")
    widget.close()


def test_svg_wallpaper_and_missing_fallback(application: QApplication, tmp_path: Path) -> None:
    """SVG artwork scales to cover the widget; a missing file uses the theme color."""
    path = tmp_path / "wallpaper.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
        '<rect width="100" height="50" fill="#ab4512"/></svg>',
        encoding="utf-8",
    )
    widget = WallpaperWidget()
    widget.resize(600, 400)
    widget.set_wallpaper(path, "#123456")
    widget.show()
    application.processEvents()
    assert widget.grab().toImage().pixelColor(0, 0) == QColor("#ab4512")
    widget.set_wallpaper(tmp_path / "missing.png", "#123456")
    application.processEvents()
    assert widget.grab().toImage().pixelColor(0, 0) == QColor("#123456")
    widget.close()
