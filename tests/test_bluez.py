"""Tests for BlueZ adapter validation and device operation orchestration."""

from threading import Event

import pytest
from PySide6.QtDBus import QDBusVariant

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway


DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
_DEVICE = "org.bluez.Device1"


class FakeBluez(BluezGateway):
    """Provide deterministic D-Bus replies without touching host Bluetooth."""

    def __init__(self) -> None:
        super().__init__("/org/pideck/agent/test")
        self.calls: list[tuple[str, str, str]] = []
        self.fail_pair = False
        self.fail_connect = False
        self.trusted = False
        self.paired = False
        self.connected = False

    def _call(self, path, interface, method, *arguments, timeout=10000):
        self.calls.append((path, interface, method))
        if method == "Introspect":
            nodes = '<node name="hci0"/>' if path == "/org/bluez" else '<node name="dev_AA_BB_CC_DD_EE_FF"/>'
            return [f"<node>{nodes}</node>"]
        if method == "Get":
            values = {
                "Powered": True,
                "Alias": "Headphones",
                "Paired": self.paired,
                "Connected": self.connected,
            }
            return [QDBusVariant(values[arguments[1]])]
        if method == "Set" and arguments[0] == _DEVICE and arguments[1] == "Trusted":
            variant = arguments[2]
            self.trusted = bool(variant.variant() if isinstance(variant, QDBusVariant) else variant)
            return []
        if method == "Pair":
            if self.fail_pair:
                raise BluetoothError("Pairing rejected")
            self.paired = True
            return []
        if method == "Connect":
            if self.fail_connect:
                raise BluetoothError("Host is down")
            self.connected = True
            return []
        return []


def test_bluez_reads_devices_and_rejects_stale_or_invalid_paths() -> None:
    """Only devices currently reported by the selected adapter are actionable."""
    gateway = FakeBluez()
    state = gateway.inspect()
    assert state.powered
    assert state.devices[0].name == "Headphones"
    assert state.devices[0].path == DEVICE
    with pytest.raises(BluetoothError, match="Invalid"):
        gateway.connect("/org/bluez/hci0/invalid")
    with pytest.raises(BluetoothError, match="no longer available"):
        gateway.connect("/org/bluez/hci0/dev_00_00_00_00_00_00")


def test_bluez_unregisters_agent_after_rejected_pairing() -> None:
    """A failed pairing always removes its temporary agent registration."""
    gateway = FakeBluez()
    gateway.fail_pair = True
    with pytest.raises(BluetoothError, match="Pairing rejected"):
        gateway.pair(DEVICE)
    methods = [method for _, _, method in gateway.calls]
    assert methods.index("RegisterAgent") < methods.index("Pair") < methods.index("UnregisterAgent")
    assert "Connect" not in methods
    assert gateway.trusted is False


def test_bluez_pair_trusts_and_connects() -> None:
    """Successful pairing marks the device trusted and opens the HID session."""
    gateway = FakeBluez()
    state = gateway.pair(DEVICE)
    methods = [method for _, _, method in gateway.calls]
    assert methods.index("Pair") < methods.index("UnregisterAgent")
    assert methods.index("UnregisterAgent") < methods.index("Set") < methods.index("Connect")
    assert gateway.trusted is True
    assert gateway.paired is True
    assert gateway.connected is True
    assert state.devices[0].paired is True
    assert state.devices[0].connected is True


def test_bluez_pair_keeps_trust_when_connect_fails() -> None:
    """An asleep controller still remains paired and trusted after pairing."""
    gateway = FakeBluez()
    gateway.fail_connect = True
    state = gateway.pair(DEVICE)
    assert gateway.trusted is True
    assert gateway.paired is True
    assert gateway.connected is False
    assert state.devices[0].paired is True
    assert state.devices[0].connected is False


def test_bluez_connect_trusts_device() -> None:
    """Manual connect marks the device trusted before opening the session."""
    gateway = FakeBluez()
    gateway.paired = True
    state = gateway.connect(DEVICE)
    methods = [method for _, _, method in gateway.calls]
    assert methods.index("Set") < methods.index("Connect")
    assert gateway.trusted is True
    assert gateway.connected is True
    assert state.devices[0].connected is True


def test_cancelled_discovery_stops_the_bluez_session() -> None:
    """An already-cancelled discovery starts and stops without waiting."""
    gateway = FakeBluez()
    cancelled = Event()
    cancelled.set()
    state = gateway.discover(cancelled)
    assert state.powered
    methods = [method for _, _, method in gateway.calls]
    assert methods.index("StartDiscovery") < methods.index("StopDiscovery")
