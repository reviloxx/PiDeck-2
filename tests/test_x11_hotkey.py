"""Tests for the global Super key grab helper."""

import pytest

from pideck.infrastructure.x11_hotkey import X11HotkeyListener


class FakeLibraries:
    """Stand-in for the ctypes Xlib grab bindings."""

    def __init__(self, *, display: int = 7) -> None:
        self.display = display
        self.grabs: list[tuple[int, int]] = []
        self.ungrabs: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.flushes = 0

    def open_display(self, name: bytes | None) -> int:
        return self.display

    def close_display(self, display: int) -> None:
        self.closed.append(display)

    def default_root_window(self, display: int) -> int:
        return 42

    def string_to_keysym(self, name: bytes) -> int:
        return {b"Super_L": 0xFFEB, b"Super_R": 0xFFEC}.get(name, 0)

    def keysym_to_keycode(self, display: int, keysym: int) -> int:
        return {0xFFEB: 133, 0xFFEC: 134}.get(keysym, 0)

    def grab_key(
        self, display: int, keycode: int, modifiers: int, root: int,
        owner_events: int, pointer_mode: int, keyboard_mode: int,
    ) -> int:
        self.grabs.append((keycode, modifiers))
        return 1

    def ungrab_key(self, display: int, keycode: int, modifiers: int, root: int) -> int:
        self.ungrabs.append((keycode, modifiers))
        return 1

    def flush(self, display: int) -> None:
        self.flushes += 1

    def pending(self, display: int) -> int:
        return 0

    def next_event(self, display: int, event: object) -> int:
        return 0

    def connection_number(self, display: int) -> int:
        return -1


def test_hotkey_listener_grabs_both_super_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Super_L and Super_R are grabbed for the usual lock masks."""
    monkeypatch.setenv("DISPLAY", ":0")
    presses: list[str] = []
    libraries = FakeLibraries()
    listener = X11HotkeyListener(lambda: presses.append("hotkey"), libraries=libraries)  # type: ignore[arg-type]
    listener.start()
    assert {(133, 0), (134, 0)}.issubset(set(libraries.grabs))
    listener.close()
    assert libraries.closed == [7]
    assert libraries.ungrabs


def test_hotkey_listener_without_display_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing DISPLAY skips library loading."""
    monkeypatch.delenv("DISPLAY", raising=False)
    libraries = FakeLibraries()
    listener = X11HotkeyListener(lambda: None, libraries=libraries)  # type: ignore[arg-type]
    listener.start()
    listener.close()
    assert libraries.grabs == [] and libraries.closed == []
