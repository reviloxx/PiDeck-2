"""Tests for persisted GitHub API cooldown and release cache."""

from pathlib import Path

from pideck.infrastructure.github_api_state import GitHubApiState
from pideck.infrastructure.http_retry import GitHubRateLimitError
import pytest


def test_cooldown_blocks_until_not_before(tmp_path: Path) -> None:
    """Active cooldowns are readable and raise until the resume time."""
    state = GitHubApiState(tmp_path)
    state.save_cooldown(2000.0, "wait")
    assert state.active_cooldown(now=1500.0) is not None
    with pytest.raises(GitHubRateLimitError):
        state.raise_if_cooling_down(now=1500.0)
    assert state.active_cooldown(now=2000.0) is None


def test_release_cache_round_trip(tmp_path: Path) -> None:
    """Cached release metadata survives a reload."""
    state = GitHubApiState(tmp_path)
    state.save_release("1.2.3", "v1.2.3", (("1.2.3", "- notes"), ("1.2.2", "")), fetched_at=10.0)
    cached = state.load_release()
    assert cached is not None
    assert cached.version == "1.2.3"
    assert cached.tag == "v1.2.3"
    assert cached.notes == (("1.2.3", "- notes"), ("1.2.2", ""))
    assert cached.fetched_at == 10.0
