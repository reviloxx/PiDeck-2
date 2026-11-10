#!/usr/bin/env python3
"""Build an experimental Debian amd64 PiDeck disk image on a trusted host."""

import argparse
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Generator
import urllib.request
import zipfile

if __package__:
    from ..build import _validate_release_checkout, file_hash
    from ..update_bundle import build_metadata, image_version
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from image.build import _validate_release_checkout, file_hash
    from image.update_bundle import build_metadata, image_version


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "image/x86"
PI_FILES = ROOT / "image/stage-pideck/00-pideck/files"
MIRROR = "http://deb.debian.org/debian"
DEBIAN_KEYRING = Path("/usr/share/keyrings/debian-archive-keyring.gpg")
IMAGE_SIZE = 12 * 1024**3
APT_COMPONENTS = "main contrib non-free non-free-firmware"
HOST_TOOLS = (
    "debootstrap", "sgdisk", "losetup", "mkfs.ext4", "mkfs.vfat",
    "mount", "umount", "chroot", "xz", "blkid", "openssl", "systemctl", "gpgv", "udevadm",
)
PREINSTALLED_FLATPAK = "com.ktechpit.red"
LOCALE = "de_DE.UTF-8"
TIMEZONE = "Europe/Berlin"


class _MountCleanupError(RuntimeError):
    """Report a temporary mount that could not be safely removed."""


def _run(*command: str, capture: bool = False) -> str:
    """Run a build command and propagate any failure."""
    result = subprocess.run(
        command, check=True, text=True, capture_output=capture,
        env={
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LC_ALL": "C",
            "DEBIAN_FRONTEND": "noninteractive",
        },
    )
    return result.stdout.strip() if capture else ""


def _install(source: Path, destination: Path, mode: int = 0o644) -> None:
    """Copy one build input into the root filesystem."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def _mount(stack: ExitStack, source: str, target: Path, *options: str) -> None:
    """Mount a target and register its cleanup immediately."""
    target.mkdir(parents=True, exist_ok=True)
    _run("mount", *options, source, str(target))
    stack.callback(_run, "umount", str(target))


def _invoking_user() -> tuple[int, int] | None:
    """Identify the user who requested privilege escalation, if available."""
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid is not None or sudo_gid is not None:
        if sudo_uid is None or sudo_gid is None:
            raise ValueError("Both SUDO_UID and SUDO_GID are required")
        uid, gid = int(sudo_uid), int(sudo_gid)
    elif "PKEXEC_UID" in os.environ:
        uid = int(os.environ["PKEXEC_UID"])
        try:
            gid = pwd.getpwuid(uid).pw_gid
        except KeyError as error:
            raise ValueError(f"No account exists for PKEXEC_UID {uid}") from error
    else:
        return None
    if uid < 0 or gid < 0:
        raise ValueError("Output owner IDs must be non-negative")
    return (uid, gid) if uid else None


@contextmanager
def _output_directory(work_dir: Path) -> Generator[None, None, None]:
    """Keep the build private and hand its unmounted outputs to the invoking user."""
    owner = _invoking_user()
    work_dir.mkdir(mode=0o700)
    safe_to_transfer = True
    try:
        yield
    except _MountCleanupError:
        safe_to_transfer = False
        raise
    finally:
        if owner is not None and safe_to_transfer:
            root = work_dir / "root"
            if os.path.ismount(root):
                raise RuntimeError(f"Cannot change output ownership while {root} is mounted")
            for entry in work_dir.iterdir():
                os.chown(entry, *owner, follow_symlinks=False)
            os.chown(work_dir, *owner, follow_symlinks=False)


def _debootstrap(root: Path) -> None:
    """Bootstrap through a space-free mount alias when the output path needs one."""
    def run(target: Path) -> None:
        _run("debootstrap", "--arch=amd64", "--variant=minbase", "--force-check-gpg",
             f"--keyring={DEBIAN_KEYRING}", "trixie", str(target), MIRROR)

    if not any(character.isspace() for character in str(root)):
        run(root)
        return
    target = Path(tempfile.mkdtemp(prefix="pideck-x86-", dir="/run"))
    try:
        with ExitStack() as stack:
            _mount(stack, str(root), target, "--bind")
            run(target)
    finally:
        try:
            target.rmdir()
        except OSError as error:
            raise _MountCleanupError(f"Cannot remove bootstrap mount alias {target}") from error


def _partition(image: Path) -> None:
    """Create the BIOS, EFI, and Linux partitions in the image file."""
    _run("sgdisk", "--clear", "--new=1:2048:+2M", "--typecode=1:ef02",
         "--new=2:0:+512M", "--typecode=2:ef00", "--change-name=2:pideck-efi",
         "--new=3:0:0", "--typecode=3:8300", "--change-name=3:pideck-root", str(image))


def _settle_loop_partitions(loop: str) -> None:
    """Wait for udev and require both filesystem partition devices."""
    _run("udevadm", "settle", "--timeout=30")
    missing = [f"{loop}p{number}" for number in (2, 3) if not Path(f"{loop}p{number}").is_block_device()]
    if missing:
        raise RuntimeError(f"Loop partition devices are unavailable: {', '.join(missing)}")


def _input_hashes() -> dict[str, str]:
    """Hash all local source inputs used by the image and release validation."""
    paths = [
        path
        for directory in (INPUTS, PI_FILES, ROOT / "src/pideck", ROOT / "assets")
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    paths.extend((ROOT / "image/build.py", ROOT / "image/update_bundle.py", ROOT / "pyproject.toml"))
    return {str(path.relative_to(ROOT)): file_hash(path) for path in paths}


def _check_host(work_dir: Path, tag: str | None) -> None:
    """Reject unsupported hosts and invalid release inputs before making files."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("A native x86-64 Linux build host is required")
    if os.geteuid() != 0:
        raise RuntimeError("Run as root on a dedicated trusted build host")
    if work_dir.exists():
        raise ValueError("The build directory must not already exist")
    missing = [tool for tool in HOST_TOOLS if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"Missing host tools: {', '.join(missing)}")
    if not DEBIAN_KEYRING.is_file():
        raise RuntimeError(f"Debian archive signing keyring is missing: {DEBIAN_KEYRING}")
    if tag is not None:
        _validate_release_checkout(ROOT, tag)


def _write_inputs(root: Path, tag: str | None) -> None:
    """Install the shared launcher, update services, and x86 boot configuration."""
    lib = root / "usr/lib/pideck"
    updater = root / "usr/lib/pideck-updater/python"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src/pideck", lib / "python/pideck", ignore=ignore)
    metadata = build_metadata()
    (lib / "python/pideck/_build_info.json").write_bytes(metadata)
    shutil.copytree(ROOT / "src/pideck", updater / "pideck", ignore=ignore)
    shutil.copytree(ROOT / "assets", root / "usr/share/pideck/assets")
    _install(PI_FILES / "pideck.yaml", root / "usr/share/pideck/config/pideck.yaml")
    _install(PI_FILES / "70-pideck-cec.rules", root / "etc/udev/rules.d/70-pideck-cec.rules")
    _install(PI_FILES / "window-modes.yaml", root / "usr/share/pideck/config/window-modes.yaml")
    _install(PI_FILES / "openbox/rc.xml", root / "usr/share/pideck/openbox/rc.xml")
    for name in ("pideck-session", "pideck-xsession"):
        _install(PI_FILES / name, lib / name, 0o755)
    # VM-safe fallback for recovery shells and images without a current release tree.
    _install(ROOT / "src/pideck/infrastructure/session/picom-vm.conf", lib / "picom.conf")
    for name in ("pideck-getty", "pideck-grow-root", "pideck-wait-drm"):
        _install(INPUTS / name, lib / name, 0o755)
    _install(PI_FILES / "pideck-profile.sh", root / "etc/profile.d/pideck.sh")
    _install(INPUTS / "pideck-network-profile.sh", root / "etc/profile.d/00-pideck-network.sh")
    _install(INPUTS / "49-pideck-network.rules", root / "etc/polkit-1/rules.d/49-pideck-network.rules")
    _install(PI_FILES / "50-pideck-udisks.rules", root / "etc/polkit-1/rules.d/50-pideck-udisks.rules")
    _install(PI_FILES / "51-pideck-timezone.rules", root / "etc/polkit-1/rules.d/51-pideck-timezone.rules")
    _install(PI_FILES / "smb.conf", root / "etc/samba/smb.conf")
    samba_shares = root / "var/lib/pideck/samba"
    samba_shares.mkdir(parents=True, exist_ok=True)
    (samba_shares / "shares.conf").write_text(
        "# Managed by PiDeck. Do not edit; Settings > Drives rewrites this file.\n",
        encoding="utf-8",
    )
    _install(PI_FILES / "00-pideck-keyboard.conf", root / "etc/X11/xorg.conf.d/00-pideck-keyboard.conf")
    cec_support = ROOT / "src/pideck/infrastructure/cec_support"
    _install(cec_support / "71-pideck-cec-attach.rules", root / "etc/udev/rules.d/71-pideck-cec-attach.rules")
    _install(cec_support / "pideck-cec-attach@.service", root / "etc/systemd/system/pideck-cec-attach@.service")
    _install(INPUTS / "getty-override.conf", root / "etc/systemd/system/getty@tty1.service.d/pideck.conf")
    _install(INPUTS / "grub-pideck.cfg", root / "etc/default/grub.d/90-pideck.cfg")
    _install(INPUTS / "initramfs-pideck.conf", root / "etc/initramfs-tools/conf.d/pideck.conf")
    _install(INPUTS / "initramfs-pideck-slim", root / "etc/initramfs-tools/hooks/pideck-slim", 0o755)
    _install(
        INPUTS / "initramfs-pideck-nvidia-load",
        root / "etc/initramfs-tools/scripts/local-bottom/pideck-nvidia",
        0o755,
    )
    _install(INPUTS / "nvidia-pideck.conf", root / "etc/modprobe.d/pideck-nvidia.conf")
    # Replace Debian's modules-load symlink so nvidia-drm does not block sysinit.
    modules_load = root / "etc/modules-load.d/nvidia.conf"
    modules_load.unlink(missing_ok=True)
    _install(INPUTS / "modules-load-nvidia.conf", modules_load)
    _install(INPUTS / "pideck-video-profile.sh", root / "etc/profile.d/10-pideck-video.sh")
    _install(INPUTS / "firefox-pideck.js", root / "etc/firefox-esr/pideck-video.js")
    _install(INPUTS / "chromium-pideck", root / "etc/chromium.d/pideck-video")
    _write_application_prerequisites(root)
    for name in ("pideck.plymouth", "pideck.script"):
        _install(PI_FILES / name, root / "usr/share/plymouth/themes/pideck" / name)
    for name in ("pideck-updater.service", "pideck-activate.service"):
        _install(PI_FILES / name, root / "etc/systemd/system" / name)
    _install(INPUTS / "pideck-grow-root.service", root / "etc/systemd/system/pideck-grow-root.service")
    _install(INPUTS / "pideck-nvidia-load.service", root / "etc/systemd/system/pideck-nvidia-load.service")
    version = image_version(ROOT, tag)
    release = lib / "releases" / version
    shutil.copytree(ROOT / "src/pideck", release / "python/pideck", ignore=ignore)
    (release / "python/pideck/_build_info.json").write_bytes(metadata)
    shutil.copytree(ROOT / "assets", release / "assets")
    (release / "config").mkdir()
    (lib / "current").symlink_to(f"releases/{version}")


def _write_application_prerequisites(root: Path) -> None:
    """Install image-level files needed by applications installed later from Settings."""
    for name in ("google-chrome.gpg", "brave-browser.gpg"):
        _install(PI_FILES / "keyrings" / name, root / "usr/share/pideck/keyrings" / name)
    for name in ("google-chrome.sources", "brave-browser.sources"):
        _install(PI_FILES / name, root / "etc/apt/sources.list.d" / name)
    _install(PI_FILES / "google-chrome.default", root / "etc/default/google-chrome")
    _install(PI_FILES / "firefox-policies.json", root / "etc/firefox/policies/policies.json")


def kodi_language_pin(path: Path = PI_FILES / "kodi-language.env") -> tuple[str, str]:
    """Return the pinned URL and SHA-256 of Kodi's German language add-on."""
    values = dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    url, sha256 = values["KODI_LANGUAGE_URL"], values["KODI_LANGUAGE_SHA256"]
    if not url.startswith("https://") or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("Invalid Kodi language add-on pin")
    return url, sha256


def _install_kodi_language(root: Path) -> None:
    """Install the verified German add-on as a bundled Kodi add-on."""
    url, sha256 = kodi_language_pin()
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("Kodi language add-on checksum does not match")
    target = root / "usr/share/kodi/addons"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if any(name.startswith("/") or ".." in Path(name).parts for name in archive.namelist()):
            raise ValueError("Unsafe path in Kodi language add-on")
        archive.extractall(target)


def _configure_locale(root: Path, chroot: Callable[..., str]) -> None:
    """Default the appliance to German with the Berlin time zone."""
    (root / "etc/locale.gen").write_text(f"{LOCALE} UTF-8\nen_US.UTF-8 UTF-8\n", encoding="utf-8")
    chroot("locale-gen")
    (root / "etc/default/locale").write_text(f"LANG={LOCALE}\n", encoding="utf-8")
    (root / "etc/timezone").write_text(f"{TIMEZONE}\n", encoding="utf-8")
    localtime = root / "etc/localtime"
    localtime.unlink(missing_ok=True)
    localtime.symlink_to(f"/usr/share/zoneinfo/{TIMEZONE}")


def flatpak_nvidia_gl_driver(package_version: str) -> str:
    """Return the Flatpak GL driver name for a Debian nvidia-driver package version."""
    upstream = package_version.strip().split(":")[-1].split("-")[0]
    if re.fullmatch(r"\d+(?:\.\d+)+", upstream) is None:
        raise ValueError(f"Unexpected NVIDIA driver version: {package_version!r}")
    return "nvidia-" + upstream.replace(".", "-")


def missing_nvidia_modules(modules: Path) -> list[str]:
    """Return installed kernel versions that lack a built NVIDIA kernel module."""
    return [
        kernel.name
        for kernel in sorted(modules.iterdir())
        if (kernel / "modules.order").is_file()
        and not any((kernel / "updates").rglob("nvidia*.ko*"))
    ]


def _build_nvidia_modules(root: Path, chroot: Callable[..., str]) -> None:
    """Build the NVIDIA module for every image kernel, not the build host's kernel."""
    modules = root / "usr/lib/modules"
    for kernel in sorted(path.name for path in modules.iterdir() if (path / "modules.order").is_file()):
        chroot("dkms", "autoinstall", "-k", kernel)
    missing = missing_nvidia_modules(modules)
    if missing:
        raise RuntimeError(f"NVIDIA kernel module was not built for: {', '.join(missing)}")


def _configure(root: Path, loop: str, tag: str | None) -> None:
    """Install packages and configure the mounted Debian appliance."""
    def chroot(*args: str, capture: bool = False) -> str:
        return _run("chroot", str(root), *args, capture=capture)

    sources = root / "etc/apt/sources.list"
    sources.write_text(
        f"deb http://deb.debian.org/debian trixie {APT_COMPONENTS}\n"
        f"deb http://deb.debian.org/debian trixie-updates {APT_COMPONENTS}\n"
        f"deb http://security.debian.org/debian-security trixie-security {APT_COMPONENTS}\n",
        encoding="utf-8",
    )
    resolver = root / "etc/resolv.conf"
    resolver.unlink(missing_ok=True)
    _install(Path("/etc/resolv.conf").resolve(), resolver)
    policy = root / "usr/sbin/policy-rc.d"
    policy.write_text("#!/bin/sh\nexit 101\n", encoding="utf-8")
    policy.chmod(0o755)
    chroot("apt-get", "update")
    packages = (INPUTS / "packages.txt").read_text(encoding="utf-8").split()
    chroot("apt-get", "install", "-y", "--no-install-recommends", *packages)
    _build_nvidia_modules(root, chroot)
    chroot("flatpak", "remote-add", "--system", "--if-not-exists", "flathub",
           "https://dl.flathub.org/repo/flathub.flatpakrepo")
    # Select the GL extensions for the image's NVIDIA driver, not the build host's.
    gl_driver = flatpak_nvidia_gl_driver(
        chroot("dpkg-query", "-W", "-f=${Version}", "nvidia-driver", capture=True)
    )
    chroot("flatpak", "config", "--system", "--set", "languages", "de;en")
    chroot("env", f"FLATPAK_GL_DRIVERS={gl_driver}:default", "flatpak", "install", "--system",
           "--noninteractive", "--assumeyes", "--arch=x86_64", "flathub", PREINSTALLED_FLATPAK)
    runtimes = chroot("flatpak", "list", "--system", "--runtime", "--columns=application", capture=True)
    if f"org.freedesktop.Platform.GL.{gl_driver}" not in runtimes.split():
        raise RuntimeError(f"Flathub did not provide the {gl_driver} GL extension")
    # A NoMachine Player installed later from Settings must never start a server.
    _run("systemctl", "--root", str(root), "mask", "nxserver.service")
    # Samba does not need to block boot on a default route; the launcher path
    # never waited on this unit, but graphical.target did.
    _run("systemctl", "--root", str(root), "mask", "NetworkManager-wait-online.service")
    # Splash is owned by nvidia show-splash + xsession quit; do not hold getty.
    _run("systemctl", "--root", str(root), "mask", "plymouth-quit-wait.service")
    policy.unlink()
    _write_inputs(root, tag)
    _install_kodi_language(root)
    _configure_locale(root, chroot)
    chroot("plymouth-set-default-theme", "pideck")
    chroot("update-initramfs", "-u", "-k", "all")
    for group in ("audio", "video", "render", "input", "plugdev"):
        chroot("groupadd", "-f", group)
    chroot("useradd", "-m", "-u", "1000", "-s", "/bin/bash", "-G", "audio,video,render,input,plugdev", "pideck")
    (root / "etc/hostname").write_text("pideck\n", encoding="utf-8")
    (root / "etc/hosts").write_text("127.0.0.1 localhost\n127.0.1.1 pideck\n::1 localhost\n", encoding="utf-8")
    root_uuid = _run("blkid", "-s", "UUID", "-o", "value", f"{loop}p3", capture=True)
    efi_uuid = _run("blkid", "-s", "UUID", "-o", "value", f"{loop}p2", capture=True)
    (root / "etc/fstab").write_text(
        f"UUID={root_uuid} / ext4 defaults 0 1\n"
        f"UUID={efi_uuid} /boot/efi vfat umask=0077 0 2\n", encoding="utf-8",
    )
    _run("systemctl", "--root", str(root), "enable", "NetworkManager", "bluetooth.service", "smbd.service",
         "nmbd.service", "pideck-updater.service", "pideck-grow-root.service", "pideck-nvidia-load.service",
         "systemd-timesyncd.service", "getty@tty1.service")
    chroot("grub-install", "--target=i386-pc", "--no-nvram", loop)
    chroot("grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
           "--bootloader-id=PiDeck", "--removable", "--no-nvram")
    chroot("update-grub")
    manifest = chroot("dpkg-query", "-W", "-f=${binary:Package}\\t${Version}\\n", capture=True)
    (root / "usr/share/pideck/package-manifest.tsv").write_text(manifest + "\n", encoding="utf-8")
    commit = chroot("flatpak", "info", "--system", "--show-commit", PREINSTALLED_FLATPAK, capture=True)
    (root / "usr/share/pideck/flatpak-manifest.tsv").write_text(
        f"{PREINSTALLED_FLATPAK}\t{commit}\n", encoding="utf-8",
    )
    chroot("apt-get", "clean")


def build(work_dir: Path, tag: str | None = None) -> Path:
    """Build one raw image in a new directory and return its compressed path."""
    work_dir = work_dir.resolve()
    _check_host(work_dir, tag)
    input_hashes = _input_hashes()
    with _output_directory(work_dir):
        return _build_image(work_dir, tag, input_hashes)


def _build_image(work_dir: Path, tag: str | None, input_hashes: dict[str, str]) -> Path:
    """Build the image inside a private, prevalidated output directory."""
    image = work_dir / "pideck-x86_64.img"
    with image.open("xb") as stream:
        stream.truncate(IMAGE_SIZE)
    _partition(image)
    with ExitStack() as stack:
        loop = _run("losetup", "--find", "--show", "--partscan", str(image), capture=True)
        stack.callback(_run, "losetup", "--detach", loop)
        _settle_loop_partitions(loop)
        _run("mkfs.vfat", "-F", "32", f"{loop}p2")
        _run("mkfs.ext4", "-F", f"{loop}p3")
        root = work_dir / "root"
        _mount(stack, f"{loop}p3", root)
        _debootstrap(root)
        _mount(stack, f"{loop}p2", root / "boot/efi")
        for name in ("dev", "proc", "sys"):
            _mount(stack, f"/{name}", root / name, "--bind")
        _configure(root, loop, tag)
        shutil.copyfile(root / "usr/share/pideck/package-manifest.tsv", work_dir / "package-manifest.tsv")
        shutil.copyfile(root / "usr/share/pideck/flatpak-manifest.tsv", work_dir / "flatpak-manifest.tsv")
        stats = os.statvfs(root)
        installed_bytes = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    if _input_hashes() != input_hashes:
        raise RuntimeError("Image source inputs changed during the build")
    _run("xz", "-T0", "-k", str(image))
    compressed = image.with_suffix(".img.xz")
    (work_dir / "SHA256SUMS").write_text(f"{file_hash(compressed)}  {compressed.name}\n", encoding="utf-8")
    (work_dir / "source-manifest.json").write_text(json.dumps({
        "distribution": "Debian trixie amd64",
        "root_filesystem_used_bytes": installed_bytes,
        "raw_image_bytes": IMAGE_SIZE,
        "inputs": input_hashes,
    }, indent=2) + "\n", encoding="utf-8")
    return compressed


def main() -> int:
    """Parse image build options and report errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--release-tag")
    args = parser.parse_args()
    try:
        image = build(args.work_dir, args.release_tag)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Image build failed: {error}", file=sys.stderr)
        return 1
    print(f"Image: {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
