"""Tests for the unsigned PiDeck update release artifact."""

import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib

import pytest
import yaml

from image.update_bundle import create_bundle, release_version


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]
TAG = f"v{VERSION}"


def test_release_workflow_publishes_without_signing() -> None:
    """Release publishing builds the manifest and archive without signing keys."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/release-update.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["push"]["tags"] == ["v*"]
    steps = workflow["jobs"]["release"]["steps"]
    build = next(step for step in steps if step["name"] == "Build update")
    assert "image/update_bundle.py" in build["run"]
    assert "signing-key" not in build["run"]
    publish = next(step for step in steps if step["name"] == "Publish stable release")
    assert "--verify-tag" in publish["run"]
    for step in steps:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


def test_release_version_rejects_nonstable_or_mismatched_tags() -> None:
    """Only exactly matching stable tags are valid for release."""
    assert release_version(ROOT, TAG) == VERSION
    for tag in (VERSION, "v999999.0.0", f"{TAG}-rc1", "v00.1.0", f"{TAG}\n"):
        with pytest.raises(ValueError):
            release_version(ROOT, tag)


def test_bundle_is_reproducible_and_contains_only_application_files(tmp_path: Path) -> None:
    """Identical input produces identical unsigned artifacts."""
    first, second = tmp_path / "first", tmp_path / "second"
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    create_bundle(ROOT, first, TAG, timestamp=timestamp)
    create_bundle(ROOT, second, TAG, timestamp=timestamp)
    for name in ("pideck-update.tar", "pideck-update.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    third = tmp_path / "third"
    subprocess.run(
        [sys.executable, str(ROOT / "image/update_bundle.py"), "--tag", TAG,
         "--output", str(third), "--build-timestamp", timestamp.isoformat()],
        check=True,
    )
    for name in ("pideck-update.tar", "pideck-update.json"):
        assert (first / name).read_bytes() == (third / name).read_bytes()
    bundle = first / "pideck-update.tar"
    manifest = json.loads((first / "pideck-update.json").read_text())
    assert manifest["version"] == VERSION
    assert manifest["updater_protocol"] == 2
    assert not (first / "pideck-update.sig").exists()
    assert manifest["size"] == bundle.stat().st_size
    assert manifest["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    with tarfile.open(bundle) as archive:
        names = archive.getnames()
        assert "python/pideck/bootstrap.py" in names
        metadata = json.load(archive.extractfile("python/pideck/_build_info.json"))
        assert metadata["built_at"] == "2026-01-01T00:00:00+00:00"
        assert "assets/icons/settings.svg" in names
        assert all(name.startswith(("python/pideck/", "assets/")) for name in names)
        assert all(member.isfile() for member in archive.getmembers())
def test_bundle_rejects_symbolic_links(tmp_path: Path) -> None:
    """A release cannot accidentally archive a symbolic link."""
    project = tmp_path / "project"
    (project / "src/pideck").mkdir(parents=True)
    (project / "assets").mkdir()
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.0.0"\n')
    (project / "assets/link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="Symbolic links"):
        create_bundle(project, tmp_path / "out", "v1.0.0")
