"""Persist GitHub API cooldown and the last successful release discovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time

from pideck.infrastructure.http_retry import GitHubRateLimitError, format_rate_limit_message


_LOGGER = logging.getLogger(__name__)
_COOLDOWN_NAME = "github-api-cooldown.json"
_CACHE_NAME = "github-releases-cache.json"


def default_github_api_state_dir() -> Path:
    """Return the XDG cache directory used for GitHub API pacing state."""
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "pideck"


@dataclass(frozen=True, slots=True)
class GitHubApiCooldown:
    """Earliest time another api.github.com request is allowed."""

    not_before: float
    message: str


@dataclass(frozen=True, slots=True)
class CachedReleasePayload:
    """Last successful release discovery stored on disk."""

    version: str
    tag: str
    notes: tuple[tuple[str, str], ...]
    fetched_at: float


class GitHubApiState:
    """Disk-backed cooldown and last-good release cache for unauthenticated checks."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _cooldown_path(self) -> Path:
        return self.directory / _COOLDOWN_NAME

    def _cache_path(self) -> Path:
        return self.directory / _CACHE_NAME

    def active_cooldown(self, *, now: float | None = None) -> GitHubApiCooldown | None:
        """Return the active cooldown, or ``None`` when API calls are allowed."""
        clock = time.time() if now is None else now
        path = self._cooldown_path()
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            not_before = float(payload["not_before"])
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                message = format_rate_limit_message(not_before)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            _LOGGER.warning("Ignoring invalid GitHub API cooldown file: %s", error)
            return None
        if clock >= not_before:
            return None
        return GitHubApiCooldown(not_before, message)

    def raise_if_cooling_down(self, *, now: float | None = None) -> None:
        """Raise ``GitHubRateLimitError`` when a persisted cooldown is still active."""
        cooldown = self.active_cooldown(now=now)
        if cooldown is not None:
            raise GitHubRateLimitError(cooldown.not_before, cooldown.message)

    def save_cooldown(self, not_before: float, message: str | None = None) -> None:
        """Persist a cooldown so Retry and timers cannot hit the API early."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "not_before": float(not_before),
            "message": message or format_rate_limit_message(not_before),
        }
        path = self._cooldown_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def save_release(
        self,
        version: str,
        tag: str,
        notes: tuple[tuple[str, str], ...],
        *,
        fetched_at: float | None = None,
    ) -> None:
        """Remember the last successful discovery for use while rate-limited."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": time.time() if fetched_at is None else float(fetched_at),
            "version": version,
            "tag": tag,
            "notes": [{"version": version_, "text": text} for version_, text in notes],
        }
        path = self._cache_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load_release(self) -> CachedReleasePayload | None:
        """Return the last cached stable release, if the file is valid."""
        path = self._cache_path()
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            version = payload["version"]
            tag = payload["tag"]
            fetched_at = float(payload.get("fetched_at", 0))
            if not isinstance(version, str) or not isinstance(tag, str):
                raise ValueError("invalid cached release identity")
            raw_notes = payload.get("notes", [])
            if not isinstance(raw_notes, list):
                raise ValueError("invalid cached notes")
            notes: list[tuple[str, str]] = []
            for item in raw_notes:
                if not isinstance(item, dict):
                    raise ValueError("invalid cached note")
                note_version = item.get("version")
                text = item.get("text")
                if not isinstance(note_version, str) or not isinstance(text, str):
                    raise ValueError("invalid cached note fields")
                notes.append((note_version, text))
            return CachedReleasePayload(version, tag, tuple(notes), fetched_at)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            _LOGGER.warning("Ignoring invalid GitHub release cache: %s", error)
            return None
