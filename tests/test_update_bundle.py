"""Tests for the signed PiDeck update release artifact."""

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

import pytest
import yaml

from image.update_bundle import create_bundle, release_version


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]
TAG = f"v{VERSION}"


def test_release_workflow_verifies_key_before_publishing() -> None:
    """Release publishing requires configured signing and matching trusted keys."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/release-update.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["push"]["tags"] == ["v*"]
    steps = workflow["jobs"]["release"]["steps"]
    build = next(step for step in steps if step["name"] == "Build and verify signed update")
    assert "PIDECK_RELEASE_SIGNING_KEY" in build["env"]["SIGNING_KEY"]
    assert "PIDECK_RELEASE_PUBLIC_KEY" in build["env"]["PUBLIC_KEY"]
    assert "openssl pkeyutl -verify" in build["run"]
    publish = next(step for step in steps if step["name"] == "Publish stable release")
    assert "--verify-tag" in publish["run"]
    for step in steps:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


def _key_pair(directory: Path) -> tuple[Path, Path]:
    private = directory / "private.pem"
    public = directory / "public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
    )
    return private, public


def test_release_version_rejects_nonstable_or_mismatched_tags() -> None:
    """Only exactly matching stable tags are valid for signing."""
    assert release_version(ROOT, TAG) == VERSION
    for tag in (VERSION, "v999999.0.0", f"{TAG}-rc1", "v00.1.0", f"{TAG}\n"):
        with pytest.raises(ValueError):
            release_version(ROOT, tag)


def test_bundle_is_reproducible_signed_and_contains_only_application_files(tmp_path: Path) -> None:
    """Identical input produces identical artifacts verified by the public key."""
    private, public = _key_pair(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    create_bundle(ROOT, first, TAG, private)
    create_bundle(ROOT, second, TAG, private)
    for name in ("pideck-update.tar", "pideck-update.json", "pideck-update.sig"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    bundle = first / "pideck-update.tar"
    manifest = json.loads((first / "pideck-update.json").read_text())
    assert manifest["version"] == VERSION
    assert manifest["size"] == bundle.stat().st_size
    assert manifest["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    with tarfile.open(bundle) as archive:
        names = archive.getnames()
        assert "python/pideck/bootstrap.py" in names
        assert "assets/icons/settings.svg" in names
        assert all(name.startswith(("python/pideck/", "assets/")) for name in names)
        assert all(member.isfile() for member in archive.getmembers())
    result = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public),
         "-in", str(first / "pideck-update.json"), "-sigfile", str(first / "pideck-update.sig")],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    (first / "pideck-update.json").write_text("{}\n")
    assert subprocess.run(result.args, capture_output=True, check=False).returncode != 0


def test_bundle_rejects_symbolic_links(tmp_path: Path) -> None:
    """A release cannot accidentally archive a symbolic link."""
    private, _ = _key_pair(tmp_path)
    project = tmp_path / "project"
    (project / "src/pideck").mkdir(parents=True)
    (project / "assets").mkdir()
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.0.0"\n')
    (project / "assets/link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="Symbolic links"):
        create_bundle(project, tmp_path / "out", "v1.0.0", private)
