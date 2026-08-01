"""Optional Linux evdev gamepad adapter with hot-plug discovery."""

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Any

from pideck.application.ports.input import InputAction, InputEvent, InputSink

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _GamepadDevice:
    """Open gamepad device and its reader thread state."""

    path: Path
    device: Any
    stop: threading.Event


class EvdevGamepadAdapter:
    """Read standard Linux gamepad events and normalize them for PiDeck."""

    def __init__(self, discovery_interval: float = 1.0) -> None:
        """Create an optional evdev adapter with runtime device discovery."""
        self._discovery_interval = discovery_interval
        self._lock = threading.RLock()
        self._sink: InputSink | None = None
        self._devices: dict[Path, _GamepadDevice] = {}
        self._stop = threading.Event()
        self._discovery_thread: threading.Thread | None = None
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

    def close(self) -> None:
        """Stop discovery and all gamepad reader threads."""
        self._stop.set()
        with self._lock:
            devices = list(self._devices.values())
            self._devices.clear()
        for managed in devices:
            managed.stop.set()
            try:
                managed.device.close()
            except OSError:
                _LOGGER.debug("Gamepad device already closed path=%s", managed.path)

    def _discover_loop(self, evdev: Any) -> None:
        """Discover and remove gamepads while the adapter is active."""
        while not self._stop.is_set():
            paths: set[Path] = set()
            for raw_path in evdev.list_devices():
                path = Path(raw_path)
                try:
                    device = evdev.InputDevice(raw_path)
                    is_gamepad = self._is_gamepad(device)
                    device.close()
                except (OSError, PermissionError):
                    continue
                if is_gamepad:
                    paths.add(path)
                    self._open_device(evdev, path)
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
        return bool(absolute_codes.intersection({16, 17}) or button_codes.intersection({304, 305, 307, 308})) or any(
            word in name for word in ("gamepad", "controller", "joystick")
        )

    def _open_device(self, evdev: Any, path: Path) -> None:
        """Open one newly discovered gamepad and start its reader."""
        with self._lock:
            if path in self._devices:
                return
        try:
            device = evdev.InputDevice(str(path))
            device.grab()
        except (OSError, PermissionError) as error:
            _LOGGER.warning("Unable to open gamepad path=%s error=%s", path, error)
            return
        managed = _GamepadDevice(path, device, threading.Event())
        with self._lock:
            self._devices[path] = managed
        threading.Thread(
            target=self._read_device,
            args=(managed,),
            name=f"pideck-gamepad-{device.name}",
            daemon=True,
        ).start()
        _LOGGER.info("Gamepad connected name=%s path=%s", device.name, path)

    def _close_device(self, path: Path) -> None:
        """Stop one disconnected gamepad reader."""
        with self._lock:
            managed = self._devices.pop(path, None)
        if managed is not None:
            managed.stop.set()
            try:
                managed.device.ungrab()
                managed.device.close()
            except OSError:
                pass
            _LOGGER.info("Gamepad disconnected path=%s", path)

    def _read_device(self, managed: _GamepadDevice) -> None:
        """Read and normalize events from one controller."""
        try:
            for event in managed.device.read_loop():
                if managed.stop.is_set() or self._stop.is_set():
                    return
                action = self._map_event(event)
                if action is not None:
                    sink = self._sink
                    if sink is not None:
                        sink.handle_input(InputEvent(action, "gamepad"))
        except (OSError, RuntimeError):
            _LOGGER.info("Gamepad reader stopped path=%s", managed.path)
        finally:
            self._close_device(managed.path)

    @staticmethod
    def _map_event(event: Any) -> InputAction | None:
        """Map Linux evdev codes to D-pad and controller actions."""
        if event.type == 3:
            if event.code == 16:
                return InputAction.LEFT if event.value < 0 else InputAction.RIGHT if event.value > 0 else None
            if event.code == 17:
                return InputAction.UP if event.value < 0 else InputAction.DOWN if event.value > 0 else None
        if event.type == 1 and event.value == 1:
            return {
                304: InputAction.ACTIVATE,
                305: InputAction.BACK,
                307: InputAction.BACK,
                315: InputAction.HOME,
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
