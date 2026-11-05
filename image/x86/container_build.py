#!/usr/bin/env python3
"""Build the x86 image in a privileged Docker container without sudo.

The container supplies the Debian build tools and root privileges needed by
``build.py``. Membership in the ``docker`` group already grants root-equivalent
access to the host, so this avoids password prompts without adding new
privileges. The image is built in a staging directory that replaces the
previous output only after a successful build.
"""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "image/x86/Dockerfile"
BUILDER_IMAGE = "pideck-x86-builder:trixie"
DEFAULT_OUTPUT = ROOT / "image-output/x86"
CONTAINER_ROOT = "/pideck"
CONTAINER_OUTPUT = "/output"
CONTAINER_PLAYER = "/inputs/nomachine-player.deb"


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Describe one containerized image build."""

    output: Path
    release_tag: str | None = None
    player: Path | None = None
    player_sha256: str | None = None

    @property
    def staging(self) -> Path:
        """Return the directory that receives the build before replacement."""
        return self.output.with_name(f"{self.output.name}.new")


def _bind(source: Path, target: str, *, read_only: bool = False) -> list[str]:
    """Return Docker bind-mount arguments for one host path."""
    if "," in str(source):
        raise ValueError(f"Paths containing commas cannot be mounted: {source}")
    option = f"type=bind,source={source},target={target}"
    return ["--mount", f"{option},readonly" if read_only else option]


def docker_run_command(request: BuildRequest, uid: int, gid: int) -> list[str]:
    """Return the ``docker run`` command that executes ``build.py``."""
    command = [
        "docker", "run", "--rm", "--privileged", "--network", "host",
        *_bind(Path("/dev"), "/dev"),
        *_bind(ROOT, CONTAINER_ROOT, read_only=True),
        *_bind(request.output.parent, CONTAINER_OUTPUT),
        "--env", f"SUDO_UID={uid}", "--env", f"SUDO_GID={gid}",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--workdir", CONTAINER_ROOT,
    ]
    build_args = ["--work-dir", f"{CONTAINER_OUTPUT}/{request.staging.name}"]
    if request.release_tag is not None:
        build_args += ["--release-tag", request.release_tag]
    if request.player is not None:
        command += _bind(request.player, CONTAINER_PLAYER, read_only=True)
        build_args += ["--nomachine-deb", CONTAINER_PLAYER]
    if request.player_sha256 is not None:
        build_args += ["--nomachine-sha256", request.player_sha256]
    return [*command, BUILDER_IMAGE, "python3", "image/x86/build.py", *build_args]


def replace_output(staging: Path, output: Path) -> None:
    """Replace the previous output directory with a completed build."""
    if output.exists():
        if os.path.ismount(output / "root"):
            raise RuntimeError(f"Cannot replace {output} while its root is mounted")
        shutil.rmtree(output)
    staging.rename(output)


def build(request: BuildRequest) -> Path:
    """Build the builder container, run the image build and publish its output."""
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is not installed; use the sudo build instead")
    if request.staging.exists():
        raise RuntimeError(f"Remove the stale staging directory first: {request.staging}")
    request.output.parent.mkdir(parents=True, exist_ok=True)
    with DOCKERFILE.open("rb") as dockerfile:
        subprocess.run(["docker", "build", "--tag", BUILDER_IMAGE, "-"], stdin=dockerfile, check=True)
    subprocess.run(docker_run_command(request, os.getuid(), os.getgid()), check=True)
    replace_output(request.staging, request.output)
    return request.output / "pideck-x86_64.img.xz"


def main() -> int:
    """Parse options, run the containerized build and report the result."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory to replace")
    parser.add_argument("--release-tag")
    parser.add_argument("--nomachine-deb", type=Path, help="Trusted local amd64 NoMachine Player package")
    parser.add_argument("--nomachine-sha256", help="Expected SHA-256 of the supplied player package")
    args = parser.parse_args()
    request = BuildRequest(
        args.output.resolve(), args.release_tag,
        args.nomachine_deb.resolve() if args.nomachine_deb else None, args.nomachine_sha256,
    )
    try:
        image = build(request)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Container image build failed: {error}", file=sys.stderr)
        return 1
    print(f"Image: {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
