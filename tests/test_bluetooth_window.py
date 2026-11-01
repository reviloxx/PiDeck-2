"""Offscreen interaction tests for Bluetooth settings and pairing prompts."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.bluetooth import BluetoothDevice, BluetoothError, BluetoothState, PairingRequest
from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.presentation.qt.bluetooth_window import BluetoothWindow
from pideck.presentation.qt.network_window import WirelessKeyDialog
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document
from tests.qt_helpers import with_qt_event_loop


@pytest.fixture(scope="module")
def application():
    """Provide an offscreen Qt application."""
    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.setQuitOnLastWindowClosed(previous)


class Agent(QObject):
    """Simulate a user-mediated pairing agent."""

    requested = Signal(object)
    notice = Signal(object)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.target = None
        self.response = None
        self.loop = None
        self.closed = False

    def set_target(self, path) -> None:
        self.target = path

    def on_request(self, callback) -> None:
        self.requested.connect(callback)

    def on_notice(self, callback) -> None:
        self.notice.connect(callback)

    def on_cancel(self, callback) -> None:
        self.cancelled.connect(callback)

    def respond(self, value) -> None:
        self.response = value
        if self.loop is not None:
            self.loop.quit()

    def close(self) -> None:
        self.closed = True


class Gateway:
    """Simulate a discovered device and pairing confirmation."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.paired = False
        self.connected = False

    def inspect(self) -> BluetoothState:
        return BluetoothState(True, (BluetoothDevice("/device", "Headphones", self.paired, self.connected),))

    def set_power(self, enabled: bool) -> BluetoothState:
        return BluetoothState(enabled, ())

    def discover(self, cancelled) -> BluetoothState:
        return self.inspect()

    def cancel_discovery(self) -> None:
        pass

    def pair(self, path: str) -> BluetoothState:
        assert self.agent.target == path
        loop = QEventLoop()
        self.agent.loop = loop
        QTimer.singleShot(0, lambda: self.agent.requested.emit(PairingRequest("confirmation", "123456")))
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
        self.agent.loop = None
        if self.agent.response is not True:
            raise BluetoothError("Pairing rejected")
        self.paired = True
        return self.inspect()

    def cancel_pairing(self, path: str) -> None:
        self.agent.respond(None)

    def connect(self, path: str) -> BluetoothState:
        self.connected = True
        return self.inspect()

    def disconnect(self, path: str) -> BluetoothState:
        self.connected = False
        return self.inspect()


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Bluetooth operation did not complete")


@pytest.mark.parametrize("confirm", [False, True])
@with_qt_event_loop
def test_pairing_requires_explicit_controller_confirmation(application: QApplication, confirm: bool) -> None:
    """Pairing is rejected by default and succeeds only after confirmation."""
    parent = QWidget()
    agent = Agent()
    gateway = Gateway(agent)
    window = BluetoothWindow(gateway, agent, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.pair.isEnabled())

    def answer() -> None:
        assert window._pair_dialog is not None
        if confirm:
            window.handle_input(InputEvent(InputAction.RIGHT, "gamepad"))
        window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))

    QTimer.singleShot(20, answer)
    window.pair.click()
    _wait(application, lambda: window._busy is None)
    assert gateway.paired is confirm
    if not confirm:
        assert "Pairing rejected" in window.status.text()
    window.close()
    assert agent.closed
    parent.close()


def test_bluetooth_entry_supports_controller(application: QApplication) -> None:
    """The Bluetooth system row is reachable by gamepad."""
    config = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(config, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    requests: list[bool] = []
    window.bluetooth_requested.connect(lambda: requests.append(True))
    window.show()
    window._select_page(3)
    window._bluetooth_row.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert requests == [True]
    window.close()


def test_unavailable_bluetooth_can_be_retried(application: QApplication) -> None:
    """A missing adapter reports an error and leaves refresh accessible."""
    class Unavailable(Gateway):
        def inspect(self) -> BluetoothState:
            raise BluetoothError("No adapter")

    parent = QWidget()
    agent = Agent()
    window = BluetoothWindow(Unavailable(agent), agent, DEFAULT_THEME, "en", parent)
    window.resize(1280, 720)
    window.show()
    _wait(application, lambda: "No adapter" in window.status.text())
    assert window.width() <= 1280 and window.height() <= 720
    assert window.refresh.isEnabled()
    assert not window.pair.isEnabled()
    window.close()
    parent.close()


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [("pin", "1234", "1234"), ("pin", "🟦" * 5, None), ("passkey", "000001", 1), ("passkey", "abc", None)],
)
def test_pairing_input_is_validated_and_cleared(
    application: QApplication, kind: str, text: str, expected,
) -> None:
    """Pairing codes are validated, passed once, and removed from the editor."""
    parent = QWidget()
    agent = Agent()
    window = BluetoothWindow(Gateway(agent), agent, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.pair.isEnabled())
    window._request_pairing_input(PairingRequest(kind))
    dialog = window._pair_dialog
    assert isinstance(dialog, WirelessKeyDialog)
    dialog.entry.setText(text)
    dialog.accept()
    assert agent.response == expected
    assert dialog.entry.text() == ""
    window.close()
    parent.close()


def test_pairing_pin_keyboard_accepts_gamepad_input(application: QApplication) -> None:
    """The Bluetooth PIN editor reuses the controller-operated keyboard."""
    parent = QWidget()
    parent.show()
    agent = Agent()
    window = BluetoothWindow(Gateway(agent), agent, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.pair.isEnabled())
    window._request_pairing_input(PairingRequest("pin"))
    dialog = window._pair_dialog
    assert isinstance(dialog, WirelessKeyDialog)
    dialog._keys[0].setFocus()
    application.processEvents()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    assert dialog.entry.text() == "a"
    dialog.accept()
    assert agent.response == "a"
    window.close()
    parent.close()
