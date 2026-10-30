"""Offscreen interaction tests for appliance wireless setup."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.network import NetworkError, WirelessNetwork
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.network_window import NetworkWindow, WirelessKeyDialog
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class FakeNetwork:
    """Simulate a single secured network without accessing host networking."""

    def __init__(self) -> None:
        self.calls: list[tuple[WirelessNetwork, str | None]] = []
        self.network = WirelessNetwork("Living room", "wlan0", "WPA2", 75)

    def connected(self) -> bool:
        return False

    def scan(self) -> tuple[WirelessNetwork, ...]:
        return (self.network,)

    def connect(self, network: WirelessNetwork, key: str | None) -> None:
        self.calls.append((network, key))


def test_wireless_setup_controller_key_entry(application: QApplication) -> None:
    """Navigate the on-screen keyboard and submit a masked secret."""
    gateway = FakeNetwork()
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    for _ in range(100):
        application.processEvents()
        if window.networks.count():
            break
        QTest.qWait(5)
    assert window.networks.count() == 1
    window.networks.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    dialog = window._key_dialog
    assert dialog is not None
    assert dialog.entry.echoMode() is QLineEdit.EchoMode.Password
    assert dialog.sizeHint().height() <= 720
    dialog._keys[0].setFocus()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert dialog.entry.text() == "a"
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert dialog.entry.text() == "ab"
    dialog.entry.setText("test-key")
    dialog.accept()
    for _ in range(100):
        application.processEvents()
        if gateway.calls:
            break
        QTest.qWait(5)
    assert gateway.calls == [(gateway.network, "test-key")]
    assert dialog.entry.text() == ""
    window.close()
    parent.close()


def test_wireless_key_entry_can_be_cancelled(application: QApplication) -> None:
    """Cancel the controller keyboard without initiating a connection."""
    gateway = FakeNetwork()
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    for _ in range(100):
        application.processEvents()
        if window.networks.count():
            break
        QTest.qWait(5)
    window.networks.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    dialog = window._key_dialog
    assert dialog is not None
    dialog.entry.setText("private-key")
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    application.processEvents()
    assert window._key_dialog is None
    assert dialog.entry.text() == ""
    assert gateway.calls == []
    window.close()
    parent.close()


def test_wireless_keyboard_enter_activates_focus_and_submits_entry(application: QApplication) -> None:
    """Return activates the focused key or submits physical keyboard input."""
    parent = QWidget()
    parent.show()
    dialog = WirelessKeyDialog(DEFAULT_THEME, "en", parent)
    dialog.show()
    application.processEvents()
    QTest.keyClick(dialog._keys[1], Qt.Key.Key_Return)
    assert dialog.entry.text() == "b"
    dialog.entry.setFocus()
    QTest.keyClicks(dialog.entry, "test-key")
    QTest.keyClick(dialog.entry, Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.entry.text() == "btest-key"
    parent.close()


def test_wireless_scan_failure_is_visible(application: QApplication) -> None:
    """Keep failures visible and allow the user to retry."""
    class UnavailableNetwork(FakeNetwork):
        def scan(self) -> tuple[WirelessNetwork, ...]:
            raise NetworkError("Adapter unavailable")

    parent = QWidget()
    parent.show()
    window = NetworkWindow(UnavailableNetwork(), DEFAULT_THEME, "en", parent)
    window.show()
    for _ in range(100):
        application.processEvents()
        if "Adapter unavailable" in window.status.text():
            break
        QTest.qWait(5)
    assert "Adapter unavailable" in window.status.text()
    assert window.rescan.isEnabled()
    window.close()
    parent.close()


def test_settings_network_entry_is_optional_and_controller_accessible(application: QApplication) -> None:
    """Only configured appliance sessions expose the new network settings page."""
    config = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(config, DEFAULT_THEME, Path.cwd(), network_enabled=True)
    requested: list[bool] = []
    window.network_requested.connect(lambda: requested.append(True))
    window.show()
    window._select_page(3)
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requested == [True]
    window.apply_language("de")
    assert window._nav_buttons[3].text() == "Netzwerk"
    window.close()
