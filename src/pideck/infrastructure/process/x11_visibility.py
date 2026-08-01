"""X11 application-window visibility detection for external launches."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

from pideck.application.ports.process import ProcessHandle
from pideck.application.ports.visibility import (
    VisibilityCallback,
    VisibilityTimeoutCallback,
)

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
            args=(watch, on_visible, on_timeout),
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
    ) -> None:
        """Poll X11 windows until readiness, cancellation, or timeout."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline and not watch.cancel.is_set():
            if self._has_visible_window(watch.handle):
                self._finish(watch.handle)
                on_visible(watch.handle)
                return
            watch.cancel.wait(self._poll_interval)
        if not watch.cancel.is_set():
            self._finish(watch.handle)
            on_timeout(watch.handle)

    def _finish(self, handle: ProcessHandle) -> None:
        """Remove a completed watch."""
        with self._lock:
            self._watches.pop(handle.token, None)

    @staticmethod
    def _has_visible_window(handle: ProcessHandle) -> bool:
        """Return whether wmctrl reports a non-zero window for this process group."""
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
            return False
        if result.returncode != 0:
            return False
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
            if pid in process_ids and width > 1 and height > 1:
                return True
        return False


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
