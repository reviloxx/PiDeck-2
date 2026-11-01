"""Tests for BlueZ adapter validation and device operation orchestration."""

from threading import Event

import pytest
from PySide6.QtDBus import QDBusVariant

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway


DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


class FakeBluez(BluezGateway):
    """Provide deterministic D-Bus replies without touching host Bluetooth."""

    def __init__(self) -> None:
        super().__init__("/org/pideck/agent/test")
        self.calls: list[tuple[str, str, str]] = []
        self.fail_pair = False

    def _call(self, path, interface, method, *arguments, timeout=10000):
        self.calls.append((path, interface, method))
        if method == "Introspect":
            nodes = '<node name="hci0"/>' if path == "/org/bluez" else '<node name="dev_AA_BB_CC_DD_EE_FF"/>'
            return [f"<node>{nodes}</node>"]
        if method == "Get":
            values = {"Powered": True, "Alias": "Headphones", "Paired": False, "Connected": False}
            return [QDBusVariant(values[arguments[1]])]
        if method == "Pair" and self.fail_pair:
            raise BluetoothError("Pairing rejected")
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


def test_cancelled_discovery_stops_the_bluez_session() -> None:
    """An already-cancelled discovery starts and stops without waiting."""
    gateway = FakeBluez()
    cancelled = Event()
    cancelled.set()
    state = gateway.discover(cancelled)
    assert state.powered
    methods = [method for _, _, method in gateway.calls]
    assert methods.index("StartDiscovery") < methods.index("StopDiscovery")
