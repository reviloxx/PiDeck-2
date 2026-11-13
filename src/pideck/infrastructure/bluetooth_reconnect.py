"""Inbound-first BlueZ reconnect for trusted gamepads (Switch Pro, etc.).

CSR dongles need page scan (Connectable). Continuous host ``Device1.Connect``
while the pad is off pages for ~5s every few seconds and races the pad's own
page-in: BlueZ then logs ``Refusing input device connect: Operation already in
progress`` and resets the link. Keep the adapter connectable and let the pad
initiate; only fall back to a rare host Connect after a long quiet window.
"""

from collections.abc import Callable
import logging
import time

from pideck.application.ports.bluetooth import BluetoothError
from pideck.infrastructure.bluez import BluezGateway, ConnectAttempt

_LOGGER = logging.getLogger(__name__)

# After a disconnect (or boot with a paired pad off), wait this long for inbound
# page-in before a single host Connect fallback.
_DEFAULT_HOST_FALLBACK_SECONDS = 25.0
# BlueZ may still own a BR connect after InProgress; do not hammer Connect.
_DEFAULT_BUSY_SECONDS = 45.0
# Client wait ended but BlueZ may still finish; PropertiesChanged clears early.
_DEFAULT_TIMEOUT_SECONDS = 12.0
# Host is down: stay quiet so inbound page-in owns the next wake window.
_DEFAULT_ASLEEP_COOLDOWN_SECONDS = 60.0
# Other finished Connect failures: brief backoff without starving wake retries.
_DEFAULT_FAILED_COOLDOWN_SECONDS = 15.0
# Match manual connect: UserspaceHID setup can exceed a short D-Bus wait.
_DEFAULT_CONNECT_TIMEOUT_MS = 30000
_IDLE_POLL_MS = 1500
_ACTIVE_POLL_MS = 1000


class BluetoothReconnectService:
    """Keep page scan on and rarely host-Connect trusted disconnected pads."""

    def __init__(
        self,
        gateway: BluezGateway,
        *,
        pairing_active: Callable[[], bool] | None = None,
        host_fallback_seconds: float = _DEFAULT_HOST_FALLBACK_SECONDS,
        busy_seconds: float = _DEFAULT_BUSY_SECONDS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        asleep_cooldown_seconds: float = _DEFAULT_ASLEEP_COOLDOWN_SECONDS,
        failed_cooldown_seconds: float = _DEFAULT_FAILED_COOLDOWN_SECONDS,
        connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS,
        # Back-compat alias used by older tests / callers.
        pending_seconds: float | None = None,
    ) -> None:
        """Create a single-flight reconnect helper bound to one BlueZ gateway."""
        self._gateway = gateway
        self._pairing_active = pairing_active or (lambda: False)
        self._host_fallback_seconds = host_fallback_seconds
        self._busy_seconds = busy_seconds if pending_seconds is None else pending_seconds
        self._timeout_seconds = timeout_seconds
        self._asleep_cooldown_seconds = asleep_cooldown_seconds
        self._failed_cooldown_seconds = failed_cooldown_seconds
        self._connect_timeout_ms = connect_timeout_ms
        self._busy = False
        self._pending_until: dict[str, float] = {}
        self._seen_since: dict[str, float] = {}
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
        """Drop a path's reconnect cooldown so the next tick may Connect again."""
        self._pending_until.pop(path, None)

    def note_connected(self, path: str) -> None:
        """BlueZ Connected became true — end any blackout for that device."""
        self.clear_pending(path)
        self._seen_since.pop(path, None)
        _LOGGER.debug("Bluetooth reconnect pending cleared path=%s reason=connected", path)

    def note_disconnected(self, path: str) -> None:
        """BlueZ Connected became false — prefer inbound; reset fallback timer."""
        self.clear_pending(path)
        self._seen_since[path] = time.monotonic()
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
        """Keep page scan enabled; host-Connect only after inbound has had time."""
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
            now = time.monotonic()
            candidate_set = set(candidates)
            for path in list(self._pending_until):
                if path not in candidate_set:
                    del self._pending_until[path]
            for path in list(self._seen_since):
                if path not in candidate_set:
                    del self._seen_since[path]
            for path in candidates:
                self._seen_since.setdefault(path, now)
                if self._pairing_active():
                    return
                pending_until = self._pending_until.get(path)
                if pending_until is not None and now < pending_until:
                    continue
                # Prefer inbound page-in; host Connect races it on CSR adapters.
                if now - self._seen_since[path] < self._host_fallback_seconds:
                    continue
                result = self._gateway.try_connect(path, timeout=self._connect_timeout_ms)
                if result is ConnectAttempt.TIMEOUT and self._gateway.device_is_connected(path):
                    result = ConnectAttempt.OK
                if result is ConnectAttempt.OK:
                    self._pending_until.pop(path, None)
                    self._seen_since.pop(path, None)
                    _LOGGER.info("Bluetooth gamepad reconnected path=%s", path)
                    continue
                if result is ConnectAttempt.BUSY:
                    cooldown = self._busy_seconds
                elif result is ConnectAttempt.TIMEOUT:
                    cooldown = self._timeout_seconds
                elif result is ConnectAttempt.ASLEEP:
                    cooldown = self._asleep_cooldown_seconds
                else:
                    cooldown = self._failed_cooldown_seconds
                self._pending_until[path] = time.monotonic() + cooldown
                # After a host attempt, restart the inbound-first quiet window.
                self._seen_since[path] = time.monotonic()
                _LOGGER.debug(
                    "Bluetooth reconnect deferred path=%s result=%s cooldown=%.0fs",
                    path,
                    result.value,
                    cooldown,
                )
            try:
                self._has_candidates = bool(self._gateway.reconnect_candidates())
            except BluetoothError:
                pass
        finally:
            self._busy = False
