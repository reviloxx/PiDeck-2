"""Offscreen tests for host timezone settings."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.timezone import TimezoneError
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.presentation.qt.timezone_window import TimezoneWindow, split_timezone


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class Timezone:
    """Inject deterministic timezone capabilities."""

    def __init__(self) -> None:
        self.current = "Europe/Berlin"
        self.zones = ("America/New_York", "Europe/Berlin", "Europe/London", "UTC")
        self.applied: str | None = None
        self.fail = False

    def current_timezone(self) -> str:
        return self.current

    def list_timezones(self) -> tuple[str, ...]:
        return self.zones

    def set_timezone(self, timezone: str) -> None:
        if self.fail:
            raise TimezoneError("denied")
        self.applied = timezone
        self.current = timezone


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Timezone operation did not complete")


def test_split_timezone_handles_regions_and_bare_ids() -> None:
    """Region/city splitting keeps multi-segment cities and bare zones intact."""
    assert split_timezone("Europe/Berlin") == ("Europe", "Berlin")
    assert split_timezone("America/Argentina/Buenos_Aires") == ("America", "Argentina/Buenos_Aires")
    assert split_timezone("UTC") == ("UTC", "UTC")


def test_timezone_window_selects_region_city_and_applies(application: QApplication) -> None:
    """The dialog loads the current zone and applies a new selection."""
    parent = QWidget()
    gateway = Timezone()
    window = TimezoneWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.region.isEnabled())
    assert window.region.currentData() == "Europe"
    assert window.city.currentData() == "Europe/Berlin"
    window.region.setCurrentIndex(window.region.findData("America"))
    assert window.city.currentData() == "America/New_York"
    window.apply.click()
    _wait(application, lambda: gateway.applied == "America/New_York")
    assert gateway.current == "America/New_York"
    window.close()
    parent.close()


def test_gamepad_timezone_navigation_and_apply(application: QApplication) -> None:
    """A controller can move through the timezone controls and apply a change."""
    parent = QWidget()
    gateway = Timezone()
    window = TimezoneWindow(gateway, DEFAULT_THEME, "de", parent)
    window.show()
    _wait(application, lambda: window.region.isEnabled())
    window.region.setFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.city.hasFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.apply.hasFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: gateway.applied == "Europe/Berlin")
    window.close()
    parent.close()
