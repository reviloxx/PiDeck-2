"""Global X11 key grabs for appliance hotkeys while another client has focus.

PiDeck images already ship ``libX11``. This adapter uses ``ctypes`` so Super
(Windows) can open the session overlay even when a launched application owns
keyboard focus. It is safe to construct when DISPLAY is unset; ``start`` then
becomes a no-op.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import select
import threading
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

# Ignore Caps Lock / Num Lock so the grab still fires with those toggles on.
_IGNORE_MASKS = (0, 2, 16, 18)  # 0, LockMask, Mod2Mask, LockMask|Mod2Mask
_KEY_PRESS = 2
_GRAB_MODE_ASYNC = 1


class _XLibraries:
    """Typed ctypes bindings for XGrabKey and a polled event read."""

    def __init__(self) -> None:
        x11 = ctypes.CDLL(_library("X11", "libX11.so.6"))
        self.open_display = x11.XOpenDisplay
        self.open_display.argtypes = [ctypes.c_char_p]
        self.open_display.restype = ctypes.c_void_p
        self.close_display = x11.XCloseDisplay
        self.close_display.argtypes = [ctypes.c_void_p]
        self.default_root_window = x11.XDefaultRootWindow
        self.default_root_window.argtypes = [ctypes.c_void_p]
        self.default_root_window.restype = ctypes.c_ulong
        self.string_to_keysym = x11.XStringToKeysym
        self.string_to_keysym.argtypes = [ctypes.c_char_p]
        self.string_to_keysym.restype = ctypes.c_ulong
        self.keysym_to_keycode = x11.XKeysymToKeycode
        self.keysym_to_keycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.keysym_to_keycode.restype = ctypes.c_ubyte
        self.grab_key = x11.XGrabKey
        self.grab_key.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        self.grab_key.restype = ctypes.c_int
        self.ungrab_key = x11.XUngrabKey
        self.ungrab_key.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        self.ungrab_key.restype = ctypes.c_int
        self.next_event = x11.XNextEvent
        self.next_event.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.next_event.restype = ctypes.c_int
        self.flush = x11.XFlush
        self.flush.argtypes = [ctypes.c_void_p]
        self.pending = x11.XPending
        self.pending.argtypes = [ctypes.c_void_p]
        self.pending.restype = ctypes.c_int
        self.connection_number = x11.XConnectionNumber
        self.connection_number.argtypes = [ctypes.c_void_p]
        self.connection_number.restype = ctypes.c_int


def _library(name: str, soname: str) -> str:
    """Prefer the stable soname and fall back to the linker search."""
    try:
        ctypes.CDLL(soname)
        return soname
    except OSError:
        return ctypes.util.find_library(name) or soname


class _XEvent(ctypes.Structure):
    """Enough of an XEvent to read the type of a KeyPress."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("pad", ctypes.c_long * 24),
    ]


class _XKeyEvent(ctypes.Structure):
    """XKeyEvent layout matching the Xlib KeyPress payload."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class X11HotkeyListener:
    """Grab Super_L / Super_R globally and notify when either is pressed."""

    def __init__(
        self,
        on_hotkey: Callable[[], None],
        *,
        libraries: _XLibraries | None = None,
    ) -> None:
        """Create a listener that calls ``on_hotkey`` on the reader thread."""
        self._on_hotkey = on_hotkey
        self._libraries = libraries
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._display: int | None = None
        self._root = 0
        self._keycodes: tuple[int, ...] = ()

    def start(self) -> None:
        """Begin grabbing; no-op when DISPLAY or X11 is unavailable."""
        if self._thread is not None:
            raise RuntimeError("Hotkey listener has already been started")
        if not os.environ.get("DISPLAY"):
            _LOGGER.info("Global Super hotkey unavailable: DISPLAY is not set")
            return
        try:
            if self._libraries is None:
                self._libraries = _XLibraries()
        except (OSError, AttributeError) as error:
            _LOGGER.warning("Global Super hotkey unavailable: %s", error)
            return
        display = self._libraries.open_display(None)
        if not display:
            _LOGGER.warning("Global Super hotkey unavailable: unable to open the X display")
            return
        self._display = display
        self._root = self._libraries.default_root_window(display)
        keycodes: list[int] = []
        for name in (b"Super_L", b"Super_R"):
            keysym = self._libraries.string_to_keysym(name)
            keycode = self._libraries.keysym_to_keycode(display, keysym) if keysym else 0
            if keycode and keycode not in keycodes:
                keycodes.append(keycode)
        if not keycodes:
            self._libraries.close_display(display)
            self._display = None
            _LOGGER.warning("Global Super hotkey unavailable: Super keys are not mapped")
            return
        self._keycodes = tuple(keycodes)
        for keycode in self._keycodes:
            for modifiers in _IGNORE_MASKS:
                self._libraries.grab_key(
                    display, keycode, modifiers, self._root, True,
                    _GRAB_MODE_ASYNC, _GRAB_MODE_ASYNC,
                )
        self._libraries.flush(display)
        self._thread = threading.Thread(target=self._run, name="pideck-hotkey", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Release grabs and stop the reader thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                _LOGGER.error("Hotkey listener did not stop")
            self._thread = None
        libraries = self._libraries
        display = self._display
        if libraries is not None and display is not None:
            for keycode in self._keycodes:
                for modifiers in _IGNORE_MASKS:
                    libraries.ungrab_key(display, keycode, modifiers, self._root)
            libraries.flush(display)
            libraries.close_display(display)
            self._display = None

    def _run(self) -> None:
        """Deliver Super presses until stop is requested."""
        libraries = self._libraries
        display = self._display
        if libraries is None or display is None:
            return
        event = _XEvent()
        keycodes = set(self._keycodes)
        connection = libraries.connection_number(display)
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([connection], [], [], 0.25)
            except (OSError, ValueError):
                break
            if self._stop.is_set():
                break
            if not readable:
                continue
            while libraries.pending(display):
                libraries.next_event(display, ctypes.byref(event))
                if event.type != _KEY_PRESS:
                    continue
                key_event = ctypes.cast(ctypes.byref(event), ctypes.POINTER(_XKeyEvent)).contents
                if key_event.keycode in keycodes:
                    try:
                        self._on_hotkey()
                    except Exception:
                        _LOGGER.exception("Global Super hotkey handler failed")
