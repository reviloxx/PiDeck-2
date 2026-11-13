"""Tests for inbound-first Bluetooth gamepad reconnect."""

import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusVariant

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway, ConnectAttempt, is_reconnect_gamepad
from pideck.infrastructure.bluetooth_reconnect import BluetoothReconnectService
from pideck.infrastructure.bluetooth_reconnect_watch import BluetoothReconnectWatch


DEVICE = "/org/bluez/hci0/dev_98_B6_E9_B2_4F_2C"
_DEVICE = "org.bluez.Device1"
_ADAPTER = "org.bluez.Adapter1"


class FakeReconnectBluez(BluezGateway):
    """Deterministic BlueZ replies for reconnect candidate and Connect tests."""

    def __init__(self) -> None:
        super().__init__("/org/pideck/agent/test")
        self.calls: list[tuple[str, str, str]] = []
        self.powered = True
        self.connectable = False
        self.fast_connectable = False
        self.expose_fast_connectable = True
        self.btmgmt_calls: list[list[str]] = []
        self.devices: dict[str, dict[str, object]] = {
            DEVICE: {
                "Alias": "Pro Controller",
                "Icon": "input-gaming",
                "Paired": True,
                "Trusted": True,
                "Connected": False,
            },
        }
        self.fail_connect: BluetoothError | None = None
        self.connect_calls = 0

    def _call(self, path, interface, method, *arguments, timeout=10000):
        self.calls.append((path, interface, method))
        if method == "Introspect":
            if path == "/org/bluez":
                return ['<node><node name="hci0"/></node>']
            names = "".join(
                f'<node name="{item.rsplit("/", 1)[-1]}"/>' for item in self.devices
            )
            return [f"<node>{names}</node>"]
        if method == "Get":
            if path.endswith("/hci0"):
                name = arguments[1]
                if name == "Powered":
                    return [QDBusVariant(self.powered)]
                if name == "Connectable":
                    return [QDBusVariant(self.connectable)]
                if name == "FastConnectable":
                    if not self.expose_fast_connectable:
                        raise BluetoothError("No such property 'FastConnectable'")
                    return [QDBusVariant(self.fast_connectable)]
            values = self.devices[path]
            return [QDBusVariant(values[arguments[1]])]
        if method == "Set" and arguments[0] == _ADAPTER:
            if arguments[1] == "Connectable":
                self.connectable = bool(arguments[2].variant())
                return []
            if arguments[1] == "FastConnectable":
                self.fast_connectable = bool(arguments[2].variant())
                return []
        if method == "Set" and arguments[0] == _DEVICE and arguments[1] == "Trusted":
            self.devices[path]["Trusted"] = True
            return []
        if method == "Disconnect":
            self.devices[path]["Connected"] = False
            return []
        if method == "Connect":
            self.connect_calls += 1
            if self.fail_connect is not None:
                raise self.fail_connect
            self.devices[path]["Connected"] = True
            return []
        return []

    def _enable_fast_connectable_btmgmt(self, adapter_path: str) -> None:
        self.btmgmt_calls.append(["btmgmt", adapter_path])
        self.fast_connectable = True


def test_is_reconnect_gamepad_matches_switch_and_icon() -> None:
    """Controllers are selected by gaming icon or Nintendo/controller names."""
    assert is_reconnect_gamepad("Headphones") is False
    assert is_reconnect_gamepad("Logitech K400") is False
    assert is_reconnect_gamepad("Something", "input-gaming") is True
    assert is_reconnect_gamepad("Pro Controller") is True
    assert is_reconnect_gamepad("Nintendo Switch Pro Controller") is True


def test_reconnect_candidates_skip_connected_and_non_gamepads() -> None:
    """Only trusted paired disconnected gamepads are reconnect candidates."""
    gateway = FakeReconnectBluez()
    gateway.devices["/org/bluez/hci0/dev_11_22_33_44_55_66"] = {
        "Alias": "Headphones",
        "Icon": "audio-headset",
        "Paired": True,
        "Trusted": True,
        "Connected": False,
    }
    gateway.devices["/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"] = {
        "Alias": "Xbox Wireless Controller",
        "Icon": "input-gaming",
        "Paired": True,
        "Trusted": True,
        "Connected": True,
    }
    assert gateway.reconnect_candidates() == (DEVICE,)
    assert gateway.trusted_gamepad_paths() == (
        DEVICE,
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
    )


def test_try_connect_returns_asleep_when_pad_is_off() -> None:
    """A rejected Connect for a powered-off pad must not raise into the timer."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Host is down")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.ASLEEP
    assert gateway.connect_calls == 1


def test_try_connect_classifies_timeout_and_busy() -> None:
    """Client timeout and BlueZ InProgress are distinct outcomes."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.TIMEOUT
    gateway.fail_connect = BluetoothError("org.bluez.Error.InProgress br-connection-busy")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.BUSY


def test_reconnect_enables_page_scan_without_host_connect() -> None:
    """Ticks only enable inbound page scan; they must not race Device1.Connect."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    service.tick()
    assert gateway.connectable is True
    assert gateway.fast_connectable is True
    assert gateway.connect_calls == 0
    assert service.has_candidates is True


def test_reconnect_uses_btmgmt_when_fast_connectable_property_missing() -> None:
    """BlueZ 5.82 omits FastConnectable on D-Bus; fall back to btmgmt."""
    gateway = FakeReconnectBluez()
    gateway.expose_fast_connectable = False
    service = BluetoothReconnectService(gateway)
    service.tick()
    assert gateway.connectable is True
    assert gateway.btmgmt_calls
    assert gateway.connect_calls == 0


def test_reconnect_tick_skips_while_pairing_ui_active() -> None:
    """Bluetooth settings must not race with background reconnect work."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway, pairing_active=lambda: True)
    service.tick()
    assert gateway.connect_calls == 0
    assert gateway.connectable is False


def test_reconnect_tick_is_single_flight() -> None:
    """Nested ticks while busy are ignored."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    reentered = False
    original = gateway.ensure_accepts_connections

    def nested() -> None:
        nonlocal reentered
        assert service.busy is True
        service.tick()
        reentered = service.busy
        original()

    gateway.ensure_accepts_connections = nested  # type: ignore[method-assign]
    service.tick()
    assert reentered is True
    assert service.busy is False


def test_reconnect_watch_connected_clears_pending() -> None:
    """PropertiesChanged Connected=true clears reconnect state."""
    app = QCoreApplication.instance() or QCoreApplication([])
    del app
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    service._pending_until[DEVICE] = time.monotonic() + 45.0
    watch = BluetoothReconnectWatch(
        QDBusConnection.systemBus(), service, pairing_active=lambda: False,
    )
    watch.handle_properties_changed(
        DEVICE, "org.bluez.Device1", {"Connected": QDBusVariant(True)},
    )
    assert DEVICE not in service._pending_until
    watch.close()


def test_reconnect_watch_disconnected_schedules_page_scan_tick() -> None:
    """PropertiesChanged Connected=false schedules a tick that enables page scan."""
    app = QCoreApplication.instance() or QCoreApplication([])
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    watch = BluetoothReconnectWatch(
        QDBusConnection.systemBus(), service, pairing_active=lambda: False,
    )
    watch.handle_properties_changed(
        DEVICE, "org.bluez.Device1", {"Connected": False},
    )
    app.processEvents()
    assert gateway.connectable is True
    assert gateway.connect_calls == 0
    watch.close()
