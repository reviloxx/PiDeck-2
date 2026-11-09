"""Hide and show the X11 pointer for the whole display, including other clients.

Qt's override cursor only affects PiDeck windows. While YouTube or Kodi has
focus, the appliance still needs a blank pointer when the remote or gamepad is
used. XFixesHideCursor applies display-wide without grabbing input.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os

_LOGGER = logging.getLogger(__name__)
# Button1Mask through Button5Mask from X.h (bits 8–12).
_BUTTON_MASK = 0x1F00


class _XLibraries:
    """Typed ctypes bindings for XFixes cursor hide/show and pointer query."""

    def __init__(self) -> None:
        x11 = ctypes.CDLL(_library("X11", "libX11.so.6"))
        xfixes = ctypes.CDLL(_library("Xfixes", "libXfixes.so.3"))
        self.open_display = x11.XOpenDisplay
        self.open_display.argtypes = [ctypes.c_char_p]
        self.open_display.restype = ctypes.c_void_p
        self.close_display = x11.XCloseDisplay
        self.close_display.argtypes = [ctypes.c_void_p]
        self.default_root_window = x11.XDefaultRootWindow
        self.default_root_window.argtypes = [ctypes.c_void_p]
        self.default_root_window.restype = ctypes.c_ulong
        self.flush = x11.XFlush
        self.flush.argtypes = [ctypes.c_void_p]
        self.query_pointer = x11.XQueryPointer
        self.query_pointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self.query_pointer.restype = ctypes.c_int
        self.query_extension = xfixes.XFixesQueryExtension
        self.query_extension.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        self.query_extension.restype = ctypes.c_int
        self.hide_cursor = xfixes.XFixesHideCursor
        self.hide_cursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.show_cursor = xfixes.XFixesShowCursor
        self.show_cursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]


def _library(name: str, soname: str) -> str:
    """Prefer the stable soname and fall back to the linker search."""
    try:
        ctypes.CDLL(soname)
        return soname
    except OSError:
        return ctypes.util.find_library(name) or soname


class X11CursorController:
    """Toggle the display-wide X11 pointer with XFixes."""

    def __init__(self, libraries: _XLibraries | None = None) -> None:
        """Defer connecting until the first hide or show request."""
        self._libraries = libraries
        self._display: int | None = None
        self._root = 0
        self._unavailable = False
        self._hidden = False

    def hide(self) -> None:
        """Hide the pointer on every client until ``show`` is called."""
        display = self._connect()
        if display is None or self._libraries is None or self._hidden:
            return
        self._libraries.hide_cursor(display, self._root)
        self._libraries.flush(display)
        self._hidden = True

    def show(self) -> None:
        """Restore the normal pointer."""
        display = self._connect()
        if display is None or self._libraries is None or not self._hidden:
            return
        self._libraries.show_cursor(display, self._root)
        self._libraries.flush(display)
        self._hidden = False

    def buttons_down(self) -> bool:
        """Return whether any mouse button is pressed on the root window."""
        display = self._connect()
        if display is None or self._libraries is None:
            return False
        root = ctypes.c_ulong()
        child = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        win_x = ctypes.c_int()
        win_y = ctypes.c_int()
        mask = ctypes.c_uint()
        if not self._libraries.query_pointer(
            display,
            self._root,
            ctypes.byref(root),
            ctypes.byref(child),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(win_x),
            ctypes.byref(win_y),
            ctypes.byref(mask),
        ):
            return False
        return (mask.value & _BUTTON_MASK) != 0

    def close(self) -> None:
        """Show the pointer again and close the display connection."""
        try:
            self.show()
        finally:
            if self._display is not None and self._libraries is not None:
                self._libraries.close_display(self._display)
            self._display = None

    def _connect(self) -> int | None:
        """Open the X display once and verify that XFixes is available."""
        if self._display is not None or self._unavailable:
            return self._display
        if not os.environ.get("DISPLAY"):
            return self._disable("DISPLAY is not set")
        try:
            if self._libraries is None:
                self._libraries = _XLibraries()
        except (OSError, AttributeError) as error:
            return self._disable(f"X11/XFixes libraries are unavailable: {error}")
        display = self._libraries.open_display(None)
        if not display:
            return self._disable("Unable to open the X display")
        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        if not self._libraries.query_extension(display, ctypes.byref(event_base), ctypes.byref(error_base)):
            self._libraries.close_display(display)
            return self._disable("The X server does not support XFixes")
        self._display = display
        self._root = self._libraries.default_root_window(display)
        return display

    def _disable(self, reason: str) -> None:
        """Stop retrying after a permanent failure."""
        _LOGGER.warning("Global cursor control unavailable: %s", reason)
        self._unavailable = True
        return None
