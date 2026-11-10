"""Mount data drives through udisks2 so they appear under ``/media/<user>``.

``lsblk`` provides the block-device tree. Every disk with swap or a mount
outside the data-drive locations (``/media``, ``/mnt``, ``/run/media``) is
treated as a system disk and hidden completely, so the appliance cannot
unmount or power off its own root, boot, EFI or home filesystems. Mounting uses ``udisksctl`` without
interactive authentication; new PiDeck images ship a polkit rule that grants
the session user the required udisks2 actions.

User-mounted filesystem UUIDs are remembered in a separate ``drives.yaml`` so
they can be remounted after restart without touching ``pideck.yaml``. Stable
``/media/<user>/by-uuid/<UUID>`` symlinks keep Kodi library paths valid when
udisks chooses a different label directory after reboot.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

import yaml

from pideck.application.ports.storage import Drive, StorageError


_LOGGER = logging.getLogger(__name__)
_DEVICE = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9._/-]*")
_UUID = re.compile(r"[0-9A-Fa-f][0-9A-Fa-f-]{3,63}")
_DATA_MOUNT_ROOTS = ("/media/", "/mnt/", "/run/media/")
_IGNORED_FILESYSTEMS = frozenset({
    "swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "zfs_member", "bcache", "squashfs",
})
_IGNORED_TYPES = frozenset({"loop", "ram", "zram"})
_LSBLK_COLUMNS = "NAME,PATH,LABEL,FSTYPE,SIZE,MOUNTPOINTS,TYPE,RM,HOTPLUG,MODEL,UUID"
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
_RESTORE_RETRY_SECONDS = 3.0
_UNSAFE_LABEL = re.compile(r"[^\w.+-]+", re.UNICODE)

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


def _filesystem_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    uuid = value.strip()
    if not uuid or _UUID.fullmatch(uuid) is None:
        return None
    return uuid


def _sanitize_label(label: str) -> str | None:
    """Return a single path component for a filesystem label, or None if unusable."""
    cleaned = _UNSAFE_LABEL.sub("_", label.strip()).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        return None
    return cleaned


def _with_numbered_labels(drives: tuple[Drive, ...]) -> tuple[Drive, ...]:
    """Number UI labels when multiple drives share the same filesystem label.

    Numbers are assigned by sorted filesystem UUID so the same disk set keeps
    stable Volume1–N (and Samba) names across remount order changes.
    """
    counts: dict[str, int] = {}
    for drive in drives:
        if drive.filesystem_label is not None:
            counts[drive.filesystem_label] = counts.get(drive.filesystem_label, 0) + 1
    numbers: dict[str, int] = {}
    for label, count in counts.items():
        if count < 2:
            continue
        group = [
            drive for drive in drives
            if drive.filesystem_label == label and drive.uuid is not None
        ]
        for index, drive in enumerate(sorted(group, key=lambda item: item.uuid or ""), start=1):
            if drive.uuid is not None:
                numbers[drive.uuid] = index
    numbered: list[Drive] = []
    for drive in drives:
        label = drive.filesystem_label
        if label is None or counts[label] < 2 or drive.uuid is None or drive.uuid not in numbers:
            numbered.append(drive)
            continue
        numbered.append(replace(drive, label=f"{label}{numbers[drive.uuid]}"))
    return tuple(numbered)


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
            raw_label = node.get("label")
            filesystem_label = (
                raw_label.strip()
                if isinstance(raw_label, str) and raw_label.strip()
                else None
            )
            if filesystem_label is not None:
                label = filesystem_label
            else:
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
                uuid=_filesystem_uuid(node.get("uuid")),
                filesystem_label=filesystem_label,
            ))
    return _with_numbered_labels(tuple(drives))


class DrivePreferences:
    """Persist mounted and shared filesystem UUIDs from Settings > Drives."""

    def __init__(self, path: Path) -> None:
        """Create a repository for remembered data-drive mounts and shares."""
        self.path = path

    def load(self) -> frozenset[str]:
        """Load validated mount UUIDs; a missing file means nothing to remount."""
        mounts, _shared = self.load_state()
        return mounts

    def load_shared(self) -> frozenset[str]:
        """Load UUIDs the user opted to export on the LAN."""
        _mounts, shared = self.load_state()
        return shared

    def load_state(self) -> tuple[frozenset[str], frozenset[str]]:
        """Load mount and shared UUID sets; version 1 files have an empty shared set."""
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return frozenset(), frozenset()
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise StorageError(f"Unable to read drive settings: {error}") from error
        if not isinstance(document, dict):
            raise StorageError("Invalid saved drive settings")
        version = document.get("version")
        if version not in (1, 2):
            raise StorageError("Invalid saved drive settings")
        mounts = self._uuid_set(document.get("uuids"))
        if version == 1:
            return mounts, frozenset()
        shared_values = document.get("shared", [])
        return mounts, self._uuid_set(shared_values)

    def save(self, uuids: frozenset[str], shared: frozenset[str] | None = None) -> None:
        """Atomically replace remembered mounts; preserve shares when omitted."""
        for uuid in uuids:
            if _filesystem_uuid(uuid) is None:
                raise StorageError("Invalid drive UUID")
        if shared is None:
            try:
                _mounts, shared = self.load_state()
            except StorageError:
                shared = frozenset()
        for uuid in shared:
            if _filesystem_uuid(uuid) is None:
                raise StorageError("Invalid drive UUID")
        document = {"version": 2, "uuids": sorted(uuids), "shared": sorted(shared)}
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                yaml.safe_dump(document, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except (OSError, yaml.YAMLError) as error:
            raise StorageError(f"Unable to save drive settings: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remember(self, uuid: str) -> None:
        """Add ``uuid`` to the remembered set after a successful user mount."""
        cleaned = _filesystem_uuid(uuid)
        if cleaned is None:
            raise StorageError("Invalid drive UUID")
        mounts, shared = self.load_state()
        self.save(mounts | {cleaned}, shared)

    def forget(self, uuid: str) -> None:
        """Remove ``uuid`` after the user explicitly unmounts the drive."""
        cleaned = _filesystem_uuid(uuid)
        if cleaned is None:
            raise StorageError("Invalid drive UUID")
        mounts, shared = self.load_state()
        self.save(mounts - {cleaned}, shared)

    def remember_shared(self, uuid: str) -> None:
        """Remember that ``uuid`` should be exported when mounted."""
        cleaned = _filesystem_uuid(uuid)
        if cleaned is None:
            raise StorageError("Invalid drive UUID")
        mounts, shared = self.load_state()
        self.save(mounts, shared | {cleaned})

    def forget_shared(self, uuid: str) -> None:
        """Forget the LAN share preference for ``uuid``."""
        cleaned = _filesystem_uuid(uuid)
        if cleaned is None:
            raise StorageError("Invalid drive UUID")
        mounts, shared = self.load_state()
        self.save(mounts, shared - {cleaned})

    @staticmethod
    def _uuid_set(values: object) -> frozenset[str]:
        if not isinstance(values, list):
            raise StorageError("Invalid saved drive settings")
        uuids: set[str] = set()
        for value in values:
            uuid = _filesystem_uuid(value)
            if uuid is None:
                raise StorageError("Invalid saved drive settings")
            uuids.add(uuid)
        return frozenset(uuids)


class UDisksStorageGateway:
    """List block devices with ``lsblk`` and change mounts with ``udisksctl``."""

    def __init__(
        self,
        runner: Runner = _run,
        which: Callable[[str], str | None] = shutil.which,
        preferences: DrivePreferences | None = None,
        sleep: Callable[[float], None] = time.sleep,
        media_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        """Create a gateway; ``runner``, ``which``, ``sleep`` and ``media_root`` are injectable for tests."""
        self._runner = runner
        self._which = which
        self._preferences = preferences
        self._sleep = sleep
        self._media_root = media_root
        self._home = home or Path.home()

    def available(self) -> bool:
        """Return whether udisks2 is installed on this image."""
        return self._which("udisksctl") is not None and self._which("lsblk") is not None

    def _user_media_root(self) -> Path:
        """Return ``/media/<user>`` where stable by-uuid links are published."""
        if self._media_root is not None:
            return self._media_root
        return Path("/media") / self._home.name

    def _execute(self, command: list[str], failure: str) -> str:
        try:
            result = self._runner(command)
        except (OSError, subprocess.SubprocessError) as error:
            raise StorageError(failure) from error
        if result.returncode:
            raise StorageError(_clean_error(result.stderr or result.stdout))
        return result.stdout

    def _listed_drives(self) -> tuple[Drive, ...]:
        """Return drives from ``lsblk`` without rewriting mount points."""
        output = self._execute(
            ["lsblk", "--json", "--bytes", "--output", _LSBLK_COLUMNS],
            "The drive list is unavailable",
        )
        try:
            document = json.loads(output)
        except json.JSONDecodeError as error:
            raise StorageError("The drive list is invalid") from error
        return parse_drives(document)

    def list_drives(self) -> tuple[Drive, ...]:
        """Return data drives with stable by-uuid mount paths when mounted."""
        drives = self._listed_drives()
        return tuple(self._with_stable_mountpoint(drive, drives) for drive in drives)

    def mount(self, drive: Drive) -> str:
        """Mount a listed drive and return the stable by-uuid mount path."""
        current = self._validated(drive)
        device = current.device
        output = self._execute(
            ["udisksctl", "mount", "--no-user-interaction", "--block-device", device],
            "The storage service is unavailable",
        )
        match = re.search(r" at (/.+?)\.?\s*$", output.strip())
        mountpoint: str | None = match.group(1) if match is not None else None
        listed = self._listed_drives()
        if mountpoint is None:
            for item in listed:
                if item.device == device and item.mountpoint is not None:
                    mountpoint = item.mountpoint
                    break
        if mountpoint is None:
            raise StorageError("The drive was not mounted")
        current = next((item for item in listed if item.device == device), current)
        stable = self._ensure_stable_links(current, mountpoint, listed)
        _LOGGER.info("Mounted drive device=%s mountpoint=%s stable=%s", device, mountpoint, stable)
        self._remember(current.uuid)
        return stable

    def unmount(self, drive: Drive) -> None:
        """Unmount a drive, then power off a removable disk with no other mounts."""
        current = self._validated(drive)
        device = current.device
        self._remove_stable_links(current)
        self._execute(
            ["udisksctl", "unmount", "--no-user-interaction", "--block-device", device],
            "The storage service is unavailable",
        )
        _LOGGER.info("Unmounted drive device=%s", device)
        self._forget(current.uuid)
        parent = current.parent or device
        if not current.removable or _DEVICE.fullmatch(parent) is None:
            return
        try:
            still_mounted = any(
                other.mounted and (other.parent or other.device) == parent
                for other in self._listed_drives()
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

    def restore_saved(self) -> None:
        """Remount remembered data drives that are present and still unmounted."""
        if self._preferences is None or not self.available():
            return
        try:
            uuids = self._preferences.load()
        except StorageError as error:
            _LOGGER.warning("Unable to load remembered drives: %s", error)
            return
        if not uuids:
            return
        drives = self._listed_drives()
        if not drives:
            self._sleep(_RESTORE_RETRY_SECONDS)
            drives = self._listed_drives()
        by_uuid = {drive.uuid: drive for drive in drives if drive.uuid}
        for uuid in sorted(uuids):
            drive = by_uuid.get(uuid)
            if drive is None:
                _LOGGER.info("Remembered drive is not present uuid=%s", uuid)
                continue
            if drive.mounted and drive.mountpoint is not None:
                self._ensure_stable_links(drive, drive.mountpoint, drives)
                continue
            try:
                self.mount(drive)
            except StorageError as error:
                _LOGGER.warning("Unable to remount drive uuid=%s error=%s", uuid, error)

    def restore_saved_async(self) -> None:
        """Remount remembered drives off the UI thread at launcher startup."""
        if self._preferences is None or not self._preferences.path.exists() or not self.available():
            return

        def restore() -> None:
            try:
                self.restore_saved()
            except StorageError as error:
                _LOGGER.warning("Unable to restore remembered drives: %s", error)

        threading.Thread(target=restore, name="pideck-drives-restore", daemon=True).start()

    def _with_stable_mountpoint(self, drive: Drive, all_drives: tuple[Drive, ...]) -> Drive:
        """Publish stable links for a mounted UUID drive and report that path."""
        if drive.mountpoint is None or drive.uuid is None:
            return drive
        stable = self._ensure_stable_links(drive, drive.mountpoint, all_drives)
        if stable == drive.mountpoint:
            return drive
        return replace(drive, mountpoint=stable)

    def _ensure_stable_links(
        self,
        drive: Drive,
        real_mount: str,
        all_drives: tuple[Drive, ...],
    ) -> str:
        """Create by-uuid/by-label symlinks; return by-uuid only when it exists."""
        if drive.uuid is None:
            return real_mount
        target = Path(real_mount)
        uuid_link = self._user_media_root() / "by-uuid" / drive.uuid
        published = self._replace_symlink(uuid_link, target)
        sanitized = (
            _sanitize_label(drive.filesystem_label)
            if drive.filesystem_label is not None
            else None
        )
        if sanitized is not None:
            same_label = sum(
                1
                for other in all_drives
                if other.filesystem_label == drive.filesystem_label
            )
            if same_label == 1:
                self._replace_symlink(self._user_media_root() / "by-label" / sanitized, target)
        return str(uuid_link) if published else real_mount

    def _remove_stable_links(self, drive: Drive) -> None:
        """Remove published symlinks for ``drive`` before unmounting."""
        root = self._user_media_root()
        if drive.uuid is not None:
            self._unlink_if_symlink(root / "by-uuid" / drive.uuid)
        if drive.filesystem_label is not None:
            sanitized = _sanitize_label(drive.filesystem_label)
            if sanitized is not None:
                self._unlink_if_symlink(root / "by-label" / sanitized)

    @staticmethod
    def _replace_symlink(link: Path, target: Path) -> bool:
        """Point ``link`` at ``target``; return True only when the symlink is ready."""
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() or link.is_file():
                if link.is_symlink() and os.readlink(link) == str(target):
                    return True
                link.unlink()
            elif link.exists():
                _LOGGER.warning("Stable mount path is not a symlink path=%s", link)
                return False
            link.symlink_to(target)
            return True
        except OSError as error:
            _LOGGER.warning("Unable to publish stable mount path=%s error=%s", link, error)
            return False

    @staticmethod
    def _unlink_if_symlink(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
        except OSError as error:
            _LOGGER.warning("Unable to remove stable mount path=%s error=%s", path, error)

    def _remember(self, uuid: str | None) -> None:
        if self._preferences is None or uuid is None:
            return
        try:
            self._preferences.remember(uuid)
        except StorageError as error:
            _LOGGER.warning("Unable to remember mounted drive uuid=%s error=%s", uuid, error)

    def _forget(self, uuid: str | None) -> None:
        if self._preferences is None or uuid is None:
            return
        try:
            self._preferences.forget(uuid)
        except StorageError as error:
            _LOGGER.warning("Unable to forget unmounted drive uuid=%s error=%s", uuid, error)

    def _validated(self, drive: Drive) -> Drive:
        """Return the freshly listed drive so stale UI snapshots cannot target other disks."""
        if _DEVICE.fullmatch(drive.device) is None or ".." in drive.device:
            raise StorageError("Invalid drive")
        for current in self._listed_drives():
            if current.device == drive.device:
                return current
        raise StorageError("The drive is no longer available")
