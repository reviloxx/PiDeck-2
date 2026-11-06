"""Tests for keyboard layout persistence, X11 application and on-screen keys."""

import os
from pathlib import Path
import string
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.domain.input_settings import CecSettings
from pideck.domain.keyboard_layout import DEFAULT_KEYBOARD_LAYOUT, KeyboardLayout
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.keyboard_layout import KeyboardPreferences, X11KeyboardLayout
from pideck.presentation.qt.network_window import WirelessKeyDialog
from pideck.presentation.qt.on_screen_keyboard import ON_SCREEN_COLUMNS, on_screen_characters, shifted
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


PREVIOUS_CHARACTERS = string.ascii_lowercase + string.digits + "-_=+!@#$%^&*()[]{};:'\",.<>/?\\|`~"


def test_german_qwertz_is_the_default(tmp_path: Path) -> None:
    preferences = KeyboardPreferences(tmp_path / "keyboard.yaml")
    assert DEFAULT_KEYBOARD_LAYOUT is KeyboardLayout.GERMAN
    assert preferences.load() is KeyboardLayout.GERMAN
    preferences.save(KeyboardLayout.ENGLISH_US)
    assert preferences.load() is KeyboardLayout.ENGLISH_US
    assert (tmp_path / "keyboard.yaml").read_text(encoding="utf-8") == "version: 1\nlayout: us\n"


@pytest.mark.parametrize("content", ["layout: fr\nversion: 1\n", "[", "version: 2\nlayout: us\n"])
def test_invalid_preferences_fall_back_to_german(tmp_path: Path, content: str) -> None:
    path = tmp_path / "keyboard.yaml"
    path.write_text(content, encoding="utf-8")
    assert KeyboardPreferences(path).load() is KeyboardLayout.GERMAN


def test_setxkbmap_applies_layout_and_failures_are_not_fatal() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0 if command[-1] == "de" else 1, "", "bad")

    applier = X11KeyboardLayout(runner, which=lambda name: f"/usr/bin/{name}")
    assert applier.apply(KeyboardLayout.GERMAN) is True
    assert calls == [["/usr/bin/setxkbmap", "-layout", "de"]]
    assert applier.apply(KeyboardLayout.ENGLISH_US) is False
    assert X11KeyboardLayout(runner, which=lambda name: None).apply(KeyboardLayout.GERMAN) is False


@pytest.mark.parametrize("layout", list(KeyboardLayout))
def test_on_screen_layouts_keep_every_previous_character(layout: KeyboardLayout) -> None:
    characters = on_screen_characters(layout)
    assert len(set(characters)) == len(characters)
    assert set(PREVIOUS_CHARACTERS) <= set(characters)


def test_german_on_screen_keyboard_is_qwertz_with_umlauts() -> None:
    characters = on_screen_characters(KeyboardLayout.GERMAN)
    rows = [characters[index:index + ON_SCREEN_COLUMNS] for index in range(0, len(characters), ON_SCREEN_COLUMNS)]
    assert rows[1] == "qwertzuiop" and rows[3].startswith("yxcvbnm")
    assert set("äöüß") <= set(characters)
    assert on_screen_characters(KeyboardLayout.ENGLISH_US)[10:20] == "qwertyuiop"
    assert shifted("ä") == "Ä" and shifted("ß") == "ß"


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


def test_wireless_key_dialog_shift_keeps_eszett(application: QApplication) -> None:
    parent = QWidget()
    dialog = WirelessKeyDialog(DEFAULT_THEME, "de", parent, keyboard_layout=KeyboardLayout.GERMAN)
    labels = [button.text() for button in dialog._keys[:len(dialog._characters)]]
    assert labels[10:20] == list("qwertzuiop")
    dialog._shift()
    shifted_labels = [button.text() for button in dialog._keys[:len(dialog._characters)]]
    assert "Ü" in shifted_labels and "ß" in shifted_labels
    ampersand = dialog._characters.index("&")
    assert dialog._keys[ampersand].text() == "&&"
    dialog._keys[ampersand].click()
    dialog._keys[dialog._characters.index("ü")].click()
    assert dialog.entry.text() == "&Ü"
    parent.close()


def test_input_page_changes_keyboard_layout(application: QApplication) -> None:
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(
        configuration, DEFAULT_THEME, Path.cwd(),
        input_settings=CecSettings(), keyboard_layout=KeyboardLayout.GERMAN,
    )
    changes: list[KeyboardLayout] = []
    window.keyboard_layout_changed.connect(changes.append)
    combo = window._keyboard_combo
    assert combo is not None
    assert combo.currentText() == "German (QWERTZ)"
    assert window._input_rows["keyboard_layout"] in window._page_controls[window._input_page]
    combo.setCurrentIndex(combo.findData("us"))
    assert changes == [KeyboardLayout.ENGLISH_US]
    window.apply_language("de")
    assert combo.itemText(combo.findData("de")) == "Deutsch (QWERTZ)"
    window.close()
    plain = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), input_settings=CecSettings())
    assert plain._keyboard_combo is None
    plain.close()
