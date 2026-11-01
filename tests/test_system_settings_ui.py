"""Verify responsive system-settings presentation and input behavior."""

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.audio_window import AudioWindow
from pideck.presentation.qt.bluetooth_window import BluetoothWindow
from pideck.presentation.qt.display_window import DisplayWindow
from pideck.presentation.qt.network_window import NetworkWindow
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_audio_window import Audio
from tests.test_bluetooth_window import Agent, Gateway
from tests.test_configuration import configuration_document
from tests.test_display_window import Display
from tests.test_network_window import FakeNetwork


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one offscreen Qt application."""
    return QApplication.instance() or QApplication([])


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("System settings window did not finish loading")


@pytest.mark.parametrize(
    ("row_name", "signal_name"),
    [
        ("_display_row", "display_requested"),
        ("_audio_row", "audio_requested"),
        ("_bluetooth_row", "bluetooth_requested"),
        ("_network_row", "network_requested"),
    ],
)
def test_system_rows_activate_with_keyboard_controller_and_mouse(
    application: QApplication, row_name: str, signal_name: str,
) -> None:
    """The entire row is a single accessible action, not an embedded button."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), network_enabled=True, system_enabled=True)
    requests: list[bool] = []
    getattr(window, signal_name).connect(lambda: requests.append(True))
    window.resize(1280, 720)
    window.show()
    window._select_page(3)
    row = getattr(window, row_name)
    row.setFocus()
    application.processEvents()
    assert row.findChildren(QPushButton) == []
    assert row.accessibleName()
    labels = row.findChildren(QLabel)
    assert all(label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for label in labels)
    QTest.keyClick(row, Qt.Key.Key_Return)
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(row.width() - 24, row.height() // 2))
    assert requests == [True, True, True]
    QTest.mouseClick(row, Qt.MouseButton.RightButton)
    QTest.mousePress(row, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseRelease(row, Qt.MouseButton.LeftButton, pos=QPoint(-1, -1))
    assert requests == [True, True, True]
    window.apply_language("de")
    assert row.accessibleName() == labels[0].text()
    assert row.accessibleDescription() == labels[1].text()
    window.close()


@pytest.mark.parametrize("action", ["restart", "shutdown"])
def test_clicking_power_row_still_requires_confirmation(application: QApplication, action: str) -> None:
    """A mouse click cannot request power until the user confirms."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    requests: list[str] = []
    window.power_requested.connect(requests.append)
    window.show()
    window._select_page(3)
    row = window._power_rows[action]
    row.setFocus()
    application.processEvents()
    assert row.findChildren(QPushButton) == []
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert requests == []
    assert window._power_dialog is not None
    window._power_dialog.reject()
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert window._power_dialog is not None
    window._power_dialog.accept()
    assert requests == [action]
    window.close()


def test_appearance_row_mouse_click_still_only_moves_focus(application: QApplication) -> None:
    """Clickable system rows do not change the behavior of appearance controls."""
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    window.show()
    window._select_page(0)
    assert not window._reduced_motion.isChecked()
    QTest.mouseClick(window._motion_row, Qt.MouseButton.LeftButton)
    assert window.focusWidget() is window._motion_row
    assert not window._reduced_motion.isChecked()
    window.close()


@pytest.mark.parametrize("kind", ["audio", "display", "bluetooth", "network"])
@pytest.mark.parametrize(("width", "height"), [(800, 600), (1280, 720), (1920, 1080), (2560, 1440)])
@pytest.mark.parametrize("language", ["en", "de"])
def test_system_panels_are_centered_responsive_and_unclipped(
    application: QApplication, kind: str, width: int, height: int, language: str,
) -> None:
    """Controls fit smaller screens and stay width-limited on large displays."""
    parent = QWidget()
    agent = Agent()
    if kind == "audio":
        window = AudioWindow(Audio(), DEFAULT_THEME, language, parent)
        ready = lambda: window.output.isEnabled()
    elif kind == "display":
        window = DisplayWindow(Display(), DEFAULT_THEME, language, parent)
        ready = lambda: window.output.isEnabled()
    elif kind == "bluetooth":
        window = BluetoothWindow(Gateway(agent), agent, DEFAULT_THEME, language, parent)
        ready = lambda: window._state is not None
    else:
        window = NetworkWindow(FakeNetwork(), DEFAULT_THEME, language, parent)
        ready = lambda: window.networks.count() == 1
    window.resize(width, height)
    window.show()
    _wait(application, ready)
    QTest.qWait(10)
    panel = window.findChild(QFrame, "system_settings_panel")
    assert panel is not None
    assert (window.width(), window.height()) == (width, height)
    assert panel.width() <= 1056
    assert abs(panel.geometry().center().x() - window.rect().center().x()) <= 1
    for button in window.findChildren(QPushButton):
        if button.isVisible():
            assert button.width() >= button.sizeHint().width(), button.text()
            if width >= 1280:
                assert button.width() <= 400, button.text()
    if kind in {"audio", "display"}:
        assert window.output.width() < panel.width()
        if width >= 1920:
            assert window.output.width() < width * 0.6
    window.close()
    parent.close()


def test_display_confirmation_buttons_fit_and_support_horizontal_navigation(application: QApplication) -> None:
    """German confirmation controls remain visible and reachable on a small screen."""
    parent = QWidget()
    display = Display()
    window = DisplayWindow(display, DEFAULT_THEME, "de", parent)
    window.resize(800, 600)
    window.show()
    _wait(application, lambda: window.output.isEnabled())
    window.mode.setCurrentIndex(1)
    window.apply.click()
    _wait(application, lambda: window.keep.isVisible())
    assert window.apply.isHidden()
    assert window.keep.width() >= window.keep.sizeHint().width()
    assert window.revert.width() >= window.revert.sizeHint().width()
    window.handle_input(InputEvent(InputAction.LEFT, "gamepad"))
    assert window.focusWidget() is window.keep
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window.focusWidget() is window.revert
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: display.change.cancelled)
    window.close()
    parent.close()


def test_bluetooth_gamepad_navigation_follows_button_rows(application: QApplication) -> None:
    """Directional navigation follows the visible Bluetooth action groups."""
    parent = QWidget()
    agent = Agent()
    window = BluetoothWindow(Gateway(agent), agent, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.pair.isEnabled())
    window.devices.setFocus()
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.focusWidget() is window.power
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window.focusWidget() is window.scan
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.focusWidget() is window.pair
    window.handle_input(InputEvent(InputAction.DOWN, "gamepad"))
    assert window.focusWidget() is window.refresh
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    assert window.focusWidget() is window.back
    window.close()
    parent.close()


def test_audio_labels_and_slider_use_the_selected_theme(application: QApplication) -> None:
    """Audio controls remain readable in both dark and light themes."""
    light = replace(DEFAULT_THEME, colors={
        **DEFAULT_THEME.colors,
        "background": "#eeeeee",
        "surface": "#dddddd",
        "text": "#172534",
        "primary": "#00665f",
        "focus": "#00665f",
        "muted_text": "#3e505e",
    })
    for theme in (DEFAULT_THEME, light):
        parent = QWidget()
        window = AudioWindow(Audio(), theme, "en", parent)
        window.show()
        _wait(application, lambda: window.output.isEnabled())
        labels = [label for label in window.findChildren(QLabel) if label.objectName() == "settings_label"]
        assert len(labels) == 2
        assert all(label.palette().color(QPalette.ColorRole.WindowText).name() == theme.colors["text"] for label in labels)
        assert (
            window.volume.palette().color(QPalette.ColorRole.Window)
            == window.volume.parentWidget().palette().color(QPalette.ColorRole.Window)
        )
        window.close()
        parent.close()


def test_network_window_uses_wallpaper_behind_the_panel(
    application: QApplication, tmp_path: Path,
) -> None:
    """Network settings retain the same themed wallpaper as other system windows."""
    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(QColor("#ab4512"))
    wallpaper = tmp_path / "wallpaper.png"
    assert image.save(str(wallpaper))
    parent = QWidget()
    window = NetworkWindow(FakeNetwork(), replace(DEFAULT_THEME, wallpaper=wallpaper), "en", parent)
    window.resize(1280, 720)
    window.show()
    _wait(application, lambda: window.networks.count() == 1)
    rendered = window.grab().toImage()
    assert rendered.pixelColor(0, 0) == QColor("#ab4512")
    window.close()
    parent.close()
