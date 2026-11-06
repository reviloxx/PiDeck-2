"""Mount data drives through udisks2 so they appear under ``/media/<user>``.

``lsblk`` provides the block-device tree. Every disk with swap or a mount
outside the data-drive locations (``/media``, ``/mnt``, ``/run/media``) is
treated as a system disk and hidden completely, so the appliance cannot
unmount or power off its own root, boot, EFI or home filesystems. Mounting uses ``udisksctl`` without
interactive authentication; new PiDeck images ship a polkit rule that grants
the session user the required udisks2 actions.
"""

from collections.abc import Callable, Iterator, Mapping
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

from pideck.application.ports.storage import Drive, StorageError


_LOGGER = logging.getLogger(__name__)
_DEVICE = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9._/-]*")
_DATA_MOUNT_ROOTS = ("/media/", "/mnt/", "/run/media/")
_IGNORED_FILESYSTEMS = frozenset({
    "swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "zfs_member", "bcache", "squashfs",
})
_IGNORED_TYPES = frozenset({"loop", "ram", "zram"})
_LSBLK_COLUMNS = "NAME,PATH,LABEL,FSTYPE,SIZE,MOUNTPOINTS,TYPE,RM,HOTPLUG,MODEL"
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, check=False, env={**os.environ, **_ENV},
    )


def _flag(value: object) -> bool:
    return value is True or value in ("1", 1)


def _size(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _mountpoints(node: Mapping[str, Any]) -> tuple[str, ...]:
    values = node.get("mountpoints")
    if values is None:
        values = [node.get("mountpoint")]
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str) and value)


def _walk(node: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield node
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                yield from _walk(child)


def _is_system_mount(mountpoint: str) -> bool:
    return not mountpoint.startswith(_DATA_MOUNT_ROOTS) and mountpoint != "/mnt"


def _clean_error(text: str) -> str:
    """Drop D-Bus error identifiers so the user sees the readable reason."""
    message = text.strip().splitlines()[-1] if text.strip() else ""
    message = re.sub(r"GDBus\.Error:[\w.]+:\s*", "", message)
    return message or "The storage service rejected the request"


def parse_drives(document: object) -> tuple[Drive, ...]:
    """Convert ``lsblk --json`` output into mountable non-system drives."""
    if not isinstance(document, dict) or not isinstance(document.get("blockdevices"), list):
        raise StorageError("The drive list is invalid")
    drives: list[Drive] = []
    for disk in document["blockdevices"]:
        if not isinstance(disk, dict) or disk.get("type") in _IGNORED_TYPES:
            continue
        nodes = list(_walk(disk))
        if any(_is_system_mount(mount) for node in nodes for mount in _mountpoints(node)):
            continue
        disk_path = disk.get("path")
        disk_removable = _flag(disk.get("rm")) or _flag(disk.get("hotplug"))
        model = disk.get("model") if isinstance(disk.get("model"), str) else ""
        for node in nodes:
            path = node.get("path")
            filesystem = node.get("fstype")
            if not isinstance(path, str) or _DEVICE.fullmatch(path) is None:
                continue
            if not isinstance(filesystem, str) or not filesystem or filesystem in _IGNORED_FILESYSTEMS:
                continue
            label = node.get("label")
            if not isinstance(label, str) or not label.strip():
                name = node.get("name") if isinstance(node.get("name"), str) else path.rsplit("/", 1)[-1]
                label = f"{model.strip()} ({name})" if model and model.strip() else name
            mounts = _mountpoints(node)
            drives.append(Drive(
                device=path,
                label=label.strip(),
                filesystem=filesystem,
                size=_size(node.get("size")),
                mountpoint=mounts[0] if mounts else None,
                removable=disk_removable or _flag(node.get("rm")) or _flag(node.get("hotplug")),
                parent=disk_path if isinstance(disk_path, str) and disk_path != path else None,
            ))
    return tuple(drives)


class UDisksStorageGateway:
    """List block devices with ``lsblk`` and change mounts with ``udisksctl``."""

    def __init__(self, runner: Runner = _run, which: Callable[[str], str | None] = shutil.which) -> None:
        """Create a gateway; ``runner`` and ``which`` are injectable for tests."""
        self._runner = runner
        self._which = which

    def available(self) -> bool:
        """Return whether udisks2 is installed on this image."""
        return self._which("udisksctl") is not None and self._which("lsblk") is not None

    def _execute(self, command: list[str], failure: str) -> str:
        try:
            result = self._runner(command)
        except (OSError, subprocess.SubprocessError) as error:
            raise StorageError(failure) from error
        if result.returncode:
            raise StorageError(_clean_error(result.stderr or result.stdout))
        return result.stdout

    def list_drives(self) -> tuple[Drive, ...]:
        """Return data drives and partitions that are not on the system disk."""
        output = self._execute(
            ["lsblk", "--json", "--bytes", "--output", _LSBLK_COLUMNS],
            "The drive list is unavailable",
        )
        try:
            document = json.loads(output)
        except json.JSONDecodeError as error:
            raise StorageError("The drive list is invalid") from error
        return parse_drives(document)

    def mount(self, drive: Drive) -> str:
        """Mount a listed drive and return the mount point udisks2 chose."""
        device = self._validated(drive).device
        output = self._execute(
            ["udisksctl", "mount", "--no-user-interaction", "--block-device", device],
            "The storage service is unavailable",
        )
        match = re.search(r" at (/.+?)\.?\s*$", output.strip())
        if match is not None:
            _LOGGER.info("Mounted drive device=%s mountpoint=%s", device, match.group(1))
            return match.group(1)
        for current in self.list_drives():
            if current.device == device and current.mountpoint is not None:
                return current.mountpoint
        raise StorageError("The drive was not mounted")

    def unmount(self, drive: Drive) -> None:
        """Unmount a drive, then power off a removable disk with no other mounts."""
        current = self._validated(drive)
        device = current.device
        self._execute(
            ["udisksctl", "unmount", "--no-user-interaction", "--block-device", device],
            "The storage service is unavailable",
        )
        _LOGGER.info("Unmounted drive device=%s", device)
        parent = current.parent or device
        if not current.removable or _DEVICE.fullmatch(parent) is None:
            return
        try:
            still_mounted = any(
                other.mounted and (other.parent or other.device) == parent
                for other in self.list_drives()
            )
            if still_mounted:
                return
            self._execute(
                ["udisksctl", "power-off", "--no-user-interaction", "--block-device", parent],
                "The storage service is unavailable",
            )
            _LOGGER.info("Powered off removable disk device=%s", parent)
        except StorageError as error:
            _LOGGER.warning("Unable to power off removable disk device=%s error=%s", parent, error)

    def _validated(self, drive: Drive) -> Drive:
        """Return the freshly listed drive so stale UI snapshots cannot target other disks."""
        if _DEVICE.fullmatch(drive.device) is None or ".." in drive.device:
            raise StorageError("Invalid drive")
        for current in self.list_drives():
            if current.device == drive.device:
                return current
        raise StorageError("The drive is no longer available")
