"""Tests for normalized Linux gamepad input."""

from types import SimpleNamespace
from pathlib import Path

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.input_gamepad import EvdevGamepadAdapter


def test_gamepad_maps_dpad_and_buttons() -> None:
    """Standard evdev codes become normalized PiDeck actions."""
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=-1)) is InputAction.LEFT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=1)) is InputAction.RIGHT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=-1)) is InputAction.UP
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=1)) is InputAction.DOWN
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=1)) is InputAction.ACTIVATE
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=305, value=1)) is InputAction.BACK
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=315, value=1)) is InputAction.MENU
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=0)) is None


def test_gamepad_event_is_source_tagged() -> None:
    """Normalized events identify their physical source."""
    event = InputEvent(InputAction.ACTIVATE, "gamepad")

    assert event.source == "gamepad"
    assert event.pressed is True


def test_gamepad_detection_accepts_integer_capability_codes() -> None:
    """Real evdev devices may expose capabilities as integer code lists."""
    device = SimpleNamespace(
        name="Generic USB Controller",
        capabilities=lambda: {1: [304, 305], 3: [16, 17]},
    )

    assert EvdevGamepadAdapter._is_gamepad(device) is True


def test_opening_gamepad_does_not_take_exclusive_control() -> None:
    """PiDeck reads controller events without preventing launched apps from reading them."""
    class FakeDevice:
        name = "Generic USB Controller"

        def __init__(self) -> None:
            self.grab_calls = 0
            self.closed = False

        def grab(self) -> None:
            self.grab_calls += 1

        def read_loop(self) -> list[object]:
            return []

        def close(self) -> None:
            self.closed = True

    device = FakeDevice()
    adapter = EvdevGamepadAdapter()
    evdev = SimpleNamespace(InputDevice=lambda path: device)

    adapter._open_device(evdev, Path("/dev/input/event0"))

    adapter.close()
    assert device.grab_calls == 0


def test_keyboard_hotplug_notifies_once_after_the_next_scan() -> None:
    """Keyboards present at startup are ignored; later ones trigger one callback."""
    keyboard = SimpleNamespace(name="USB Keyboard", capabilities=lambda: {1: list(range(1, 100))}, close=lambda: None)
    mouse = SimpleNamespace(name="USB Mouse", capabilities=lambda: {1: [272, 273]}, close=lambda: None)
    devices = {"/dev/input/event0": mouse}
    notifications: list[bool] = []
    scans = 0
    adapter = EvdevGamepadAdapter(discovery_interval=0, keyboard_added=lambda: notifications.append(True))

    def list_devices() -> list[str]:
        nonlocal scans
        scans += 1
        if scans == 2:
            devices["/dev/input/event1"] = keyboard
        if scans == 5:
            adapter._stop.set()
        return list(devices)

    evdev = SimpleNamespace(list_devices=list_devices, InputDevice=lambda path: devices[path])
    adapter._discover_loop(evdev)

    assert EvdevGamepadAdapter._is_keyboard(keyboard) is True
    assert EvdevGamepadAdapter._is_keyboard(mouse) is False
    assert notifications == [True]
