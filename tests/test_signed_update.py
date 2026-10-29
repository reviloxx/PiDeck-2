"""Verify signed update acceptance and hostile archive rejection."""

import io
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

import pytest

from image.update_bundle import create_bundle
from pideck.infrastructure.signed_update import InvalidUpdate, unpack_bundle, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]


def _signed_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    output = tmp_path / "release"
    create_bundle(ROOT, output, f"v{VERSION}", private)
    return output / "pideck-update.json", output / "pideck-update.sig", output / "pideck-update.tar", public


def test_verified_bundle_extracts_and_downgrade_is_rejected(tmp_path: Path) -> None:
    """The trusted key, version and content must all agree before extraction."""
    manifest, signature, bundle, public = _signed_bundle(tmp_path)
    assert verify_bundle(manifest, signature, bundle, public, "0.0.0") == VERSION
    unpack_bundle(bundle, tmp_path / "unpacked")
    assert (tmp_path / "unpacked/python/pideck/bootstrap.py").is_file()
    with pytest.raises(InvalidUpdate, match="not newer"):
        verify_bundle(manifest, signature, bundle, public, VERSION)


def test_modified_bundle_is_rejected(tmp_path: Path) -> None:
    """A signed manifest cannot authenticate altered bundle bytes."""
    manifest, signature, bundle, public = _signed_bundle(tmp_path)
    data = bytearray(bundle.read_bytes())
    data[512] ^= 1
    bundle.write_bytes(data)
    with pytest.raises(InvalidUpdate, match="hash"):
        verify_bundle(manifest, signature, bundle, public, "0.0.0")


def test_modified_manifest_is_rejected(tmp_path: Path) -> None:
    """Modified version metadata cannot pass signature verification."""
    manifest, signature, bundle, public = _signed_bundle(tmp_path)
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(InvalidUpdate, match="signature"):
        verify_bundle(manifest, signature, bundle, public, "0.0.0")


@pytest.mark.parametrize("change", [
    {"format": 2},
    {"updater_protocol": 2},
    {"size": True},
    {"artifact": "../bundle.tar"},
    {"version": "2.0.0-rc1"},
    {"extra": "unexpected"},
])
def test_invalid_signed_metadata_is_rejected(tmp_path: Path, change: dict[str, object]) -> None:
    """Even correctly signed metadata must conform to the update protocol."""
    manifest, signature, bundle, public = _signed_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data.update(change)
    manifest.write_text(json.dumps(data))
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(tmp_path / "private.pem"),
         "-in", str(manifest), "-out", str(signature)],
        check=True,
    )
    with pytest.raises(InvalidUpdate):
        verify_bundle(manifest, signature, bundle, public, "0.0.0")


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
