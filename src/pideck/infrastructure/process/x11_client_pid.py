"""Resolve an X11 window's host PID using the server's XRes extension."""

import argparse
import ctypes
import logging


_LOGGER = logging.getLogger(__name__)


class _ClientIdSpec(ctypes.Structure):
    _fields_ = [("client", ctypes.c_ulong), ("mask", ctypes.c_uint)]


class _ClientIdValue(ctypes.Structure):
    _fields_ = [("spec", _ClientIdSpec), ("length", ctypes.c_long), ("value", ctypes.c_void_p)]


def client_pid(window_id: int) -> int | None:
    """Return the host PID reported by XRes, or None if it is unavailable."""
    x11 = ctypes.CDLL("libX11.so.6")
    xres = ctypes.CDLL("libXRes.so.1")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xres.XResQueryVersion.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    xres.XResQueryVersion.restype = ctypes.c_int
    xres.XResQueryClientIds.argtypes = [
        ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(_ClientIdSpec),
        ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.POINTER(_ClientIdValue)),
    ]
    xres.XResQueryClientIds.restype = ctypes.c_int
    xres.XResGetClientPid.argtypes = [ctypes.POINTER(_ClientIdValue)]
    xres.XResGetClientPid.restype = ctypes.c_int
    xres.XResClientIdsDestroy.argtypes = [ctypes.c_long, ctypes.POINTER(_ClientIdValue)]
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot open the X11 display")
    values = ctypes.POINTER(_ClientIdValue)()
    count = ctypes.c_long()
    try:
        major, minor = ctypes.c_int(), ctypes.c_int()
        if not xres.XResQueryVersion(display, ctypes.byref(major), ctypes.byref(minor)):
            raise RuntimeError("XRes extension is unavailable")
        if (major.value, minor.value) < (1, 2):
            raise RuntimeError("XRes version 1.2 or newer is required")
        spec = _ClientIdSpec(window_id, 2)
        if xres.XResQueryClientIds(display, 1, ctypes.byref(spec), ctypes.byref(count), ctypes.byref(values)) != 0:
            raise RuntimeError("XRes client PID query failed")
        for index in range(count.value):
            pid = xres.XResGetClientPid(ctypes.byref(values[index]))
            if pid > 0:
                return pid
        return None
    finally:
        if values:
            xres.XResClientIdsDestroy(count, values)
        x11.XCloseDisplay(display)


def main() -> int:
    """Print a window's host PID for the isolated visibility detector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window_id", type=lambda value: int(value, 0))
    args = parser.parse_args()
    try:
        pid = client_pid(args.window_id)
    except (OSError, RuntimeError) as error:
        _LOGGER.error("Unable to identify X11 client: %s", error)
        return 1
    if pid is None:
        return 1
    print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
