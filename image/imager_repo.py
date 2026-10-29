#!/usr/bin/env python3
"""Generate a local Raspberry Pi Imager repository for a PiDeck image."""

import argparse
from datetime import date
import hashlib
import json
import lzma
from pathlib import Path
import sys


def write_repository(image: Path) -> Path:
    """Write an Imager repository entry with verified uncompressed image metadata."""
    image = image.resolve(strict=True)
    if not image.is_file() or not image.name.endswith(".img.xz"):
        raise ValueError("Expected a compressed .img.xz image")
    digest = hashlib.sha256()
    size = 0
    with lzma.open(image, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    if size == 0:
        raise ValueError("The uncompressed image is empty")
    repository = {
        "os_list": [
            {
                "name": "PiDeck for Raspberry Pi 5",
                "description": "PiDeck launcher on Raspberry Pi OS Lite 64-bit",
                "icon": "",
                "url": image.as_uri(),
                "extract_size": size,
                "extract_sha256": digest.hexdigest(),
                "image_download_size": image.stat().st_size,
                "release_date": date.today().isoformat(),
                "devices": ["pi5"],
                "init_format": "systemd",
                "architecture": "armv8",
            }
        ]
    }
    output = image.parent / "pideck-imager-repository.json"
    output.write_text(json.dumps(repository, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    """Generate an Imager repository entry for an existing compressed image."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Path to the PiDeck .img.xz")
    arguments = parser.parse_args()
    try:
        output = write_repository(arguments.image)
    except (OSError, EOFError, ValueError, lzma.LZMAError) as error:
        print(f"Unable to generate Imager repository: {error}", file=sys.stderr)
        return 1
    print(f"Imager repository: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
