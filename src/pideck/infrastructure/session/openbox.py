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
_WM_READY_ATTEMPTS = 100
_WM_READY_INTERVAL = 0.1


def select_openbox_config(*, directory: Path | None = None) -> Path:
    """Return the release Openbox rc, falling back to the image-installed copy."""
    release = (directory or _SESSION_DIR) / "openbox-rc.xml"
    if release.is_file():
        return release
    return _IMAGE_OPENBOX_RC


def apply_release_openbox(*, config: Path | None = None) -> bool:
    """Restart Openbox with the release rc when a display is available.

    Failures are logged and never raised so a missing window manager cannot
    block the launcher. No-op when openbox is absent, DISPLAY is unset, or
    openbox is already running with the chosen config (same resolved path or
    identical file contents). Never SIGKILL openbox.

    Also best-effort copies the release rc onto the image share path so a
    fallback xsession still gets maximize rules after an OTA.

    Returns True when Openbox was restarted with the release config.
    """
    conf = config or select_openbox_config()
    if not conf.is_file():
        _LOGGER.warning("PiDeck openbox config missing at %s", conf)
        return False
    _sync_image_openbox(conf)
    if not os.environ.get("DISPLAY"):
        return False
    binary = shutil.which("openbox")
    if binary is None:
        return False
    _reap_child_zombies()
    if _openbox_already_uses(conf):
        _LOGGER.info("openbox already using %s", conf)
        if _wait_for_window_manager(attempts=5):
            return False
        _LOGGER.warning("openbox listed but window manager not ready; restarting")
    try:
        if not _stop_openbox():
            _LOGGER.error(
                "Openbox still running after SIGTERM; release config %s was NOT applied. "
                "Browser maximize rules may be missing until reboot.",
                conf,
            )
            return False
        subprocess.Popen(  # noqa: S603 — fixed binary from PATH, fixed config path
            [binary, "--config-file", str(conf.resolve())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        _LOGGER.warning("Unable to start openbox with %s: %s", conf, error)
        return False
    if not _wait_for_window_manager():
        _LOGGER.warning("Openbox started with %s but did not become ready", conf)
    else:
        _LOGGER.info("Started openbox with %s", conf)
    return True


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

    Bootstrap inherits ``openbox &`` / ``picom &`` from the xsession script via
    ``exec``. After SIGTERM those become zombies under the launcher; ``kill(pid,
    0)`` still succeeds for zombies, which previously made the restart abort and
    left the session with no window manager.
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


def _sync_image_openbox(conf: Path) -> None:
    """Copy the release Openbox rc onto the image fallback path when writable."""
    try:
        if _IMAGE_OPENBOX_RC.is_file() and _IMAGE_OPENBOX_RC.read_bytes() == conf.read_bytes():
            return
        _IMAGE_OPENBOX_RC.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(conf, _IMAGE_OPENBOX_RC)
        _LOGGER.info("Synced Openbox config to %s", _IMAGE_OPENBOX_RC)
    except OSError as error:
        _LOGGER.warning(
            "Unable to sync Openbox config to %s: %s (release path %s still used when available)",
            _IMAGE_OPENBOX_RC, error, conf,
        )


def _wait_for_window_manager(*, attempts: int | None = None) -> bool:
    """Poll until ``wmctrl -m`` succeeds or the readiness timeout expires."""
    binary = shutil.which("wmctrl")
    if binary is None:
        time.sleep(_WM_READY_INTERVAL * 5)
        return True
    for _ in range(_WM_READY_ATTEMPTS if attempts is None else attempts):
        try:
            result = subprocess.run(  # noqa: S603
                [binary, "-m"],
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            time.sleep(_WM_READY_INTERVAL)
            continue
        if result.returncode == 0:
            return True
        time.sleep(_WM_READY_INTERVAL)
    return False


def _openbox_already_uses(config: Path) -> bool:
    """Return whether running openbox already has an equivalent config loaded.

    Compares resolved paths so ``/usr/lib/pideck/current/...`` matches the
    release directory Path(__file__) resolves to, and compares file bytes so a
    fixed image rc is not restarted needlessly.
    """
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
    try:
        want = config.resolve()
        want_bytes = want.read_bytes()
    except OSError:
        return False
    for line in result.stdout.splitlines():
        if "<defunct>" in line:
            continue
        tokens = line.split()
        if len(tokens) < 2 or not tokens[0].isdigit():
            continue
        if not _pid_is_alive(int(tokens[0])):
            continue
        candidates: list[Path] = []
        for index, token in enumerate(tokens):
            if token == "--config-file" and index + 1 < len(tokens):
                candidates.append(Path(tokens[index + 1]))
            elif token.endswith(".xml"):
                candidates.append(Path(token))
        for candidate in candidates:
            if not candidate.is_absolute():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == want:
                return True
            try:
                if resolved.is_file() and resolved.read_bytes() == want_bytes:
                    return True
            except OSError:
                continue
    return False


def _stop_openbox() -> bool:
    """Terminate existing openbox processes with SIGTERM only.

    Returns True when no live openbox remains (or none was running), False when a
    process is still alive after the wait so a second WM is not started. Zombie
    leftovers from the xsession ``exec`` hand-off count as stopped.
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
    pids = [pid for pid in pids if _pid_is_alive(pid)]
    if not pids:
        _reap_child_zombies()
        return True
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            _LOGGER.warning("Unable to stop openbox pid %s: %s", pid, error)
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
        _LOGGER.warning("openbox still running after SIGTERM: %s", surviving)
        return False
    return True
