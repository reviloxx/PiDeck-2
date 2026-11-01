"""Offscreen tests for the system audio settings surface."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.audio import AudioError, AudioOutput, AudioState
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.audio_window import AudioWindow
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class Audio:
    """In-memory audio gateway."""

    def __init__(self) -> None:
        self.current = 1
        self.volume = 50

    def inspect(self) -> AudioState:
        return AudioState((AudioOutput(1, "one", "HDMI"), AudioOutput(2, "two", "Speakers")), self.current, self.volume)

    def set_output(self, identifier: int) -> None:
        self.current = identifier

    def set_volume(self, volume: int) -> None:
        self.volume = volume


def _wait(application: QApplication, condition) -> None:
    for _ in range(100):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Audio operation did not complete")


def test_audio_controls_are_asynchronous_and_controller_accessible(application: QApplication) -> None:
    """The controller can navigate and adjust audio using the injected gateway."""
    parent = QWidget()
    audio = Audio()
    window = AudioWindow(audio, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    assert window.output.currentText() == "HDMI"
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.volume.hasFocus()
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    _wait(application, lambda: audio.volume == 55 and window.output.isEnabled())
    assert window.volume.hasFocus()
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: audio.current == 2 and window.output.isEnabled())
    assert window.output.currentText() == "Speakers"
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window.isVisible()
    parent.close()


def test_keyboard_enter_opens_audio_selector(application: QApplication) -> None:
    """Keyboard activation opens output choices instead of a default action."""
    parent = QWidget()
    audio = Audio()
    window = AudioWindow(audio, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    QTest.keyClick(window.output, Qt.Key.Key_Return)
    assert window.output.view().isVisible()
    assert audio.current == 1
    QTest.keyClick(window.output.view(), Qt.Key.Key_Down)
    QTest.keyClick(window.output.view(), Qt.Key.Key_Return)
    _wait(application, lambda: audio.current == 2 and window.output.isEnabled())
    assert window.output.currentText() == "Speakers"
    window.close()
    parent.close()


def test_audio_errors_are_visible(application: QApplication) -> None:
    """Unavailable audio is reported and refresh stays accessible."""
    class Unavailable(Audio):
        def inspect(self) -> AudioState:
            raise AudioError("No audio server")

    parent = QWidget()
    window = AudioWindow(Unavailable(), DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: "No audio server" in window.status.text())
    assert window.refresh.isEnabled()
    window.close()
    parent.close()


def test_system_audio_entry_supports_gamepad(application: QApplication) -> None:
    """The system category exposes audio to controller users."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    requests: list[bool] = []
    window.audio_requested.connect(lambda: requests.append(True))
    window.show()
    window._select_page(3)
    window._audio_row.setFocus()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [True]
    window.close()


@pytest.mark.parametrize("action", ["restart", "shutdown"])
def test_power_requires_controller_confirmation(application: QApplication, action: str) -> None:
    """System power is only requested after explicitly confirming the dialog."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    requests: list[str] = []
    window.power_requested.connect(requests.append)
    window.show()
    window._select_page(3)
    window._power_rows[action].setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window._power_dialog is not None
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == []
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window._power_dialog is not None
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [action]
    window.close()
