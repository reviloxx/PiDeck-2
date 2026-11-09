"""Tests for PiDeck's stable release discovery."""

from email.message import EmailMessage
from io import BytesIO
import json
from pathlib import Path

import pytest
from urllib.error import HTTPError

from pideck.infrastructure import pideck_releases
from pideck.infrastructure.github_api_state import GitHubApiState
from pideck.infrastructure.pideck_releases import ReleaseCheckError, StableRelease, version_key


def _release(tag: str, **fields: object) -> dict[str, object]:
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [{"name": name} for name in pideck_releases._REQUIRED_ASSETS],
        **fields,
    }


def _stub_pages(monkeypatch: pytest.MonkeyPatch, pages: dict[int, list[object]] | list[object]) -> None:
    def read_page(page: int, _state: GitHubApiState) -> tuple[list[object], int | None]:
        if isinstance(pages, list):
            return pages, None
        return pages[page], None

    monkeypatch.setattr(pideck_releases, "_read_page", read_page)


def test_stable_release_comparison_is_numeric() -> None:
    """Numeric versions are compared by component, not lexicographically."""
    assert version_key("1.10.0") > version_key("1.9.9")
    for invalid in ("v1.0.0", "1.02.0", "1.0.0-rc1", "1.0"):
        with pytest.raises(ValueError):
            version_key(invalid)


def test_discovery_ignores_drafts_and_prereleases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only stable releases can be reported as available."""
    _stub_pages(monkeypatch, [
        _release("v1.2.0"),
        _release("v1.10.0"),
        _release("v2.0.0", prerelease=True),
        _release("v3.0.0", draft=True),
        _release("v5.0.0-rc1"),
    ])
    assert pideck_releases.find_latest_release(tmp_path).version == "1.10.0"


def test_unsigned_release_requires_only_manifest_and_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A stable release is discoverable without a signature asset."""
    _stub_pages(monkeypatch, [
        _release("v1.0.0", assets=[
            {"name": "pideck-update.json"}, {"name": "pideck-update.tar"},
        ]),
    ])
    assert pideck_releases.find_latest_release(tmp_path).version == "1.0.0"


def test_discovery_reports_incomplete_latest_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A broken latest release is not hidden by reporting an older one."""
    _stub_pages(monkeypatch, [
        _release("v1.0.0"),
        _release("v2.0.0", assets=[]),
    ])
    with pytest.raises(ReleaseCheckError, match="missing update assets"):
        pideck_releases.find_latest_release(tmp_path)


def test_discovery_reads_later_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A higher version on a later page is not missed."""
    _stub_pages(monkeypatch, {1: [_release("v1.0.0")] * 100, 2: [_release("v2.0.0")]})
    assert pideck_releases.find_latest_release(tmp_path).version == "2.0.0"


def test_discovery_fails_on_incomplete_pagination(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A page limit cannot silently turn incomplete data into a result."""
    _stub_pages(monkeypatch, [_release("v1.0.0")] * 100)
    with pytest.raises(ReleaseCheckError, match="Too many"):
        pideck_releases.find_latest_release(tmp_path)


def test_discovery_rejects_malformed_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Malformed response entries cannot be mistaken for an empty release list."""
    _stub_pages(monkeypatch, ["bad entry"])
    with pytest.raises(ReleaseCheckError, match="Invalid release"):
        pideck_releases.find_latest_release(tmp_path)


def test_discovery_collects_bounded_release_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Notes of stable releases are kept newest first without control characters."""
    _stub_pages(monkeypatch, [
        _release("v1.0.0", body="- old"),
        _release("v1.2.0", body="- newest\r\n\x1b[31mred\x07"),
        _release("v1.1.0", body=None),
        _release("v2.0.0", prerelease=True, body="- beta"),
        _release("v1.3.0", draft=True, body="- draft"),
    ])
    release = pideck_releases.find_latest_release(tmp_path)
    assert release.version == "1.2.0"
    assert [(note.version, note.text) for note in release.notes] == [
        ("1.2.0", "- newest\n[31mred"), ("1.1.0", ""), ("1.0.0", "- old"),
    ]
    assert len(pideck_releases._clean_notes("x" * 20000)) == pideck_releases._MAX_NOTES_CHARACTERS


def test_discovery_stops_after_enough_notes_on_a_full_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Newest-first history is not walked once the latest release and notes are known."""
    page = [_release(f"v1.{index}.0") for index in range(pideck_releases._MAX_NOTES, -1, -1)]
    assert len(page) == pideck_releases._MAX_NOTES + 1
    calls = {"count": 0}

    def read_page(page_number: int, _state: GitHubApiState) -> tuple[list[object], int | None]:
        calls["count"] += 1
        assert page_number == 1
        return page, None

    monkeypatch.setattr(pideck_releases, "_read_page", read_page)
    release = pideck_releases.find_latest_release(tmp_path)
    assert release.version == f"v1.{pideck_releases._MAX_NOTES}.0"[1:]
    assert calls["count"] == 1


def test_discovery_stops_when_rate_limit_remaining_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A successful page with remaining=0 must not trigger another request."""
    def read_page(page: int, _state: GitHubApiState) -> tuple[list[object], int | None]:
        assert page == 1
        return [_release("v1.5.0")] * 100, 0

    monkeypatch.setattr(pideck_releases, "_read_page", read_page)
    assert pideck_releases.find_latest_release(tmp_path).version == "1.5.0"


def test_read_page_retries_transient_gateway_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A single GitHub API 504 does not fail release discovery."""
    payload = json.dumps([_release("v1.4.0")]).encode()
    attempts = {"count": 0}

    class Response:
        headers = EmailMessage()

        def read(self, _size: int) -> bytes:
            return payload

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def urlopen(_request, timeout=15):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(
                "https://api.github.com/repos/reviloxx/PiDeck-2/releases",
                504, "Gateway Timeout", EmailMessage(), BytesIO(),
            )
        return Response()

    monkeypatch.setattr(pideck_releases, "urlopen", urlopen)
    monkeypatch.setattr("pideck.infrastructure.http_retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    releases, remaining = pideck_releases._read_page(1, GitHubApiState(tmp_path))
    assert releases[0]["tag_name"] == "v1.4.0"
    assert remaining is None
    assert attempts["count"] == 2


def test_rate_limit_persists_cooldown_and_blocks_follow_up_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A rate-limited response writes cooldown and blocks the next network call."""
    attempts = {"count": 0}
    headers = EmailMessage()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "9999999999"

    def urlopen(_request, timeout=15):
        attempts["count"] += 1
        raise HTTPError(
            "https://api.github.com/repos/reviloxx/PiDeck-2/releases",
            403, "rate limit", headers, BytesIO(b"API rate limit exceeded"),
        )

    monkeypatch.setattr(pideck_releases, "urlopen", urlopen)
    with pytest.raises(ReleaseCheckError, match="GitHub rate limit"):
        pideck_releases.find_latest_release(tmp_path)
    assert attempts["count"] == 1
    with pytest.raises(ReleaseCheckError, match="GitHub rate limit"):
        pideck_releases.find_latest_release(tmp_path)
    assert attempts["count"] == 1


def test_rate_limit_uses_cached_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """While cooling down, the last successful discovery is still offered."""
    state = GitHubApiState(tmp_path)
    state.save_release("1.7.0", "v1.7.0", (("1.7.0", "- newer"),))
    state.save_cooldown(9999999999.0, "GitHub rate limit; next check after 12:00 UTC")

    def fail_read(_page: int, _state: GitHubApiState) -> tuple[list[object], int | None]:
        raise AssertionError("network must not be used during cooldown")

    monkeypatch.setattr(pideck_releases, "_read_page", fail_read)
    release = pideck_releases.find_latest_release(tmp_path)
    assert isinstance(release, StableRelease)
    assert release.version == "1.7.0"
    assert release.advisory is not None
    assert "GitHub rate limit" in release.advisory


def test_non_rate_limit_forbidden_does_not_write_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A plain 403 without rate-limit signals must not start a long cooldown."""
    def urlopen(_request, timeout=15):
        raise HTTPError(
            "https://api.github.com/repos/reviloxx/PiDeck-2/releases",
            403, "Forbidden", EmailMessage(), BytesIO(b"Repository access blocked"),
        )

    monkeypatch.setattr(pideck_releases, "urlopen", urlopen)
    with pytest.raises(ReleaseCheckError, match="Unable to check GitHub releases"):
        pideck_releases.find_latest_release(tmp_path)
    assert GitHubApiState(tmp_path).active_cooldown() is None
