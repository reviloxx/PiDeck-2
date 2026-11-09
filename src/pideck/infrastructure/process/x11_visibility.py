"""X11 application-window visibility detection for external launches."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from pideck.application.ports.process import ProcessHandle
from pideck.application.ports.visibility import (
    VisibilityCallback,
    VisibilityTimeoutCallback,
)
from pideck.domain.configuration import WindowMode

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _VisibilityWatch:
    """Cancellation state for one process visibility watch."""

    handle: ProcessHandle
    cancel: threading.Event


class X11WindowVisibilityDetector:
    """Detect visible X11 windows belonging to a managed process group."""

    def __init__(self, timeout: float = 30.0, poll_interval: float = 0.1) -> None:
        """Create a detector with bounded polling for application readiness."""
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._lock = threading.RLock()
        self._watches: dict[str, _VisibilityWatch] = {}
        self._closed = False

    def wait_until_visible(
        self,
        handle: ProcessHandle,
        on_visible: VisibilityCallback,
        on_timeout: VisibilityTimeoutCallback,
        window_mode: WindowMode | None = None,
    ) -> None:
        """Monitor one process group in a daemon thread until a window appears."""
        if not os.environ.get("DISPLAY"):
            on_timeout(handle)
            return
        watch = _VisibilityWatch(handle=handle, cancel=threading.Event())
        with self._lock:
            if self._closed:
                on_timeout(handle)
                return
            self._watches[handle.token] = watch
        threading.Thread(
            target=self._watch,
            args=(watch, on_visible, on_timeout, window_mode),
            name=f"pideck-visibility-{handle.token}",
            daemon=True,
        ).start()

    def cancel(self, handle: ProcessHandle) -> None:
        """Stop monitoring one process group."""
        with self._lock:
            watch = self._watches.pop(handle.token, None)
        if watch is not None:
            watch.cancel.set()

    def close(self) -> None:
        """Stop all active visibility watches."""
        with self._lock:
            self._closed = True
            watches = list(self._watches.values())
            self._watches.clear()
        for watch in watches:
            watch.cancel.set()

    def _watch(
        self,
        watch: _VisibilityWatch,
        on_visible: VisibilityCallback,
        on_timeout: VisibilityTimeoutCallback,
        window_mode: WindowMode | None,
    ) -> None:
        """Poll X11 windows until readiness, cancellation, or timeout."""
        managed = window_mode in (WindowMode.MAXIMIZED, WindowMode.FULLSCREEN)
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline and not watch.cancel.is_set():
            window_id = self._find_visible_window(watch.handle, main_only=managed)
            if window_id:
                self._apply_window_mode(window_id, window_mode, watch.cancel)
                self._finish(watch.handle)
                on_visible(watch.handle)
                return
            watch.cancel.wait(self._poll_interval)
        if watch.cancel.is_set():
            return
        # Last chance: browsers/Flatpak often expose a usable window that failed
        # the main-window filter. Still apply maximize/fullscreen before giving up.
        window_id = None
        if managed:
            window_id = self._find_visible_window(watch.handle, main_only=True)
            if window_id is None:
                window_id = self._find_visible_window(watch.handle, main_only=False)
            if window_id is not None:
                self._apply_window_mode(window_id, window_mode, watch.cancel)
                self._finish(watch.handle)
                on_visible(watch.handle)
                return
        self._finish(watch.handle)
        on_timeout(watch.handle)

    @staticmethod
    def _apply_window_mode(
        window_id: str,
        window_mode: WindowMode | None,
        cancel: threading.Event,
    ) -> None:
        """Apply the configured presentation mode to one mapped window.

        Chromium-based browsers often map at a restored size and only accept
        maximize a moment later, so managed modes are applied twice.
        """
        if window_mode is WindowMode.MAXIMIZED:
            _maximize_window(window_id, cancel)
            if not cancel.wait(1.0):
                _maximize_window(window_id, cancel)
        elif window_mode is WindowMode.FULLSCREEN:
            _fullscreen_window(window_id, cancel)
            if not cancel.wait(1.0):
                _fullscreen_window(window_id, cancel)

    def _finish(self, handle: ProcessHandle) -> None:
        """Remove a completed watch."""
        with self._lock:
            self._watches.pop(handle.token, None)

    @staticmethod
    def _has_visible_window(handle: ProcessHandle) -> bool:
        """Return whether wmctrl reports a non-zero window for this process group."""
        return X11WindowVisibilityDetector._find_visible_window(handle) is not None

    @staticmethod
    def _find_visible_window(handle: ProcessHandle, *, main_only: bool = False) -> str | None:
        """Find a visible matching process window, optionally excluding dialogs."""
        process_ids = _process_group_ids(handle.process_group_id)
        if not process_ids:
            process_ids = {handle.pid}
        try:
            result = subprocess.run(
                ["wmctrl", "-lGpx"],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            _LOGGER.debug("Unable to inspect X11 windows: %s", error)
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=8)
            if len(fields) < 7:
                continue
            try:
                pid = int(fields[2])
                width = int(fields[5])
                height = int(fields[6])
            except ValueError:
                continue
            if width <= 1 or height <= 1:
                continue
            if pid not in process_ids and _window_client_pid(fields[0]) not in process_ids:
                continue
            if main_only and not _is_main_window(fields[0]):
                continue
            return fields[0]
        return None


def _window_properties(window_id: str) -> str | None:
    """Read EWMH and transient properties without raising on vanished windows."""
    try:
        result = subprocess.run(
            ["xprop", "-id", window_id, "WM_TRANSIENT_FOR", "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE"],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.warning("Unable to inspect window %s: %s", window_id, error)
        return None
    if result.returncode:
        _LOGGER.debug("Unable to inspect window %s: %s", window_id, result.stderr.strip())
        return None
    return result.stdout


def _is_main_window(window_id: str) -> bool:
    """Exclude transient and non-normal EWMH windows from window management."""
    properties = _window_properties(window_id)
    if properties is None:
        return False
    for line in properties.splitlines():
        if line.startswith("WM_TRANSIENT_FOR") and "window id #" in line:
            return False
        if line.startswith("_NET_WM_WINDOW_TYPE") and "=" in line:
            if "_NET_WM_WINDOW_TYPE_NORMAL" not in line:
                return False
    return True


def _maximize_window(window_id: str, cancel: threading.Event) -> None:
    """Ask the window manager to maximize a normal, non-fullscreen window."""
    properties = _window_properties(window_id)
    if properties is None:
        _LOGGER.warning("Cannot maximize window %s: properties unavailable", window_id)
        return
    if "_NET_WM_STATE_FULLSCREEN" in properties:
        return
    try:
        result = subprocess.run(
            ["wmctrl", "-ir", window_id, "-b", "add,maximized_vert,maximized_horz"],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.warning("Unable to maximize window %s: %s", window_id, error)
        return
    if result.returncode:
        _LOGGER.warning("Unable to maximize window %s: %s", window_id, result.stderr.strip())
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not cancel.is_set():
        properties = _window_properties(window_id)
        if properties is None:
            break
        if "_NET_WM_STATE_MAXIMIZED_VERT" in properties and "_NET_WM_STATE_MAXIMIZED_HORZ" in properties:
            return
        cancel.wait(0.1)
    if not cancel.is_set():
        _LOGGER.warning("Window %s did not enter the maximized state", window_id)


def _fullscreen_window(window_id: str, cancel: threading.Event) -> None:
    """Ask the window manager to present a main window fullscreen."""
    properties = _window_properties(window_id)
    if properties is None:
        _LOGGER.warning("Cannot fullscreen window %s: properties unavailable", window_id)
        return
    if "_NET_WM_STATE_FULLSCREEN" in properties:
        return
    try:
        result = subprocess.run(
            ["wmctrl", "-ir", window_id, "-b", "add,fullscreen"],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.warning("Unable to fullscreen window %s: %s", window_id, error)
        return
    if result.returncode:
        _LOGGER.warning("Unable to fullscreen window %s: %s", window_id, result.stderr.strip())
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not cancel.is_set():
        properties = _window_properties(window_id)
        if properties is None:
            break
        if "_NET_WM_STATE_FULLSCREEN" in properties:
            return
        cancel.wait(0.1)
    if not cancel.is_set():
        _LOGGER.warning("Window %s did not enter the fullscreen state", window_id)


def _window_client_pid(window_id: str) -> int | None:
    """Query XRes in a subprocess so X11 protocol errors cannot exit PiDeck."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pideck.infrastructure.process.x11_client_pid", window_id],
            check=False, capture_output=True, text=True, timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.debug("Unable to inspect X11 client PID: %s", error)
        return None
    if result.returncode != 0:
        _LOGGER.debug("Unable to inspect X11 client PID: %s", result.stderr.strip())
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        _LOGGER.debug("X11 client PID helper returned invalid output")
        return None


def _process_group_ids(process_group_id: int) -> set[int]:
    """Read process groups from procfs, including Flatpak descendants."""
    process_ids: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
            fields = stat.rsplit(") ", 1)[1].split()
            process_group = int(fields[2])
        except (OSError, IndexError, ValueError):
            continue
        if process_group == process_group_id:
            process_ids.add(int(entry.name))
    return process_ids
