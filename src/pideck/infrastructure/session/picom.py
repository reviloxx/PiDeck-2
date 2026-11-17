"""Apply the release picom configuration so composited video stays tear-free."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)
_SESSION_DIR = Path(__file__).resolve().parent
_NVIDIA_VERSION = Path("/proc/driver/nvidia/version")

# Absolute delays from launcher map for forced post-boot picom recovery.
POST_MAP_REFRESH_DELAYS_MS = (500, 2000, 5000, 15000)


def post_map_refresh_followup_delay_ms(attempt: int) -> int | None:
    """Return ms until the next post-map refresh after *attempt*, if any remain."""
    if attempt < 0 or attempt + 1 >= len(POST_MAP_REFRESH_DELAYS_MS):
        return None
    return POST_MAP_REFRESH_DELAYS_MS[attempt + 1] - POST_MAP_REFRESH_DELAYS_MS[attempt]


def select_picom_config(
    *,
    directory: Path | None = None,
    nvidia_present: Callable[[], bool] | None = None,
    virtual_machine: Callable[[], bool] | None = None,
) -> Path:
    """Return the release picom config for this GPU / virtualization environment."""
    base = directory or _SESSION_DIR
    if (nvidia_present or _nvidia_driver_loaded)():
        return base / "picom-nvidia.conf"
    if (virtual_machine or _running_in_vm)():
        return base / "picom-vm.conf"
    return base / "picom.conf"


def apply_release_picom(
    *,
    config: Path | None = None,
    nvidia_present: Callable[[], bool] | None = None,
    virtual_machine: Callable[[], bool] | None = None,
    force: bool = False,
) -> None:
    """Restart picom with the selected release config when a display is available.

    Failures are logged and never raised so an old image or missing compositor
    cannot block the launcher. No-op when picom is absent, DISPLAY is unset, or
    (unless *force*) picom is already running with the chosen config. Use
    ``force=True`` after the launcher has mapped: xsession may start picom
    before NVIDIA GLX is ready, leaving a black redirected pixmap for the
    whole session even though the config path already matches. Never SIGKILL
    picom: killing a stuck GLX client can wedge the X server.
    """
    conf = config or select_picom_config(
        nvidia_present=nvidia_present,
        virtual_machine=virtual_machine,
    )
    if not conf.is_file():
        _LOGGER.warning("PiDeck picom config missing at %s", conf)
        return
    if not os.environ.get("DISPLAY"):
        return
    binary = shutil.which("picom")
    if binary is None:
        return
    _reap_child_zombies()
    if not force and _picom_already_uses(conf):
        _LOGGER.info("picom already using %s", conf)
        return
    if not _start_picom(binary, conf, restarted=force):
        _LOGGER.warning("Retrying picom start after reap")
        _reap_child_zombies()
        time.sleep(0.1)
        _start_picom(binary, conf, restarted=True)


def live_picom_running() -> bool:
    """Return whether a non-zombie picom process exists for this user."""
    return bool(_live_picom_pids())


def pause_picom_for_client() -> None:
    """Stop picom so a fullscreen streaming client is not GLX-composited.

    Steam Link (and similar) drop frames when picom keeps the window redirected
    with ``unredir-if-possible = false``. Failures are logged and never raised.
    """
    if not live_picom_running():
        return
    if _stop_picom():
        _LOGGER.info("Paused picom for fullscreen streaming client")
    else:
        _LOGGER.warning("Unable to pause picom for fullscreen streaming client")


def resume_picom_after_client() -> None:
    """Restart release picom after a fullscreen streaming client exits."""
    apply_release_picom(force=True)


def _start_picom(binary: str, conf: Path, *, restarted: bool) -> bool:
    """Stop any live picom, start a new one, and confirm it stayed alive briefly."""
    try:
        if not _stop_picom():
            _LOGGER.warning("Leaving existing picom running; new config not applied")
            return live_picom_running()
        subprocess.Popen(  # noqa: S603 — fixed binary from PATH, fixed config path
            [binary, "--config", str(conf)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        _LOGGER.warning("Unable to start picom with %s: %s", conf, error)
        return False
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        _reap_child_zombies()
        if live_picom_running():
            _LOGGER.info("%s picom with %s", "Restarted" if restarted else "Started", conf)
            return True
        time.sleep(0.05)
    _LOGGER.warning("picom exited immediately after start with %s", conf)
    return False


def _live_picom_pids() -> list[int]:
    """Return live (non-zombie) picom PIDs for this user."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-u", str(os.getuid()), "-x", "picom"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [
        int(line)
        for line in result.stdout.splitlines()
        if line.strip().isdigit() and _pid_is_alive(int(line))
    ]


def _reap_child_zombies() -> None:
    """Collect exited child processes so SIGTERM'd session helpers are not zombies."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _pid_is_alive(pid: int) -> bool:
    """Return whether *pid* is a live (non-zombie) process.

    See ``openbox._pid_is_alive``: xsession helpers become zombies under bootstrap
    after SIGTERM, and ``kill(pid, 0)`` still succeeds for them.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    rparen = raw.rfind(")")
    if rparen < 0:
        return False
    parts = raw[rparen + 2 :].split()
    if not parts:
        return False
    if parts[0] == "Z":
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        except OSError:
            pass
        return False
    return True


def _nvidia_driver_loaded() -> bool:
    """Return whether the proprietary NVIDIA kernel driver is present."""
    return _NVIDIA_VERSION.is_file()


def _running_in_vm() -> bool:
    """Return whether this host reports a virtual machine to systemd."""
    binary = shutil.which("systemd-detect-virt")
    if binary is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [binary],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    virt = (result.stdout or "").strip()
    return result.returncode == 0 and virt not in ("", "none")


def _picom_already_uses(config: Path) -> bool:
    """Return whether a user picom process was started with ``config``."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-u", str(os.getuid()), "-ax", "picom"],
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
    for line in result.stdout.splitlines():
        if "<defunct>" in line or needle not in line:
            continue
        tokens = line.split()
        if tokens and tokens[0].isdigit() and _pid_is_alive(int(tokens[0])):
            return True
    return False


def _stop_picom() -> bool:
    """Terminate existing picom processes with SIGTERM only.

    Returns True when no live picom remains (or none was running), False when a
    process is still alive after the wait so a second compositor is not started.
    Zombie leftovers from the xsession ``exec`` hand-off count as stopped.
    """
    pids = _live_picom_pids()
    if not pids:
        _reap_child_zombies()
        return True
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            _LOGGER.warning("Unable to stop picom pid %s: %s", pid, error)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and pids:
        _reap_child_zombies()
        surviving = [pid for pid in pids if _pid_is_alive(pid)]
        if not surviving:
            return True
        pids = surviving
        time.sleep(0.05)
    _reap_child_zombies()
    surviving = [pid for pid in pids if _pid_is_alive(pid)]
    if surviving:
        _LOGGER.warning("picom still running after SIGTERM: %s", surviving)
        return False
    return True
