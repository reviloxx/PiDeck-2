"""Tests for normalized Linux gamepad input."""

from types import SimpleNamespace
from pathlib import Path

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.input_gamepad import EvdevGamepadAdapter


def test_gamepad_maps_dpad_and_buttons() -> None:
    """Standard Xbox-style evdev codes become normalized PiDeck actions."""
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=-1)) is InputAction.LEFT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=1)) is InputAction.RIGHT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=-1)) is InputAction.UP
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=1)) is InputAction.DOWN
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=1)) is InputAction.ACTIVATE
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=305, value=1)) is InputAction.BACK
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=315, value=1)) is InputAction.MENU
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=316, value=1)) is InputAction.MENU
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=0)) is None


def test_nintendo_layout_maps_a_to_activate_and_b_to_back() -> None:
    """Nintendo face labels put A on east (confirm) and B on south (back)."""
    assert (
        EvdevGamepadAdapter._map_event(
            SimpleNamespace(type=1, code=305, value=1), nintendo_layout=True,
        )
        is InputAction.ACTIVATE
    )
    assert (
        EvdevGamepadAdapter._map_event(
            SimpleNamespace(type=1, code=304, value=1), nintendo_layout=True,
        )
        is InputAction.BACK
    )
    assert (
        EvdevGamepadAdapter._map_event(
            SimpleNamespace(type=1, code=316, value=1), nintendo_layout=True,
        )
        is InputAction.MENU
    )


def test_nintendo_controller_detection() -> None:
    """Switch Pro and Joy-Con names select the Nintendo face-button layout."""
    assert EvdevGamepadAdapter._is_nintendo_controller("Nintendo Switch Pro Controller")
    assert EvdevGamepadAdapter._is_nintendo_controller("Nintendo Switch Left Joy-Con")
    assert EvdevGamepadAdapter._is_nintendo_controller("Pro Controller")
    assert EvdevGamepadAdapter._is_nintendo_controller("Joy-Con (R)")
    assert EvdevGamepadAdapter._is_nintendo_controller("Xbox Wireless Controller") is False
    assert EvdevGamepadAdapter._is_nintendo_controller("8BitDo Pro 2") is False


def test_gamepad_detection_skips_imu_nodes() -> None:
    """Motion sensors that share a controller name must not be treated as gamepads."""
    imu = SimpleNamespace(
        name="Pro Controller (IMU)",
        capabilities=lambda: {3: [0, 1, 2]},
    )
    assert EvdevGamepadAdapter._is_gamepad(imu) is False


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
    """By default PiDeck shares the pad so native-gamepad apps can still read it."""
    class FakeDevice:
        name = "Generic USB Controller"

        def __init__(self) -> None:
            self.grab_calls = 0
            self.ungrab_calls = 0
            self.closed = False

        def grab(self) -> None:
            self.grab_calls += 1

        def ungrab(self) -> None:
            self.ungrab_calls += 1

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


def test_exclusive_mode_grabs_and_releases_open_pads() -> None:
    """YouTube/Kodi sessions grab the pad so injected keys are not doubled natively."""
    import threading

    release = threading.Event()

    class FakeDevice:
        name = "Nintendo Switch Pro Controller"

        def __init__(self) -> None:
            self.grab_calls = 0
            self.ungrab_calls = 0

        def grab(self) -> None:
            self.grab_calls += 1

        def ungrab(self) -> None:
            self.ungrab_calls += 1

        def read_loop(self):  # type: ignore[no-untyped-def]
            release.wait(1)
            return
            yield  # pragma: no cover

        def close(self) -> None:
            release.set()

    device = FakeDevice()
    adapter = EvdevGamepadAdapter()
    evdev = SimpleNamespace(InputDevice=lambda path: device)
    adapter._open_device(evdev, Path("/dev/input/event0"))
    assert device.grab_calls == 0
    assert adapter.connected_gamepad_count() == 1

    adapter.set_exclusive(True)
    assert device.grab_calls == 1
    # Re-applying exclusive must retry grab so a prior failure can recover.
    adapter.set_exclusive(True)
    assert device.grab_calls == 2
    adapter.set_exclusive(False)
    assert device.ungrab_calls == 1
    adapter.close()


def test_opening_nintendo_gamepad_stores_nintendo_layout() -> None:
    """Detected Nintendo controllers keep the face-label layout for mapping."""
    import threading

    release = threading.Event()

    class FakeDevice:
        name = "Nintendo Switch Pro Controller"

        def grab(self) -> None:
            return None

        def ungrab(self) -> None:
            return None

        def read_loop(self):  # type: ignore[no-untyped-def]
            release.wait(1)
            return
            yield  # pragma: no cover — make this a generator

        def close(self) -> None:
            release.set()

    device = FakeDevice()
    adapter = EvdevGamepadAdapter()
    evdev = SimpleNamespace(InputDevice=lambda path: device)
    path = Path("/dev/input/event3")
    adapter._open_device(evdev, path)
    assert adapter._devices[path].nintendo_layout is True
    adapter.close()


def test_keyboard_hotplug_notifies_once_after_the_next_scan() -> None:
    """Keyboards present at startup are ignored; later ones trigger one callback."""
    keyboard = SimpleNamespace(
        name="USB Keyboard",
        info=SimpleNamespace(bustype=3),
        capabilities=lambda: {1: list(range(1, 100))},
        close=lambda: None,
    )
    mouse = SimpleNamespace(
        name="USB Mouse",
        info=SimpleNamespace(bustype=3),
        capabilities=lambda: {1: [272, 273]},
        close=lambda: None,
    )
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


def test_platform_i8042_keyboard_is_not_counted() -> None:
    """Motherboard AT/i8042 keyboards must not light the footer keyboard icon."""
    platform = SimpleNamespace(
        name="AT Raw Set 2 keyboard",
        info=SimpleNamespace(bustype=17),
        capabilities=lambda: {1: list(range(1, 100))},
    )
    assert EvdevGamepadAdapter._is_keyboard(platform) is False


def test_usb_keyboard_without_power_supply_is_online(tmp_path: Path) -> None:
    """Wired USB keyboards with no battery online attribute count as present."""
    event = tmp_path / "event1"
    device_dir = tmp_path / "sys" / "input1"
    device_dir.mkdir(parents=True)
    event.mkdir()
    (event / "device").symlink_to(device_dir)
    adapter = EvdevGamepadAdapter(sysfs_input_root=tmp_path)
    assert adapter._input_device_online(Path("/dev/input/event1")) is True


def test_wireless_keyboard_requires_power_supply_online(tmp_path: Path) -> None:
    """Unifying-style keyboards expose online=0 while powered off."""
    event = tmp_path / "event2"
    hid = tmp_path / "sys" / "hid"
    supply = hid / "power_supply" / "hidpp_battery_0"
    supply.mkdir(parents=True)
    event.mkdir()
    (event / "device").symlink_to(hid)
    adapter = EvdevGamepadAdapter(sysfs_input_root=tmp_path)

    (supply / "online").write_text("0\n", encoding="utf-8")
    assert adapter._input_device_online(Path("/dev/input/event2")) is False

    (supply / "online").write_text("1\n", encoding="utf-8")
    assert adapter._input_device_online(Path("/dev/input/event2")) is True


def test_reader_stop_suppresses_reopen_and_notifies_presence() -> None:
    """Power-off must clear the pad immediately and not re-open a lingering node."""
    import threading

    path = Path("/dev/input/event9")
    presence: list[int] = []
    open_calls = 0
    fail_read = True
    hold = threading.Event()

    class FakeDevice:
        name = "Xbox Wireless Controller"

        def __init__(self) -> None:
            nonlocal open_calls
            open_calls += 1

        def grab(self) -> None:
            return None

        def ungrab(self) -> None:
            return None

        def read_loop(self):  # type: ignore[no-untyped-def]
            hold.wait(1)
            if fail_read:
                raise OSError("device gone")
            return
            yield  # pragma: no cover

        def close(self) -> None:
            hold.set()

    adapter = EvdevGamepadAdapter(
        discovery_interval=0,
        presence_changed=lambda: presence.append(adapter.connected_gamepad_count()),
    )
    evdev = SimpleNamespace(InputDevice=lambda _path: FakeDevice())
    adapter._open_device(evdev, path)
    assert adapter.connected_gamepad_count() == 1
    assert presence == [1]

    # Simulate the reader dying (Bluetooth/USB power-off).
    hold.set()
    for _ in range(50):
        if adapter.connected_gamepad_count() == 0:
            break
        threading.Event().wait(0.02)
    assert adapter.connected_gamepad_count() == 0
    assert presence[-1] == 0
    assert path in adapter._suppress_until_absent

    # Stale node still listed must not reopen while suppressed.
    adapter._open_device(evdev, path)
    assert adapter.connected_gamepad_count() == 0
    assert open_calls == 1

    # Once the node leaves list_devices, suppress clears and a later open works.
    fail_read = False
    hold = threading.Event()
    with adapter._lock:
        adapter._suppress_until_absent.discard(path)
    adapter._open_device(evdev, path)
    assert adapter.connected_gamepad_count() == 1
    assert open_calls == 2
    adapter.close()
