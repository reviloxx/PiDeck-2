"""Tests for the udisks2 storage gateway and the Drives settings window."""

import json
import os
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.storage import Drive, StorageError
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.udisks_storage import DrivePreferences, UDisksStorageGateway, parse_drives
from pideck.presentation.qt.drive_actions_dialog import format_size
from pideck.presentation.qt.drives_window import DriveRowWidget, DrivesWindow
from pideck.presentation.qt.settings_window import SettingsWindow
from tests.test_configuration import configuration_document


MEDIA_UUID = "A1B2-C3D4"
DATA_UUID = "12345678-1234-1234-1234-123456789abc"

LSBLK = {
    "blockdevices": [
        {"name": "loop0", "path": "/dev/loop0", "fstype": "squashfs", "type": "loop",
         "mountpoints": ["/snap/core"], "rm": False, "hotplug": False, "size": 10},
        {"name": "sda", "path": "/dev/sda", "fstype": None, "type": "disk", "rm": False,
         "hotplug": False, "size": 500, "model": "System SSD", "mountpoints": [None], "children": [
             {"name": "sda1", "path": "/dev/sda1", "fstype": "vfat", "type": "part",
              "mountpoints": ["/boot/efi"], "label": None, "size": 100, "uuid": "EFI-0001"},
             {"name": "sda2", "path": "/dev/sda2", "fstype": "ext4", "type": "part",
              "mountpoints": ["/"], "label": "root", "size": 300, "uuid": "root-uuid"},
             {"name": "sda3", "path": "/dev/sda3", "fstype": "ext4", "type": "part",
              "mountpoints": [None], "label": "spare", "size": 100, "uuid": "spare-uuid"},
         ]},
        {"name": "sdb", "path": "/dev/sdb", "fstype": None, "type": "disk", "rm": True,
         "hotplug": True, "size": 32_000_000_000, "model": "Flash Disk", "mountpoints": [None], "children": [
             {"name": "sdb1", "path": "/dev/sdb1", "fstype": "exfat", "type": "part",
              "mountpoints": [None], "label": "MEDIA", "size": 32_000_000_000, "uuid": MEDIA_UUID},
             {"name": "sdb2", "path": "/dev/sdb2", "fstype": "swap", "type": "part",
              "mountpoints": [None], "label": None, "size": 1, "uuid": None},
         ]},
        {"name": "sdc", "path": "/dev/sdc", "fstype": None, "type": "disk", "rm": "0",
         "hotplug": "0", "size": "2000", "model": "Data HDD ", "mountpoints": [None], "children": [
             {"name": "sdc1", "path": "/dev/sdc1", "fstype": "ntfs", "type": "part",
              "mountpoints": ["/media/pideck/Data"], "label": None, "size": "2000", "uuid": DATA_UUID},
         ]},
    ],
}


class Runner:
    """Record commands and return scripted ``udisksctl``/``lsblk`` results."""

    def __init__(self, document: dict = LSBLK) -> None:
        self.document = document
        self.commands: list[list[str]] = []
        self.failures: dict[str, str] = {}

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        action = command[1] if command[0] == "udisksctl" else command[0]
        if action in self.failures:
            return subprocess.CompletedProcess(command, 1, "", self.failures[action])
        if command[0] == "lsblk":
            return subprocess.CompletedProcess(command, 0, json.dumps(self.document), "")
        if action == "mount":
            return subprocess.CompletedProcess(command, 0, f"Mounted {command[-1]} at /media/pideck/MEDIA\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_system_disk_loop_and_unmountable_filesystems_are_hidden() -> None:
    """Partitions on the root disk are never offered, even unmounted ones."""
    drives = parse_drives(LSBLK)
    assert [drive.device for drive in drives] == ["/dev/sdb1", "/dev/sdc1"]
    usb, hdd = drives
    assert usb == Drive(
        "/dev/sdb1", "MEDIA", "exfat", 32_000_000_000, None, True, "/dev/sdb", MEDIA_UUID, "MEDIA",
    )
    assert hdd.label == "Data HDD (sdc1)"
    assert hdd.uuid == DATA_UUID
    assert hdd.filesystem_label is None
    assert hdd.size == 2000 and hdd.mounted and hdd.removable is False
    with pytest.raises(StorageError):
        parse_drives({"unexpected": []})


def test_disks_with_mounts_outside_data_locations_are_system_disks() -> None:
    """A separate /home disk is hidden; fstab data mounts under /mnt stay listed."""
    document = {"blockdevices": [
        {"name": "sdd", "path": "/dev/sdd", "type": "disk", "children": [
            {"name": "sdd1", "path": "/dev/sdd1", "fstype": "ext4", "type": "part", "mountpoints": ["/home"]},
            {"name": "sdd2", "path": "/dev/sdd2", "fstype": "ext4", "type": "part", "mountpoints": [None]},
        ]},
        {"name": "sde", "path": "/dev/sde", "type": "disk", "children": [
            {"name": "sde1", "path": "/dev/sde1", "fstype": "ntfs", "type": "part", "mountpoints": ["/mnt/data"]},
        ]},
    ]}
    assert [drive.device for drive in parse_drives(document)] == ["/dev/sde1"]
    assert parse_drives(document)[0].uuid is None


def test_mount_returns_stable_by_uuid_path_and_creates_symlinks(tmp_path: Path) -> None:
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    gateway = UDisksStorageGateway(
        runner, which=lambda name: f"/usr/bin/{name}", media_root=media_root,
    )
    assert gateway.available()
    drives = gateway.list_drives()
    drive = drives[0]
    stable = str(media_root / "by-uuid" / MEDIA_UUID)
    assert gateway.mount(drive) == stable
    assert [
        "udisksctl", "mount", "--no-user-interaction", "--block-device", "/dev/sdb1",
    ] in runner.commands
    uuid_link = media_root / "by-uuid" / MEDIA_UUID
    assert uuid_link.is_symlink() and os.readlink(uuid_link) == "/media/pideck/MEDIA"
    label_link = media_root / "by-label" / "MEDIA"
    assert label_link.is_symlink() and os.readlink(label_link) == "/media/pideck/MEDIA"
    already = drives[1]
    assert already.mountpoint == str(media_root / "by-uuid" / DATA_UUID)
    assert os.readlink(media_root / "by-uuid" / DATA_UUID) == "/media/pideck/Data"


def test_unmounting_removable_drive_powers_off_its_disk(tmp_path: Path) -> None:
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    gateway = UDisksStorageGateway(runner, which=lambda name: None, media_root=media_root)
    assert not gateway.available()
    drive = Drive(
        "/dev/sdb1", "MEDIA", "exfat", 1, "/media/pideck/MEDIA", True, "/dev/sdb",
        MEDIA_UUID, "MEDIA",
    )
    (media_root / "by-uuid").mkdir(parents=True)
    (media_root / "by-label").mkdir(parents=True)
    (media_root / "by-uuid" / MEDIA_UUID).symlink_to("/media/pideck/MEDIA")
    (media_root / "by-label" / "MEDIA").symlink_to("/media/pideck/MEDIA")
    gateway.unmount(drive)
    udisks = [command for command in runner.commands if command[0] == "udisksctl"]
    assert udisks == [
        ["udisksctl", "unmount", "--no-user-interaction", "--block-device", "/dev/sdb1"],
        ["udisksctl", "power-off", "--no-user-interaction", "--block-device", "/dev/sdb"],
    ]
    assert not (media_root / "by-uuid" / MEDIA_UUID).exists()
    assert not (media_root / "by-label" / "MEDIA").exists()


def test_internal_drive_is_not_powered_off_and_power_off_failure_is_not_fatal(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    gateway = UDisksStorageGateway(runner, media_root=media_root)
    # A stale snapshot claiming the internal disk is removable must not power it off.
    gateway.unmount(Drive(
        "/dev/sdc1", "Data", "ntfs", 1, "/media/pideck/Data", True, "/dev/sda", DATA_UUID,
    ))
    assert not any(command[1:2] == ["power-off"] for command in runner.commands)
    runner.failures["power-off"] = "Error powering off drive: busy"
    gateway.unmount(Drive(
        "/dev/sdb1", "MEDIA", "exfat", 1, "/media/x", True, "/dev/sdb", MEDIA_UUID, "MEDIA",
    ))


def test_udisks_errors_are_readable_and_unknown_devices_rejected(tmp_path: Path) -> None:
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    runner.failures["mount"] = (
        "Error mounting /dev/sdb1: GDBus.Error:org.freedesktop.UDisks2.Error.NotAuthorizedCanObtain: "
        "Not authorized to perform operation"
    )
    gateway = UDisksStorageGateway(runner, media_root=media_root)
    with pytest.raises(StorageError, match="^Error mounting /dev/sdb1: Not authorized"):
        gateway.mount(gateway.list_drives()[0])
    with pytest.raises(StorageError, match="no longer available"):
        gateway.mount(Drive("/dev/sda2", "root", "ext4", 1, "/", False, "/dev/sda"))
    with pytest.raises(StorageError, match="Invalid drive"):
        gateway.unmount(Drive("/dev/../etc", "x", "ext4", 1, None, False))


def test_drive_preferences_round_trip(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    assert preferences.load() == frozenset()
    assert preferences.load_shared() == frozenset()
    preferences.remember(MEDIA_UUID)
    preferences.remember(DATA_UUID)
    assert preferences.load() == frozenset({MEDIA_UUID, DATA_UUID})
    saved = (tmp_path / "drives.yaml").read_text(encoding="utf-8")
    assert "version: 2" in saved and MEDIA_UUID in saved and DATA_UUID in saved
    preferences.forget(MEDIA_UUID)
    assert preferences.load() == frozenset({DATA_UUID})
    preferences.remember_shared(DATA_UUID)
    assert preferences.load_shared() == frozenset({DATA_UUID})
    preferences.forget(DATA_UUID)
    assert preferences.load() == frozenset()
    assert preferences.load_shared() == frozenset({DATA_UUID})
    preferences.forget_shared(DATA_UUID)
    assert preferences.load_shared() == frozenset()
    with pytest.raises(StorageError):
        preferences.remember("not a uuid!")
    (tmp_path / "drives.yaml").write_text("version: 1\nuuids:\n- A1B2-C3D4\n", encoding="utf-8")
    assert preferences.load() == frozenset({MEDIA_UUID})
    assert preferences.load_shared() == frozenset()
    (tmp_path / "drives.yaml").write_text("version: 3\nuuids: []\n", encoding="utf-8")
    with pytest.raises(StorageError, match="Invalid saved drive settings"):
        preferences.load()


def test_mount_and_unmount_update_remembered_uuids(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    gateway = UDisksStorageGateway(runner, preferences=preferences, media_root=media_root)
    drive = gateway.list_drives()[0]
    assert drive.uuid == MEDIA_UUID
    gateway.mount(drive)
    assert preferences.load() == frozenset({MEDIA_UUID})
    assert (media_root / "by-uuid" / MEDIA_UUID).is_symlink()
    gateway.unmount(drive)
    assert preferences.load() == frozenset()
    assert not (media_root / "by-uuid" / MEDIA_UUID).exists()
    assert not (media_root / "by-label" / "MEDIA").exists()


def test_mount_without_uuid_does_not_write_preferences(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    media_root = tmp_path / "media" / "pideck"
    document = {"blockdevices": [
        {"name": "sdf", "path": "/dev/sdf", "type": "disk", "rm": True, "hotplug": True, "children": [
            {"name": "sdf1", "path": "/dev/sdf1", "fstype": "ext4", "type": "part",
             "mountpoints": [None], "label": "NOUUID", "size": 100, "uuid": ""},
        ]},
    ]}
    gateway = UDisksStorageGateway(Runner(document), preferences=preferences, media_root=media_root)
    assert gateway.mount(gateway.list_drives()[0]) == "/media/pideck/MEDIA"
    assert not preferences.path.exists()
    assert not (media_root / "by-uuid").exists()


def test_list_drives_keeps_real_mount_when_stable_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media" / "pideck"
    gateway = UDisksStorageGateway(
        Runner(), which=lambda name: f"/usr/bin/{name}", media_root=media_root,
    )
    monkeypatch.setattr(
        UDisksStorageGateway,
        "_replace_symlink",
        staticmethod(lambda _link, _target: False),
    )
    drives = {drive.device: drive for drive in gateway.list_drives()}
    assert drives["/dev/sdc1"].mountpoint == "/media/pideck/Data"
    assert gateway.mount(drives["/dev/sdb1"]) == "/media/pideck/MEDIA"
    assert not (media_root / "by-uuid").exists()


def test_restore_saved_mounts_remembered_drives_and_publishes_stable_paths(
    tmp_path: Path,
) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    preferences.save(frozenset({MEDIA_UUID, DATA_UUID, "DEAD-BEEF"}))
    media_root = tmp_path / "media" / "pideck"
    runner = Runner()
    sleeps: list[float] = []
    gateway = UDisksStorageGateway(
        runner,
        which=lambda name: f"/usr/bin/{name}",
        preferences=preferences,
        sleep=sleeps.append,
        media_root=media_root,
    )
    gateway.restore_saved()
    mounts = [command for command in runner.commands if command[:2] == ["udisksctl", "mount"]]
    assert mounts == [["udisksctl", "mount", "--no-user-interaction", "--block-device", "/dev/sdb1"]]
    assert preferences.load() == frozenset({MEDIA_UUID, DATA_UUID, "DEAD-BEEF"})
    assert sleeps == []
    assert os.readlink(media_root / "by-uuid" / DATA_UUID) == "/media/pideck/Data"
    assert os.readlink(media_root / "by-uuid" / MEDIA_UUID) == "/media/pideck/MEDIA"


def test_restore_saved_retries_when_drives_are_still_probing(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    preferences.remember(MEDIA_UUID)
    media_root = tmp_path / "media" / "pideck"
    empty = {"blockdevices": []}
    runner = Runner(empty)
    lists = {"count": 0}

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "lsblk":
            lists["count"] += 1
            if lists["count"] >= 2:
                runner.document = LSBLK
        return runner(command)

    sleeps: list[float] = []
    gateway = UDisksStorageGateway(
        run,
        which=lambda name: f"/usr/bin/{name}",
        preferences=preferences,
        sleep=sleeps.append,
        media_root=media_root,
    )
    gateway.restore_saved()
    assert sleeps == [3.0]
    assert any(command[:2] == ["udisksctl", "mount"] for command in runner.commands)


def test_restore_saved_continues_after_one_mount_failure(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    second = "BBBB-CCCC"
    media_root = tmp_path / "media" / "pideck"
    document = {
        "blockdevices": [
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "fstype": "exfat", "type": "part",
                 "mountpoints": [None], "label": "MEDIA", "size": 1, "uuid": MEDIA_UUID},
            ]},
            {"name": "sdg", "path": "/dev/sdg", "type": "disk", "rm": False, "hotplug": False, "children": [
                {"name": "sdg1", "path": "/dev/sdg1", "fstype": "ext4", "type": "part",
                 "mountpoints": [None], "label": "MORE", "size": 1, "uuid": second},
            ]},
        ],
    }
    preferences.save(frozenset({MEDIA_UUID, second}))

    class Selective(Runner):
        def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
            self.commands.append(command)
            action = command[1] if command[0] == "udisksctl" else command[0]
            if action == "mount" and command[-1] == "/dev/sdb1":
                return subprocess.CompletedProcess(command, 1, "", "Error mounting /dev/sdb1: busy")
            if command[0] == "lsblk":
                return subprocess.CompletedProcess(command, 0, json.dumps(self.document), "")
            if action == "mount":
                return subprocess.CompletedProcess(
                    command, 0, f"Mounted {command[-1]} at /media/pideck/MORE\n", "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

    selective = Selective(document)
    gateway = UDisksStorageGateway(
        selective,
        which=lambda name: f"/usr/bin/{name}",
        preferences=preferences,
        sleep=lambda _: None,
        media_root=media_root,
    )
    gateway.restore_saved()
    mounts = [command[-1] for command in selective.commands if command[:2] == ["udisksctl", "mount"]]
    assert mounts == ["/dev/sdb1", "/dev/sdg1"]


def test_duplicate_filesystem_labels_skip_by_label_symlink(tmp_path: Path) -> None:
    media_root = tmp_path / "media" / "pideck"
    first = "AAAA-1111"
    second = "BBBB-2222"
    document = {
        "blockdevices": [
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "fstype": "exfat", "type": "part",
                 "mountpoints": ["/media/pideck/SHARE"], "label": "SHARE", "size": 1, "uuid": first},
            ]},
            {"name": "sdc", "path": "/dev/sdc", "type": "disk", "rm": True, "hotplug": True, "children": [
                {"name": "sdc1", "path": "/dev/sdc1", "fstype": "exfat", "type": "part",
                 "mountpoints": ["/media/pideck/SHARE1"], "label": "SHARE", "size": 1, "uuid": second},
            ]},
        ],
    }
    gateway = UDisksStorageGateway(Runner(document), media_root=media_root)
    drives = gateway.list_drives()
    assert [drive.mountpoint for drive in drives] == [
        str(media_root / "by-uuid" / first),
        str(media_root / "by-uuid" / second),
    ]
    assert os.readlink(media_root / "by-uuid" / first) == "/media/pideck/SHARE"
    assert os.readlink(media_root / "by-uuid" / second) == "/media/pideck/SHARE1"
    assert not (media_root / "by-label" / "SHARE").exists()


def test_format_size_uses_decimal_units() -> None:
    assert format_size(512) == "512 B"
    assert format_size(32_000_000_000) == "32.0 GB"
    assert format_size(2_000_000_000_000) == "2.0 TB"


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide an offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class Storage:
    """In-memory storage gateway."""

    def __init__(self, available: bool = True, drives: dict[str, Drive] | None = None) -> None:
        self.is_available = available
        self.drives = drives or {
            "/dev/sdb1": Drive(
                "/dev/sdb1", "MEDIA", "exfat", 32_000_000_000, None, True, "/dev/sdb", MEDIA_UUID, "MEDIA",
            ),
        }
        self.fail: str | None = None

    def available(self) -> bool:
        return self.is_available

    def list_drives(self) -> tuple[Drive, ...]:
        return tuple(self.drives.values())

    def mount(self, drive: Drive) -> str:
        if self.fail:
            raise StorageError(self.fail)
        path = f"/media/pideck/by-uuid/{drive.uuid or 'MEDIA'}"
        self.drives[drive.device] = Drive(**{**_fields(drive), "mountpoint": path})
        return path

    def unmount(self, drive: Drive) -> None:
        self.drives[drive.device] = Drive(**{**_fields(drive), "mountpoint": None})


class Shares:
    """In-memory share gateway."""

    def __init__(self, available: bool = True) -> None:
        self.is_available = available
        self.shared: set[str] = set()
        self.released: list[str] = []
        self.fail: str | None = None

    def available(self) -> bool:
        return self.is_available

    def is_shared(self, uuid: str | None) -> bool:
        return uuid is not None and uuid in self.shared

    def share_name(self, drive: Drive) -> str:
        return drive.filesystem_label or drive.label

    def share(self, drive: Drive) -> str:
        from pideck.application.ports.sharing import ShareError

        if self.fail:
            raise ShareError(self.fail)
        assert drive.uuid is not None
        self.shared.add(drive.uuid)
        return f"smb://pideck/{self.share_name(drive)}"

    def unshare(self, drive: Drive) -> None:
        if drive.uuid is not None:
            self.shared.discard(drive.uuid)

    def release(self, drive: Drive) -> None:
        if drive.uuid is not None:
            self.released.append(drive.uuid)

    def restore_shared(self) -> None:
        return None

    def ensure_preferred(self, drive: Drive) -> str | None:
        if self.is_shared(drive.uuid):
            return self.share(drive)
        return None


def _fields(drive: Drive) -> dict:
    return {name: getattr(drive, name) for name in Drive.__slots__}


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Drive operation did not finish")


def test_drives_window_opens_options_on_single_click(application: QApplication) -> None:
    parent = QWidget()
    window = DrivesWindow(Storage(), DEFAULT_THEME, "en", parent, shares=Shares())
    window.show()
    _wait(application, lambda: window.drives.count() == 1 and window.drives.isEnabled())
    assert window.dialog is None
    window.drives.itemClicked.emit(window.drives.item(0))
    application.processEvents()
    assert window.dialog is not None
    assert window.dialog.actions() == ("mount",)
    assert window.dialog.isVisible()
    assert not window.dialog.buttons[0].autoDefault()
    window.dialog.reject()
    window.close()
    parent.close()


def test_drives_window_enter_does_not_auto_mount(application: QApplication) -> None:
    """Enter that opens the menu must not also press Mount via Qt default-button handling."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    parent = QWidget()
    storage = Storage()
    window = DrivesWindow(storage, DEFAULT_THEME, "en", parent, shares=Shares())
    window.show()
    _wait(application, lambda: window.drives.count() == 1 and window.drives.isEnabled())
    window.drives.setFocus()
    window.drives.itemActivated.emit(window.drives.item(0))
    application.processEvents()
    dialog = window.dialog
    assert dialog is not None and dialog.isVisible()
    assert not dialog.buttons[0].autoDefault() and not dialog.buttons[0].isDefault()
    QApplication.sendEvent(
        dialog.buttons[0],
        QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
    )
    application.processEvents()
    assert window.dialog is dialog and dialog.isVisible()
    assert not storage.drives["/dev/sdb1"].mounted
    dialog.reject()
    window.close()
    parent.close()


def _row_widget(window: DrivesWindow, row: int = 0) -> DriveRowWidget:
    item = window.drives.item(row)
    widget = window.drives.itemWidget(item)
    assert isinstance(widget, DriveRowWidget)
    return widget


def test_drives_window_mounts_and_unmounts_with_controller(application: QApplication) -> None:
    from pideck.application.drive_items import DriveAction

    parent = QWidget()
    storage = Storage()
    shares = Shares()
    window = DrivesWindow(storage, DEFAULT_THEME, "en", parent, shares=shares)
    window.show()
    _wait(application, lambda: window.drives.count() == 1 and window.drives.isEnabled())
    assert window.drives.item(0).text() == "MEDIA · 32.0 GB · exfat"
    assert not _row_widget(window).mounted_icon.isVisible()
    assert not _row_widget(window).shared_icon.isVisible()
    window.drives.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window.dialog is not None
    assert window.dialog.actions() == ("mount",)
    assert "not mounted" in window.dialog.mount_state.text()
    window.dialog.choose(DriveAction.MOUNT)
    _wait(application, lambda: storage.drives["/dev/sdb1"].mounted and window.drives.isEnabled())
    assert window.drives.item(0).text() == "MEDIA · 32.0 GB · exfat"
    assert _row_widget(window).mounted_icon.isVisible()
    assert not _row_widget(window).shared_icon.isVisible()
    window.drives.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window.dialog is not None
    assert "/media/pideck/by-uuid/" in window.dialog.mount_state.text()
    assert "unmount" in window.dialog.actions()
    assert "share" in window.dialog.actions()
    window.dialog.choose(DriveAction.SHARE)
    _wait(application, lambda: MEDIA_UUID in shares.shared and window.drives.isEnabled())
    assert "smb://" not in window.status.text()
    assert _row_widget(window).shared_icon.isVisible()
    window.drives.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    application.processEvents()
    assert window.dialog is not None
    assert "shared as MEDIA" in window.dialog.share_state.text()
    assert "smb://" in window.dialog.share_url_label.text()
    assert "/MEDIA" in window.dialog.share_url_label.text()
    window.dialog.choose(DriveAction.UNMOUNT)
    _wait(application, lambda: not storage.drives["/dev/sdb1"].mounted and window.drives.isEnabled())
    assert not _row_widget(window).mounted_icon.isVisible()
    assert not _row_widget(window).shared_icon.isVisible()
    assert shares.released == [MEDIA_UUID]
    assert MEDIA_UUID in shares.shared
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window.isVisible()
    parent.close()


def test_drives_window_share_details_live_in_dialog(application: QApplication) -> None:
    from pideck.application.drive_items import DriveAction

    parent = QWidget()
    other = "BBBB-CCCC"
    storage = Storage(drives={
        "/dev/sdb1": Drive(
            "/dev/sdb1", "MEDIA", "exfat", 1, "/media/pideck/by-uuid/A1B2-C3D4", True,
            "/dev/sdb", MEDIA_UUID, "MEDIA",
        ),
        "/dev/sdc1": Drive(
            "/dev/sdc1", "DATA", "ext4", 1, "/media/pideck/by-uuid/BBBB-CCCC", False,
            "/dev/sdc", other, "DATA",
        ),
    })
    shares = Shares()
    window = DrivesWindow(storage, DEFAULT_THEME, "en", parent, shares=shares)
    window.show()
    _wait(application, lambda: window.drives.count() == 2 and window.drives.isEnabled())
    window.drives.setCurrentRow(0)
    window.activate_selected()
    application.processEvents()
    window.dialog.choose(DriveAction.SHARE)
    _wait(application, lambda: MEDIA_UUID in shares.shared and window.drives.isEnabled())
    assert "smb://" not in window.status.text()
    assert _row_widget(window, 0).shared_icon.isVisible()
    assert not _row_widget(window, 1).shared_icon.isVisible()
    window.drives.setCurrentRow(1)
    window.activate_selected()
    application.processEvents()
    assert window.dialog is not None
    assert "shared as" not in window.dialog.share_state.text()
    assert not window.dialog.share_url_label.isVisible()
    window.dialog.reject()
    application.processEvents()
    window.drives.setCurrentRow(0)
    window.activate_selected()
    application.processEvents()
    assert window.dialog is not None
    assert "shared as MEDIA" in window.dialog.share_state.text()
    assert "smb://" in window.dialog.share_url_label.text()
    window.close()
    parent.close()

def test_drives_window_reports_errors_and_old_images(application: QApplication) -> None:
    from pideck.application.drive_items import DriveAction

    parent = QWidget()
    storage = Storage()
    storage.fail = "Not authorized"
    window = DrivesWindow(storage, DEFAULT_THEME, "de", parent, shares=Shares())
    window.show()
    _wait(application, lambda: window.drives.isEnabled())
    window.activate_selected()
    application.processEvents()
    assert window.dialog is not None
    window.dialog.choose(DriveAction.MOUNT)
    _wait(application, lambda: window.drives.isEnabled() and "Not authorized" in window.status.text())
    assert window.status.property("error") is True
    window.close()
    old = DrivesWindow(Storage(available=False), DEFAULT_THEME, "en", parent)
    old.show()
    application.processEvents()
    assert "newer PiDeck image" in old.status.text()
    assert not old.drives.isEnabled() and old.back.isEnabled()
    old.close()
    parent.close()


def test_system_page_offers_drives(application: QApplication) -> None:
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    assert window._drives_row in window._page_controls[window._system_page]
    window.apply_language("de")
    assert window._drives_labels[0].text() == "Laufwerke"
    window.close()
