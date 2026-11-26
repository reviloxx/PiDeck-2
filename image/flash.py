#!/usr/bin/env python3
"""Flash a PiDeck .img.xz with Raspberry Pi Imager CLI.

Post-write verification is off by default (it roughly doubles flash time).
Pass --verify to re-enable it. GUI Imager always starts verification; click
Cancel Verify there, or use this CLI path when customization is not required.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def imager_command(image: Path, destination: str, *, verify: bool) -> list[str]:
    """Build the rpi-imager CLI argv for a local compressed image."""
    command = ["rpi-imager", "--cli"]
    if not verify:
        command.append("--disable-verify")
    command.extend([str(image), destination])
    return command


def main() -> int:
    """Flash an image to a block device via rpi-imager --cli."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Path to the PiDeck .img.xz")
    parser.add_argument(
        "destination",
        help="Target block device (for example /dev/mmcblk0 or /dev/sdX)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Re-read the media after writing (off by default)",
    )
    arguments = parser.parse_args()
    image = arguments.image.resolve()
    if not image.is_file() or not image.name.endswith(".img.xz"):
        print("Expected a compressed .img.xz image", file=sys.stderr)
        return 1
    if shutil.which("rpi-imager") is None:
        print("rpi-imager is not installed or not on PATH", file=sys.stderr)
        return 1
    command = imager_command(image, arguments.destination, verify=arguments.verify)
    print(" ".join(command), file=sys.stderr)
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
