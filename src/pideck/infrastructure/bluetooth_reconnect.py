"""Host-initiated BlueZ reconnect for trusted gamepads (Switch Pro, etc.)."""

from collections.abc import Callable
import logging
import time

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
        failure_backoff_seconds: float = 1.5,
    ) -> None:
        """Create a single-flight reconnect helper bound to one BlueZ gateway."""
        self._gateway = gateway
        self._pairing_active = pairing_active or (lambda: False)
        self._failure_backoff_seconds = failure_backoff_seconds
        self._busy = False
        self._retry_after = 0.0

    @property
    def busy(self) -> bool:
        """Return whether a Connect attempt is currently in progress."""
        return self._busy

    def tick(self) -> None:
        """Attempt Connect for each reconnect candidate; no overlapping ticks."""
        if self._busy or self._pairing_active():
            return
        now = time.monotonic()
        if now < self._retry_after:
            return
        self._busy = True
        try:
            try:
                candidates = self._gateway.reconnect_candidates()
            except BluetoothError as error:
                _LOGGER.debug("Bluetooth reconnect scan skipped: %s", error)
                return
            if not candidates:
                return
            connected_any = False
            for path in candidates:
                if self._pairing_active():
                    return
                if self._gateway.try_connect(path):
                    _LOGGER.info("Bluetooth gamepad reconnected path=%s", path)
                    connected_any = True
            if not connected_any:
                self._retry_after = time.monotonic() + self._failure_backoff_seconds
        finally:
            self._busy = False
