"""Qt tests for the in-app session overlay."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.session_overlay import (
    SessionOverlay,
    active_window_id,
    blur_pixmap,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application for the module."""
    return QApplication.instance() or QApplication([])


def test_activate_requests_return_to_pideck(application: QApplication) -> None:
    """OK / Activate clicks Back to PiDeck and emits return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert returns == ["return"]
    assert not overlay.isVisible()


def test_menu_dismisses_without_stopping_the_application(application: QApplication) -> None:
    """Exit / Guide toggles dismiss the overlay without return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    dismissals: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.dismissed.connect(lambda: dismissals.append("dismissed"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.MENU, "cec"))
    assert returns == []
    assert dismissals == ["dismissed"]
    assert not overlay.isVisible()


def test_back_dismisses_without_stopping_the_application(application: QApplication) -> None:
    """Return/Back dismisses the overlay without quitting the application."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    returns: list[str] = []
    dismissals: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.dismissed.connect(lambda: dismissals.append("dismissed"))
    overlay.open_overlay()
    overlay.handle_input(InputEvent(InputAction.BACK, "cec"))
    assert returns == []
    assert dismissals == ["dismissed"]
    assert not overlay.isVisible()


def test_button_click_requests_return(application: QApplication) -> None:
    """Clicking the primary button stops the application via return_requested."""
    overlay = SessionOverlay(DEFAULT_THEME, "de")
    returns: list[str] = []
    overlay.return_requested.connect(lambda: returns.append("return"))
    overlay.open_overlay()
    overlay.return_button.click()
    assert returns == ["return"]
    assert not overlay.isVisible()


def test_overlay_has_no_instructional_copy(application: QApplication) -> None:
    """The overlay shows only the return action, not hotkey explanations."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    assert overlay.findChildren(QLabel) == []
    assert "Windows" not in overlay.return_button.text()
    assert overlay.return_button.text() == "Back to PiDeck"
    overlay.hide()


def test_open_overlay_tolerates_empty_screen_grab(application: QApplication) -> None:
    """Offscreen grabs may be empty; opening must still show a usable overlay."""
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    assert overlay.isVisible()
    assert not overlay._backdrop.isNull()
    overlay.hide()


def test_blur_pixmap_returns_same_size(application: QApplication) -> None:
    """Blur preserves the source size for full-bleed painting."""
    source = QPixmap(64, 48)
    source.fill(QColor("#336699"))
    blurred = blur_pixmap(source, scale=2, radius=8)
    assert not blurred.isNull()
    assert blurred.size() == source.size()


def test_blur_pixmap_rejects_empty_source(application: QApplication) -> None:
    """An empty grab produces an empty blur result."""
    assert blur_pixmap(QPixmap()).isNull()


def test_active_window_id_parses_xprop_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The focused window id is read from the root _NET_ACTIVE_WINDOW property."""
    class Result:
        returncode = 0
        stdout = "_NET_ACTIVE_WINDOW: window id # 0x1a2b3c\n"
        stderr = ""

    monkeypatch.setattr(
        "pideck.presentation.qt.session_overlay.subprocess.run",
        lambda *args, **kwargs: Result(),
    )
    assert active_window_id() == 0x1A2B3C


def test_open_overlay_refreshes_backdrop_each_time(
    application: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each open captures a new backdrop instead of reusing the previous blur."""
    frames = [
        _solid_pixmap(QColor("#112233")),
        _solid_pixmap(QColor("#445566")),
    ]

    def capture() -> QPixmap:
        return frames.pop(0)

    monkeypatch.setattr(
        "pideck.presentation.qt.session_overlay.capture_blurred_screen", capture,
    )
    overlay = SessionOverlay(DEFAULT_THEME, "en")
    overlay.open_overlay()
    first = overlay._backdrop.cacheKey()
    overlay.dismiss()
    overlay.open_overlay()
    second = overlay._backdrop.cacheKey()
    assert first != second
    overlay.hide()


def _solid_pixmap(color: QColor) -> QPixmap:
    pixmap = QPixmap(32, 24)
    pixmap.fill(color)
    return pixmap
