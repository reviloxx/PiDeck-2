"""Verify and safely unpack signed PiDeck image update artifacts."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

from pideck.infrastructure.pideck_releases import version_key


_MAX_MANIFEST_SIZE = 16 * 1024
_MAX_BUNDLE_SIZE = 256 * 1024 * 1024
_MAX_FILES = 10000
_DIGEST = re.compile(r"[0-9a-f]{64}")


class InvalidUpdate(Exception):
    """A downloaded update is invalid, untrusted or incompatible."""


def verify_bundle(
    manifest_path: Path,
    signature_path: Path,
    bundle_path: Path,
    public_key: Path,
    current_version: str,
) -> str:
    """Authenticate metadata and confirm that the matching bundle is a newer release."""
    if manifest_path.stat().st_size > _MAX_MANIFEST_SIZE:
        raise InvalidUpdate("Update manifest is too large")
    if signature_path.stat().st_size != 64:
        raise InvalidUpdate("Invalid Ed25519 signature size")
    result = subprocess.run(
        [
            "/usr/bin/openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public_key),
            "-in", str(manifest_path), "-sigfile", str(signature_path),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise InvalidUpdate("Update signature verification failed")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidUpdate("Invalid signed update manifest") from error
    expected_keys = {"format", "updater_protocol", "version", "artifact", "size", "sha256"}
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise InvalidUpdate("Unexpected update manifest fields")
    if type(manifest["format"]) is not int or manifest["format"] != 1:
        raise InvalidUpdate("Unsupported update format")
    if type(manifest["updater_protocol"]) is not int or manifest["updater_protocol"] != 1:
        raise InvalidUpdate("Incompatible updater protocol")
    version = manifest["version"]
    if not isinstance(version, str):
        raise InvalidUpdate("Invalid update version")
    try:
        if version_key(version) <= version_key(current_version):
            raise InvalidUpdate("Update is not newer than the installed release")
    except ValueError as error:
        raise InvalidUpdate("Invalid stable version") from error
    size = manifest["size"]
    digest = manifest["sha256"]
    if (
        manifest["artifact"] != "pideck-update.tar"
        or type(size) is not int
        or not 0 < size <= _MAX_BUNDLE_SIZE
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        raise InvalidUpdate("Invalid signed artifact metadata")
    if bundle_path.stat().st_size != size:
        raise InvalidUpdate("Update bundle size does not match signed metadata")
    hasher = hashlib.sha256()
    with bundle_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise InvalidUpdate("Update bundle hash does not match signed metadata")
    return version


def unpack_bundle(bundle_path: Path, destination: Path) -> None:
    """Unpack only regular application files into a new private directory.

    The caller must ensure the destination parent is not writable by
    untrusted users and remove the destination if unpacking fails.
    """
    destination.mkdir(mode=0o700)
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(bundle_path, "r:") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or not path.parts
                    or any(part in ("", ".", "..") for part in member.name.split("/"))
                    or member.name != path.as_posix()
                    or not (member.name.startswith("python/pideck/") or member.name.startswith("assets/"))
                    or member.name in seen
                ):
                    raise InvalidUpdate(f"Unsafe update archive member: {member.name!r}")
                seen.add(member.name)
                total += member.size
                if len(seen) > _MAX_FILES or total > _MAX_BUNDLE_SIZE:
                    raise InvalidUpdate("Update archive exceeds extraction limits")
                source = archive.extractfile(member)
                if source is None:
                    raise InvalidUpdate(f"Unable to read update member: {member.name!r}")
                target = destination.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(0o644)
    except (tarfile.TarError, OSError) as error:
        raise InvalidUpdate(f"Unable to extract update bundle: {error}") from error
    if not (destination / "python/pideck/bootstrap.py").is_file():
        raise InvalidUpdate("Update bundle is missing the PiDeck entry point")
