"""Apply the release picom configuration so composited video stays tear-free."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

_LOGGER = logging.getLogger(__name__)
_CONFIG = Path(__file__).with_name("picom.conf")


def apply_release_picom(*, config: Path | None = None) -> None:
    """Restart picom with the release config when a display session is available.

    Failures are logged and never raised so an old image or missing compositor
    cannot block the launcher. No-op when picom is absent or DISPLAY is unset.
    """
    conf = config or _CONFIG
    if not conf.is_file():
        _LOGGER.warning("PiDeck picom config missing at %s", conf)
        return
    if not os.environ.get("DISPLAY"):
        return
    binary = shutil.which("picom")
    if binary is None:
        return
    try:
        _stop_picom()
        subprocess.Popen(  # noqa: S603 — fixed binary from PATH, fixed config path
            [binary, "--config", str(conf)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        _LOGGER.warning("Unable to start picom with %s: %s", conf, error)
        return
    _LOGGER.info("Started picom with %s", conf)


def _stop_picom() -> None:
    """Terminate any existing picom processes for this user."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-u", str(os.getuid()), "-x", "picom"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        return
    pids = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            _LOGGER.warning("Unable to stop picom pid %s: %s", pid, error)
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
            return
        pids = surviving
        time.sleep(0.05)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
