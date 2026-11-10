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
    wired = SimpleNamespace(info=SimpleNamespace(bustype=3), uniq="")
    assert adapter._device_is_live(wired, Path("/dev/input/event1"), frozenset()) is True


def test_wireless_usb_keyboard_without_power_supply_is_offline(tmp_path: Path) -> None:
    """Unifying receivers keep a uniq even before hidpp online sysfs appears."""
    event = tmp_path / "event2"
    hid = tmp_path / "sys" / "hid"
    hid.mkdir(parents=True)
    event.mkdir()
    (event / "device").symlink_to(hid)
    adapter = EvdevGamepadAdapter(sysfs_input_root=tmp_path)
    wireless = SimpleNamespace(info=SimpleNamespace(bustype=3), uniq="ba-fd-59-7c")
    assert adapter._device_is_live(wireless, Path("/dev/input/event2"), frozenset()) is False


def test_wireless_keyboard_requires_power_supply_online(tmp_path: Path) -> None:
    """Unifying-style keyboards expose online=0 while powered off."""
    event = tmp_path / "event2"
    hid = tmp_path / "sys" / "hid"
    supply = hid / "power_supply" / "hidpp_battery_0"
    supply.mkdir(parents=True)
    event.mkdir()
    (event / "device").symlink_to(hid)
    adapter = EvdevGamepadAdapter(sysfs_input_root=tmp_path)
    wireless = SimpleNamespace(info=SimpleNamespace(bustype=3), uniq="ba-fd-59-7c")

    (supply / "online").write_text("0\n", encoding="utf-8")
    assert adapter._device_is_live(wireless, Path("/dev/input/event2"), frozenset()) is False

    (supply / "online").write_text("1\n", encoding="utf-8")
    assert adapter._device_is_live(wireless, Path("/dev/input/event2"), frozenset()) is True


def test_bluetooth_device_live_requires_bluez_connected(tmp_path: Path) -> None:
    """Bluetooth pads/keyboards are live only while BlueZ reports Connected."""
    adapter = EvdevGamepadAdapter(sysfs_input_root=tmp_path)
    pad = SimpleNamespace(
        name="Pro Controller",
        info=SimpleNamespace(bustype=5),
        uniq="98:b6:e9:b2:4f:2c",
        capabilities=lambda: {1: [304, 305], 3: [16, 17]},
    )
    path = Path("/dev/input/pideck-bt-pad")
    assert adapter._device_is_live(pad, path, frozenset()) is False
    assert adapter._device_is_live(pad, path, frozenset({"98:B6:E9:B2:4F:2C"})) is True


def test_reader_stop_suppresses_reopen_until_live_again(tmp_path: Path) -> None:
    """Power-off clears the icon; reconnect clears suppress when the pad is live."""
    import threading

    path = Path("/dev/input/pideck-test-pad")
    presence: list[int] = []
    fail_read = True
    hold = threading.Event()
    bt_connected: set[str] = set()

    class FakeDevice:
        name = "Pro Controller"
        info = SimpleNamespace(bustype=5)
        uniq = "98:B6:E9:B2:4F:2C"

        def __init__(self) -> None:
            self._reading = False

        def capabilities(self) -> dict[int, list[int]]:
            return {1: [304, 305], 3: [16, 17]}

        def grab(self) -> None:
            return None

        def ungrab(self) -> None:
            return None

        def read_loop(self):  # type: ignore[no-untyped-def]
            self._reading = True
            hold.wait(2)
            if fail_read:
                raise OSError("device gone")
            return
            yield  # pragma: no cover

        def close(self) -> None:
            # Classification probes also call close(); only wake an active reader.
            if self._reading:
                hold.set()

    adapter = EvdevGamepadAdapter(
        discovery_interval=0,
        presence_changed=lambda: presence.append(adapter.connected_gamepad_count()),
        sysfs_input_root=tmp_path,
    )
    adapter._bluetooth_connected_addresses = lambda: frozenset(bt_connected)  # type: ignore[method-assign]
    evdev = SimpleNamespace(InputDevice=lambda _path: FakeDevice())
    bt_connected.add("98:B6:E9:B2:4F:2C")
    adapter._open_device(evdev, path)
    assert adapter.connected_gamepad_count() == 1
    assert presence == [1]

    # Simulate the reader dying (Bluetooth power-off).
    hold.set()
    for _ in range(50):
        if adapter.connected_gamepad_count() == 0:
            break
        threading.Event().wait(0.02)
    assert adapter.connected_gamepad_count() == 0
    assert presence[-1] == 0
    assert path in adapter._suppress_until_absent
    bt_connected.clear()

    # Stale node still listed must not reopen while BlueZ says disconnected.
    scans = 0

    def list_devices() -> list[str]:
        nonlocal scans
        scans += 1
        if scans >= 2:
            adapter._stop.set()
        return [str(path)]

    adapter._discover_loop(
        SimpleNamespace(list_devices=list_devices, InputDevice=lambda _p: FakeDevice())
    )
    assert adapter.connected_gamepad_count() == 0
    assert path in adapter._suppress_until_absent

    # Power-on: BlueZ Connected again clears suppress and reopens the same path.
    fail_read = False
    hold = threading.Event()
    bt_connected.add("98:B6:E9:B2:4F:2C")
    scans = 0

    def list_devices_live() -> list[str]:
        nonlocal scans
        scans += 1
        if scans >= 2:
            adapter._stop.set()
        return [str(path)]

    adapter._stop.clear()
    adapter._discover_loop(
        SimpleNamespace(list_devices=list_devices_live, InputDevice=lambda _p: FakeDevice())
    )
    assert adapter.connected_gamepad_count() == 1
    assert path not in adapter._suppress_until_absent
    adapter.close()
