"""Inbound BlueZ reconnect for trusted gamepads (Switch Pro, etc.).

CSR USB dongles need page scan (``Connectable`` + ``fast-connectable``). Host
``Device1.Connect`` while the pad is off pages for several seconds and races the
pad's own page-in: BlueZ then logs ``Refusing input device connect: Operation
already in progress`` and resets the link. Do not auto-Connect; keep the
adapter accepting inbound pages and react to ``Connected`` via PropertiesChanged.
Manual Connect from Bluetooth settings still works.
"""

from collections.abc import Callable
import logging

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway

_LOGGER = logging.getLogger(__name__)

_IDLE_POLL_MS = 2000
_ACTIVE_POLL_MS = 1000


class BluetoothReconnectService:
    """Keep page scan / fast-connectable on for trusted disconnected pads."""

    def __init__(
        self,
        gateway: BluezGateway,
        *,
        pairing_active: Callable[[], bool] | None = None,
        # Retained for call-site / test compatibility; auto host-Connect is off.
        host_fallback_seconds: float = 0.0,
        busy_seconds: float = 45.0,
        timeout_seconds: float = 12.0,
        asleep_cooldown_seconds: float = 60.0,
        failed_cooldown_seconds: float = 15.0,
        connect_timeout_ms: int = 30000,
        pending_seconds: float | None = None,
    ) -> None:
        """Create a reconnect helper that only maintains inbound page scan."""
        del host_fallback_seconds, busy_seconds, timeout_seconds
        del asleep_cooldown_seconds, failed_cooldown_seconds, connect_timeout_ms
        del pending_seconds
        self._gateway = gateway
        self._pairing_active = pairing_active or (lambda: False)
        self._busy = False
        self._pending_until: dict[str, float] = {}
        self._has_candidates = False
        self._watched_paths: tuple[str, ...] = ()

    @property
    def busy(self) -> bool:
        """Return whether a Connect attempt is currently in progress."""
        return self._busy

    @property
    def has_candidates(self) -> bool:
        """Return whether the last tick saw disconnected trusted gamepads."""
        return self._has_candidates

    @property
    def poll_interval_ms(self) -> int:
        """Return the recommended QTimer interval for the current candidate set."""
        return _ACTIVE_POLL_MS if self._has_candidates else _IDLE_POLL_MS

    @property
    def watched_paths(self) -> tuple[str, ...]:
        """Return trusted gamepad paths that should have PropertiesChanged watches."""
        return self._watched_paths

    def clear_pending(self, path: str) -> None:
        """Drop a path's reconnect cooldown (kept for callers / PropertiesChanged)."""
        self._pending_until.pop(path, None)

    def note_connected(self, path: str) -> None:
        """BlueZ Connected became true — clear any leftover reconnect state."""
        self.clear_pending(path)
        _LOGGER.debug("Bluetooth reconnect pending cleared path=%s reason=connected", path)

    def note_disconnected(self, path: str) -> None:
        """BlueZ Connected became false — ensure page scan stays enabled."""
        self.clear_pending(path)
        _LOGGER.debug("Bluetooth reconnect pending cleared path=%s reason=disconnected", path)

    def refresh_watched_paths(self) -> tuple[str, ...]:
        """Refresh trusted gamepad paths used for PropertiesChanged subscriptions."""
        try:
            self._watched_paths = self._gateway.trusted_gamepad_paths()
        except BluetoothError as error:
            _LOGGER.debug("Bluetooth reconnect watch scan skipped: %s", error)
            self._watched_paths = ()
        return self._watched_paths

    def tick(self) -> None:
        """Enable inbound page scan; never host-Connect (races Switch Pro page-in)."""
        if self._busy or self._pairing_active():
            return
        self._busy = True
        try:
            self._gateway.ensure_accepts_connections()
            self.refresh_watched_paths()
            try:
                candidates = self._gateway.reconnect_candidates()
            except BluetoothError as error:
                _LOGGER.debug("Bluetooth reconnect scan skipped: %s", error)
                self._has_candidates = False
                return
            self._has_candidates = bool(candidates)
            candidate_set = set(candidates)
            for path in list(self._pending_until):
                if path not in candidate_set:
                    del self._pending_until[path]
        finally:
            self._busy = False
