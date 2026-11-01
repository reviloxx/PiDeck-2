"""Verify unsigned update validation and hostile archive rejection."""

import io
import hashlib
import json
from pathlib import Path
import tarfile
import tomllib

import pytest

from image.update_bundle import create_bundle
from pideck.infrastructure.image_update_bundle import InvalidUpdate, unpack_bundle, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    output = tmp_path / "release"
    create_bundle(ROOT, output, f"v{VERSION}")
    return output / "pideck-update.json", output / "pideck-update.tar"


def test_verified_bundle_extracts_and_downgrade_is_rejected(tmp_path: Path) -> None:
    """The manifest, version and content must agree before extraction."""
    manifest, bundle = _bundle(tmp_path)
    assert verify_bundle(manifest, bundle, "0.0.0") == VERSION
    unpack_bundle(bundle, tmp_path / "unpacked")
    assert (tmp_path / "unpacked/python/pideck/bootstrap.py").is_file()
    assert (tmp_path / "unpacked/assets/catalog/applications.yaml").is_file()
    assert (tmp_path / "unpacked/assets/icons/youtube.svg").read_bytes() == (
        ROOT / "assets/icons/youtube.svg"
    ).read_bytes()
    with pytest.raises(InvalidUpdate, match="not newer"):
        verify_bundle(manifest, bundle, VERSION)


def test_modified_bundle_is_rejected(tmp_path: Path) -> None:
    """A mismatched bundle is rejected against its manifest."""
    manifest, bundle = _bundle(tmp_path)
    data = bytearray(bundle.read_bytes())
    data[512] ^= 1
    bundle.write_bytes(data)
    with pytest.raises(InvalidUpdate, match="hash"):
        verify_bundle(manifest, bundle, "0.0.0")


def test_unsigned_manifest_cannot_authenticate_changed_content(tmp_path: Path) -> None:
    """Matching altered metadata and content are accepted without a signature."""
    manifest, bundle = _bundle(tmp_path)
    data = bytearray(bundle.read_bytes())
    data[512] ^= 1
    bundle.write_bytes(data)
    metadata = json.loads(manifest.read_text())
    metadata["sha256"] = hashlib.sha256(data).hexdigest()
    manifest.write_text(json.dumps(metadata))
    assert verify_bundle(manifest, bundle, "0.0.0") == VERSION


def test_invalid_manifest_is_rejected(tmp_path: Path) -> None:
    """Malformed metadata cannot be accepted."""
    manifest, bundle = _bundle(tmp_path)
    manifest.write_bytes(b"not json")
    with pytest.raises(InvalidUpdate, match="manifest"):
        verify_bundle(manifest, bundle, "0.0.0")


@pytest.mark.parametrize("change", [
    {"format": 2},
    {"updater_protocol": 1},
    {"size": True},
    {"artifact": "../bundle.tar"},
    {"version": "2.0.0-rc1"},
    {"extra": "unexpected"},
])
def test_invalid_metadata_is_rejected(tmp_path: Path, change: dict[str, object]) -> None:
    """Metadata must conform to the unsigned update protocol."""
    manifest, bundle = _bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data.update(change)
    manifest.write_text(json.dumps(data))
    with pytest.raises(InvalidUpdate):
        verify_bundle(manifest, bundle, "0.0.0")


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "assets/../escape", "assets//double",
    "python/pideck/../../escape", "other/file",
])
def test_unsafe_archive_paths_are_rejected(tmp_path: Path, name: str) -> None:
    """Archive paths cannot escape or write outside permitted subtrees."""
    bundle = tmp_path / "bad.tar"
    with tarfile.open(bundle, "w") as archive:
        info = tarfile.TarInfo(name)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(InvalidUpdate, match="Unsafe"):
        unpack_bundle(bundle, tmp_path / "unpacked")
    assert not (tmp_path / "escape").exists()


def test_archive_links_and_duplicate_members_are_rejected(tmp_path: Path) -> None:
    """Neither links nor repeated archive names can redirect extraction."""
    for duplicate in (False, True):
        bundle = tmp_path / f"bad-{duplicate}.tar"
        with tarfile.open(bundle, "w") as archive:
            info = tarfile.TarInfo("assets/file")
            if duplicate:
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
                archive.addfile(info, io.BytesIO(b"y"))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
        with pytest.raises(InvalidUpdate, match="Unsafe"):
            unpack_bundle(bundle, tmp_path / f"unpacked-{duplicate}")
