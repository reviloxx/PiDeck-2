"""Tests for host-initiated Bluetooth gamepad reconnect."""

from PySide6.QtDBus import QDBusVariant

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway, is_reconnect_gamepad
from pideck.infrastructure.bluetooth_reconnect import BluetoothReconnectService


DEVICE = "/org/bluez/hci0/dev_98_B6_E9_B2_4F_2C"
_DEVICE = "org.bluez.Device1"


class FakeReconnectBluez(BluezGateway):
    """Deterministic BlueZ replies for reconnect candidate and Connect tests."""

    def __init__(self) -> None:
        super().__init__("/org/pideck/agent/test")
        self.calls: list[tuple[str, str, str]] = []
        self.powered = True
        self.devices: dict[str, dict[str, object]] = {
            DEVICE: {
                "Alias": "Pro Controller",
                "Icon": "input-gaming",
                "Paired": True,
                "Trusted": True,
                "Connected": False,
            },
        }
        self.fail_connect = False
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
            if path.endswith("/hci0") and arguments[1] == "Powered":
                return [QDBusVariant(self.powered)]
            values = self.devices[path]
            return [QDBusVariant(values[arguments[1]])]
        if method == "Set" and arguments[0] == _DEVICE and arguments[1] == "Trusted":
            self.devices[path]["Trusted"] = True
            return []
        if method == "Connect":
            self.connect_calls += 1
            if self.fail_connect:
                raise BluetoothError("Host is down")
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

    gateway.devices[DEVICE]["Connected"] = True
    assert gateway.reconnect_candidates() == ()


def test_try_connect_returns_false_when_pad_is_off() -> None:
    """A sleeping controller must not raise into the reconnect timer."""
    gateway = FakeReconnectBluez()
    gateway.fail_connect = True
    assert gateway.try_connect(DEVICE) is False
    assert gateway.connect_calls == 1
    assert gateway.devices[DEVICE]["Connected"] is False


def test_reconnect_tick_connects_and_is_single_flight() -> None:
    """One tick connects a candidate; a nested tick while busy is ignored."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway)
    reentered = False

    original_try = gateway.try_connect

    def blocking_try(path: str, *, timeout: int = 4000) -> bool:
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


def test_reconnect_tick_skips_while_pairing_ui_active() -> None:
    """Bluetooth settings must not race Pair with background Connect."""
    gateway = FakeReconnectBluez()
    service = BluetoothReconnectService(gateway, pairing_active=lambda: True)
    service.tick()
    assert gateway.connect_calls == 0
