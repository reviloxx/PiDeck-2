"""Stand-in Bluetooth gateway and pairing agent used when BlueZ is unavailable.

The Bluetooth window is opened with this object so the failure is shown
inside that window, with the same Refresh and Back actions as other errors,
instead of as an error line on the Settings page.
"""

from collections.abc import Callable
from threading import Event

from pideck.application.ports.bluetooth import BluetoothError, BluetoothState, PairingRequest


class UnavailableBluetooth:
    """Report one fixed error for every Bluetooth operation."""

    path = ""

    def __init__(self, message: str) -> None:
        """Remember the user-facing reason Bluetooth cannot be used."""
        self._message = message

    def _fail(self) -> BluetoothState:
        raise BluetoothError(self._message)

    def inspect(self) -> BluetoothState:
        """Fail with the unavailability reason."""
        return self._fail()

    def set_power(self, enabled: bool) -> BluetoothState:
        """Fail with the unavailability reason."""
        del enabled
        return self._fail()

    def discover(self, cancelled: Event) -> BluetoothState:
        """Fail with the unavailability reason."""
        del cancelled
        return self._fail()

    def cancel_discovery(self) -> None:
        """Nothing is running."""

    def pair(self, path: str) -> BluetoothState:
        """Fail with the unavailability reason."""
        del path
        return self._fail()

    def cancel_pairing(self, path: str) -> None:
        """Nothing is pairing."""
        del path

    def connect(self, path: str) -> BluetoothState:
        """Fail with the unavailability reason."""
        del path
        return self._fail()

    def disconnect(self, path: str) -> BluetoothState:
        """Fail with the unavailability reason."""
        del path
        return self._fail()

    def on_request(self, callback: Callable[[PairingRequest], None]) -> None:
        """No pairing requests can arrive."""
        del callback

    def on_notice(self, callback: Callable[[PairingRequest], None]) -> None:
        """No pairing notices can arrive."""
        del callback

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """No pairing cancellations can arrive."""
        del callback

    def set_target(self, path: str | None) -> None:
        """No pairing target exists."""
        del path

    def respond(self, value: bool | str | int | None) -> None:
        """No request awaits a response."""
        del value

    def close(self) -> None:
        """Nothing to release."""
