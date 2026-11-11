"""Tests for theme-colored background style rendering."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from pideck.domain.backgrounds import BACKGROUND_STYLE_IDS, DEFAULT_BACKGROUND
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.backgrounds import (
    background_template_path,
    normalize_background,
    render_background_pixmap,
)
from pideck.presentation.qt.theme import apply_theme, set_active_background
from pideck.presentation.qt.wallpaper import WallpaperWidget


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for background tests."""
    return QApplication.instance() or QApplication([])


def test_normalize_background_falls_back_to_default() -> None:
    """Unknown or empty style ids resolve to Glow."""
    assert normalize_background(None) == DEFAULT_BACKGROUND
    assert normalize_background("") == DEFAULT_BACKGROUND
    assert normalize_background("nebula") == DEFAULT_BACKGROUND
    assert normalize_background("horizon") == "horizon"


def test_every_background_template_is_shipped() -> None:
    """Each catalog style has a checked-in SVG template."""
    for style_id in BACKGROUND_STYLE_IDS:
        path = background_template_path(style_id)
        assert path is not None and path.is_file(), style_id


def test_background_styles_render_distinct_pixmaps(application: QApplication) -> None:
    """Different styles produce different wallpapers for the same theme colors."""
    del application
    pixmaps = {
        style_id: render_background_pixmap(style_id, DEFAULT_THEME.colors)
        for style_id in BACKGROUND_STYLE_IDS
    }
    assert all(not pixmap.isNull() for pixmap in pixmaps.values())
    digests = {style_id: _digest(pixmap.toImage()) for style_id, pixmap in pixmaps.items()}
    assert len(set(digests.values())) == len(BACKGROUND_STYLE_IDS)


def test_background_colors_follow_theme_palette(application: QApplication) -> None:
    """The same style retints when theme colors change."""
    del application
    warm = dict(DEFAULT_THEME.colors)
    warm.update(
        {
            "background": "#201008",
            "surface": "#402010",
            "primary": "#ffaa00",
            "running": "#ff6600",
            "focus": "#ffaa00",
        }
    )
    cool = dict(DEFAULT_THEME.colors)
    cool.update(
        {
            "background": "#081020",
            "surface": "#102040",
            "primary": "#00aaff",
            "running": "#66ccff",
            "focus": "#00aaff",
        }
    )
    warm_image = render_background_pixmap("ribbons", warm).toImage()
    cool_image = render_background_pixmap("ribbons", cool).toImage()
    assert _digest(warm_image) != _digest(cool_image)


def test_wallpaper_widget_paints_generated_background(application: QApplication) -> None:
    """WallpaperWidget accepts a rendered pixmap from apply_theme."""
    set_active_background("constellation", Path.cwd())
    widget = WallpaperWidget()
    widget.resize(320, 180)
    widget.set_wallpaper(None, "#000000")
    widget.show()
    application.processEvents()
    before = widget.grab().toImage().pixelColor(160, 90)
    apply_theme(widget, DEFAULT_THEME, background="constellation", asset_root=Path.cwd())
    application.processEvents()
    after = widget.grab().toImage().pixelColor(160, 90)
    assert after != before
    assert after != QColor("#000000")
    expected = render_background_pixmap(
        "constellation", DEFAULT_THEME.colors, Path.cwd(),
    ).toImage()
    assert _digest(widget.grab().toImage()) == _digest(expected)
    widget.close()


def _digest(image: QImage) -> tuple[int, ...]:
    """Sample a few pixels so pixmap comparisons stay cheap."""
    width = image.width()
    height = image.height()
    points = (
        (width // 5, height // 5),
        (width // 2, height // 2),
        (width * 4 // 5, height // 3),
        (width // 3, height * 4 // 5),
    )
    return tuple(image.pixel(x, y) for x, y in points)
