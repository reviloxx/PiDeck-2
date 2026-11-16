"""Remote support key planting and catalog hiding."""

from pathlib import Path

from pideck.application.app_management import ApplicationManagementService
from pideck.domain.catalog import CatalogEntry, InstallMethod, InstallSpec
from pideck.domain.configuration import ApplicationDefinition
from pideck.infrastructure.config.defaults import default_configuration
from pideck.infrastructure.remote_support import (
    SUPPORT_APP_ID,
    RemoteSupportService,
    RemoteSupportSnapshot,
    clear_authorized_keys,
    clock_looks_wrong,
    is_enabled,
    support_public_keys,
    write_authorized_keys,
)
from pideck.presentation.qt.localization import format_remote_support_status


def test_support_public_keys_are_loaded_from_assets(tmp_path: Path) -> None:
    """Release assets carry the support authorized_keys file."""
    keys_file = tmp_path / "assets" / "support" / "ssh-authorized-keys"
    keys_file.parent.mkdir(parents=True)
    keys_file.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKs test\n", encoding="utf-8")
    assert support_public_keys(tmp_path / "config") == (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKs test",
    )


def test_authorized_keys_round_trip(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Enable writes the marker key; disable removes only managed lines."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pideck.infrastructure.remote_support.appliance_home", lambda: home)
    write_authorized_keys(("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKs pideck-tv-support",))
    assert is_enabled()
    text = (home / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
    assert "pideck-tv-support" in text
    clear_authorized_keys()
    assert not is_enabled()


def test_clock_looks_wrong_detects_epoch() -> None:
    """TLS-breaking clocks before 2024 are rejected."""
    assert clock_looks_wrong(floor_epoch=10**12) is True
    assert clock_looks_wrong(floor_epoch=0) is False


def test_status_snapshot_and_localized_line_omit_angle_brackets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Settings shows ssh user@ip without placeholder brackets, in the UI language."""
    monkeypatch.setattr(
        "pideck.infrastructure.remote_support.is_enabled", lambda: True,
    )
    monkeypatch.setattr(
        "pideck.infrastructure.remote_support.ssh_listening", lambda: True,
    )
    monkeypatch.setattr(
        "pideck.infrastructure.remote_support.local_ipv4_addresses",
        lambda: ("192.168.0.11",),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.remote_support._uid_name", lambda: "pideck",
    )
    monkeypatch.setattr(
        "pwd.getpwuid",
        lambda uid: type("Pw", (), {"pw_name": "pideck"})(),
    )
    service = RemoteSupportService(
        packages=type("P", (), {})(),  # type: ignore[arg-type]
        asset_root=Path("."),
    )
    snap = service.status_snapshot()
    assert snap == RemoteSupportSnapshot("on", "pideck", "192.168.0.11")
    english = format_remote_support_status("en", snap.kind, snap.user, snap.addresses)
    german = format_remote_support_status("de", snap.kind, snap.user, snap.addresses)
    assert english == "on · ssh pideck@192.168.0.11"
    assert german == "an · ssh pideck@192.168.0.11"
    assert "<" not in english and ">" not in english
    assert "<" not in german and ">" not in german


def test_remote_support_is_hidden_from_application_statuses() -> None:
    """The support catalog entry is not listed under Settings > Applications."""
    catalog = (
        CatalogEntry(
            application=ApplicationDefinition("kodi", "Kodi", "kodi"),
            install={"amd64": InstallSpec(method=InstallMethod.APT, packages=("kodi",))},
        ),
        CatalogEntry(
            application=ApplicationDefinition(SUPPORT_APP_ID, "Remote Support", "/usr/sbin/sshd"),
            install={"amd64": InstallSpec(method=InstallMethod.APT, packages=("openssh-server",))},
        ),
    )

    class Packages:
        def is_installed(self, spec):  # type: ignore[no-untyped-def]
            del spec
            return False

        def can_install(self, spec):  # type: ignore[no-untyped-def]
            del spec
            return True

        def can_remove(self, spec):  # type: ignore[no-untyped-def]
            del spec
            return False

        def needs_password(self, spec):  # type: ignore[no-untyped-def]
            del spec
            return False

    service = ApplicationManagementService(catalog, Packages(), "amd64")
    ids = [status.identifier for status in service.statuses(default_configuration())]
    assert ids == ["kodi"]
