"""Tests for x86 image build inputs without privileged disk operations."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from image.x86 import build as x86_build
from pideck.infrastructure.config.catalog import load_catalog
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
    assert [app.identifier for app in config.applications] == ["kodi", "youtube"]
    youtube = next(app for app in config.applications if app.identifier == "youtube")
    assert youtube.arguments == ("run", "com.ktechpit.red", "--tv")
    catalog = load_catalog(ROOT / "assets/catalog/applications.yaml")
    youtube_catalog = next(item for item in catalog if item.identifier == "youtube")
    assert "--env=LIBVA_DRIVER_NAME=nvidia" in youtube_catalog.definition_for("amd64").arguments
    chrome = next(item for item in catalog if item.identifier == "chrome")
    assert "VaapiOnNvidiaGPUs" in "\x00".join(chrome.definition_for("amd64").arguments)
    assert (x86_build.INPUTS / "pideck-video-profile.sh").is_file()
    assert (x86_build.INPUTS / "chromium-pideck").is_file()
    assert {"kodi", "flatpak"} <= packages
    assert config.settings.language == "de"
    assert {
        "bluez", "python3-pyside6.qtdbus", "pulseaudio-utils",
        "cec-utils", "inputattach",
    } <= packages
    assert "alsa-utils" not in packages
    assert (tmp_path / "etc/udev/rules.d/71-pideck-cec-attach.rules").read_text() == (
        x86_build.PI_FILES / "71-pideck-cec-attach.rules"
    ).read_text()
    assert (tmp_path / "etc/systemd/system/pideck-cec-attach@.service").read_text() == (
        x86_build.PI_FILES / "pideck-cec-attach@.service"
    ).read_text()
    assert (tmp_path / "usr/share/pideck/cec_support/71-pideck-cec-attach.rules").is_file()
    assert not (ROOT / "src/pideck/infrastructure/cec_support").exists()
    assert not (tmp_path / "usr/lib/pideck/python/pideck/infrastructure/cec_support").exists()
    assert "cec" in config.input.enabled_sources
    assert (tmp_path / "etc/udev/rules.d/70-pideck-cec.rules").read_text() == (
        x86_build.PI_FILES / "70-pideck-cec.rules"
    ).read_text()
    assert not (tmp_path / "etc/udev/rules.d/91-pideck-oxygen-pulse.rules").exists()
    assert not (
        tmp_path / "usr/share/pulseaudio/alsa-mixer/profile-sets/pideck-oxygen.conf"
    ).exists()
    assert {"firmware-nvidia-graphics", "zstd"} <= packages
    assert (tmp_path / "usr/lib/pideck/python/pideck/bootstrap.py").is_file()
    metadata = tmp_path / "usr/lib/pideck/python/pideck/_build_info.json"
    assert json.loads(metadata.read_text())["built_at"]
    assert (tmp_path / "usr/share/pideck/config/window-modes.yaml").read_text() == (
        x86_build.PI_FILES / "window-modes.yaml"
    ).read_text()
    assert (tmp_path / "usr/share/pideck/openbox/rc.xml").read_text() == (
        x86_build.PI_FILES / "openbox/rc.xml"
    ).read_text()
    assert (tmp_path / "usr/lib/pideck-updater/python/pideck/infrastructure/image_update_daemon.py").is_file()
    assert (tmp_path / "usr/lib/pideck/pideck-getty").stat().st_mode & 0o111
    assert (tmp_path / "usr/lib/pideck/pideck-wait-drm").stat().st_mode & 0o111
    picom = (tmp_path / "usr/lib/pideck/picom.conf").read_text()
    assert picom == (
        x86_build.ROOT / "src/pideck/infrastructure/session/picom-vm.conf"
    ).read_text()
    xsession = (tmp_path / "usr/lib/pideck/pideck-xsession").read_text()
    assert "select_picom_config" in xsession
    assert "picom-nvidia.conf" not in xsession
    assert "/usr/lib/pideck/picom.conf" in xsession
    assert "openbox-rc.xml" in xsession
    assert "--power-off-on-shutdown" in xsession
    assert "--suspend-on-shutdown" not in xsession
    assert "--hibernate-on-shutdown" not in xsession
    assert 'name="Frame"' not in (tmp_path / "usr/share/pideck/openbox/rc.xml").read_text()
    from image.update_bundle import project_version

    version = project_version(x86_build.ROOT)
    assert os.readlink(tmp_path / "usr/lib/pideck/current") == f"releases/{version}"
    assert (tmp_path / f"usr/lib/pideck/releases/{version}/python/pideck/bootstrap.py").is_file()
    release_session = (
        tmp_path / f"usr/lib/pideck/releases/{version}/python/pideck/infrastructure/session"
    )
    assert (release_session / "picom.conf").is_file()
    assert (release_session / "picom-nvidia.conf").is_file()
    assert (release_session / "picom-vm.conf").is_file()
    assert (release_session / "openbox-rc.xml").is_file()
    override = (tmp_path / "etc/systemd/system/getty@tty1.service.d/pideck.conf").read_text()
    assert "userconfig.service" not in override
    assert "pideck-wait-drm" in override
    assert "udevadm settle" not in override
    assert "pideck-nvidia-load.service" in override
    assert "After=pideck-activate.service pideck-nvidia-load.service" in override
    assert "After=pideck-updater.service" not in override
    assert "PIDECK_NETWORK=1" in (tmp_path / "etc/profile.d/00-pideck-network.sh").read_text()
    policy = (tmp_path / "etc/polkit-1/rules.d/49-pideck-network.rules").read_text()
    assert 'subject.user === "pideck"' in policy
    assert "subject.local && subject.active" in policy
    assert "org.freedesktop.NetworkManager.network-control" in policy
    assert "org.freedesktop.policykit.exec" not in policy
    udisks = (tmp_path / "etc/polkit-1/rules.d/50-pideck-udisks.rules").read_text()
    assert 'subject.isInGroup("plugdev")' in udisks and "subject.local && subject.active" in udisks
    assert "org.freedesktop.udisks2.filesystem-mount" in udisks
    timezone = (tmp_path / "etc/polkit-1/rules.d/51-pideck-timezone.rules").read_text()
    assert 'subject.user === "pideck"' in timezone and "subject.local && subject.active" in timezone
    assert "org.freedesktop.timedate1.set-timezone" in timezone
    login1 = (tmp_path / "etc/polkit-1/rules.d/52-pideck-login1.rules").read_text()
    assert 'subject.user === "pideck"' in login1 and "subject.local && subject.active" in login1
    assert "org.freedesktop.login1.hibernate" in login1
    assert 'Option "XkbLayout" "de"' in (tmp_path / "etc/X11/xorg.conf.d/00-pideck-keyboard.conf").read_text()
    assert "UserspaceHID=true" in (tmp_path / "etc/bluetooth/input.conf").read_text()
    assert "FastConnectable=true" in (tmp_path / "etc/bluetooth/main.conf").read_text()
    assert "blacklist hid-nintendo" in (
        tmp_path / "etc/modprobe.d/pideck-blacklist-hid-nintendo.conf"
    ).read_text()
    assert "pideck-switch-pro-led" in (
        tmp_path / "etc/udev/rules.d/99-pideck-switch-pro-hidraw.rules"
    ).read_text()
    assert (tmp_path / "usr/lib/pideck/pideck-switch-pro-led").is_file()
    assert {"udisks2", "samba", "x11-xkb-utils", "intel-microcode"} <= packages
    assert (tmp_path / "etc/samba/smb.conf").read_text() == (x86_build.PI_FILES / "smb.conf").read_text()
    assert "Managed by PiDeck" in (tmp_path / "var/lib/pideck/samba/shares.conf").read_text()
    grub = (tmp_path / "etc/default/grub.d/90-pideck.cfg").read_text()
    assert "GRUB_TIMEOUT_STYLE=hidden\n" in grub
    assert "GRUB_TIMEOUT=0\n" in grub
    assert "GRUB_RECORDFAIL_TIMEOUT=0\n" in grub
    assert "quiet loglevel=3 systemd.show_status=false" in grub
    assert "splash" not in grub.split("GRUB_CMDLINE_LINUX_DEFAULT=", 1)[-1]
    assert "GRUB_TERMINAL=console\n" in grub
    nvidia_load = (tmp_path / "etc/systemd/system/pideck-nvidia-load.service").read_text()
    assert "modprobe nvidia-drm" in nvidia_load
    assert "WantedBy=sysinit.target" in nvidia_load
    assert "Before=sysinit.target" not in nvidia_load
    assert "After=systemd-udevd.service" in nvidia_load
    assert "local-fs.target" not in nvidia_load
    assert "plymouth-start.service" not in nvidia_load
    assert not (tmp_path / "etc/systemd/system/plymouth-start.service.d/pideck.conf").exists()
    modules_load = (tmp_path / "etc/modules-load.d/nvidia.conf").read_text()
    assert not any(
        line.strip() and not line.lstrip().startswith("#")
        for line in modules_load.splitlines()
    )
    assert "pideck-nvidia-load.service" in modules_load
    initramfs = (tmp_path / "etc/initramfs-tools/conf.d/pideck.conf").read_text()
    assert "RESUME=none" not in initramfs
    assert "pideck-hibernate-swap" in initramfs
    assert (tmp_path / "usr/lib/pideck/pideck-hibernate-swap").is_file()
    assert (tmp_path / "usr/lib/pideck/pideck-hibernate-swap").stat().st_mode & 0o111
    hibernate_unit = (tmp_path / "etc/systemd/system/pideck-hibernate-swap.service").read_text()
    assert "After=pideck-grow-root.service" in hibernate_unit
    assert "Before=getty@tty1.service" not in hibernate_unit
    assert "ExecStart=/usr/lib/pideck/pideck-hibernate-swap" in hibernate_unit
    assert (tmp_path / "etc/initramfs-tools/hooks/pideck-slim").stat().st_mode & 0o111
    nvidia_bottom = tmp_path / "etc/initramfs-tools/scripts/local-bottom/pideck-nvidia"
    assert nvidia_bottom.is_file()
    assert nvidia_bottom.stat().st_mode & 0o111
    assert "nvidia-drm" in nvidia_bottom.read_text()
    assert not (tmp_path / "usr/share/plymouth/themes/pideck/pideck.script").exists()
    assert "plymouth quit" not in (tmp_path / "usr/lib/pideck/pideck-xsession").read_text()
    assert "plymouth quit" not in (tmp_path / "usr/lib/pideck/pideck-session").read_text()
    assert not (tmp_path / "usr/lib/pideck/pideck-plymouth-handoff").exists()
    assert not (tmp_path / "etc/systemd/system/pideck-plymouth-handoff.service").exists()
    getty = (tmp_path / "etc/systemd/system/getty@tty1.service.d/pideck.conf").read_text()
    assert "Type=simple" in getty
    assert "TTYVTDisallocate" not in getty


def test_x86_image_uses_proprietary_nvidia_driver_with_video_decoding(tmp_path: Path) -> None:
    """Install the NVIDIA driver, DKMS build inputs and per-application decode settings."""
    packages = set((x86_build.INPUTS / "packages.txt").read_text().split())
    assert {
        "nvidia-driver", "nvidia-kernel-dkms", "linux-headers-amd64", "nvidia-vaapi-driver",
        "libnvcuvid1", "libcuda1", "vdpauinfo", "vainfo", "pciutils",
    } <= packages
    assert "nvidia-open-kernel-dkms" not in packages
    assert x86_build.APT_COMPONENTS.split() == ["main", "contrib", "non-free", "non-free-firmware"]
    x86_build._write_inputs(tmp_path, None)
    assert "options nvidia-drm modeset=1 fbdev=1" in (
        tmp_path / "etc/modprobe.d/pideck-nvidia.conf"
    ).read_text()
    profile = tmp_path / "etc/profile.d/10-pideck-video.sh"
    # /etc/profile sources profile.d in glob order; pideck.sh then execs the session.
    assert sorted([profile.name, "pideck.sh"])[0] == profile.name
    assert "LIBVA_DRIVER_NAME=nvidia" in profile.read_text()
    firefox = (tmp_path / "etc/firefox-esr/pideck-video.js").read_text()
    assert 'pref("media.ffmpeg.vaapi.enabled", true);' in firefox
    chromium_flags = (tmp_path / "etc/chromium.d/pideck-video").read_text()
    assert "AcceleratedVideoDecodeLinuxGL" in chromium_flags
    assert "VaapiOnNvidiaGPUs" in chromium_flags
    assert "UseChromeOSDirectVideoDecoder" in chromium_flags


@pytest.mark.parametrize("name", ["pideck-video-profile.sh", "chromium-pideck"])
def test_video_shell_snippets_only_apply_to_nvidia(name: str, tmp_path: Path) -> None:
    """Keep libva's automatic driver selection on systems without the NVIDIA driver."""
    script = x86_build.INPUTS / name
    subprocess.run(["sh", "-n", str(script)], check=True)
    result = subprocess.run(
        ["sh", "-c", f'CHROMIUM_FLAGS=""; . "{script}"; echo "${{LIBVA_DRIVER_NAME:-}}|$CHROMIUM_FLAGS"'],
        check=True, capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
    )
    if not Path("/proc/driver/nvidia/version").exists():
        assert result.stdout.strip() == "|"


@pytest.mark.parametrize(
    ("version", "expected"),
    [("550.163.01-2", "nvidia-550-163-01"), ("1:550.163.01-2+deb13u1", "nvidia-550-163-01")],
)
def test_flatpak_gl_driver_name_follows_debian_package(version: str, expected: str) -> None:
    """Derive the Flathub GL extension that matches the installed kernel driver."""
    assert x86_build.flatpak_nvidia_gl_driver(version) == expected


def test_flatpak_gl_driver_rejects_unexpected_versions() -> None:
    """Fail the build rather than install an unrelated GL extension."""
    with pytest.raises(ValueError):
        x86_build.flatpak_nvidia_gl_driver("")


def test_missing_nvidia_modules_reports_unbuilt_kernels(tmp_path: Path) -> None:
    """Require a DKMS-built module for each installed image kernel."""
    for kernel in ("6.12.1-amd64", "6.12.2-amd64"):
        (tmp_path / kernel).mkdir()
        (tmp_path / kernel / "modules.order").touch()
    built = tmp_path / "6.12.2-amd64/updates/dkms"
    built.mkdir(parents=True)
    (built / "nvidia-current.ko.xz").touch()
    (tmp_path / "6.12.0-headers-only").mkdir()
    assert x86_build.missing_nvidia_modules(tmp_path) == ["6.12.1-amd64"]


def test_x86_image_only_preinstalls_kodi_and_red() -> None:
    """Every other catalog application is installed later from Settings."""
    packages = set((x86_build.INPUTS / "packages.txt").read_text().split())
    catalog = load_catalog(ROOT / "assets/catalog/applications.yaml")
    optional = {
        package
        for entry in catalog if entry.identifier not in ("kodi", "youtube")
        for package in entry.spec_for("amd64").packages
    }
    assert not optional & packages
    assert x86_build.PREINSTALLED_FLATPAK == "com.ktechpit.red"
    assert {"steam-devices", "locales", "tzdata", "nvidia-vaapi-driver"} <= packages
    assert not (x86_build.INPUTS / "applications.yaml").exists()


def test_x86_image_ships_application_prerequisites(tmp_path: Path) -> None:
    """Vendor repositories and the Firefox language policy are part of the image."""
    x86_build._write_inputs(tmp_path, None)
    for name in ("google-chrome", "brave-browser"):
        source = (tmp_path / f"etc/apt/sources.list.d/{name}.sources").read_text()
        keyring = f"/usr/share/pideck/keyrings/{name}.gpg"
        assert f"Signed-By: {keyring}" in source and "URIs: https://" in source
        assert (tmp_path / keyring.lstrip("/")).stat().st_size > 1000
    assert 'repo_add_once="false"' in (tmp_path / "etc/default/google-chrome").read_text()
    policy = json.loads((tmp_path / "etc/firefox/policies/policies.json").read_text())
    assert policy["policies"]["RequestedLocales"][0] == "de"


def test_x86_locale_defaults_to_german(tmp_path: Path) -> None:
    """The system locale and time zone default to Germany."""
    commands: list[tuple[str, ...]] = []
    (tmp_path / "etc/default").mkdir(parents=True)
    x86_build._configure_locale(tmp_path, lambda *command: commands.append(command) or "")
    assert commands == [("locale-gen",)]
    assert "de_DE.UTF-8 UTF-8" in (tmp_path / "etc/locale.gen").read_text()
    assert (tmp_path / "etc/default/locale").read_text() == "LANG=de_DE.UTF-8\n"
    assert os.readlink(tmp_path / "etc/localtime") == "/usr/share/zoneinfo/Europe/Berlin"


def test_kodi_language_pin_is_verified() -> None:
    """The German Kodi add-on is pinned to an HTTPS URL and SHA-256."""
    url, sha256 = x86_build.kodi_language_pin()
    assert url.startswith("https://") and "resource.language.de_de" in url
    assert len(sha256) == 64


def test_kodi_language_install_rejects_wrong_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tampered download never reaches the image."""
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b"tampered"

    monkeypatch.setattr(x86_build.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="checksum"):
        x86_build._install_kodi_language(tmp_path)
    assert not (tmp_path / "usr/share/kodi/addons").exists()


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


@pytest.mark.parametrize(
    "name",
    [
        "pideck-getty",
        "pideck-grow-root",
        "pideck-hibernate-swap",
        "pideck-wait-drm",
        "pideck-xsession",
        "initramfs-pideck-slim",
        "initramfs-pideck-nvidia-load",
    ],
)
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


def test_initramfs_hook_removes_gpu_drivers_and_firmware(tmp_path: Path) -> None:
    """Strip GPU payloads after Plymouth copies them while keeping other files."""
    version = "6.12.0-test"
    modules = tmp_path / f"usr/lib/modules/{version}/kernel/drivers"
    (modules / "gpu/drm/nouveau").mkdir(parents=True)
    (modules / "ata").mkdir(parents=True)
    firmware = tmp_path / "usr/lib/firmware"
    for name in ("nvidia", "amdgpu", "rtl_nic"):
        (firmware / name).mkdir(parents=True)
    source = tmp_path / "source"
    (source / "kernel/drivers/gpu/drm/nouveau").mkdir(parents=True)
    (source / "kernel/drivers/gpu/drm/nouveau/nouveau.ko.xz").touch()
    (source / "kernel/drivers/gpu/drm/drm.ko.xz").touch()
    queue = tmp_path / "queue"
    queue.write_text("ahci\nnouveau\ndrm\nnvidia-drm\nnvidia-current-modeset\nusb-storage\n")
    dkms = tmp_path / f"usr/lib/modules/{version}/updates/dkms"
    dkms.mkdir(parents=True)
    (dkms / "nvidia-current.ko.xz").touch()
    (dkms / "other.ko.xz").touch()
    hook = x86_build.INPUTS / "initramfs-pideck-slim"
    assert subprocess.run(["sh", str(hook), "prereqs"], capture_output=True, text=True).stdout.strip() == ""
    subprocess.run(["sh", str(hook)], check=True, env={
        **os.environ, "DESTDIR": str(tmp_path), "version": version,
        "MODULESDIR": str(source), "__MODULES_TO_ADD": str(queue),
    })
    assert queue.read_text() == "ahci\nusb-storage\n"
    assert not (modules / "gpu").exists()
    assert (modules / "ata").is_dir()
    assert sorted(path.name for path in dkms.iterdir()) == ["other.ko.xz"]
    assert not (firmware / "nvidia").exists() and not (firmware / "amdgpu").exists()
    assert (firmware / "rtl_nic").is_dir()


def test_container_build_command_maps_inputs_and_owner(tmp_path: Path) -> None:
    """Run build.py in the container with mapped output and invoking user."""
    from image.x86.container_build import BuildRequest, docker_run_command

    request = BuildRequest(tmp_path / "out/x86", "v1.2.3")
    command = docker_run_command(request, 1000, 1001)
    assert command[:4] == ["docker", "run", "--rm", "--privileged"]
    assert f"type=bind,source={x86_build.ROOT},target=/pideck,readonly" in command
    assert f"type=bind,source={tmp_path / 'out'},target=/output" in command
    assert "SUDO_UID=1000" in command and "SUDO_GID=1001" in command
    build_args = command[command.index("image/x86/build.py") + 1:]
    assert build_args == ["--work-dir", "/output/x86.new", "--release-tag", "v1.2.3"]


def test_container_build_replaces_previous_output_only_with_staging(tmp_path: Path) -> None:
    """Swap the completed staging directory into place and remove stale files."""
    from image.x86.container_build import replace_output

    output, staging = tmp_path / "x86", tmp_path / "x86.new"
    (output / "root").mkdir(parents=True)
    (output / "old.img").write_text("old")
    staging.mkdir()
    (staging / "new.img").write_text("new")
    replace_output(staging, output)
    assert not staging.exists()
    assert [path.name for path in output.iterdir()] == ["new.img"]


def test_container_build_rejects_unmountable_paths(tmp_path: Path) -> None:
    """Refuse paths that would corrupt Docker mount options."""
    from image.x86.container_build import BuildRequest, docker_run_command

    with pytest.raises(ValueError):
        docker_run_command(BuildRequest(tmp_path / "a,b/x86"), 1000, 1000)
