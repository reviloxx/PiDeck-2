"""Tests for drive LAN sharing actions, Samba broker ops and the share gateway."""

import os
from pathlib import Path
import subprocess
import threading

import pytest

from pideck.application.drive_items import DriveAction, available_actions
from pideck.application.ports.sharing import ShareError
from pideck.application.ports.storage import Drive
from pideck.infrastructure import image_update_client
from pideck.infrastructure.image_update_client import ImageUpdateError
from pideck.infrastructure.image_update_daemon import UpdateServer
from pideck.infrastructure.image_update_store import ImageUpdateStore
from pideck.infrastructure.privileged_shares import ShareOperations, render_shares_conf
from pideck.infrastructure.samba_shares import (
    SambaShareGateway, export_path_for, share_name_for, smb_url,
)
from pideck.infrastructure.udisks_storage import DrivePreferences


MEDIA_UUID = "A1B2-C3D4"


def _drive(**overrides: object) -> Drive:
    values = {
        "device": "/dev/sdb1",
        "label": "MEDIA",
        "filesystem": "exfat",
        "size": 1,
        "mountpoint": "/media/pideck/by-uuid/A1B2-C3D4",
        "removable": True,
        "parent": "/dev/sdb",
        "uuid": MEDIA_UUID,
        "filesystem_label": "MEDIA",
    }
    values.update(overrides)
    return Drive(**values)  # type: ignore[arg-type]


def _media_tree(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a real udisks-style mount and a by-uuid symlink to it."""
    media_root = tmp_path / "media"
    real = media_root / "pideck" / "MEDIA"
    real.mkdir(parents=True)
    link = media_root / "pideck" / "by-uuid" / MEDIA_UUID
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    return real, link, str(media_root) + "/"


def test_available_actions_for_mount_share_and_busy_states() -> None:
    unmounted = _drive(mountpoint=None)
    mounted = _drive()
    assert available_actions(unmounted) == (DriveAction.MOUNT,)
    assert available_actions(mounted) == (DriveAction.UNMOUNT,)
    assert available_actions(mounted, share_available=True) == (
        DriveAction.UNMOUNT, DriveAction.SHARE,
    )
    assert available_actions(mounted, share_available=True, shared=True) == (
        DriveAction.UNMOUNT, DriveAction.STOP_SHARING,
    )
    assert available_actions(mounted, busy=True, share_available=True) == ()
    assert available_actions(_drive(uuid=None), share_available=True) == (DriveAction.UNMOUNT,)


def test_share_name_and_smb_url() -> None:
    assert share_name_for(_drive()) == "MEDIA"
    assert share_name_for(_drive(filesystem_label=None, label="Data HDD (sdc1)")) == "Data_HDD_sdc1"
    assert share_name_for(_drive(filesystem_label=None, label="???", uuid="ABCD-EF01")) == "ABCDEF01"
    assert share_name_for(_drive(label="SHARE1", filesystem_label="SHARE")) == "SHARE1"
    assert share_name_for(_drive(label="SHARE2", filesystem_label="SHARE")) == "SHARE2"
    assert smb_url("MEDIA", host="pideck") == "smb://pideck/MEDIA"


def test_export_path_resolves_by_uuid_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pideck.infrastructure.samba_shares as samba_shares

    real, link, media_root = _media_tree(tmp_path)
    monkeypatch.setattr(samba_shares, "_DATA_MOUNT_ROOTS", (media_root,))
    assert export_path_for(str(link)) == str(real.resolve())
    with pytest.raises(ShareError, match="missing"):
        export_path_for(str(tmp_path / "media" / "pideck" / "by-uuid" / "missing"))


def test_share_operations_write_conf_and_reject_unsafe_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pideck.infrastructure.privileged_shares as privileged_shares

    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    real, link, media_root = _media_tree(tmp_path)
    monkeypatch.setattr(privileged_shares, "_DATA_MOUNT_ROOTS", (media_root,))
    shares = ShareOperations(root=tmp_path / "samba", runner=runner)
    assert shares.enable(MEDIA_UUID, str(link), "MEDIA") == {
        "uuid": MEDIA_UUID, "name": "MEDIA", "path": str(real.resolve()),
    }
    conf = (tmp_path / "samba" / "shares.conf").read_text(encoding="utf-8")
    assert "[MEDIA]" in conf and str(real.resolve()) in conf and "guest ok = yes" in conf
    assert "force user = pideck" in conf
    assert "by-uuid" not in conf
    assert commands == [["systemctl", "reload", "smbd.service"]]
    with pytest.raises(ValueError, match="data mount"):
        shares.enable(MEDIA_UUID, "/etc/passwd", "MEDIA")
    with pytest.raises(ValueError, match="Invalid share name"):
        shares.enable(MEDIA_UUID, str(real), "../evil")
    with pytest.raises(ValueError, match="missing"):
        shares.enable(MEDIA_UUID, str(tmp_path / "media" / "pideck" / "gone"), "MEDIA")
    shares.disable(MEDIA_UUID)
    assert "shares" in (tmp_path / "samba" / "shares.json").read_text(encoding="utf-8")
    assert "[MEDIA]" not in (tmp_path / "samba" / "shares.conf").read_text(encoding="utf-8")


def test_render_shares_conf_orders_by_uuid() -> None:
    text = render_shares_conf({
        "BBBB-0002": {"name": "Second", "path": "/media/pideck/by-uuid/BBBB-0002"},
        "AAAA-0001": {"name": "First", "path": "/media/pideck/by-uuid/AAAA-0001"},
    })
    assert text.index("[First]") < text.index("[Second]")


def test_samba_share_gateway_remembers_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pideck.infrastructure.samba_shares as samba_shares

    preferences = DrivePreferences(tmp_path / "drives.yaml")
    real, link, media_root = _media_tree(tmp_path)
    monkeypatch.setattr(samba_shares, "_DATA_MOUNT_ROOTS", (media_root,))
    calls: list[tuple[str, dict[str, str]]] = []

    def request(action: str, **fields: str) -> dict[str, object]:
        calls.append((action, fields))
        if action == "capabilities":
            return {"applications": 1, "shares": 1}
        return {"ok": True}

    mounted = _drive(mountpoint=str(link))
    gateway = SambaShareGateway(
        preferences=preferences,
        request=request,
        list_drives=lambda: (mounted,),
        hostname=lambda: "pideck",
    )
    assert gateway.available()
    assert gateway.share(mounted) == "smb://pideck/MEDIA"
    assert preferences.load_shared() == frozenset({MEDIA_UUID})
    assert ("share_enable", {
        "uuid": MEDIA_UUID, "path": str(real.resolve()), "name": "MEDIA",
    }) in [(action, fields) for action, fields in calls if action == "share_enable"]
    gateway.release(mounted)
    assert preferences.load_shared() == frozenset({MEDIA_UUID})
    gateway.unshare(mounted)
    assert preferences.load_shared() == frozenset()
    preferences.remember_shared(MEDIA_UUID)
    gateway.restore_shared()
    assert any(action == "share_enable" for action, _fields in calls[3:])


def test_samba_share_gateway_unavailable_without_capability() -> None:
    def request(action: str, **_fields: str) -> dict[str, object]:
        if action == "capabilities":
            return {"applications": 1}
        raise ImageUpdateError("unsupported")

    gateway = SambaShareGateway(request=request)
    assert not gateway.available()
    with pytest.raises(ShareError, match="newer PiDeck image"):
        gateway.share(_drive())


def test_broker_enables_and_disables_shares(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pideck.infrastructure.privileged_shares as privileged_shares

    root = tmp_path / "image"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/1.0.0")
    path = tmp_path / "service.sock"
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    real, link, media_root = _media_tree(tmp_path)
    monkeypatch.setattr(privileged_shares, "_DATA_MOUNT_ROOTS", (media_root,))
    shares = ShareOperations(root=tmp_path / "samba", runner=runner)
    broker = UpdateServer(
        path, ImageUpdateStore(root), allowed_uid=os.getuid(), shares=shares,
    )
    monkeypatch.setattr(image_update_client, "SOCKET_PATH", path)
    thread = threading.Thread(target=broker.serve_forever)
    thread.start()
    try:
        assert image_update_client.request(
            "share_enable", uuid=MEDIA_UUID, path=str(link), name="MEDIA",
        ) == {"uuid": MEDIA_UUID, "name": "MEDIA", "path": str(real.resolve())}
        with pytest.raises(ImageUpdateError, match="data mount"):
            image_update_client.request(
                "share_enable", uuid=MEDIA_UUID, path="/root/secret", name="MEDIA",
            )
        assert image_update_client.request("share_disable", uuid=MEDIA_UUID) == {"uuid": MEDIA_UUID}
        assert commands and commands[0] == ["systemctl", "reload", "smbd.service"]
    finally:
        broker.shutdown()
        broker.server_close()
        thread.join()
