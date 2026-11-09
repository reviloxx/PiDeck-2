"""Enable LAN SSH remote support using the appliance user's authorized_keys.

OpenSSH is installed through the existing privileged catalog broker (so an OTA
can enable it without updating the root daemon). Host keys and the ssh service
come from the Debian package; this module only plants the support public key
and reports status.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import pwd
import socket
import subprocess
import time
from typing import Callable

from pideck.application.ports.installation import InstallProgressCallback
from pideck.application.ports.packages import PackageOperationError
from pideck.infrastructure.packages.manager import SystemPackageManager

_LOGGER = logging.getLogger(__name__)

SUPPORT_APP_ID = "remote-support"
_SUPPORT_MARKER = "# pideck-remote-support"
_SSH_PORT = 22
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}


def support_public_keys(asset_root: Path) -> tuple[str, ...]:
    """Load support public keys shipped with the release assets."""
    candidates = (
        asset_root.parent / "assets/support/ssh-authorized-keys",
        Path(__file__).resolve().parents[3] / "assets/support/ssh-authorized-keys",
    )
    for path in candidates:
        if not path.is_file():
            continue
        keys = tuple(
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        if keys:
            return keys
    return ()


def appliance_home() -> Path:
    """Return the home directory of the appliance UID 1000 account."""
    try:
        return Path(pwd.getpwuid(1000).pw_dir)
    except KeyError:
        return Path.home()


def authorized_keys_path() -> Path:
    """Return the path of the appliance user's authorized_keys file."""
    return appliance_home() / ".ssh" / "authorized_keys"


def is_enabled() -> bool:
    """Return whether the support public key is present in authorized_keys."""
    path = authorized_keys_path()
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return _SUPPORT_MARKER in text or "pideck-tv-support" in text


def ssh_listening(port: int = _SSH_PORT) -> bool:
    """Return whether something accepts TCP connections on the SSH port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def local_ipv4_addresses() -> tuple[str, ...]:
    """Best-effort list of non-loopback IPv4 addresses for the status line."""
    addresses: list[str] = []
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, check=False, timeout=5, env=_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    for token in result.stdout.split():
        if token.count(".") == 3 and not token.startswith("127."):
            addresses.append(token)
    return tuple(addresses)


def write_authorized_keys(keys: tuple[str, ...]) -> None:
    """Replace managed support keys while preserving unrelated authorized keys."""
    if not keys:
        raise PackageOperationError("Remote support public key is missing from this release")
    ssh_dir = authorized_keys_path().parent
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing: list[str] = []
    path = authorized_keys_path()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(_SUPPORT_MARKER) or "pideck-tv-support" in stripped:
                continue
            existing.append(line.rstrip("\n"))
    lines = existing + [_SUPPORT_MARKER, *keys, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)
    try:
        info = pwd.getpwuid(1000)
        os.chown(ssh_dir, info.pw_uid, info.pw_gid)
        os.chown(path, info.pw_uid, info.pw_gid)
    except (KeyError, PermissionError, OSError):
        pass


def clear_authorized_keys() -> None:
    """Remove managed support keys from authorized_keys."""
    path = authorized_keys_path()
    if not path.is_file():
        return
    kept = [
        line.rstrip("\n")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not line.strip().startswith(_SUPPORT_MARKER)
        and "pideck-tv-support" not in line
    ]
    if kept:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        path.chmod(0o600)
    else:
        path.unlink(missing_ok=True)


def repair_system_clock() -> bool:
    """Ask systemd-timedated to enable NTP when the session may do so."""
    try:
        result = subprocess.run(
            [
                "busctl", "call",
                "org.freedesktop.timedate1", "/org/freedesktop/timedate1",
                "org.freedesktop.timedate1", "SetNTP", "bb", "true", "false",
            ],
            capture_output=True, text=True, check=False, timeout=15, env=_ENV,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _LOGGER.info("Unable to request NTP: %s", error)
        return False
    if result.returncode != 0:
        _LOGGER.info("SetNTP failed: %s", (result.stderr or result.stdout).strip())
        return False
    # Give timesync a moment before Flatpak/apt TLS checks.
    time.sleep(2.0)
    return True


def clock_looks_wrong(*, floor_epoch: float = 1_704_067_200.0) -> bool:
    """Return whether the system clock is before 2024-01-01 UTC (TLS will fail)."""
    return time.time() < floor_epoch


class RemoteSupportService:
    """Install OpenSSH via the catalog broker and plant the support pubkey."""

    def __init__(
        self,
        packages: SystemPackageManager,
        asset_root: Path,
        *,
        repair_clock: Callable[[], bool] = repair_system_clock,
    ) -> None:
        """Use ``packages`` for privileged install/remove of ``remote-support``."""
        self._packages = packages
        self._asset_root = asset_root
        self._repair_clock = repair_clock

    def status_message(self) -> str:
        """Short status for the Settings row."""
        enabled = is_enabled()
        listening = ssh_listening()
        addresses = ", ".join(local_ipv4_addresses()) or "unknown"
        user = pwd.getpwuid(1000).pw_name if _uid_name() else "pideck"
        if enabled and listening:
            return f"on · ssh {user}@<{addresses}>"
        if enabled:
            return f"key installed · ssh not listening · {addresses}"
        return "off"

    def enable(self, on_progress: InstallProgressCallback) -> None:
        """Install OpenSSH if needed and authorize the support key."""
        if clock_looks_wrong():
            _LOGGER.warning("System clock looks wrong; requesting NTP before package install")
            self._repair_clock()
            if clock_looks_wrong():
                raise PackageOperationError(
                    "System clock is wrong (TLS will fail). Connect Ethernet/Wi-Fi, "
                    "wait for time sync, then try again."
                )
        keys = support_public_keys(self._asset_root)
        from pideck.domain.catalog import InstallMethod, InstallSpec

        spec = InstallSpec(
            method=InstallMethod.APT,
            packages=("openssh-server",),
            dependencies=("ca-certificates",),
        )
        if not self._packages.is_installed(spec):
            self._packages.install(SUPPORT_APP_ID, spec, on_progress, None)
        write_authorized_keys(keys)
        if not ssh_listening():
            # Package postinst usually starts ssh; give it a moment.
            time.sleep(1.5)
        if not ssh_listening():
            raise PackageOperationError(
                "OpenSSH is installed but not listening on port 22. Reboot and try again."
            )

    def disable(self, on_progress: InstallProgressCallback) -> None:
        """Remove the support key; leave OpenSSH installed for simpler re-enable."""
        del on_progress
        clear_authorized_keys()


def _uid_name() -> str | None:
    try:
        return pwd.getpwuid(1000).pw_name
    except KeyError:
        return None
