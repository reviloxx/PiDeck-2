"""Exercise the pairing agent on a private D-Bus without real devices."""

import subprocess
import time
import os
from uuid import uuid4
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusObjectPath, QDBusPendingCallWatcher
from PySide6.QtCore import ClassInfo, QEventLoop, QObject, QTimer, Slot
from PySide6.QtWidgets import QApplication

from pideck.infrastructure.bluez_agent import BluezAgent
from pideck.application.ports.bluetooth import BluetoothError, BluetoothState
from pideck.infrastructure.bluez import BluezGateway
from tests.qt_helpers import with_qt_event_loop


@pytest.fixture(scope="module")
def application():
    """Provide an event loop for local D-Bus agent callbacks."""
    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.setQuitOnLastWindowClosed(previous)


@pytest.fixture
def private_bus(application):
    """Start an isolated D-Bus and connect an owner and client."""
    process = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None
    address = process.stdout.readline().strip()
    owner_name = "owner-" + uuid4().hex
    client_name = "client-" + uuid4().hex
    owner = QDBusConnection.connectToBus(address, owner_name)
    client = QDBusConnection.connectToBus(address, client_name)
    assert owner.isConnected() and client.isConnected()
    assert owner.registerService("org.bluez")
    try:
        yield owner, client
    finally:
        QDBusConnection.disconnectFromBus(client_name)
        QDBusConnection.disconnectFromBus(owner_name)
        process.terminate()
        process.wait(timeout=5)


def _call(application, sender, client, agent, method: str, device: str) -> QDBusMessage:
    message = QDBusMessage.createMethodCall(client.baseService(), agent.path, "org.bluez.Agent1", method)
    message.setArguments([QDBusObjectPath(device)])
    watcher = QDBusPendingCallWatcher(sender.asyncCall(message, 3000))
    deadline = time.monotonic() + 4
    while not watcher.isFinished() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert watcher.isFinished(), "D-Bus pairing request timed out"
    return watcher.reply()


@with_qt_event_loop
def test_agent_accepts_only_selected_device_and_bluez_owner(application, private_bus) -> None:
    """The agent returns a user-provided PIN only to the selected BlueZ request."""
    owner, client = private_bus
    agent = BluezAgent(client)
    target = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    agent.set_target(target)
    seen = []
    agent.requested.connect(lambda request: (seen.append(request.kind), agent.respond("1234")))
    result = _call(application, owner, client, agent, "RequestPinCode", target)
    assert result.type() == QDBusMessage.MessageType.ReplyMessage
    assert seen == ["pin"]
    assert result.arguments() == ["1234"]
    denied = _call(application, owner, client, agent, "RequestPinCode", "/org/bluez/hci0/dev_00_00_00_00_00_00")
    assert denied.type() == QDBusMessage.MessageType.ErrorMessage
    assert seen == ["pin"]
    wrong_sender = _call(application, client, client, agent, "RequestPinCode", target)
    assert wrong_sender.type() == QDBusMessage.MessageType.ErrorMessage
    assert seen == ["pin"]
    agent.close()


@with_qt_event_loop
def test_agent_passkey_is_unsigned_and_rejection_is_an_error(application, private_bus) -> None:
    """Zero is a valid passkey, while cancellation rejects the D-Bus request."""
    owner, client = private_bus
    agent = BluezAgent(client)
    target = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    agent.set_target(target)
    handler = lambda request: agent.respond(0)
    agent.requested.connect(handler)
    result = _call(application, owner, client, agent, "RequestPasskey", target)
    assert result.type() == QDBusMessage.MessageType.ReplyMessage
    assert result.signature() == "u"
    assert result.arguments() == [0]
    agent.requested.disconnect(handler)
    agent.requested.connect(lambda request: agent.respond(None))
    result = _call(application, owner, client, agent, "RequestPasskey", target)
    assert result.type() == QDBusMessage.MessageType.ErrorMessage
    agent.close()


@with_qt_event_loop
def test_agent_rejects_oversized_utf8_pin(application, private_bus) -> None:
    """A PIN longer than the Bluetooth 16-byte limit is never sent to BlueZ."""
    owner, client = private_bus
    agent = BluezAgent(client)
    target = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    agent.set_target(target)
    agent.requested.connect(lambda request: agent.respond("🟦" * 5))
    result = _call(application, owner, client, agent, "RequestPinCode", target)
    assert result.type() == QDBusMessage.MessageType.ErrorMessage
    agent.close()


@with_qt_event_loop
def test_gateway_keeps_qt_event_loop_responsive(application, private_bus) -> None:
    """Asynchronous D-Bus calls continue to process Qt events while waiting."""
    owner, client = private_bus

    @ClassInfo(**{"D-Bus Interface": "org.pideck.Test"})
    class Service(QObject):
        @Slot(result=str)
        def Ping(self) -> str:
            return "pong"

    service = Service()
    assert owner.registerObject("/org/pideck/test", service, QDBusConnection.RegisterOption.ExportAllSlots)
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    gateway = BluezGateway("/org/pideck/agent", client)
    assert gateway._call("/org/pideck/test", "org.pideck.Test", "Ping") == ["pong"]
    assert ticks == [True]
    owner.unregisterObject("/org/pideck/test")


@with_qt_event_loop
def test_agent_exports_bluez_pairing_signatures(application, private_bus) -> None:
    """BlueZ can call the standard confirmation and passkey agent methods."""
    owner, client = private_bus
    agent = BluezAgent(client)
    message = QDBusMessage.createMethodCall(
        client.baseService(), agent.path, "org.freedesktop.DBus.Introspectable", "Introspect",
    )
    watcher = QDBusPendingCallWatcher(owner.asyncCall(message, 3000))
    loop = QEventLoop()
    watcher.finished.connect(loop.quit)
    if not watcher.isFinished():
        loop.exec()
    xml = ET.fromstring(watcher.reply().arguments()[0])
    interface = next(item for item in xml.findall("interface") if item.get("name") == "org.bluez.Agent1")
    methods = {method.get("name"): method for method in interface.findall("method")}
    assert [arg.get("type") for arg in methods["RequestConfirmation"].findall("arg")] == ["o", "u"]
    passkey_args = methods["RequestPasskey"].findall("arg")
    assert [arg.get("type") for arg in passkey_args if arg.get("direction") == "in"] == ["o"]
    assert [arg.get("type") for arg in passkey_args if arg.get("direction") == "out"] == ["u"]
    agent.close()


@pytest.mark.parametrize("accept", [False, True])
@with_qt_event_loop
def test_end_to_end_pairing_uses_registered_agent(application, private_bus, accept: bool) -> None:
    """BlueZ pairing waits for the selected agent's explicit response."""
    owner, client = private_bus
    agent = BluezAgent(client)
    target = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    agent.set_target(target)
    agent.requested.connect(lambda request: agent.respond(accept))
    calls = []
    connection_info = {}

    @ClassInfo(**{"D-Bus Interface": "org.bluez.AgentManager1"})
    class Manager(QObject):
        @Slot(QDBusObjectPath, str, QDBusMessage)
        def RegisterAgent(self, path, capability, message):
            connection_info["sender"] = message.service()
            connection_info["path"] = path.path()
            calls.append("register")

        @Slot(QDBusObjectPath)
        def UnregisterAgent(self, path):
            calls.append("unregister")

    @ClassInfo(**{"D-Bus Interface": "org.bluez.Device1"})
    class Device(QObject):
        @Slot(QDBusMessage)
        def Pair(self, message):
            calls.append("pair")
            request = QDBusMessage.createMethodCall(
                connection_info["sender"], connection_info["path"], "org.bluez.Agent1", "RequestAuthorization",
            )
            request.setArguments([QDBusObjectPath(target)])
            watcher = QDBusPendingCallWatcher(owner.asyncCall(request, 3000))
            loop = QEventLoop()
            watcher.finished.connect(loop.quit)
            if not watcher.isFinished():
                loop.exec()
            if watcher.reply().type() == QDBusMessage.MessageType.ErrorMessage:
                message.setDelayedReply(True)
                owner.send(message.createErrorReply("org.bluez.Error.Rejected", "Pairing rejected"))

    class Gateway(BluezGateway):
        def _device_path(self, path):
            return path

        def inspect(self):
            return BluetoothState(True, ())

    manager, device = Manager(), Device()
    assert owner.registerObject("/org/bluez", manager, QDBusConnection.RegisterOption.ExportAllSlots)
    assert owner.registerObject(target, device, QDBusConnection.RegisterOption.ExportAllSlots)
    gateway = Gateway(agent.path, client)
    if accept:
        assert gateway.pair(target).powered
    else:
        with pytest.raises(BluetoothError, match="Pairing rejected"):
            gateway.pair(target)
    assert calls == ["register", "pair", "unregister"]
    agent.close()
