#!/usr/bin/env python3
"""Build a reproducible application update bundle for an image release."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import tomllib


VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "pideck-update.tar"
MANIFEST_NAME = "pideck-update.json"


def build_metadata(timestamp: datetime | None = None) -> bytes:
    """Encode the actual build time as a stable UTC metadata document."""
    timestamp = timestamp or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Build time must include a timezone")
    value = timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    return (json.dumps({"built_at": value}, sort_keys=True) + "\n").encode("utf-8")


def project_version(project_root: Path) -> str:
    """Return the stable version declared identically by both project sources."""
    with (project_root / "pyproject.toml").open("rb") as stream:
        declared = tomllib.load(stream)["project"]["version"]
    init_source = (project_root / "src/pideck/__init__.py").read_text(encoding="utf-8")
    version_lines = re.findall(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', init_source, re.MULTILINE)
    if version_lines != [declared] or not VERSION_PATTERN.fullmatch(declared):
        raise ValueError("Project version declarations must match and be MAJOR.MINOR.PATCH")
    return declared


def release_version(project_root: Path, tag: str) -> str:
    """Validate a stable release tag against both project version declarations."""
    version = tag.removeprefix("v")
    if tag != f"v{version}" or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    try:
        declared = project_version(project_root)
    except ValueError as error:
        raise ValueError("Release tag and project version declarations do not match") from error
    if declared != version:
        raise ValueError("Release tag and project version declarations do not match")
    return version


def image_version(project_root: Path, tag: str | None) -> str:
    """Return the updatable release version installed into every image.

    A tag additionally pins the version; without one the project version is used,
    so development builds can also receive later published releases.
    """
    return release_version(project_root, tag) if tag is not None else project_version(project_root)


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


def create_bundle(
    project_root: Path, output: Path, tag: str,
    *, timestamp: datetime | None = None,
) -> None:
    """Create an unsigned bundle reproducible for a fixed build timestamp."""
    version = release_version(project_root, tag)
    sources = _sources(project_root)
    metadata_name = "python/pideck/_build_info.json"
    if any(name == metadata_name for _, name in sources):
        raise ValueError("Build metadata must be generated, not supplied in source")
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
        data = build_metadata(timestamp)
        info = tarfile.TarInfo(metadata_name)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        archive.addfile(info, io.BytesIO(data))

    manifest = {
        "format": 1,
        "updater_protocol": 2,
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


def main() -> None:
    """Build update assets from the current checkout and release tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--build-timestamp", type=datetime.fromisoformat,
        help="Original timezone-aware ISO 8601 build time for reproducing an artifact",
    )
    args = parser.parse_args()
    create_bundle(ROOT, args.output, args.tag, timestamp=args.build_timestamp)


if __name__ == "__main__":
    main()
