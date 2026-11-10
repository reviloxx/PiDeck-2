"""Optional Linux evdev gamepad adapter with hot-plug discovery."""

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any
import xml.etree.ElementTree as ET

from pideck.application.ports.input import InputAction, InputEvent, InputSink

_LOGGER = logging.getLogger(__name__)

# Switch Pro HID keeps reporting while powered; silence means the link is dead
# even when BlueZ still reports Connected for many seconds.
_BLUETOOTH_SILENCE_SECONDS = 2.5

# Linux gamepad face buttons are position-based (south/east), not label-based.
_BTN_SOUTH = 304
_BTN_EAST = 305
_BTN_NORTH = 307
_BTN_START = 315
_BTN_MODE = 316

# linux/input.h bus types for physical keyboards users actually plug in / pair.
_BUS_USB = 3
_BUS_BLUETOOTH = 5
_KEYBOARD_BUSES = frozenset({_BUS_USB, _BUS_BLUETOOTH})
_BLUEZ_ADAPTER = re.compile(r"hci[0-9]+\Z")
_BLUEZ_DEVICE = re.compile(r"dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}\Z")


@dataclass(slots=True)
class _GamepadDevice:
    """Open gamepad device and its reader thread state."""

    path: Path
    device: Any
    stop: threading.Event
    nintendo_layout: bool = False
    grabbed: bool = False
    address: str = ""
    last_event_at: float = field(default_factory=time.monotonic)


class EvdevGamepadAdapter:
    """Read standard Linux gamepad events and normalize them for PiDeck."""

    def __init__(
        self,
        discovery_interval: float = 1.0,
        keyboard_added: Callable[[], None] | None = None,
        presence_changed: Callable[[], None] | None = None,
        device_lost: Callable[[str], None] | None = None,
        *,
        sysfs_input_root: Path | None = None,
    ) -> None:
        """Create an optional evdev adapter with runtime device discovery.

        ``keyboard_added`` is called from the discovery thread one scan after a
        keyboard appears, giving the X server time to register the device.
        ``presence_changed`` fires when open gamepads or counted keyboards change.
        ``device_lost`` receives a Bluetooth MAC when a pad reader dies so BlueZ
        can Disconnect a stale Connected session.
        """
        self._discovery_interval = discovery_interval
        self.keyboard_added = keyboard_added
        self.presence_changed = presence_changed
        self.device_lost = device_lost
        self._sysfs_input_root = sysfs_input_root or Path("/sys/class/input")
        self._keyboards: set[Path] | None = None
        self._lock = threading.RLock()
        self._sink: InputSink | None = None
        self._devices: dict[Path, _GamepadDevice] = {}
        self._suppress_until_absent: set[Path] = set()
        self._suppress_addresses: dict[Path, str] = {}
        self._stop = threading.Event()
        self._discovery_thread: threading.Thread | None = None
        self._exclusive = False
        self.available = False

    def start(self, sink: InputSink) -> None:
        """Start discovery; missing evdev or hardware is non-fatal."""
        self._sink = sink
        try:
            import evdev
        except ImportError:
            _LOGGER.warning("Gamepad support unavailable: install evdev")
            return
        self.available = True
        self._discovery_thread = threading.Thread(
            target=self._discover_loop,
            args=(evdev,),
            name="pideck-gamepad-discovery",
            daemon=True,
        )
        self._discovery_thread.start()

    def set_exclusive(self, exclusive: bool) -> None:
        """Grab or release all open pads so apps cannot also read them.

        Used while PiDeck forwards gamepad actions as keys (YouTube/Kodi). Native
        gamepad apps (Steam Link) leave ``exclusive`` false. Always re-applies
        grab/ungrab to every open device so a prior failed grab is retried.
        """
        with self._lock:
            self._exclusive = exclusive
            devices = list(self._devices.values())
        for managed in devices:
            self._apply_grab(managed, exclusive, force=True)

    def connected_gamepad_count(self) -> int:
        """Return how many gamepad event nodes are currently open."""
        with self._lock:
            return len(self._devices)

    def connected_keyboard_count(self) -> int:
        """Return how many keyboard event nodes the discovery loop last saw."""
        with self._lock:
            return 0 if self._keyboards is None else len(self._keyboards)

    def close(self) -> None:
        """Stop discovery and all gamepad reader threads."""
        self._stop.set()
        with self._lock:
            self._exclusive = False
            devices = list(self._devices.values())
            self._devices.clear()
            self._suppress_until_absent.clear()
            self._suppress_addresses.clear()
        for managed in devices:
            managed.stop.set()
            self._apply_grab(managed, False)
            try:
                managed.device.close()
            except OSError:
                _LOGGER.debug("Gamepad device already closed path=%s", managed.path)

    def _notify_presence_changed(self) -> None:
        """Tell the UI that connected gamepad/keyboard presence may have changed."""
        callback = self.presence_changed
        if callback is None:
            return
        try:
            callback()
        except Exception:
            _LOGGER.exception("Connected-input presence handler failed")

    def _notify_device_lost(self, address: str) -> None:
        """Ask BlueZ to drop a stale Connected session for one Bluetooth MAC."""
        if not address:
            return
        callback = self.device_lost
        if callback is None:
            return
        try:
            callback(address)
        except Exception:
            _LOGGER.exception("Gamepad device-lost handler failed address=%s", address)

    def _discover_loop(self, evdev: Any) -> None:
        """Discover and remove gamepads while the adapter is active."""
        keyboard_pending = False
        while not self._stop.is_set():
            callback = self.keyboard_added
            if keyboard_pending and callback is not None:
                keyboard_pending = False
                try:
                    callback()
                except Exception:
                    _LOGGER.exception("Keyboard hot-plug handler failed")
            bt_connected = self._bluetooth_connected_addresses()
            seen_gamepads: set[Path] = set()
            live_paths: set[Path] = set()
            path_addresses: dict[Path, str] = {}
            keyboards: set[Path] = set()
            for raw_path in evdev.list_devices():
                path = Path(raw_path)
                try:
                    device = evdev.InputDevice(raw_path)
                    is_gamepad = self._is_gamepad(device)
                    live = self._device_is_live(device, path, bt_connected)
                    address = _normalize_bluetooth_address(getattr(device, "uniq", "") or "")
                    is_keyboard = (
                        not is_gamepad and self._is_keyboard(device) and live
                    )
                    device.close()
                except (OSError, PermissionError):
                    continue
                if is_gamepad:
                    seen_gamepads.add(path)
                    if address:
                        path_addresses[path] = address
                    if live:
                        live_paths.add(path)
                elif is_keyboard:
                    keyboards.add(path)
            with self._lock:
                # Clear suppress only after BlueZ drops Connected (or the node
                # vanishes). Do not reopen while Connected is still stale.
                for path in list(self._suppress_until_absent):
                    address = self._suppress_addresses.get(path) or path_addresses.get(path, "")
                    if path not in seen_gamepads or (address and address not in bt_connected):
                        self._suppress_until_absent.discard(path)
                        self._suppress_addresses.pop(path, None)
            for path in live_paths:
                if path not in self._suppress_until_absent:
                    self._open_device(evdev, path)
            previous_keyboards = self._keyboards
            if previous_keyboards is not None and keyboards.difference(previous_keyboards):
                _LOGGER.info(
                    "Keyboard connected paths=%s",
                    sorted(map(str, keyboards - previous_keyboards)),
                )
                keyboard_pending = True
            keyboards_changed = previous_keyboards != keyboards
            self._keyboards = keyboards
            with self._lock:
                removed = set(self._devices).difference(live_paths)
                open_devices = list(self._devices.items())
            for path in removed:
                self._close_device(path, suppress_reopen=True)
            # BlueZ often keeps Connected=true ~20s after power-off; HID goes
            # silent immediately, so drop the icon and Disconnect early.
            now = time.monotonic()
            for path, managed in open_devices:
                if path in removed or not managed.address:
                    continue
                if now - managed.last_event_at < _BLUETOOTH_SILENCE_SECONDS:
                    continue
                _LOGGER.info(
                    "Gamepad silent; closing path=%s address=%s age=%.1fs",
                    path,
                    managed.address,
                    now - managed.last_event_at,
                )
                self._close_device(path, suppress_reopen=True)
            if keyboards_changed:
                self._notify_presence_changed()
            self._stop.wait(self._discovery_interval)

    @staticmethod
    def _is_gamepad(device: Any) -> bool:
        """Identify controllers by joystick/gamepad capabilities."""
        name = (getattr(device, "name", "") or "").casefold()
        capabilities = device.capabilities()
        absolute_codes = _capability_codes(capabilities.get(3, []))
        button_codes = _capability_codes(capabilities.get(1, []))
        if "(imu)" in name:
            return False
        return bool(
            absolute_codes.intersection({16, 17})
            or button_codes.intersection({_BTN_SOUTH, _BTN_EAST, _BTN_NORTH, 308})
        ) or any(word in name for word in ("gamepad", "controller", "joystick"))

    @staticmethod
    def _is_keyboard(device: Any) -> bool:
        """Identify a USB/Bluetooth typing keyboard (not platform i8042 nodes)."""
        if not {30, 44}.issubset(_capability_codes(device.capabilities().get(1, []))):
            return False
        info = getattr(device, "info", None)
        bustype = getattr(info, "bustype", None)
        return bustype in _KEYBOARD_BUSES

    @staticmethod
    def _is_nintendo_controller(name: str) -> bool:
        """Detect Switch Pro / Joy-Con style controllers by device name."""
        folded = name.casefold()
        return (
            "nintendo" in folded
            or "joy-con" in folded
            or "joycon" in folded
            or folded.strip() == "pro controller"
            or folded.startswith("pro controller ")
        )

    def _device_is_live(self, device: Any, path: Path, bt_connected: frozenset[str]) -> bool:
        """Return whether a keyboard/gamepad is powered on and usable right now."""
        online_values = list(self._power_supply_online_values(path))
        if online_values:
            return any(value == "1" for value in online_values)
        info = getattr(device, "info", None)
        bustype = getattr(info, "bustype", None)
        if bustype == _BUS_BLUETOOTH:
            address = _normalize_bluetooth_address(getattr(device, "uniq", "") or "")
            return bool(address) and address in bt_connected
        if bustype == _BUS_USB:
            uniq = (getattr(device, "uniq", "") or "").strip()
            # Unifying-style wireless USB keeps the node with a non-empty uniq
            # even while powered off; without online=1 treat it as absent.
            if uniq:
                return False
            return True
        return False

    def _power_supply_online_values(self, path: Path) -> list[str]:
        """Read ``online`` attributes under the input device's sysfs parents."""
        values: list[str] = []
        try:
            current = (self._sysfs_input_root / path.name / "device").resolve()
        except OSError:
            return values
        for _ in range(8):
            supply_root = current / "power_supply"
            if supply_root.is_dir():
                try:
                    supplies = sorted(supply_root.iterdir())
                except OSError:
                    supplies = []
                for supply in supplies:
                    online = supply / "online"
                    if not online.is_file():
                        continue
                    try:
                        values.append(online.read_text(encoding="utf-8").strip())
                    except OSError:
                        continue
            parent = current.parent
            if parent == current:
                break
            current = parent
        return values

    def _bluetooth_connected_addresses(self) -> frozenset[str]:
        """Return normalized MAC addresses BlueZ currently reports as Connected."""
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return frozenset()
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            return frozenset()
        addresses: set[str] = set()
        try:
            for adapter in _dbus_child_names(bus, "/org/bluez"):
                if not _BLUEZ_ADAPTER.fullmatch(adapter):
                    continue
                adapter_path = f"/org/bluez/{adapter}"
                for name in _dbus_child_names(bus, adapter_path):
                    if not _BLUEZ_DEVICE.fullmatch(name):
                        continue
                    path = f"{adapter_path}/{name}"
                    if not _dbus_device_connected(bus, path):
                        continue
                    addresses.add(
                        _normalize_bluetooth_address(name.removeprefix("dev_").replace("_", ":"))
                    )
        except Exception:
            _LOGGER.debug("Unable to query BlueZ connected devices", exc_info=True)
            return frozenset()
        return frozenset(addresses)

    def _open_device(self, evdev: Any, path: Path) -> None:
        """Open one newly discovered gamepad and start its reader."""
        with self._lock:
            if path in self._devices or path in self._suppress_until_absent:
                return
        try:
            device = evdev.InputDevice(str(path))
        except (OSError, PermissionError) as error:
            _LOGGER.warning("Unable to open gamepad path=%s error=%s", path, error)
            return
        nintendo = self._is_nintendo_controller(getattr(device, "name", "") or "")
        address = _normalize_bluetooth_address(getattr(device, "uniq", "") or "")
        managed = _GamepadDevice(
            path, device, threading.Event(), nintendo_layout=nintendo, address=address,
        )
        with self._lock:
            if path in self._devices or path in self._suppress_until_absent:
                try:
                    device.close()
                except OSError:
                    pass
                return
            self._devices[path] = managed
            exclusive = self._exclusive
        self._apply_grab(managed, exclusive)
        threading.Thread(
            target=self._read_device,
            args=(managed,),
            name=f"pideck-gamepad-{device.name}",
            daemon=True,
        ).start()
        _LOGGER.info(
            "Gamepad connected name=%s path=%s layout=%s exclusive=%s",
            device.name,
            path,
            "nintendo" if nintendo else "xbox",
            exclusive,
        )
        self._notify_presence_changed()

    def _close_device(self, path: Path, *, suppress_reopen: bool = True) -> None:
        """Stop one disconnected gamepad reader."""
        with self._lock:
            managed = self._devices.pop(path, None)
            if managed is not None and suppress_reopen:
                self._suppress_until_absent.add(path)
                if managed.address:
                    self._suppress_addresses[path] = managed.address
        if managed is not None:
            managed.stop.set()
            self._apply_grab(managed, False)
            try:
                managed.device.close()
            except OSError:
                pass
            _LOGGER.info("Gamepad disconnected path=%s", path)
            self._notify_presence_changed()
            if suppress_reopen:
                self._notify_device_lost(managed.address)

    @staticmethod
    def _apply_grab(managed: _GamepadDevice, exclusive: bool, *, force: bool = False) -> None:
        """Grab or ungrab one device; ignore devices that reject the ioctl."""
        if not force and exclusive == managed.grabbed:
            return
        try:
            if exclusive:
                managed.device.grab()
                managed.grabbed = True
            elif managed.grabbed:
                managed.device.ungrab()
                managed.grabbed = False
            _LOGGER.info(
                "Gamepad %s path=%s",
                "grabbed" if exclusive else "released",
                managed.path,
            )
        except (OSError, AttributeError, IOError) as error:
            if exclusive and managed.grabbed:
                _LOGGER.debug("Gamepad already grabbed path=%s error=%s", managed.path, error)
                return
            _LOGGER.warning(
                "Unable to %s gamepad path=%s error=%s",
                "grab" if exclusive else "ungrab",
                managed.path,
                error,
            )
            if exclusive:
                managed.grabbed = False

    def _read_device(self, managed: _GamepadDevice) -> None:
        """Read and normalize events from one controller."""
        try:
            for event in managed.device.read_loop():
                if managed.stop.is_set() or self._stop.is_set():
                    return
                managed.last_event_at = time.monotonic()
                action = self._map_event(event, nintendo_layout=managed.nintendo_layout)
                if action is not None:
                    sink = self._sink
                    if sink is not None:
                        sink.handle_input(InputEvent(action, "gamepad"))
        except (OSError, RuntimeError):
            _LOGGER.info("Gamepad reader stopped path=%s", managed.path)
        finally:
            # Reader exit means the pad died or was closed; suppress re-open while
            # a stale Bluetooth/USB node may still appear in list_devices().
            self._close_device(managed.path, suppress_reopen=True)

    @staticmethod
    def _map_event(event: Any, *, nintendo_layout: bool = False) -> InputAction | None:
        """Map Linux evdev codes to D-pad and controller actions.

        Default (Xbox-style) mapping uses physical positions: south confirms,
        east goes back. Nintendo face labels put A on east and B on south.
        """
        if event.type == 3:
            if event.code == 16:
                return InputAction.LEFT if event.value < 0 else InputAction.RIGHT if event.value > 0 else None
            if event.code == 17:
                return InputAction.UP if event.value < 0 else InputAction.DOWN if event.value > 0 else None
        if event.type == 1 and event.value == 1:
            activate, back = (
                (_BTN_EAST, _BTN_SOUTH) if nintendo_layout else (_BTN_SOUTH, _BTN_EAST)
            )
            return {
                activate: InputAction.ACTIVATE,
                back: InputAction.BACK,
                _BTN_NORTH: InputAction.BACK,
                _BTN_START: InputAction.MENU,
                _BTN_MODE: InputAction.MENU,
            }.get(event.code)
        return None


def _normalize_bluetooth_address(value: str) -> str:
    """Normalize BlueZ / evdev Bluetooth addresses for set membership."""
    return value.strip().upper().replace("-", ":")


def _dbus_child_names(bus: Any, path: str) -> tuple[str, ...]:
    """Return child node names under a BlueZ D-Bus object path."""
    from PySide6.QtDBus import QDBusInterface, QDBusReply

    interface = QDBusInterface(
        "org.bluez", path, "org.freedesktop.DBus.Introspectable", bus,
    )
    reply = QDBusReply(interface.call("Introspect"))
    if not reply.isValid() or not isinstance(reply.value(), str):
        return ()
    try:
        root = ET.fromstring(reply.value())
    except ET.ParseError:
        return ()
    return tuple(name for node in root.findall("node") if (name := node.get("name")) is not None)


def _dbus_device_connected(bus: Any, path: str) -> bool:
    """Return whether BlueZ reports Device1.Connected for one device path."""
    from PySide6.QtDBus import QDBusInterface, QDBusReply, QDBusVariant

    interface = QDBusInterface(
        "org.bluez", path, "org.freedesktop.DBus.Properties", bus,
    )
    reply = QDBusReply(interface.call("Get", "org.bluez.Device1", "Connected"))
    if not reply.isValid():
        return False
    value = reply.value()
    if isinstance(value, QDBusVariant):
        value = value.variant()
    return value is True


def _capability_codes(entries: Any) -> set[int]:
    """Normalize evdev capability entries across backend representations."""
    codes: set[int] = set()
    for entry in entries:
        if isinstance(entry, int):
            codes.add(entry)
        elif isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], int):
            codes.add(entry[0])
    return codes
