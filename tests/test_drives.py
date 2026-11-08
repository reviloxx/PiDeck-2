"""Tests for the udisks2 storage gateway and the Drives settings window."""

import json
import os
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.storage import Drive, StorageError
from pideck.infrastructure.config.defaults import DEFAULT_THEME
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.udisks_storage import DrivePreferences, UDisksStorageGateway, parse_drives
from pideck.presentation.qt.drives_window import DrivesWindow, format_size
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
        "/dev/sdb1", "MEDIA", "exfat", 32_000_000_000, None, True, "/dev/sdb", MEDIA_UUID,
    )
    assert hdd.label == "Data HDD (sdc1)"
    assert hdd.uuid == DATA_UUID
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


def test_mount_uses_udisks_without_interaction() -> None:
    runner = Runner()
    gateway = UDisksStorageGateway(runner, which=lambda name: f"/usr/bin/{name}")
    assert gateway.available()
    drive = gateway.list_drives()[0]
    assert gateway.mount(drive) == "/media/pideck/MEDIA"
    assert runner.commands[-1] == ["udisksctl", "mount", "--no-user-interaction", "--block-device", "/dev/sdb1"]


def test_unmounting_removable_drive_powers_off_its_disk() -> None:
    runner = Runner()
    gateway = UDisksStorageGateway(runner, which=lambda name: None)
    assert not gateway.available()
    drive = Drive("/dev/sdb1", "MEDIA", "exfat", 1, "/media/pideck/MEDIA", True, "/dev/sdb")
    gateway.unmount(drive)
    udisks = [command for command in runner.commands if command[0] == "udisksctl"]
    assert udisks == [
        ["udisksctl", "unmount", "--no-user-interaction", "--block-device", "/dev/sdb1"],
        ["udisksctl", "power-off", "--no-user-interaction", "--block-device", "/dev/sdb"],
    ]


def test_internal_drive_is_not_powered_off_and_power_off_failure_is_not_fatal() -> None:
    runner = Runner()
    gateway = UDisksStorageGateway(runner)
    # A stale snapshot claiming the internal disk is removable must not power it off.
    gateway.unmount(Drive("/dev/sdc1", "Data", "ntfs", 1, "/media/pideck/Data", True, "/dev/sda"))
    assert not any(command[1:2] == ["power-off"] for command in runner.commands)
    runner.failures["power-off"] = "Error powering off drive: busy"
    gateway.unmount(Drive("/dev/sdb1", "MEDIA", "exfat", 1, "/media/x", True, "/dev/sdb"))


def test_udisks_errors_are_readable_and_unknown_devices_rejected() -> None:
    runner = Runner()
    runner.failures["mount"] = (
        "Error mounting /dev/sdb1: GDBus.Error:org.freedesktop.UDisks2.Error.NotAuthorizedCanObtain: "
        "Not authorized to perform operation"
    )
    gateway = UDisksStorageGateway(runner)
    with pytest.raises(StorageError, match="^Error mounting /dev/sdb1: Not authorized"):
        gateway.mount(gateway.list_drives()[0])
    with pytest.raises(StorageError, match="no longer available"):
        gateway.mount(Drive("/dev/sda2", "root", "ext4", 1, "/", False, "/dev/sda"))
    with pytest.raises(StorageError, match="Invalid drive"):
        gateway.unmount(Drive("/dev/../etc", "x", "ext4", 1, None, False))


def test_drive_preferences_round_trip(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    assert preferences.load() == frozenset()
    preferences.remember(MEDIA_UUID)
    preferences.remember(DATA_UUID)
    assert preferences.load() == frozenset({MEDIA_UUID, DATA_UUID})
    saved = (tmp_path / "drives.yaml").read_text(encoding="utf-8")
    assert "version: 1" in saved and MEDIA_UUID in saved and DATA_UUID in saved
    preferences.forget(MEDIA_UUID)
    assert preferences.load() == frozenset({DATA_UUID})
    with pytest.raises(StorageError):
        preferences.remember("not a uuid!")
    (tmp_path / "drives.yaml").write_text("version: 2\nuuids: []\n", encoding="utf-8")
    with pytest.raises(StorageError, match="Invalid saved drive settings"):
        preferences.load()


def test_mount_and_unmount_update_remembered_uuids(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    runner = Runner()
    gateway = UDisksStorageGateway(runner, preferences=preferences)
    drive = gateway.list_drives()[0]
    assert drive.uuid == MEDIA_UUID
    gateway.mount(drive)
    assert preferences.load() == frozenset({MEDIA_UUID})
    gateway.unmount(drive)
    assert preferences.load() == frozenset()


def test_mount_without_uuid_does_not_write_preferences(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    document = {"blockdevices": [
        {"name": "sdf", "path": "/dev/sdf", "type": "disk", "rm": True, "hotplug": True, "children": [
            {"name": "sdf1", "path": "/dev/sdf1", "fstype": "ext4", "type": "part",
             "mountpoints": [None], "label": "NOUUID", "size": 100, "uuid": ""},
        ]},
    ]}
    gateway = UDisksStorageGateway(Runner(document), preferences=preferences)
    gateway.mount(gateway.list_drives()[0])
    assert not preferences.path.exists()


def test_restore_saved_mounts_remembered_drives(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    preferences.save(frozenset({MEDIA_UUID, DATA_UUID, "DEAD-BEEF"}))
    runner = Runner()
    sleeps: list[float] = []
    gateway = UDisksStorageGateway(
        runner, which=lambda name: f"/usr/bin/{name}", preferences=preferences, sleep=sleeps.append,
    )
    gateway.restore_saved()
    mounts = [command for command in runner.commands if command[:2] == ["udisksctl", "mount"]]
    assert mounts == [["udisksctl", "mount", "--no-user-interaction", "--block-device", "/dev/sdb1"]]
    assert preferences.load() == frozenset({MEDIA_UUID, DATA_UUID, "DEAD-BEEF"})
    assert sleeps == []


def test_restore_saved_retries_when_drives_are_still_probing(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    preferences.remember(MEDIA_UUID)
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
        run, which=lambda name: f"/usr/bin/{name}", preferences=preferences, sleep=sleeps.append,
    )
    gateway.restore_saved()
    assert sleeps == [3.0]
    assert any(command[:2] == ["udisksctl", "mount"] for command in runner.commands)


def test_restore_saved_continues_after_one_mount_failure(tmp_path: Path) -> None:
    preferences = DrivePreferences(tmp_path / "drives.yaml")
    second = "BBBB-CCCC"
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
        selective, which=lambda name: f"/usr/bin/{name}", preferences=preferences, sleep=lambda _: None,
    )
    gateway.restore_saved()
    mounts = [command[-1] for command in selective.commands if command[:2] == ["udisksctl", "mount"]]
    assert mounts == ["/dev/sdb1", "/dev/sdg1"]


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

    def __init__(self, available: bool = True) -> None:
        self.is_available = available
        self.drives = {
            "/dev/sdb1": Drive("/dev/sdb1", "MEDIA", "exfat", 32_000_000_000, None, True, "/dev/sdb"),
        }
        self.fail: str | None = None

    def available(self) -> bool:
        return self.is_available

    def list_drives(self) -> tuple[Drive, ...]:
        return tuple(self.drives.values())

    def mount(self, drive: Drive) -> str:
        if self.fail:
            raise StorageError(self.fail)
        self.drives[drive.device] = Drive(**{**_fields(drive), "mountpoint": "/media/pideck/MEDIA"})
        return "/media/pideck/MEDIA"

    def unmount(self, drive: Drive) -> None:
        self.drives[drive.device] = Drive(**{**_fields(drive), "mountpoint": None})


def _fields(drive: Drive) -> dict:
    return {name: getattr(drive, name) for name in Drive.__slots__}


def _wait(application: QApplication, condition) -> None:
    for _ in range(200):
        application.processEvents()
        if condition():
            return
        QTest.qWait(5)
    pytest.fail("Drive operation did not finish")


def test_drives_window_mounts_and_unmounts_with_controller(application: QApplication) -> None:
    parent = QWidget()
    storage = Storage()
    window = DrivesWindow(storage, DEFAULT_THEME, "en", parent)
    window.show()
    _wait(application, lambda: window.drives.count() == 1 and window.drives.isEnabled())
    assert "not mounted" in window.drives.item(0).text()
    assert window.toggle.text() == "Mount"
    window.drives.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: storage.drives["/dev/sdb1"].mounted and window.drives.isEnabled())
    assert "/media/pideck/MEDIA" in window.drives.item(0).text()
    assert "/media/pideck/MEDIA" in window.status.text()
    assert window.toggle.text() == "Unmount"
    window.toggle.setFocus()
    window.handle_input(InputEvent(InputAction.ACTIVATE, "gamepad"))
    _wait(application, lambda: not storage.drives["/dev/sdb1"].mounted and window.drives.isEnabled())
    assert "removed safely" in window.status.text()
    window.handle_input(InputEvent(InputAction.BACK, "gamepad"))
    assert not window.isVisible()
    parent.close()


def test_drives_window_reports_errors_and_old_images(application: QApplication) -> None:
    parent = QWidget()
    storage = Storage()
    storage.fail = "Not authorized"
    window = DrivesWindow(storage, DEFAULT_THEME, "de", parent)
    window.show()
    _wait(application, lambda: window.drives.isEnabled())
    window.toggle.click()
    _wait(application, lambda: window.drives.isEnabled() and "Not authorized" in window.status.text())
    assert window.status.property("error") is True
    window.close()
    old = DrivesWindow(Storage(available=False), DEFAULT_THEME, "en", parent)
    old.show()
    application.processEvents()
    assert "newer PiDeck image" in old.status.text()
    assert not old.toggle.isEnabled() and old.back.isEnabled()
    old.close()
    parent.close()


def test_system_page_offers_drives(application: QApplication) -> None:
    configuration = YamlConfigurationParser().parse(configuration_document())
    window = SettingsWindow(configuration, DEFAULT_THEME, Path.cwd(), system_enabled=True)
    assert window._drives_row in window._page_controls[window._system_page]
    window.apply_language("de")
    assert window._drives_labels[0].text() == "Laufwerke"
    window.close()
