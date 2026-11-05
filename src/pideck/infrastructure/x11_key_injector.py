"""Synthesize key presses for the focused X11 window through the XTest extension.

PiDeck images already ship ``libX11`` and ``libXtst`` (pulled in by PulseAudio),
so this adapter only uses ``ctypes`` and needs neither root, ``/dev/uinput``
nor additional packages. It is meant to be used from a single (the Qt) thread.
"""

import ctypes
import ctypes.util
import logging
import os

from pideck.application.ports.keyboard import VirtualKey

_LOGGER = logging.getLogger(__name__)

_KEYSYMS = {
    VirtualKey.UP: b"Up",
    VirtualKey.DOWN: b"Down",
    VirtualKey.LEFT: b"Left",
    VirtualKey.RIGHT: b"Right",
    VirtualKey.RETURN: b"Return",
    VirtualKey.ESCAPE: b"Escape",
    # Chromium-based clients treat XF86AudioPlay as the play/pause toggle.
    VirtualKey.MEDIA_PLAY: b"XF86AudioPlay",
    VirtualKey.MEDIA_PAUSE: b"XF86AudioPause",
    VirtualKey.MEDIA_PLAY_PAUSE: b"XF86AudioPlay",
    VirtualKey.MEDIA_STOP: b"XF86AudioStop",
    VirtualKey.MEDIA_REWIND: b"XF86AudioRewind",
    VirtualKey.MEDIA_FAST_FORWARD: b"XF86AudioForward",
}


class _XLibraries:
    """Typed ctypes bindings for the few Xlib/XTest functions that are needed."""

    def __init__(self) -> None:
        x11 = ctypes.CDLL(_library("X11", "libX11.so.6"))
        xtst = ctypes.CDLL(_library("Xtst", "libXtst.so.6"))
        self.open_display = x11.XOpenDisplay
        self.open_display.argtypes = [ctypes.c_char_p]
        self.open_display.restype = ctypes.c_void_p
        self.close_display = x11.XCloseDisplay
        self.close_display.argtypes = [ctypes.c_void_p]
        self.string_to_keysym = x11.XStringToKeysym
        self.string_to_keysym.argtypes = [ctypes.c_char_p]
        self.string_to_keysym.restype = ctypes.c_ulong
        self.keysym_to_keycode = x11.XKeysymToKeycode
        self.keysym_to_keycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.keysym_to_keycode.restype = ctypes.c_ubyte
        self.flush = x11.XFlush
        self.flush.argtypes = [ctypes.c_void_p]
        self.query_extension = xtst.XTestQueryExtension
        self.query_extension.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int)] * 4
        self.query_extension.restype = ctypes.c_int
        self.fake_key_event = xtst.XTestFakeKeyEvent
        self.fake_key_event.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.fake_key_event.restype = ctypes.c_int


def _library(name: str, soname: str) -> str:
    """Prefer the stable soname and fall back to the linker search."""
    try:
        ctypes.CDLL(soname)
        return soname
    except OSError:
        return ctypes.util.find_library(name) or soname


class X11KeyInjector:
    """Send complete key presses to the window that currently has X11 focus."""

    def __init__(self, libraries: _XLibraries | None = None) -> None:
        """Defer loading libraries and connecting until the first key press."""
        self._libraries = libraries
        self._display: int | None = None
        self._unavailable = False
        self._keycodes: dict[VirtualKey, int] = {}

    def tap(self, key: VirtualKey) -> None:
        """Press and release one key; unavailable keys or displays are logged."""
        display = self._connect()
        if display is None or self._libraries is None:
            return
        keycode = self._keycode(display, key)
        if not keycode:
            return
        libraries = self._libraries
        if not (
            libraries.fake_key_event(display, keycode, 1, 0)
            and libraries.fake_key_event(display, keycode, 0, 0)
        ):
            _LOGGER.warning("XTest rejected key %s", key.value)
        libraries.flush(display)

    def close(self) -> None:
        """Close the display connection if it was opened."""
        if self._display is not None and self._libraries is not None:
            self._libraries.close_display(self._display)
        self._display = None

    def _connect(self) -> int | None:
        """Open the X display once and verify that XTest is available."""
        if self._display is not None or self._unavailable:
            return self._display
        if not os.environ.get("DISPLAY"):
            return self._disable("DISPLAY is not set")
        try:
            if self._libraries is None:
                self._libraries = _XLibraries()
        except (OSError, AttributeError) as error:
            return self._disable(f"X11/XTest libraries are unavailable: {error}")
        display = self._libraries.open_display(None)
        if not display:
            return self._disable("Unable to open the X display")
        values = [ctypes.c_int() for _ in range(4)]
        if not self._libraries.query_extension(display, *(ctypes.byref(value) for value in values)):
            self._libraries.close_display(display)
            return self._disable("The X server does not support XTest")
        self._display = display
        return display

    def _keycode(self, display: int, key: VirtualKey) -> int:
        """Resolve and cache the keycode for a virtual key in the active keymap."""
        if key not in self._keycodes:
            assert self._libraries is not None
            keysym = self._libraries.string_to_keysym(_KEYSYMS[key])
            keycode = self._libraries.keysym_to_keycode(display, keysym) if keysym else 0
            if not keycode:
                _LOGGER.warning("Key %s is not mapped in the X keyboard layout", key.value)
            self._keycodes[key] = keycode
        return self._keycodes[key]

    def _disable(self, reason: str) -> None:
        """Stop retrying after a permanent failure."""
        _LOGGER.warning("Remote key forwarding unavailable: %s", reason)
        self._unavailable = True
        return None
