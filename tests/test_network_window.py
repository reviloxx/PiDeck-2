"""Offscreen interaction tests for appliance wireless setup."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.network import NetworkError, WiredLink, WirelessNetwork
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.localization import tr
from pideck.presentation.qt.network_window import (
    NetworkWindow,
    WirelessKeyDialog,
    format_link_speed,
)
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class FakeNetwork:
    """Simulate a single secured network without accessing host networking."""

    def __init__(
        self,
        *,
        wired: WiredLink | None = None,
        network: WirelessNetwork | None = None,
        connected: bool | None = None,
    ) -> None:
        self.calls: list[tuple[WirelessNetwork, str | None]] = []
        self.network = network or WirelessNetwork("Living room", "wlan0", "WPA2", 75)
        self._wired = wired
        self._connected = connected

    def connected(self) -> bool:
        if self._connected is not None:
            return self._connected
        return self._wired is not None or self.network.connected

    def wired_link(self) -> WiredLink | None:
        return self._wired

    def scan(self) -> tuple[WirelessNetwork, ...]:
        return (self.network,)

    def connect(self, network: WirelessNetwork, key: str | None) -> None:
        self.calls.append((network, key))


def _wait_for_networks(application: QApplication, window: NetworkWindow) -> None:
    for _ in range(100):
        application.processEvents()
        if window.networks.count() or not window.networks.isVisible():
            if window.link_summary.title.text():
                break
        QTest.qWait(5)


def test_wireless_setup_controller_key_entry(application: QApplication) -> None:
    """Navigate the on-screen keyboard and submit a masked secret."""
    gateway = FakeNetwork()
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait_for_networks(application, window)
    assert window.networks.isVisible()
    assert window.networks.count() == 1
    assert window.link_summary.title.text() == tr("en", "network_disconnected")
    assert window.link_summary.status.text() == tr("en", "network_disconnected")
    assert not window.link_summary.detail.isVisible()
    assert not window.status.isVisible()
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
    assert dialog.entry.text() == "1"
    window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert dialog.entry.text() == "12"
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
    _wait_for_networks(application, window)
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
    assert dialog.entry.text() == "2"
    dialog.entry.setFocus()
    QTest.keyClicks(dialog.entry, "test-key")
    QTest.keyClick(dialog.entry, Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.entry.text() == "2test-key"
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
    assert window.status.isVisible()
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
    window._select_page(2)
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requested == [True]
    window.apply_language("de")
    assert window._nav_buttons[2].text() == "Netzwerk"
    window.close()


def test_system_category_includes_network_and_scrolls_to_focused_row(application: QApplication) -> None:
    """Network joins System settings, whose rows remain controller-accessible."""
    config = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(config, DEFAULT_THEME, Path.cwd(), network_enabled=True, system_enabled=True)
    requested: list[bool] = []
    window.network_requested.connect(lambda: requested.append(True))
    window.resize(1280, 720)
    window.show()
    window._select_page(2)
    application.processEvents()
    assert window.width() <= 1280
    assert window.height() <= 720
    window._network_row.setFocus()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requested == [True]
    window._power_rows["shutdown"].setFocus()
    application.processEvents()
    scrollbar = window._system_scroll.verticalScrollBar()
    if scrollbar.maximum() > 0:
        assert scrollbar.value() > 0
    window.close()


class EmptyNetwork(FakeNetwork):
    """Report no visible wireless networks."""

    def scan(self) -> tuple[WirelessNetwork, ...]:
        return ()


def test_ethernet_hides_wifi_list_and_shows_summary(application: QApplication) -> None:
    """Ethernet summary shows the link speed; Wi-Fi list stays hidden and connect is skipped."""
    gateway = FakeNetwork(
        wired=WiredLink("enp3s0", "Wired connection 1", "192.168.1.20", speed_mbps=1000),
    )
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait_for_networks(application, window)
    assert window.link_summary.title.text() == tr("en", "network_ethernet_label")
    assert window.link_summary.status.text() == tr(
        "en", "network_ethernet_connected_speed", speed=format_link_speed("en", 1000),
    )
    assert window.link_summary.detail.text() == "192.168.1.20"
    assert window.link_summary.detail.isVisible()
    assert not window.networks.isVisible()
    assert window.networks.count() == 0
    assert not window.status.isVisible()
    assert gateway.calls == []
    window.close()
    parent.close()


def test_ethernet_link_speed_updates_live_without_rescan(application: QApplication) -> None:
    """Silent polling refreshes negotiated speed when the link changes."""
    gateway = FakeNetwork(
        wired=WiredLink("enp3s0", "Wired connection 1", "192.168.1.20", speed_mbps=100),
    )
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait_for_networks(application, window)
    assert window.link_summary.status.text() == tr(
        "en", "network_ethernet_connected_speed", speed=format_link_speed("en", 100),
    )
    gateway._wired = WiredLink("enp3s0", "Wired connection 1", "192.168.1.20", speed_mbps=1000)
    window._poll_timer.stop()
    window._poll_link()
    for _ in range(100):
        application.processEvents()
        if window.link_summary.status.text() == tr(
            "en", "network_ethernet_connected_speed", speed=format_link_speed("en", 1000),
        ):
            break
        QTest.qWait(5)
    assert window.link_summary.status.text() == tr(
        "en", "network_ethernet_connected_speed", speed=format_link_speed("en", 1000),
    )
    window.close()
    parent.close()


def test_ethernet_unplug_restores_wifi_list(application: QApplication) -> None:
    """Losing the wired link triggers a full scan so Wi-Fi setup returns."""
    gateway = FakeNetwork(
        wired=WiredLink("enp3s0", "Wired connection 1", "192.168.1.20", speed_mbps=1000),
    )
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait_for_networks(application, window)
    assert not window.networks.isVisible()
    gateway._wired = None
    window._poll_timer.stop()
    window._poll_link()
    for _ in range(200):
        application.processEvents()
        if window.networks.isVisible() and window.networks.count() == 1:
            break
        QTest.qWait(5)
    assert window.networks.isVisible()
    assert window.networks.count() == 1
    assert window.link_summary.title.text() == tr("en", "network_disconnected")
    window.close()
    parent.close()


def test_wifi_connected_marks_summary_and_row(application: QApplication) -> None:
    """Connected AP updates the summary title and marks the list row without status prose."""
    gateway = FakeNetwork(
        network=WirelessNetwork("Living room", "wlan0", "WPA2", 80, connected=True),
        connected=True,
    )
    parent = QWidget()
    parent.show()
    window = NetworkWindow(gateway, DEFAULT_THEME, "en", parent)
    window.show()
    _wait_for_networks(application, window)
    assert window.link_summary.title.text() == "Living room"
    assert window.link_summary.status.text() == tr("en", "system_info_link_connected")
    assert not window.link_summary.detail.isVisible()
    assert window.networks.isVisible()
    assert window.networks.count() == 1
    row = window.networks.itemWidget(window.networks.item(0))
    assert row is not None
    labels = [child.text() for child in row.findChildren(QLabel)]
    assert "Living room" in labels
    assert "✓" in labels
    assert not window.status.isVisible()
    window.close()
    parent.close()


@pytest.mark.parametrize(
    ("speed_mbps", "expected"),
    [(1000, "1 Gb/s"), (100, "100 Mb/s"), (2500, "2500 Mb/s")],
)
def test_format_link_speed(speed_mbps: int, expected: str) -> None:
    """Prefer whole-gigabit labels when the negotiated rate divides evenly."""
    assert format_link_speed("en", speed_mbps) == expected


def test_empty_network_list_never_takes_invisible_focus(application: QApplication) -> None:
    """Vertical navigation stays on the visible actions when no networks exist."""
    parent = QWidget()
    parent.show()
    window = NetworkWindow(EmptyNetwork(), DEFAULT_THEME, "en", parent)
    window.show()
    for _ in range(100):
        application.processEvents()
        if window.rescan.hasFocus():
            break
        QTest.qWait(5)
    assert window.rescan.hasFocus()

    QTest.keyClick(window.rescan, Qt.Key.Key_Down)
    QTest.keyClick(window.rescan, Qt.Key.Key_Up)

    assert window.rescan.hasFocus()
    assert window.networks.focusPolicy() == Qt.FocusPolicy.NoFocus
    window.close()
    parent.close()
