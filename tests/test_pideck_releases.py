"""Tests for PiDeck's stable release discovery."""

import pytest

from pideck.infrastructure import pideck_releases
from pideck.infrastructure.pideck_releases import ReleaseCheckError, version_key


def _release(tag: str, **fields: object) -> dict[str, object]:
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [{"name": name} for name in pideck_releases._REQUIRED_ASSETS],
        **fields,
    }


def test_stable_release_comparison_is_numeric() -> None:
    """Numeric versions are compared by component, not lexicographically."""
    assert version_key("1.10.0") > version_key("1.9.9")
    for invalid in ("v1.0.0", "1.02.0", "1.0.0-rc1", "1.0"):
        with pytest.raises(ValueError):
            version_key(invalid)


def test_discovery_ignores_drafts_and_prereleases(monkeypatch) -> None:
    """Only stable releases can be reported as available."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: [
        _release("v1.2.0"),
        _release("v1.10.0"),
        _release("v2.0.0", prerelease=True),
        _release("v3.0.0", draft=True),
        _release("v5.0.0-rc1"),
    ])
    assert pideck_releases.find_latest_release().version == "1.10.0"


def test_unsigned_release_requires_only_manifest_and_bundle(monkeypatch) -> None:
    """A stable release is discoverable without a signature asset."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: [
        _release("v1.0.0", assets=[
            {"name": "pideck-update.json"}, {"name": "pideck-update.tar"},
        ]),
    ])
    assert pideck_releases.find_latest_release().version == "1.0.0"


def test_discovery_reports_incomplete_latest_release(monkeypatch) -> None:
    """A broken latest release is not hidden by reporting an older one."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: [
        _release("v1.0.0"),
        _release("v2.0.0", assets=[]),
    ])
    with pytest.raises(ReleaseCheckError, match="missing update assets"):
        pideck_releases.find_latest_release()


def test_discovery_reads_later_pages(monkeypatch) -> None:
    """A higher version on a later page is not missed."""
    pages = {1: [_release("v1.0.0")] * 100, 2: [_release("v2.0.0")]}
    monkeypatch.setattr(pideck_releases, "_read_page", lambda page: pages[page])
    assert pideck_releases.find_latest_release().version == "2.0.0"


def test_discovery_fails_on_incomplete_pagination(monkeypatch) -> None:
    """A page limit cannot silently turn incomplete data into a result."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: [_release("v1.0.0")] * 100)
    with pytest.raises(ReleaseCheckError, match="Too many"):
        pideck_releases.find_latest_release()


def test_discovery_rejects_malformed_response(monkeypatch) -> None:
    """Malformed response entries cannot be mistaken for an empty release list."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: ["bad entry"])
    with pytest.raises(ReleaseCheckError, match="Invalid release"):
        pideck_releases.find_latest_release()


def test_discovery_collects_bounded_release_notes(monkeypatch) -> None:
    """Notes of stable releases are kept newest first without control characters."""
    monkeypatch.setattr(pideck_releases, "_read_page", lambda _: [
        _release("v1.0.0", body="- old"),
        _release("v1.2.0", body="- newest\r\n\x1b[31mred\x07"),
        _release("v1.1.0", body=None),
        _release("v2.0.0", prerelease=True, body="- beta"),
        _release("v1.3.0", draft=True, body="- draft"),
    ])
    release = pideck_releases.find_latest_release()
    assert release.version == "1.2.0"
    assert [(note.version, note.text) for note in release.notes] == [
        ("1.2.0", "- newest\n[31mred"), ("1.1.0", ""), ("1.0.0", "- old"),
    ]
    assert len(pideck_releases._clean_notes("x" * 20000)) == pideck_releases._MAX_NOTES_CHARACTERS
