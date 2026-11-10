"""Host-initiated BlueZ reconnect for trusted gamepads (Switch Pro, etc.)."""

from collections.abc import Callable
import logging
import time

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway, ConnectAttempt

_LOGGER = logging.getLogger(__name__)

# BlueZ keeps a BR connect running after the client D-Bus wait times out; do not
# start another Connect for the same device until this window ends.
_DEFAULT_PENDING_SECONDS = 45.0
_DEFAULT_CONNECT_TIMEOUT_MS = 10000


class BluetoothReconnectService:
    """Periodically Connect trusted disconnected controllers on the Qt thread."""

    def __init__(
        self,
        gateway: BluezGateway,
        *,
        pairing_active: Callable[[], bool] | None = None,
        pending_seconds: float = _DEFAULT_PENDING_SECONDS,
        connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS,
    ) -> None:
        """Create a single-flight reconnect helper bound to one BlueZ gateway."""
        self._gateway = gateway
        self._pairing_active = pairing_active or (lambda: False)
        self._pending_seconds = pending_seconds
        self._connect_timeout_ms = connect_timeout_ms
        self._busy = False
        self._pending_until: dict[str, float] = {}

    @property
    def busy(self) -> bool:
        """Return whether a Connect attempt is currently in progress."""
        return self._busy

    def tick(self) -> None:
        """Attempt Connect for candidates that are not already BlueZ-pending."""
        if self._busy or self._pairing_active():
            return
        self._busy = True
        try:
            try:
                candidates = self._gateway.reconnect_candidates()
            except BluetoothError as error:
                _LOGGER.debug("Bluetooth reconnect scan skipped: %s", error)
                return
            now = time.monotonic()
            candidate_set = set(candidates)
            for path in list(self._pending_until):
                if path not in candidate_set:
                    # Connected (or unpaired) — BlueZ finished; allow future retries.
                    del self._pending_until[path]
            for path in candidates:
                if self._pairing_active():
                    return
                pending_until = self._pending_until.get(path)
                if pending_until is not None and now < pending_until:
                    continue
                result = self._gateway.try_connect(path, timeout=self._connect_timeout_ms)
                if result is ConnectAttempt.OK:
                    self._pending_until.pop(path, None)
                    _LOGGER.info("Bluetooth gamepad reconnected path=%s", path)
                    continue
                # Client timeout / busy / failure: BlueZ may still own the attempt.
                self._pending_until[path] = time.monotonic() + self._pending_seconds
                _LOGGER.debug(
                    "Bluetooth reconnect deferred path=%s result=%s cooldown=%.0fs",
                    path,
                    result.value,
                    self._pending_seconds,
                )
        finally:
            self._busy = False
