"""Apply the release Openbox config so mouse clicks reach applications."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

_LOGGER = logging.getLogger(__name__)
_SESSION_DIR = Path(__file__).resolve().parent
_IMAGE_OPENBOX_RC = Path("/usr/share/pideck/openbox/rc.xml")


def select_openbox_config(*, directory: Path | None = None) -> Path:
    """Return the release Openbox rc, falling back to the image-installed copy."""
    release = (directory or _SESSION_DIR) / "openbox-rc.xml"
    if release.is_file():
        return release
    return _IMAGE_OPENBOX_RC


def apply_release_openbox(*, config: Path | None = None) -> None:
    """Restart Openbox with the release rc when a display is available.

    Failures are logged and never raised so a missing window manager cannot
    block the launcher. No-op when openbox is absent, DISPLAY is unset, or
    openbox is already running with the chosen config. Never SIGKILL openbox.
    """
    conf = config or select_openbox_config()
    if not conf.is_file():
        _LOGGER.warning("PiDeck openbox config missing at %s", conf)
        return
    if not os.environ.get("DISPLAY"):
        return
    binary = shutil.which("openbox")
    if binary is None:
        return
    if _openbox_already_uses(conf):
        _LOGGER.info("openbox already using %s", conf)
        return
    try:
        if not _stop_openbox():
            _LOGGER.warning("Leaving existing openbox running; new config not applied")
            return
        subprocess.Popen(  # noqa: S603 — fixed binary from PATH, fixed config path
            [binary, "--config-file", str(conf)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        _LOGGER.warning("Unable to start openbox with %s: %s", conf, error)
        return
    _LOGGER.info("Started openbox with %s", conf)


def _openbox_already_uses(config: Path) -> bool:
    """Return whether a user openbox process was started with ``config``."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-u", str(os.getuid()), "-ax", "openbox"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    needle = str(config)
    return any(needle in line for line in result.stdout.splitlines())


def _stop_openbox() -> bool:
    """Terminate existing openbox processes with SIGTERM only.

    Returns True when no openbox remains (or none was running), False when a
    process is still alive after the wait so a second WM is not started.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-u", str(os.getuid()), "-x", "openbox"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if result.returncode != 0:
        return True
    pids = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            _LOGGER.warning("Unable to stop openbox pid %s: %s", pid, error)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and pids:
        surviving: list[int] = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            surviving.append(pid)
        if not surviving:
            return True
        pids = surviving
        time.sleep(0.05)
    if pids:
        _LOGGER.warning("openbox still running after SIGTERM: %s", pids)
        return False
    return True
