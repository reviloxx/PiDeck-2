#!/usr/bin/env python3
"""Build an experimental Debian amd64 PiDeck disk image on a trusted host."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

if __package__:
    from ..build import _validate_release_checkout, file_hash
    from ..update_bundle import release_version
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from image.build import _validate_release_checkout, file_hash
    from image.update_bundle import release_version


ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "image/x86"
PI_FILES = ROOT / "image/stage-pideck/00-pideck/files"
MIRROR = "http://deb.debian.org/debian"
DEBIAN_KEYRING = Path("/usr/share/keyrings/debian-archive-keyring.gpg")
IMAGE_SIZE = 12 * 1024**3
HOST_TOOLS = (
    "debootstrap", "sgdisk", "losetup", "mkfs.ext4", "mkfs.vfat",
    "mount", "umount", "chroot", "xz", "blkid", "openssl", "dpkg-deb", "systemctl", "gpgv",
)


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


def _partition(image: Path) -> None:
    """Create the BIOS, EFI, and Linux partitions in the image file."""
    _run("sgdisk", "--clear", "--new=1:2048:+2M", "--typecode=1:ef02",
         "--new=2:0:+512M", "--typecode=2:ef00", "--change-name=2:pideck-efi",
         "--new=3:0:0", "--typecode=3:8300", "--change-name=3:pideck-root", str(image))


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


def _check_host(
    work_dir: Path, tag: str | None, key: Path | None,
    player: Path | None, player_sha256: str | None,
) -> None:
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
    if (tag is None) != (key is None):
        raise ValueError("Release tag and public key must be supplied together")
    if (player is None) != (player_sha256 is None):
        raise ValueError("NoMachine player package and SHA-256 must be supplied together")
    if player is not None and player_sha256 is not None:
        if re.fullmatch(r"[a-fA-F0-9]{64}", player_sha256) is None:
            raise ValueError("Invalid NoMachine player SHA-256")
        if file_hash(player) != player_sha256.lower():
            raise ValueError("NoMachine player package checksum does not match")
        package = _run("dpkg-deb", "--field", str(player), "Package", capture=True)
        architecture = _run("dpkg-deb", "--field", str(player), "Architecture", capture=True)
        if package != "nomachine-player" or architecture != "amd64":
            raise ValueError("Expected the amd64 nomachine-player package")
    if tag is not None and key is not None:
        _validate_release_checkout(ROOT, tag)
        details = _run("openssl", "pkey", "-pubin", "-in", str(key), "-text", "-noout", capture=True)
        if not details.startswith("ED25519 Public-Key:"):
            raise ValueError("The update public key must be Ed25519")


def _write_inputs(root: Path, tag: str | None, key: Path | None) -> None:
    """Install the shared launcher, update services, and x86 boot configuration."""
    lib = root / "usr/lib/pideck"
    updater = root / "usr/lib/pideck-updater/python"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src/pideck", lib / "python/pideck", ignore=ignore)
    shutil.copytree(ROOT / "src/pideck", updater / "pideck", ignore=ignore)
    shutil.copytree(ROOT / "assets", root / "usr/share/pideck/assets")
    _install(PI_FILES / "pideck.yaml", root / "usr/share/pideck/config/pideck.yaml")
    for name in ("pideck-session", "pideck-xsession"):
        _install(PI_FILES / name, lib / name, 0o755)
    for name in ("pideck-getty", "pideck-grow-root"):
        _install(INPUTS / name, lib / name, 0o755)
    _install(PI_FILES / "pideck-profile.sh", root / "etc/profile.d/pideck.sh")
    _install(INPUTS / "pideck-network-profile.sh", root / "etc/profile.d/00-pideck-network.sh")
    _install(INPUTS / "49-pideck-network.rules", root / "etc/polkit-1/rules.d/49-pideck-network.rules")
    _install(INPUTS / "getty-override.conf", root / "etc/systemd/system/getty@tty1.service.d/pideck.conf")
    _install(INPUTS / "grub-pideck.cfg", root / "etc/default/grub.d/90-pideck.cfg")
    for name in ("pideck-updater.service", "pideck-activate.service"):
        _install(PI_FILES / name, root / "etc/systemd/system" / name)
    _install(INPUTS / "pideck-grow-root.service", root / "etc/systemd/system/pideck-grow-root.service")
    if tag is not None and key is not None:
        version = release_version(ROOT, tag)
        release = lib / "releases" / version
        shutil.copytree(ROOT / "src/pideck", release / "python/pideck", ignore=ignore)
        shutil.copytree(ROOT / "assets", release / "assets")
        (release / "config").mkdir()
        (lib / "current").symlink_to(f"releases/{version}")
        _install(key, root / "usr/share/pideck/update-public.pem")


def _configure(
    root: Path, loop: str, tag: str | None, key: Path | None, player: Path | None,
    player_sha256: str | None,
) -> None:
    """Install packages and configure the mounted Debian appliance."""
    def chroot(*args: str, capture: bool = False) -> str:
        return _run("chroot", str(root), *args, capture=capture)

    sources = root / "etc/apt/sources.list"
    sources.write_text(
        "deb http://deb.debian.org/debian trixie main contrib non-free-firmware\n"
        "deb http://deb.debian.org/debian trixie-updates main contrib non-free-firmware\n"
        "deb http://security.debian.org/debian-security trixie-security main contrib non-free-firmware\n",
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
    chroot("flatpak", "remote-add", "--system", "--if-not-exists", "flathub",
           "https://dl.flathub.org/repo/flathub.flatpakrepo")
    chroot("flatpak", "install", "--system", "--noninteractive", "--assumeyes",
           "--arch=x86_64", "flathub", "com.valvesoftware.SteamLink")
    if player is not None:
        player_copy = root / "tmp/nomachine-player.deb"
        _install(player, player_copy, 0o600)
        if player_sha256 is None or file_hash(player_copy) != player_sha256.lower():
            raise ValueError("NoMachine player package changed during the build")
        _run("systemctl", "--root", str(root), "mask", "nxserver.service")
        chroot("apt-get", "install", "-y", "--no-install-recommends", "/tmp/nomachine-player.deb")
        player_copy.unlink()
        if not (root / "usr/NX/bin/nxplayer").is_file():
            raise RuntimeError("NoMachine Player did not install its expected executable")
    policy.unlink()
    _write_inputs(root, tag, key)
    app_configurator = root / "tmp/pideck-configure-apps.py"
    app_definitions = root / "tmp/pideck-x86-applications.yaml"
    _install(INPUTS / "configure_apps.py", app_configurator)
    _install(INPUTS / "applications.yaml", app_definitions)
    arguments = [
        "python3", "/tmp/pideck-configure-apps.py",
        "/usr/share/pideck/config/pideck.yaml", "/tmp/pideck-x86-applications.yaml",
    ]
    if player is not None:
        arguments.append("--nomachine")
    chroot(*arguments)
    app_configurator.unlink()
    app_definitions.unlink()
    for group in ("audio", "video", "render", "input"):
        chroot("groupadd", "-f", group)
    chroot("useradd", "-m", "-u", "1000", "-s", "/bin/bash", "-G", "audio,video,render,input", "pideck")
    (root / "etc/hostname").write_text("pideck\n", encoding="utf-8")
    (root / "etc/hosts").write_text("127.0.0.1 localhost\n127.0.1.1 pideck\n::1 localhost\n", encoding="utf-8")
    root_uuid = _run("blkid", "-s", "UUID", "-o", "value", f"{loop}p3", capture=True)
    efi_uuid = _run("blkid", "-s", "UUID", "-o", "value", f"{loop}p2", capture=True)
    (root / "etc/fstab").write_text(
        f"UUID={root_uuid} / ext4 defaults 0 1\n"
        f"UUID={efi_uuid} /boot/efi vfat umask=0077 0 2\n", encoding="utf-8",
    )
    _run("systemctl", "--root", str(root), "enable", "NetworkManager", "pideck-updater.service",
         "pideck-grow-root.service", "getty@tty1.service")
    chroot("grub-install", "--target=i386-pc", "--no-nvram", loop)
    chroot("grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
           "--bootloader-id=PiDeck", "--removable", "--no-nvram")
    chroot("update-grub")
    manifest = chroot("dpkg-query", "-W", "-f=${binary:Package}\\t${Version}\\n", capture=True)
    (root / "usr/share/pideck/package-manifest.tsv").write_text(manifest + "\n", encoding="utf-8")
    commit = chroot("flatpak", "info", "--system", "--show-commit",
                    "com.valvesoftware.SteamLink", capture=True)
    (root / "usr/share/pideck/flatpak-manifest.tsv").write_text(
        f"com.valvesoftware.SteamLink\t{commit}\n", encoding="utf-8",
    )
    chroot("apt-get", "clean")


def build(
    work_dir: Path, tag: str | None = None, key: Path | None = None,
    player: Path | None = None, player_sha256: str | None = None,
) -> Path:
    """Build one raw image in a new directory and return its compressed path."""
    work_dir = work_dir.resolve()
    _check_host(work_dir, tag, key, player, player_sha256)
    input_hashes = _input_hashes()
    key_hash = file_hash(key) if key is not None else None
    work_dir.mkdir(mode=0o700)
    image = work_dir / "pideck-x86_64.img"
    with image.open("xb") as stream:
        stream.truncate(IMAGE_SIZE)
    _partition(image)
    with ExitStack() as stack:
        loop = _run("losetup", "--find", "--show", "--partscan", str(image), capture=True)
        stack.callback(_run, "losetup", "--detach", loop)
        _run("mkfs.vfat", "-F", "32", f"{loop}p2")
        _run("mkfs.ext4", "-F", f"{loop}p3")
        root = work_dir / "root"
        _mount(stack, f"{loop}p3", root)
        _run("debootstrap", "--arch=amd64", "--variant=minbase", "--force-check-gpg",
             f"--keyring={DEBIAN_KEYRING}", "trixie", str(root), MIRROR)
        _mount(stack, f"{loop}p2", root / "boot/efi")
        for name in ("dev", "proc", "sys"):
            _mount(stack, f"/{name}", root / name, "--bind")
        _configure(root, loop, tag, key, player, player_sha256)
        shutil.copyfile(root / "usr/share/pideck/package-manifest.tsv", work_dir / "package-manifest.tsv")
        shutil.copyfile(root / "usr/share/pideck/flatpak-manifest.tsv", work_dir / "flatpak-manifest.tsv")
        stats = os.statvfs(root)
        installed_bytes = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    if _input_hashes() != input_hashes:
        raise RuntimeError("Image source inputs changed during the build")
    if key is not None and file_hash(key) != key_hash:
        raise RuntimeError("Release public key changed during the build")
    _run("xz", "-T0", "-k", str(image))
    compressed = image.with_suffix(".img.xz")
    (work_dir / "SHA256SUMS").write_text(f"{file_hash(compressed)}  {compressed.name}\n", encoding="utf-8")
    (work_dir / "source-manifest.json").write_text(json.dumps({
        "distribution": "Debian trixie amd64",
        "root_filesystem_used_bytes": installed_bytes,
        "raw_image_bytes": IMAGE_SIZE,
        "nomachine_player_sha256": player_sha256,
        "release_public_key_sha256": key_hash,
        "inputs": input_hashes,
    }, indent=2) + "\n", encoding="utf-8")
    return compressed


def main() -> int:
    """Parse image build options and report errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--release-tag")
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--nomachine-deb", type=Path, help="Trusted local amd64 NoMachine Player package")
    parser.add_argument("--nomachine-sha256", help="Expected SHA-256 of the supplied player package")
    args = parser.parse_args()
    try:
        if args.nomachine_deb is None:
            print("NoMachine Player is omitted; supply --nomachine-deb and --nomachine-sha256.", file=sys.stderr)
        image = build(args.work_dir, args.release_tag, args.public_key, args.nomachine_deb, args.nomachine_sha256)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Image build failed: {error}", file=sys.stderr)
        return 1
    print(f"Image: {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
