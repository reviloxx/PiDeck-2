"""Tests for publishing release notes from CHANGELOG.md."""

from pathlib import Path

import pytest

from image.release_notes import release_notes
from image.update_bundle import project_version

ROOT = Path(__file__).resolve().parents[1]


def test_current_version_has_release_notes() -> None:
    """A release cannot be tagged without notes for its version."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = release_notes(changelog, f"v{project_version(ROOT)}")
    assert notes.strip() and "## " not in notes


def test_extracts_only_the_requested_section() -> None:
    """Sections end at the next version heading; missing versions fail."""
    changelog = "# Changelog\n\nintro\n\n## v1.1.0\n\n- new\n\n### Details\n\nmore\n\n## v1.0.0\n\n- old\n"
    assert release_notes(changelog, "v1.1.0") == "- new\n\n### Details\n\nmore\n"
    assert release_notes(changelog, "v1.0.0") == "- old\n"
    with pytest.raises(ValueError, match="v2.0.0"):
        release_notes(changelog, "v2.0.0")
    with pytest.raises(ValueError):
        release_notes("## v1.0.0\n\n## v0.9.0\n- x\n", "v1.0.0")
