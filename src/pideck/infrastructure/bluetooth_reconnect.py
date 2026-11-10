"""Host-initiated BlueZ reconnect for trusted gamepads (Switch Pro, etc.)."""

from collections.abc import Callable
import logging

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway

_LOGGER = logging.getLogger(__name__)


class BluetoothReconnectService:
    """Periodically Connect trusted disconnected controllers on the Qt thread."""

    def __init__(
        self,
        gateway: BluezGateway,
        *,
        pairing_active: Callable[[], bool] | None = None,
    ) -> None:
        """Create a single-flight reconnect helper bound to one BlueZ gateway."""
        self._gateway = gateway
        self._pairing_active = pairing_active or (lambda: False)
        self._busy = False

    @property
    def busy(self) -> bool:
        """Return whether a Connect attempt is currently in progress."""
        return self._busy

    def tick(self) -> None:
        """Attempt Connect for each reconnect candidate; no overlapping ticks."""
        if self._busy or self._pairing_active():
            return
        self._busy = True
        try:
            try:
                candidates = self._gateway.reconnect_candidates()
            except BluetoothError as error:
                _LOGGER.debug("Bluetooth reconnect scan skipped: %s", error)
                return
            for path in candidates:
                if self._pairing_active():
                    return
                if self._gateway.try_connect(path):
                    _LOGGER.info("Bluetooth gamepad reconnected path=%s", path)
        finally:
            self._busy = False
