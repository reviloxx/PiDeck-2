"""Tests for metadata in the running PiDeck application build."""

import json
from pathlib import Path

from pideck.infrastructure import build_info


def test_build_info_reads_current_release_and_rejects_bad_metadata(
    tmp_path: Path, monkeypatch,
) -> None:
    """Missing or malformed build metadata is never presented as a date."""
    module = tmp_path / "pideck" / "infrastructure" / "build_info.py"
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(build_info, "__file__", str(module))
    metadata = module.parent.parent / "_build_info.json"
    assert build_info.installed_build_date() is None
    metadata.write_text(json.dumps({"built_at": "2026-01-01T10:00:00+00:00"}))
    assert build_info.installed_build_date() == "2026-01-01T10:00:00+00:00"
    metadata.write_text(json.dumps({"built_at": "2026-01-01T10:00:00"}))
    assert build_info.installed_build_date() is None
    metadata.write_text("{")
    assert build_info.installed_build_date() is None
