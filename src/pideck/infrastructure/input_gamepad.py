"""Optional Linux evdev gamepad adapter with hot-plug discovery."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Any

from pideck.application.ports.input import InputAction, InputEvent, InputSink

_LOGGER = logging.getLogger(__name__)

# Linux gamepad face buttons are position-based (south/east), not label-based.
_BTN_SOUTH = 304
_BTN_EAST = 305
_BTN_NORTH = 307
_BTN_START = 315
_BTN_MODE = 316


@dataclass(slots=True)
class _GamepadDevice:
    """Open gamepad device and its reader thread state."""

    path: Path
    device: Any
    stop: threading.Event
    nintendo_layout: bool = False
    grabbed: bool = False


class EvdevGamepadAdapter:
    """Read standard Linux gamepad events and normalize them for PiDeck."""

    def __init__(
        self, discovery_interval: float = 1.0, keyboard_added: Callable[[], None] | None = None,
    ) -> None:
        """Create an optional evdev adapter with runtime device discovery.

        ``keyboard_added`` is called from the discovery thread one scan after a
        keyboard appears, giving the X server time to register the device.
        """
        self._discovery_interval = discovery_interval
        self.keyboard_added = keyboard_added
        self._keyboards: set[Path] | None = None
        self._lock = threading.RLock()
        self._sink: InputSink | None = None
        self._devices: dict[Path, _GamepadDevice] = {}
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
        for managed in devices:
            managed.stop.set()
            self._apply_grab(managed, False)
            try:
                managed.device.close()
            except OSError:
                _LOGGER.debug("Gamepad device already closed path=%s", managed.path)

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
            paths: set[Path] = set()
            keyboards: set[Path] = set()
            for raw_path in evdev.list_devices():
                path = Path(raw_path)
                try:
                    device = evdev.InputDevice(raw_path)
                    is_gamepad = self._is_gamepad(device)
                    is_keyboard = not is_gamepad and self._is_keyboard(device)
                    device.close()
                except (OSError, PermissionError):
                    continue
                if is_gamepad:
                    paths.add(path)
                    self._open_device(evdev, path)
                elif is_keyboard:
                    keyboards.add(path)
            if self._keyboards is not None and keyboards.difference(self._keyboards):
                _LOGGER.info("Keyboard connected paths=%s", sorted(map(str, keyboards - self._keyboards)))
                keyboard_pending = True
            self._keyboards = keyboards
            with self._lock:
                removed = set(self._devices).difference(paths)
            for path in removed:
                self._close_device(path)
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
        """Identify typing keyboards by the presence of letter keys A and Z."""
        return {30, 44}.issubset(_capability_codes(device.capabilities().get(1, [])))

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

    def _open_device(self, evdev: Any, path: Path) -> None:
        """Open one newly discovered gamepad and start its reader."""
        with self._lock:
            if path in self._devices:
                return
        try:
            device = evdev.InputDevice(str(path))
        except (OSError, PermissionError) as error:
            _LOGGER.warning("Unable to open gamepad path=%s error=%s", path, error)
            return
        nintendo = self._is_nintendo_controller(getattr(device, "name", "") or "")
        managed = _GamepadDevice(path, device, threading.Event(), nintendo_layout=nintendo)
        with self._lock:
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

    def _close_device(self, path: Path) -> None:
        """Stop one disconnected gamepad reader."""
        with self._lock:
            managed = self._devices.pop(path, None)
        if managed is not None:
            managed.stop.set()
            self._apply_grab(managed, False)
            try:
                managed.device.close()
            except OSError:
                pass
            _LOGGER.info("Gamepad disconnected path=%s", path)

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
                action = self._map_event(event, nintendo_layout=managed.nintendo_layout)
                if action is not None:
                    sink = self._sink
                    if sink is not None:
                        sink.handle_input(InputEvent(action, "gamepad"))
        except (OSError, RuntimeError):
            _LOGGER.info("Gamepad reader stopped path=%s", managed.path)
        finally:
            self._close_device(managed.path)

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


def _capability_codes(entries: Any) -> set[int]:
    """Normalize evdev capability entries across backend representations."""
    codes: set[int] = set()
    for entry in entries:
        if isinstance(entry, int):
            codes.add(entry)
        elif isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], int):
            codes.add(entry[0])
    return codes
