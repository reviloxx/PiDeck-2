"""Tests for x86 image build inputs without privileged disk operations."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from image.x86 import build as x86_build
from image.x86.configure_apps import configure
from pideck.infrastructure.config.parser import YamlConfigurationParser


ROOT = Path(__file__).resolve().parents[1]


def test_preflight_rejects_non_root_without_creating_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unprivileged builds fail before allocating or modifying a disk image."""
    monkeypatch.setattr(x86_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(x86_build.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(x86_build.os, "geteuid", lambda: 1000)
    directory = tmp_path / "image"
    with pytest.raises(RuntimeError, match="root"):
        x86_build.build(directory)
    assert not directory.exists()


def test_preflight_reports_missing_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report required host tools instead of beginning an incomplete build."""
    monkeypatch.setattr(x86_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(x86_build.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(x86_build.os, "geteuid", lambda: 0)
    monkeypatch.setattr(x86_build.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="debootstrap"):
        x86_build.build(tmp_path / "image")
    assert not (tmp_path / "image").exists()


def test_preflight_requires_debian_archive_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject missing archive keys before starting an unsigned bootstrap."""
    monkeypatch.setattr(x86_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(x86_build.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(x86_build.os, "geteuid", lambda: 0)
    monkeypatch.setattr(x86_build.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(x86_build, "DEBIAN_KEYRING", tmp_path / "missing.gpg")
    with pytest.raises(RuntimeError, match="signing keyring"):
        x86_build.build(tmp_path / "image")
    assert not (tmp_path / "image").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_output_directory_is_returned_to_sudo_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    """Hand outputs back only after the build completes or its mounts unwind."""
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    ownership: list[tuple[Path, int, int, bool]] = []
    monkeypatch.setattr(
        x86_build.os, "chown",
        lambda path, uid, gid, *, follow_symlinks: ownership.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
    )
    output = tmp_path / "image"

    def create_output() -> None:
        with x86_build._output_directory(output):
            (output / "disk.img").touch()
            if fail:
                raise RuntimeError("build failed")

    if fail:
        with pytest.raises(RuntimeError, match="build failed"):
            create_output()
    else:
        create_output()
    assert ownership == [
        (output / "disk.img", 1234, 5678, False),
        (output, 1234, 5678, False),
    ]
    assert output.stat().st_mode & 0o777 == 0o700


def test_output_ownership_is_not_changed_if_root_is_still_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not hand a mounted filesystem to an unprivileged user after a failure."""
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    monkeypatch.setattr(x86_build.os.path, "ismount", lambda path: True)
    chown_calls: list[Path] = []
    monkeypatch.setattr(
        x86_build.os, "chown",
        lambda path, *args, **kwargs: chown_calls.append(Path(path)),
    )
    with pytest.raises(RuntimeError, match="still mounted|while .* is mounted"):
        with x86_build._output_directory(tmp_path / "image"):
            pass
    assert not chown_calls


def test_output_ownership_is_not_changed_if_alias_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the output private when a bootstrap alias may still be mounted."""
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    chown_calls: list[Path] = []
    monkeypatch.setattr(
        x86_build.os, "chown",
        lambda path, *args, **kwargs: chown_calls.append(Path(path)),
    )
    with pytest.raises(x86_build._MountCleanupError, match="alias"):
        with x86_build._output_directory(tmp_path / "image"):
            raise x86_build._MountCleanupError("alias is still mounted")
    assert not chown_calls


def test_pkexec_output_owner_uses_invoking_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the authenticated user's primary group for pkexec builds."""
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.delenv("SUDO_GID", raising=False)
    monkeypatch.setenv("PKEXEC_UID", str(os.getuid()))
    account = x86_build.pwd.getpwuid(os.getuid())
    assert x86_build._invoking_user() == (account.pw_uid, account.pw_gid)


@pytest.mark.skipif(
    os.geteuid() != 0 or int(os.environ.get("PKEXEC_UID", "0")) == 0,
    reason="requires a non-root user invoking pytest through pkexec",
)
@pytest.mark.parametrize("fail", [False, True])
def test_real_privileged_output_ownership(tmp_path: Path, fail: bool) -> None:
    """Verify ownership and privacy using real privileged filesystem operations."""
    account = x86_build.pwd.getpwuid(int(os.environ["PKEXEC_UID"]))
    output = tmp_path / "image"

    def create_output() -> None:
        with x86_build._output_directory(output):
            (output / "disk.img").touch()
            if fail:
                raise RuntimeError("build failed")

    if fail:
        with pytest.raises(RuntimeError, match="build failed"):
            create_output()
    else:
        create_output()
    for path in (output, output / "disk.img"):
        assert (path.stat().st_uid, path.stat().st_gid) == (account.pw_uid, account.pw_gid)
    assert output.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("fail", [False, True])
def test_debootstrap_uses_and_releases_alias_for_space_in_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    """Keep actual files in the workspace while bootstrapping via a safe path."""
    root = tmp_path / "workspace with spaces" / "root"
    root.mkdir(parents=True)
    calls: list[tuple[str, ...]] = []
    make_directory = tempfile.mkdtemp
    monkeypatch.setattr(
        x86_build.tempfile, "mkdtemp",
        lambda **kwargs: make_directory(prefix="pideck-test-", dir=tmp_path),
    )

    def run(*command: str, capture: bool = False) -> str:
        calls.append(command)
        if command[0] == "debootstrap" and fail:
            raise RuntimeError("bootstrap failed")
        return ""

    monkeypatch.setattr(x86_build, "_run", run)
    if fail:
        with pytest.raises(RuntimeError, match="bootstrap failed"):
            x86_build._debootstrap(root)
    else:
        x86_build._debootstrap(root)
    mount, bootstrap, unmount = calls
    alias = Path(mount[-1])
    assert mount == ("mount", "--bind", str(root), str(alias))
    assert bootstrap[-2] == str(alias)
    assert not any(character.isspace() for character in str(alias))
    assert unmount == ("umount", str(alias))
    assert not alias.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="requires root for a real bind mount")
def test_real_bootstrap_alias_uses_workspace_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a real alias reaches the space-containing output and is unmounted."""
    root = tmp_path / "workspace with spaces" / "root"
    root.mkdir(parents=True)
    real_run = x86_build._run
    aliases: list[Path] = []

    def run(*command: str, capture: bool = False) -> str:
        if command[0] == "debootstrap":
            alias = Path(command[-2])
            assert os.path.ismount(alias)
            assert not any(character.isspace() for character in str(alias))
            aliases.append(alias)
            (alias / "marker").write_text("workspace data")
            return ""
        return real_run(*command, capture=capture)

    monkeypatch.setattr(x86_build, "_run", run)
    x86_build._debootstrap(root)
    assert (root / "marker").read_text() == "workspace data"
    assert len(aliases) == 1
    assert not aliases[0].exists()


def test_x86_runtime_inputs_match_shared_appliance(tmp_path: Path) -> None:
    """Install the shared session and configured apps in the expected locations."""
    x86_build._write_inputs(tmp_path, None)
    config = YamlConfigurationParser().load(tmp_path / "usr/share/pideck/config/pideck.yaml")
    packages = set((x86_build.INPUTS / "packages.txt").read_text().split())
    assert {app.executable for app in config.applications} <= packages
    assert {"bluez", "python3-pyside6.qtdbus", "pulseaudio-utils", "cec-utils", "inputattach"} <= packages
    assert (tmp_path / "etc/udev/rules.d/71-pideck-cec-attach.rules").is_file()
    assert (tmp_path / "etc/systemd/system/pideck-cec-attach@.service").is_file()
    assert "cec" in config.input.enabled_sources
    assert (tmp_path / "etc/udev/rules.d/70-pideck-cec.rules").read_text() == (
        x86_build.PI_FILES / "70-pideck-cec.rules"
    ).read_text()
    assert "firmware-nvidia-graphics" in packages
    assert (tmp_path / "usr/lib/pideck/python/pideck/bootstrap.py").is_file()
    metadata = tmp_path / "usr/lib/pideck/python/pideck/_build_info.json"
    assert json.loads(metadata.read_text())["built_at"]
    assert (tmp_path / "usr/share/pideck/config/window-modes.yaml").read_text() == (
        x86_build.PI_FILES / "window-modes.yaml"
    ).read_text()
    assert (tmp_path / "usr/lib/pideck-updater/python/pideck/infrastructure/image_update_daemon.py").is_file()
    assert (tmp_path / "usr/lib/pideck/pideck-getty").stat().st_mode & 0o111
    assert not (tmp_path / "usr/lib/pideck/current").exists()
    override = (tmp_path / "etc/systemd/system/getty@tty1.service.d/pideck.conf").read_text()
    assert "userconfig.service" not in override
    assert "PIDECK_NETWORK=1" in (tmp_path / "etc/profile.d/00-pideck-network.sh").read_text()
    policy = (tmp_path / "etc/polkit-1/rules.d/49-pideck-network.rules").read_text()
    assert 'subject.user === "pideck"' in policy
    assert "subject.local && subject.active" in policy
    assert "org.freedesktop.NetworkManager.network-control" in policy
    assert "org.freedesktop.policykit.exec" not in policy
    grub = (tmp_path / "etc/default/grub.d/90-pideck.cfg").read_text()
    assert "GRUB_TIMEOUT_STYLE=hidden\n" in grub
    assert "GRUB_TIMEOUT=0\n" in grub
    assert "GRUB_RECORDFAIL_TIMEOUT=0\n" in grub
    assert "quiet splash" in grub
    assert (tmp_path / "usr/share/plymouth/themes/pideck/pideck.script").is_file()
    assert {"plymouth", "plymouth-label"} <= packages


@pytest.mark.parametrize("nomachine", [False, True])
def test_x86_configuration_only_lists_installed_optional_apps(tmp_path: Path, nomachine: bool) -> None:
    """Include Steam Link and gate the player tile on its private package."""
    x86_build._write_inputs(tmp_path, None)
    path = tmp_path / "usr/share/pideck/config/pideck.yaml"
    configure(path, x86_build.INPUTS / "applications.yaml", nomachine=nomachine)
    config = YamlConfigurationParser().load(path)
    applications = {app.identifier: app for app in config.applications}
    assert applications["steam-link"].executable == "flatpak"
    assert "com.valvesoftware.SteamLink" in applications["steam-link"].arguments
    assert ("nomachine" in applications) is nomachine
    assert set(config.home.visible_applications) == set(applications)


def test_x86_application_definitions_are_configurable(tmp_path: Path) -> None:
    """Load application details from YAML rather than embedding them in code."""
    x86_build._write_inputs(tmp_path, None)
    path = tmp_path / "usr/share/pideck/config/pideck.yaml"
    definitions = tmp_path / "apps.yaml"
    definitions.write_text(
        "always:\n  - id: custom\n    name: Custom\n    executable: custom-player\n"
        "    arguments: []\n    profiles: []\n    preferred_input: gamepad\nnomachine: []\n",
        encoding="utf-8",
    )
    configure(path, definitions, nomachine=False)
    config = YamlConfigurationParser().load(path)
    assert any(app.identifier == "custom" and app.executable == "custom-player" for app in config.applications)
    assert "custom" in config.home.visible_applications


def test_x86_release_is_update_enabled_without_key(tmp_path: Path) -> None:
    """A tagged x86 image includes the versioned release without a key."""
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    x86_build._write_inputs(tmp_path, f"v{version}")
    root = tmp_path / "usr/lib/pideck"
    assert os.readlink(root / "current") == f"releases/{version}"
    assert (root / "current/python/pideck/bootstrap.py").is_file()
    assert not (tmp_path / "usr/share/pideck/update-public.pem").exists()


def test_private_player_checksum_is_required_before_creating_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unverified private packages before any privileged image work."""
    monkeypatch.setattr(x86_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(x86_build.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(x86_build.os, "geteuid", lambda: 0)
    monkeypatch.setattr(x86_build.shutil, "which", lambda name: "/usr/bin/" + name)
    keyring = tmp_path / "debian-archive-keyring.gpg"
    keyring.touch()
    monkeypatch.setattr(x86_build, "DEBIAN_KEYRING", keyring)
    player = tmp_path / "player.deb"
    player.write_bytes(b"not the expected package")
    with pytest.raises(ValueError, match="checksum"):
        x86_build.build(tmp_path / "image", player=player, player_sha256="0" * 64)
    assert not (tmp_path / "image").exists()


@pytest.mark.skipif(shutil.which("sgdisk") is None, reason="sgdisk is required for partition inspection")
def test_real_gpt_layout_on_sparse_image(tmp_path: Path) -> None:
    """Inspect actual GPT partitions without loop devices or root privileges."""
    image = tmp_path / "disk.img"
    with image.open("wb") as stream:
        stream.truncate(700 * 1024 * 1024)
    x86_build._partition(image)
    first = subprocess.check_output(["sgdisk", "--info=1", str(image)], text=True)
    second = subprocess.check_output(["sgdisk", "--info=2", str(image)], text=True)
    third = subprocess.check_output(["sgdisk", "--info=3", str(image)], text=True)
    assert "(BIOS boot partition)" in first
    assert "(EFI system partition)" in second
    assert "pideck-efi" in second
    assert "(Linux filesystem)" in third
    assert "pideck-root" in third


def test_failure_releases_mounts_and_loop_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rootfs setup unwinds mounts before detaching its own loop device."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(x86_build, "_check_host", lambda *args: None)
    monkeypatch.setattr(x86_build, "IMAGE_SIZE", 1024)

    def run(*command: str, capture: bool = False) -> str:
        calls.append(command)
        return "/dev/loop987" if command[:1] == ("losetup",) and capture else ""

    def fail(*args: object) -> None:
        raise RuntimeError("configuration failed")

    monkeypatch.setattr(x86_build, "_run", run)
    monkeypatch.setattr(x86_build, "_configure", fail)
    monkeypatch.setattr(Path, "is_block_device", lambda self: True)
    with pytest.raises(RuntimeError, match="configuration failed"):
        x86_build.build(tmp_path / "image")
    bootstrap = next(command for command in calls if command[0] == "debootstrap")
    assert calls.index(("udevadm", "settle", "--timeout=30")) < next(
        index for index, command in enumerate(calls) if command[0] == "mkfs.vfat"
    )
    assert "--force-check-gpg" in bootstrap
    assert f"--keyring={x86_build.DEBIAN_KEYRING}" in bootstrap
    cleanup = [command for command in calls if command[0] == "umount" or command[:2] == ("losetup", "--detach")]
    assert [Path(command[-1]).name for command in cleanup] == ["sys", "proc", "dev", "efi", "root", "loop987"]


@pytest.mark.parametrize("settle_fails", [False, True])
def test_missing_loop_partitions_abort_and_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settle_fails: bool,
) -> None:
    """A device failure must detach the loop without formatting either partition."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(x86_build, "_check_host", lambda *args: None)
    monkeypatch.setattr(x86_build, "IMAGE_SIZE", 1024)
    monkeypatch.setattr(Path, "is_block_device", lambda self: False)

    def run(*command: str, capture: bool = False) -> str:
        calls.append(command)
        if command[0] == "udevadm" and settle_fails:
            raise subprocess.CalledProcessError(1, command)
        return "/dev/loop987" if command[:1] == ("losetup",) and capture else ""

    monkeypatch.setattr(x86_build, "_run", run)
    error = subprocess.CalledProcessError if settle_fails else RuntimeError
    with pytest.raises(error):
        x86_build.build(tmp_path / "image")
    assert not any(command[0].startswith("mkfs.") for command in calls)
    assert calls[-1] == ("losetup", "--detach", "/dev/loop987")


def test_source_changes_abort_before_compression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not publish a manifest for inputs modified during an image build."""
    monkeypatch.setattr(x86_build, "_check_host", lambda *args: None)
    monkeypatch.setattr(x86_build, "IMAGE_SIZE", 1024)
    hashes = iter(({"source": "before"}, {"source": "after"}))
    monkeypatch.setattr(x86_build, "_input_hashes", lambda: next(hashes))
    commands: list[tuple[str, ...]] = []

    def run(*command: str, capture: bool = False) -> str:
        commands.append(command)
        return "/dev/loop987" if command[:1] == ("losetup",) and capture else ""

    def configure(root: Path, *args: object) -> None:
        manifest_dir = root / "usr/share/pideck"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "package-manifest.tsv").write_text("packages")
        (manifest_dir / "flatpak-manifest.tsv").write_text("flatpaks")

    monkeypatch.setattr(x86_build, "_run", run)
    monkeypatch.setattr(x86_build, "_configure", configure)
    monkeypatch.setattr(Path, "is_block_device", lambda self: True)
    with pytest.raises(RuntimeError, match="source inputs changed"):
        x86_build.build(tmp_path / "image")
    assert not any(command[0] == "xz" for command in commands)
    assert not (tmp_path / "image/SHA256SUMS").exists()


@pytest.mark.parametrize("name", ["pideck-getty", "pideck-grow-root"])
def test_x86_session_shell_is_valid(name: str) -> None:
    """Keep installed shell scripts syntactically valid."""
    subprocess.run(["sh", "-n", str(x86_build.INPUTS / name)], check=True)


def test_resize_service_is_scoped_to_the_image_root() -> None:
    """Require the labeled GPT partition before attempting any disk expansion."""
    script = (x86_build.INPUTS / "pideck-grow-root").read_text()
    assert 'PARTLABEL "$root")" != "pideck-root"' in script
    assert 'PARTN "$root")" != "3"' in script
    assert 'PTTYPE "$disk")" != "gpt"' in script
    assert 'growpart "$disk" 3' in script


@pytest.mark.parametrize(
    ("label", "grow_result", "expected", "should_resize"),
    [
        ("wrong-root", "success", 1, False),
        ("pideck-root", "success", 0, True),
        ("pideck-root", "nochange", 0, True),
        ("pideck-root", "failure", 1, False),
    ],
)
def test_resize_behavior_without_touching_real_disks(
    tmp_path: Path, label: str, grow_result: str, expected: int, should_resize: bool,
) -> None:
    """Only grow the checked root disk and tolerate an already full partition."""
    commands = tmp_path / "bin"
    commands.mkdir()
    scripts = {
        "findmnt": "echo /dev/fake3",
        "lsblk": (
            'test "$1" = -dnro || exit 1; case "$2:$3" in '
            'TYPE:/dev/fake3) echo part ;; PARTLABEL:/dev/fake3) echo "$LABEL" ;; '
            'PARTN:/dev/fake3) echo 3 ;; PKNAME:/dev/fake3) echo fake ;; '
            'TYPE:/dev/fake) echo disk ;; PTTYPE:/dev/fake) echo gpt ;; '
            '*) exit 1 ;; esac'
        ),
        "growpart": (
            'echo "$*" >> "$CALLS"; case "$GROW_RESULT" in '
            'nochange) echo "NOCHANGE: already full"; exit 1 ;; '
            'failure) echo error; exit 1 ;; esac'
        ),
        "resize2fs": 'echo "resize $*" >> "$CALLS"',
    }
    for name, body in scripts.items():
        path = commands / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["sh", str(x86_build.INPUTS / "pideck-grow-root")],
        env={
            **os.environ, "PATH": f"{commands}:/usr/bin:/bin", "CALLS": str(calls),
            "LABEL": label, "GROW_RESULT": grow_result,
        },
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == expected
    lines = calls.read_text().splitlines() if calls.exists() else []
    assert ("resize /dev/fake3" in lines) is should_resize
    assert all(line in {"/dev/fake 3", "resize /dev/fake3"} for line in lines)
