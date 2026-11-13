"""Tests for host-initiated Bluetooth gamepad reconnect."""

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
        self.connect_marks_connected_on_fail = False
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
                if self.connect_marks_connected_on_fail:
                    self.devices[path]["Connected"] = True
                raise self.fail_connect
            self.devices[path]["Connected"] = True
            return []
        return []


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

    gateway.devices[DEVICE]["Connected"] = True
    assert gateway.reconnect_candidates() == ()


def test_try_connect_returns_asleep_when_pad_is_off() -> None:
    """A rejected Connect for a powered-off pad must not raise into the timer."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Host is down")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.ASLEEP
    assert gateway.connect_calls == 1
    methods = [method for _, _, method in gateway.calls]
    assert "Disconnect" not in methods
    assert "Connect" in methods


def test_try_connect_classifies_generic_failed() -> None:
    """Non-asleep Connect errors remain a generic finished failure."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("org.bluez.Error.Failed Operation failed")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.FAILED


def test_try_connect_does_not_disconnect_first() -> None:
    """Auto-reconnect must not Disconnect before Connect when Connected is false."""
    gateway = FakeReconnectBluez()
    assert gateway.try_connect(DEVICE) is ConnectAttempt.OK
    methods = [method for _, _, method in gateway.calls]
    assert "Disconnect" not in methods
    assert methods.index("Set") < methods.index("Connect")


def test_try_connect_classifies_timeout_and_busy() -> None:
    """Client timeout and BlueZ InProgress are distinct outcomes."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.TIMEOUT

    gateway.fail_connect = BluetoothError("org.bluez.Error.InProgress br-connection-busy")
    assert gateway.try_connect(DEVICE) is ConnectAttempt.BUSY


def test_reconnect_tick_connects_and_is_single_flight() -> None:
    """One tick connects a candidate; a nested tick while busy is ignored."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway, busy_seconds=45.0)
    reentered = False

    original_try = gateway.try_connect

    def blocking_try(path: str, *, timeout: int = 10000) -> ConnectAttempt:
        nonlocal reentered
        assert service.busy is True
        service.tick()
        reentered = service.busy
        return original_try(path, timeout=timeout)

    gateway.try_connect = blocking_try  # type: ignore[method-assign]
    service.tick()
    assert gateway.connect_calls == 1
    assert gateway.devices[DEVICE]["Connected"] is True
    assert reentered is True
    assert service.busy is False
    assert service.has_candidates is False
    assert service.poll_interval_ms == 1500


def test_reconnect_tick_skips_while_pairing_ui_active() -> None:
    """Bluetooth settings must not race Pair with background Connect."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway, pairing_active=lambda: True)
    service.tick()
    assert gateway.connect_calls == 0


def test_reconnect_does_not_retry_within_cooldown_after_timeout() -> None:
    """A timed-out Connect must not start another attempt while BlueZ may still run."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    service = BluetoothReconnectService(gateway, timeout_seconds=12.0, connect_timeout_ms=10000)

    service.tick()
    assert gateway.connect_calls == 1

    gateway.fail_connect = BluetoothError("org.bluez.Error.InProgress br-connection-busy")
    service.tick()
    assert gateway.connect_calls == 1

    # After cooldown, a new Connect is allowed.
    service._pending_until[DEVICE] = 0.0
    gateway.fail_connect = None
    service.tick()
    assert gateway.connect_calls == 2
    assert gateway.devices[DEVICE]["Connected"] is True


def test_reconnect_timeout_connected_recheck_counts_as_success() -> None:
    """D-Bus wait may end while BlueZ already finished Connected=true."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    gateway.connect_marks_connected_on_fail = True
    service = BluetoothReconnectService(gateway, timeout_seconds=12.0, connect_timeout_ms=10000)
    service.tick()
    assert gateway.connect_calls == 1
    assert DEVICE not in service._pending_until
    assert gateway.devices[DEVICE]["Connected"] is True


def test_reconnect_clear_pending_allows_immediate_retry() -> None:
    """PropertiesChanged / device_lost must clear a TIMEOUT blackout immediately."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    service = BluetoothReconnectService(gateway, timeout_seconds=12.0, connect_timeout_ms=10000)
    service.tick()
    assert gateway.connect_calls == 1
    assert DEVICE in service._pending_until

    service.clear_pending(DEVICE)
    gateway.fail_connect = None
    gateway.devices[DEVICE]["Connected"] = False
    service.tick()
    assert gateway.connect_calls == 2
    assert gateway.devices[DEVICE]["Connected"] is True


def test_reconnect_timeout_cooldown_shorter_than_busy() -> None:
    """TIMEOUT uses a shorter blackout than InProgress/BUSY."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Bluetooth service did not complete Connect")
    service = BluetoothReconnectService(
        gateway, busy_seconds=45.0, timeout_seconds=12.0, connect_timeout_ms=10000,
    )
    before = time.monotonic()
    service.tick()
    timeout_until = service._pending_until[DEVICE]
    assert 0 < timeout_until - before <= 12.5

    service.clear_pending(DEVICE)
    gateway.fail_connect = BluetoothError("org.bluez.Error.InProgress br-connection-busy")
    before = time.monotonic()
    service.tick()
    busy_until = service._pending_until[DEVICE]
    assert busy_until - before > 40.0


def test_reconnect_enables_page_scan_before_connect() -> None:
    """Paired pads can only page in when the adapter is Connectable."""
    gateway = FakeReconnectBluez()
    assert gateway.connectable is False
    service = BluetoothReconnectService(gateway)
    service.tick()
    assert gateway.connectable is True
    assert gateway.fast_connectable is True
    assert gateway.connect_calls == 1


def test_reconnect_retries_after_asleep_cooldown_clears() -> None:
    """Asleep failures use a short wake-window cooldown, not the busy window."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Host is down")
    service = BluetoothReconnectService(
        gateway, busy_seconds=45.0, asleep_cooldown_seconds=0.0, connect_timeout_ms=10000,
    )
    service.tick()
    assert gateway.connect_calls == 1
    gateway.fail_connect = None
    service.tick()
    assert gateway.connect_calls == 2


def test_reconnect_default_asleep_cooldown_blocks_immediate_retry() -> None:
    """Host-is-down must not hammer Connect every timer tick."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Host is down")
    service = BluetoothReconnectService(gateway, busy_seconds=45.0)
    service.tick()
    assert gateway.connect_calls == 1
    service.tick()
    assert gateway.connect_calls == 1
    assert service._pending_until[DEVICE] > 0
    assert service.has_candidates is True
    assert service.poll_interval_ms == 500


def test_reconnect_asleep_cooldown_is_shorter_than_pending() -> None:
    """Wake retries must clear well before the InProgress blackout window."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("Host is down")
    service = BluetoothReconnectService(gateway, busy_seconds=45.0)
    before = time.monotonic()
    service.tick()
    pending_until = service._pending_until[DEVICE]
    assert 0 < pending_until - before <= 2.5


def test_reconnect_generic_failed_uses_failed_cooldown() -> None:
    """Non-asleep finished failures use the failed cooldown, not busy seconds."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = BluetoothError("org.bluez.Error.Failed Operation failed")
    service = BluetoothReconnectService(
        gateway, busy_seconds=45.0, failed_cooldown_seconds=0.0, connect_timeout_ms=10000,
    )
    service.tick()
    assert gateway.connect_calls == 1
    gateway.fail_connect = None
    service.tick()
    assert gateway.connect_calls == 2


def test_reconnect_watch_connected_clears_pending() -> None:
    """PropertiesChanged Connected=true ends a reconnect blackout immediately."""
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


def test_reconnect_watch_disconnected_clears_pending_and_ticks() -> None:
    """PropertiesChanged Connected=false clears blackout and schedules a tick."""
    app = QCoreApplication.instance() or QCoreApplication([])
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    service._pending_until[DEVICE] = time.monotonic() + 45.0
    watch = BluetoothReconnectWatch(
        QDBusConnection.systemBus(), service, pairing_active=lambda: False,
    )
    watch.handle_properties_changed(
        DEVICE, "org.bluez.Device1", {"Connected": False},
    )
    assert DEVICE not in service._pending_until
    app.processEvents()
    assert gateway.connect_calls == 1
    assert gateway.devices[DEVICE]["Connected"] is True
    watch.close()
