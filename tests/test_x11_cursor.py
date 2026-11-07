"""Tests for display-wide X11 cursor hide/show."""

import pytest

from pideck.infrastructure.x11_cursor import X11CursorController


class FakeLibraries:
    """Stand-in for the ctypes Xlib/XFixes bindings."""

    def __init__(self, *, display: int = 7, xfixes: bool = True) -> None:
        self.display = display
        self.xfixes = xfixes
        self.hidden: list[int] = []
        self.shown: list[int] = []
        self.closed: list[int] = []
        self.flushes = 0

    def open_display(self, name: bytes | None) -> int:
        return self.display

    def close_display(self, display: int) -> None:
        self.closed.append(display)

    def default_root_window(self, display: int) -> int:
        return 99

    def query_extension(self, display: int, event_base: object, error_base: object) -> int:
        return int(self.xfixes)

    def hide_cursor(self, display: int, root: int) -> None:
        self.hidden.append(root)

    def show_cursor(self, display: int, root: int) -> None:
        self.shown.append(root)

    def flush(self, display: int) -> None:
        self.flushes += 1


def test_cursor_controller_hides_and_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide/show call XFixes once each and flush the display."""
    monkeypatch.setenv("DISPLAY", ":0")
    libraries = FakeLibraries()
    cursor = X11CursorController(libraries)  # type: ignore[arg-type]
    cursor.hide()
    cursor.hide()
    cursor.show()
    cursor.show()
    assert libraries.hidden == [99]
    assert libraries.shown == [99]
    assert libraries.flushes == 2
    cursor.close()
    assert libraries.closed == [7]


def test_cursor_controller_without_display_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing DISPLAY skips library loading."""
    monkeypatch.delenv("DISPLAY", raising=False)
    libraries = FakeLibraries()
    cursor = X11CursorController(libraries)  # type: ignore[arg-type]
    cursor.hide()
    cursor.show()
    cursor.close()
    assert libraries.hidden == [] and libraries.shown == [] and libraries.closed == []
