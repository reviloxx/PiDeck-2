"""Offscreen tests for guarded display settings."""

import os
from pathlib import Path
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.display import DisplayError, DisplayMode, DisplayOutput
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.display_window import DisplayWindow
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class Change:
    """Represent a pending display transaction."""

    def __init__(self, timeout: float) -> None:
        self.deadline = time.monotonic() + timeout
        self.confirmed = False
        self.cancelled = False

    def confirm(self) -> None:
        self.confirmed = True

    def cancel(self) -> None:
        self.cancelled = True


class Display:
    """Inject deterministic display capabilities."""

    def __init__(self, timeout: float = 10) -> None:
        self.modes = (DisplayMode("1920x1080", "60.00"), DisplayMode("1280x720", "60.00"))
        self.change = Change(timeout)
        self.selected = None

    def inspect(self) -> tuple[DisplayOutput, ...]:
        return (DisplayOutput("HDMI-1", self.modes[0], self.modes),)

    def begin(self, output: str, mode: DisplayMode) -> Change:
        self.selected = (output, mode)
        return self.change


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Display operation did not complete")


def test_gamepad_display_selection_and_confirmation(application: QApplication) -> None:
    """The gamepad selects a mode and explicitly confirms the temporary change."""
    parent = QWidget()
    display = Display()
    window = DisplayWindow(display, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.mode.hasFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: window.keep.isEnabled() and window.keep.isVisible())
    assert display.selected == ("HDMI-1", display.modes[1])
    application.processEvents()
    assert window.focusWidget() is window.revert
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: display.change.confirmed)
    assert not display.change.cancelled
    window.close()
    parent.close()


def test_timeout_and_closing_revert_pending_changes(application: QApplication) -> None:
    """A temporary mode is cancelled on timeout and when the dialog closes."""
    for timeout in (0.1, 10.0):
        parent = QWidget()
        display = Display(timeout)
        window = DisplayWindow(display, DEFAULT_THEME, "en", parent)
        window.show()
        _wait(application, lambda: window.output.isEnabled())
        window.mode.setCurrentIndex(1)
        window.apply.click()
        _wait(application, lambda: window.revert.isVisible())
        if timeout > 1:
            window.close()
        _wait(application, lambda: display.change.cancelled)
        assert not display.change.confirmed
        window.close()
        parent.close()


def test_display_unavailable_is_visible(application: QApplication) -> None:
    """Unsupported display sessions show an error and offer refresh."""
    class Unsupported(Display):
        def inspect(self) -> tuple[DisplayOutput, ...]:
            raise DisplayError("X11 is unavailable")

    parent = QWidget()
    window = DisplayWindow(Unsupported(), DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: "X11 is unavailable" in window.status.text())
    assert window.refresh.isEnabled()
    window.close()
    parent.close()


def test_closing_while_mode_change_starts_reverts_it(application: QApplication) -> None:
    """A mode finishing after the dialog closes is still rolled back."""
    started, release = Event(), Event()

    class SlowDisplay(Display):
        def begin(self, output: str, mode: DisplayMode) -> Change:
            started.set()
            assert release.wait(2)
            return self.change

    parent = QWidget()
    display = SlowDisplay()
    window = DisplayWindow(display, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    window.mode.setCurrentIndex(1)
    window.apply.click()
    assert started.wait(1)
    window.close()
    release.set()
    _wait(application, lambda: display.change.cancelled)
    parent.close()


def test_system_display_entry_supports_gamepad(application: QApplication) -> None:
    """The first System row opens display settings with a controller."""
    config = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(config, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    requests: list[bool] = []
    window.display_requested.connect(lambda: requests.append(True))
    window.show()
    window._select_page(3)
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [True]
    window.close()
