#!/usr/bin/env python3
"""Build a PiDeck image using a pinned native ARM64 pi-gen checkout."""

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

if __package__:
    from .imager_repo import write_repository
    from .update_bundle import release_version
else:
    from imager_repo import write_repository
    from update_bundle import release_version

PI_GEN_URL = "https://github.com/RPi-Distro/pi-gen.git"
PI_GEN_COMMIT = "c851f00178c64b87a86c06c2be216bd89d62e55b"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_LITE_PACKAGES = frozenset(
    {
        "build-essential",
        "ed",
        "gdb",
        "htop",
        "manpages-dev",
        "ncdu",
        "pkg-config",
        "rpi-connect-lite",
        "strace",
    }
)


def prepare_stage(
    project_root: Path,
    pi_gen: Path,
    release_tag: str | None = None,
    public_key: Path | None = None,
) -> Path:
    """Copy the image stage and a snapshot of PiDeck into a private checkout."""
    stage = pi_gen / "stage-pideck"
    shutil.copytree(project_root / "image" / "stage-pideck", stage)
    project = stage / "00-pideck" / "files" / "project"
    project.mkdir()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    shutil.copytree(project_root / "src", project / "src", ignore=ignore)
    shutil.copytree(project_root / "assets", project / "assets", ignore=ignore)
    if (release_tag is None) != (public_key is None):
        raise ValueError("A release tag and public key must be supplied together")
    if release_tag is not None and public_key is not None:
        version = release_version(project_root, release_tag)
        key_details = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(public_key), "-text", "-noout"],
            capture_output=True, text=True, check=True,
        )
        if not key_details.stdout.startswith("ED25519 Public-Key:"):
            raise ValueError("The update public key must be Ed25519")
        (project / "release-version").write_text(version + "\n", encoding="utf-8")
        shutil.copyfile(public_key, project / "update-public.pem")
    shutil.copyfile(
        project_root / "image" / "export" / "02-run-chroot.sh",
        pi_gen / "export-image" / "01-user-rename" / "02-run-chroot.sh",
    )
    for script in (stage / "prerun.sh", stage / "00-pideck" / "01-run.sh"):
        script.chmod(0o755)
    (pi_gen / "stage2" / "SKIP_IMAGES").touch()
    (pi_gen / "stage2" / "03-accept-mathematica-eula" / "SKIP").touch()
    (pi_gen / "stage2" / "04-cloud-init" / "SKIP").touch()
    package_list = pi_gen / "stage2" / "01-sys-tweaks" / "00-packages"
    packages = package_list.read_text(encoding="utf-8").split()
    missing = EXCLUDED_LITE_PACKAGES.difference(packages)
    if missing:
        raise ValueError(f"Unexpected pi-gen package list; missing {', '.join(sorted(missing))}")
    package_list.write_text(
        "\n".join(package for package in packages if package not in EXCLUDED_LITE_PACKAGES) + "\n",
        encoding="utf-8",
    )
    return stage


def write_build_config(pi_gen: Path) -> Path:
    """Write the pi-gen settings for a Lite-based, customized image."""
    config = pi_gen / "pideck-config"
    config.write_text(
        "IMG_NAME='pideck'\n"
        "PI_GEN_RELEASE='PiDeck'\n"
        "RELEASE='trixie'\n"
        "STAGE_LIST='stage0 stage1 stage2 stage-pideck'\n"
        "DEPLOY_COMPRESSION='xz'\n"
        "ENABLE_SSH=0\n"
        "PASSWORDLESS_SUDO=0\n"
        "DISABLE_FIRST_BOOT_USER_RENAME=0\n"
        "FIRST_USER_PASS=''\n"
        "ENABLE_CLOUD_INIT=0\n",
        encoding="utf-8",
    )
    return config


def file_hash(path: Path) -> str:
    """Calculate a SHA-256 digest without loading a complete image into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_source_manifest(
    stage: Path, output: Path, project_root: Path = PROJECT_ROOT
) -> None:
    """Record the exact local source files included in the build."""
    hashes = {
        str(path.relative_to(stage)): file_hash(path)
        for path in sorted(stage.rglob("*"))
        if path.is_file()
    }
    output.write_text(
        json.dumps(
            {
                "pi_gen_commit": PI_GEN_COMMIT,
                "builder_sha256": file_hash(project_root / "image" / "build.py"),
                "imager_repository_generator_sha256": file_hash(
                    project_root / "image" / "imager_repo.py"
                ),
                "export_stage_sha256": file_hash(
                    stage.parent / "export-image" / "01-user-rename" / "02-run-chroot.sh"
                ),
                "pi_gen_config_sha256": file_hash(stage.parent / "pideck-config"),
                "lite_package_list_sha256": file_hash(
                    stage.parent / "stage2" / "01-sys-tweaks" / "00-packages"
                ),
                "excluded_lite_packages": sorted(EXCLUDED_LITE_PACKAGES),
                "files": hashes,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _validate_release_checkout(project_root: Path, tag: str) -> None:
    """Require the checked-out release inputs to exactly match the tag."""
    release_version(project_root, tag)
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", f"safe.directory={project_root.resolve()}", "-C", str(project_root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    if git("rev-parse", "HEAD") != git("rev-parse", f"refs/tags/{tag}^{{commit}}"):
        raise ValueError("Release tag does not identify the checked-out commit")
    if git("status", "--porcelain", "--untracked-files=all", "--", "src/pideck", "assets", "pyproject.toml", "image"):
        raise ValueError("Release image inputs must match the clean tagged checkout")


def build(
    work_dir: Path,
    project_root: Path = PROJECT_ROOT,
    release_tag: str | None = None,
    public_key: Path | None = None,
) -> Path:
    """Build and checksum one image in a new, dedicated working directory."""
    if platform.machine() not in ("aarch64", "arm64"):
        raise RuntimeError("A native ARM64 Linux build host is required")
    if platform.system() != "Linux":
        raise RuntimeError("The image must be built on Linux")
    if os.geteuid() != 0:
        raise RuntimeError("Run the build as root; pi-gen requires mount privileges")
    if (release_tag is None) != (public_key is None):
        raise ValueError("A release tag and public key must be supplied together")
    if release_tag is not None:
        _validate_release_checkout(project_root, release_tag)
    work_dir = work_dir.resolve()
    if any(character.isspace() for character in str(work_dir)):
        raise ValueError("The pi-gen working directory must not contain whitespace")
    work_dir.mkdir()
    build_env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(work_dir),
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    pi_gen = work_dir / "pi-gen"
    subprocess.run(
        ["git", "clone", "--branch", "arm64", "--single-branch", PI_GEN_URL, str(pi_gen)],
        check=True,
        env=build_env,
    )
    subprocess.run(
        ["git", "-C", str(pi_gen), "checkout", "--detach", PI_GEN_COMMIT],
        check=True,
        env=build_env,
    )
    stage = prepare_stage(project_root, pi_gen, release_tag, public_key)
    config = write_build_config(pi_gen)
    write_source_manifest(stage, work_dir / "source-manifest.json", project_root)
    subprocess.run(["./build.sh", "-c", str(config)], cwd=pi_gen, check=True, env=build_env)
    images = list((pi_gen / "deploy").glob("*.img.xz"))
    if len(images) != 1:
        raise RuntimeError(f"Expected one compressed image, found {len(images)}")
    image = images[0]
    (image.parent / "SHA256SUMS").write_text(
        f"{file_hash(image)}  {image.name}\n", encoding="utf-8"
    )
    write_repository(image)
    return image


def main() -> int:
    """Parse the build directory and report image output or build failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="New, space-free directory for the isolated build and output",
    )
    parser.add_argument("--release-tag", help="Stable tag for an update-enabled release image")
    parser.add_argument("--public-key", type=Path, help="Trusted Ed25519 release public key (PEM)")
    arguments = parser.parse_args()
    try:
        image = build(arguments.work_dir, release_tag=arguments.release_tag, public_key=arguments.public_key)
    except (OSError, EOFError, ValueError, RuntimeError, lzma.LZMAError, subprocess.CalledProcessError) as error:
        print(f"Image build failed: {error}", file=sys.stderr)
        return 1
    print(f"Image: {image}")
    print(f"SHA-256: {image.parent / 'SHA256SUMS'}")
    print(f"Imager repository: {image.parent / 'pideck-imager-repository.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
