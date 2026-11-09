"""Tests for shared fullscreen presentation of PiDeck dialogs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from pideck.presentation.qt.fullscreen import present_fullscreen
from pideck.presentation.qt.wallpaper import WallpaperDialog


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


def test_wallpaper_dialog_show_fullscreen_fills_screen(application: QApplication) -> None:
    """WallpaperDialog.showFullScreen sizes to the screen and requests fullscreen."""
    parent = QWidget()
    dialog = WallpaperDialog(parent)
    dialog.showFullScreen()
    application.processEvents()
    assert dialog.isFullScreen()
    assert dialog.geometry() == dialog.screen().geometry()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    dialog.close()
    parent.close()


def test_present_fullscreen_stays_on_top(application: QApplication) -> None:
    """Optional stays-on-top is applied for overlays above running apps."""
    widget = QWidget()
    present_fullscreen(widget, stays_on_top=True, reassert_ms=0)
    application.processEvents()
    assert widget.isFullScreen()
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    widget.close()
