"""BlueZ PropertiesChanged watches for gamepad Connected edges."""

from __future__ import annotations

from collections.abc import Callable
import logging

from PySide6.QtCore import QObject, QTimer, SLOT, Slot
from PySide6.QtDBus import QDBusConnection, QDBusVariant

from pideck.infrastructure.bluetooth_reconnect import BluetoothReconnectService

_LOGGER = logging.getLogger(__name__)
_SERVICE = "org.bluez"
_PROPERTIES = "org.freedesktop.DBus.Properties"
_DEVICE = "org.bluez.Device1"
_SIGNAL = "PropertiesChanged"
_SIGNATURE = "sa{sv}as"
_SLOT = SLOT("on_properties_changed(QString,QVariantMap,QStringList)")


def _unwrap_bool(value: object) -> bool | None:
    """Return a Connected property as bool when BlueZ encodes it as a variant."""
    if isinstance(value, QDBusVariant):
        value = value.variant()
    if type(value) is bool:
        return value
    return None


class _PathWatch(QObject):
    """Forward one device path's PropertiesChanged into the parent watcher."""

    def __init__(self, path: str, parent: "BluetoothReconnectWatch") -> None:
        super().__init__(parent)
        self._path = path
        self._parent = parent

    @Slot(str, "QVariantMap", "QStringList")
    def on_properties_changed(self, interface: str, changed: object, invalidated: object) -> None:
        """Handle Device1 property updates for this watched path."""
        del invalidated
        self._parent.handle_properties_changed(self._path, interface, changed)


class BluetoothReconnectWatch(QObject):
    """Subscribe to Connected changes on trusted paired gamepads."""

    def __init__(
        self,
        bus: QDBusConnection,
        service: BluetoothReconnectService,
        *,
        pairing_active: Callable[[], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Create a watcher bound to the reconnect service and system bus."""
        super().__init__(parent)
        self._bus = bus
        self._service = service
        self._pairing_active = pairing_active or (lambda: False)
        self._watches: dict[str, _PathWatch] = {}

    def sync_paths(self, paths: tuple[str, ...] | None = None) -> None:
        """Ensure PropertiesChanged is connected for the given gamepad paths."""
        if paths is None:
            paths = self._service.refresh_watched_paths()
        desired = set(paths)
        for path in list(self._watches):
            if path not in desired:
                self._unsubscribe(path)
        for path in paths:
            if path not in self._watches:
                self._subscribe(path)

    def close(self) -> None:
        """Drop every active PropertiesChanged subscription."""
        for path in list(self._watches):
            self._unsubscribe(path)

    def handle_properties_changed(self, path: str, interface: str, changed: object) -> None:
        """React to Connected true/false for a watched gamepad path."""
        if interface != _DEVICE or not isinstance(changed, dict) or "Connected" not in changed:
            return
        connected = _unwrap_bool(changed.get("Connected"))
        if connected is None:
            return
        if connected:
            self._service.note_connected(path)
            return
        self._service.note_disconnected(path)
        if self._pairing_active():
            return
        QTimer.singleShot(0, self._service.tick)

    def _subscribe(self, path: str) -> None:
        watch = _PathWatch(path, self)
        if not self._bus.connect(
            _SERVICE, path, _PROPERTIES, _SIGNAL, _SIGNATURE, watch, _SLOT,
        ):
            _LOGGER.debug("Bluetooth Connected watch failed path=%s", path)
            watch.deleteLater()
            return
        self._watches[path] = watch
        _LOGGER.debug("Bluetooth Connected watch added path=%s", path)

    def _unsubscribe(self, path: str) -> None:
        watch = self._watches.pop(path, None)
        if watch is None:
            return
        self._bus.disconnect(
            _SERVICE, path, _PROPERTIES, _SIGNAL, _SIGNATURE, watch, _SLOT,
        )
        watch.deleteLater()
        _LOGGER.debug("Bluetooth Connected watch removed path=%s", path)
