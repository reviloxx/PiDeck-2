#!/usr/bin/env python3
"""Build a signed, reproducible application update bundle for an image release."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib


VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "pideck-update.tar"
MANIFEST_NAME = "pideck-update.json"
SIGNATURE_NAME = "pideck-update.sig"


def release_version(project_root: Path, tag: str) -> str:
    """Validate a stable release tag against both project version declarations."""
    version = tag.removeprefix("v")
    if tag != f"v{version}" or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    with (project_root / "pyproject.toml").open("rb") as stream:
        declared = tomllib.load(stream)["project"]["version"]
    init_source = (project_root / "src/pideck/__init__.py").read_text(encoding="utf-8")
    version_lines = re.findall(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', init_source, re.MULTILINE)
    if declared != version or version_lines != [version]:
        raise ValueError("Release tag and project version declarations do not match")
    return version


def _sources(project_root: Path) -> list[tuple[Path, str]]:
    """List regular release files in stable archive order."""
    files: list[tuple[Path, str]] = []
    for source, prefix in (
        (project_root / "src/pideck", "python/pideck"),
        (project_root / "assets", "assets"),
    ):
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValueError(f"Symbolic links are not allowed in an update: {path}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"Unsupported update source: {path}")
            files.append((path, f"{prefix}/{relative.as_posix()}"))
    if not files:
        raise ValueError("Update bundle is empty")
    return files


def create_bundle(project_root: Path, output: Path, tag: str, signing_key: Path) -> None:
    """Create a deterministic bundle and sign its metadata with an Ed25519 key."""
    version = release_version(project_root, tag)
    key_details = subprocess.run(
        ["openssl", "pkey", "-in", str(signing_key), "-pubout", "-text", "-noout"],
        capture_output=True,
        text=True,
        check=True,
    )
    if not key_details.stdout.startswith("ED25519 Public-Key:"):
        raise ValueError("The release signing key must be an Ed25519 private key")
    sources = _sources(project_root)
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / BUNDLE_NAME
    with tarfile.open(bundle, "w", format=tarfile.USTAR_FORMAT) as archive:
        for path, name in sources:
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))

    manifest = {
        "format": 1,
        "updater_protocol": 1,
        "version": version,
        "artifact": BUNDLE_NAME,
        "size": bundle.stat().st_size,
        "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    }
    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(signing_key),
            "-in", str(manifest_path), "-out", str(output / SIGNATURE_NAME),
        ],
        check=True,
    )


def main() -> None:
    """Build update assets from the current checkout and release tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_bundle(ROOT, args.output, args.tag, args.signing_key)


if __name__ == "__main__":
    main()
