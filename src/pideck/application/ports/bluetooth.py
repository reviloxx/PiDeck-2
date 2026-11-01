"""Bluetooth device models and operations independent of the UI."""

from dataclasses import dataclass
from threading import Event
from typing import Protocol


class BluetoothError(Exception):
    """Report a Bluetooth service, adapter, or device operation failure."""


@dataclass(frozen=True, slots=True)
class BluetoothDevice:
    """Describe one discovered or previously paired Bluetooth device."""

    path: str
    name: str
    paired: bool
    connected: bool


@dataclass(frozen=True, slots=True)
class BluetoothState:
    """Describe the selected adapter and its known devices."""

    powered: bool
    devices: tuple[BluetoothDevice, ...]


@dataclass(frozen=True, slots=True)
class PairingRequest:
    """Describe information or confirmation requested during pairing."""

    kind: str
    value: str = ""


class BluetoothGateway(Protocol):
    """Expose Bluetooth adapter and device operations to the presentation."""

    def inspect(self) -> BluetoothState:
        """Return the adapter state and currently known devices."""
        ...

    def set_power(self, enabled: bool) -> BluetoothState:
        """Enable or disable the adapter and return its current state."""
        ...

    def discover(self, cancelled: Event) -> BluetoothState:
        """Discover devices for a bounded period, or until cancelled."""
        ...

    def cancel_discovery(self) -> None:
        """Stop a discovery operation that is currently waiting."""
        ...

    def pair(self, path: str) -> BluetoothState:
        """Pair with a device using the registered pairing agent."""
        ...

    def cancel_pairing(self, path: str) -> None:
        """Cancel an in-progress pairing attempt."""
        ...

    def connect(self, path: str) -> BluetoothState:
        """Connect to a previously paired device."""
        ...

    def disconnect(self, path: str) -> BluetoothState:
        """Disconnect a device."""
        ...
