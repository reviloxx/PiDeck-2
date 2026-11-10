"""Offscreen tests for the system audio settings surface."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.audio import (
    AudioError,
    AudioOutput,
    AudioState,
    HdmiAudioMode,
)
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.audio_preferences import AudioPreferencesStore
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
        self.current = "sink:one"
        self.volume = 50
        self.last_mode: HdmiAudioMode | None = None

    def inspect(self, *, hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND) -> AudioState:
        self.last_mode = hdmi_mode
        return AudioState(
            (
                AudioOutput("sink:one", "one", "HDMI"),
                AudioOutput("sink:two", "two", "Speakers"),
            ),
            self.current,
            self.volume,
        )

    def set_output(
        self,
        identifier: str,
        *,
        hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND,
    ) -> None:
        self.current = identifier
        self.last_mode = hdmi_mode

    def ensure_hdmi(self, *, hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND) -> None:
        self.last_mode = hdmi_mode

    def set_volume(self, volume: int) -> None:
        self.volume = volume


def _wait(application: QApplication, condition) -> None:
    for _ in range(100):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Audio operation did not complete")


def test_audio_controls_are_asynchronous_and_controller_accessible(
    application: QApplication, tmp_path: Path,
) -> None:
    """The controller can navigate and adjust audio using the injected gateway."""
    parent = QWidget()
    audio = Audio()
    preferences = AudioPreferencesStore(tmp_path / "audio.yaml")
    window = AudioWindow(audio, preferences, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    assert window.mode.currentData() == HdmiAudioMode.SURROUND.value
    assert window.output.currentText() == "HDMI"
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.output.hasFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.volume.hasFocus()
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    _wait(application, lambda: audio.volume == 55 and window.output.isEnabled())
    window.handle_input(InputEvent(InputAction.UP, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: audio.current == "sink:two" and window.output.isEnabled())
    assert preferences.load().default == "sink:two"
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window.isVisible()
    parent.close()


def test_hdmi_mode_change_is_persisted(application: QApplication, tmp_path: Path) -> None:
    """Choosing bitstream mode writes audio.yaml and re-prepares the sink."""
    parent = QWidget()
    audio = Audio()
    preferences = AudioPreferencesStore(tmp_path / "audio.yaml")
    window = AudioWindow(audio, preferences, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.mode.isEnabled())
    bitstream = window.mode.findData(HdmiAudioMode.BITSTREAM.value)
    assert bitstream >= 0
    window.mode.setCurrentIndex(bitstream)
    window._apply_mode(bitstream)
    _wait(application, lambda: preferences.load().hdmi_mode is HdmiAudioMode.BITSTREAM)
    assert audio.last_mode is HdmiAudioMode.BITSTREAM
    window.close()
    parent.close()


def test_keyboard_enter_opens_audio_selector(application: QApplication, tmp_path: Path) -> None:
    """Keyboard activation opens output choices instead of a default action."""
    parent = QWidget()
    audio = Audio()
    preferences = AudioPreferencesStore(tmp_path / "audio.yaml")
    window = AudioWindow(audio, preferences, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    QTest.keyClick(window.output, Qt.Key.Key_Return)
    assert window.output.view().isVisible()
    QTest.keyClick(window.output.view(), Qt.Key.Key_Down)
    QTest.keyClick(window.output.view(), Qt.Key.Key_Return)
    _wait(application, lambda: audio.current == "sink:two" and window.output.isEnabled())
    window.close()
    parent.close()


def test_audio_errors_are_visible(application: QApplication, tmp_path: Path) -> None:
    """Unavailable audio is reported and refresh stays accessible."""
    class Unavailable(Audio):
        def inspect(self, *, hdmi_mode: HdmiAudioMode = HdmiAudioMode.SURROUND) -> AudioState:
            raise AudioError("No audio server")

    parent = QWidget()
    preferences = AudioPreferencesStore(tmp_path / "audio.yaml")
    window = AudioWindow(Unavailable(), preferences, DEFAULT_THEME, "en", parent)
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
    window._select_page(2)
    window._audio_row.setFocus()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [True]
    window.close()
