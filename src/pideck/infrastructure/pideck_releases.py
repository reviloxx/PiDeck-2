"""Discover stable PiDeck releases without changing the installed application."""

from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pideck.application.pideck_updates import ReleaseNote
from pideck.infrastructure.github_api_state import GitHubApiState, default_github_api_state_dir
from pideck.infrastructure.http_retry import (
    GitHubRateLimitError,
    call_with_retries,
    github_rate_limit_error,
    is_github_rate_limit,
)


_LOGGER = logging.getLogger(__name__)
_RELEASES_URL = "https://api.github.com/repos/reviloxx/PiDeck-2/releases"
_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_PAGES = 20
_PAGE_SIZE = 100
_REQUIRED_ASSETS = frozenset({"pideck-update.tar", "pideck-update.json"})
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MAX_NOTES_CHARACTERS = 8000
_MAX_NOTES = 20


class ReleaseCheckError(Exception):
    """A release check could not establish reliable availability."""


@dataclass(frozen=True, slots=True)
class StableRelease:
    """A stable release whose update assets are present."""

    version: str
    tag: str
    notes: tuple[ReleaseNote, ...] = ()
    """Release notes of recent stable releases, newest first."""
    advisory: str | None = None
    """Optional notice such as a GitHub rate-limit wait message."""


@dataclass(frozen=True, slots=True)
class _PageFetch:
    """One releases API page and the remaining primary rate-limit budget."""

    body: bytes
    remaining: int | None


def _clean_notes(body: object) -> str:
    """Bound a release body and drop control characters other than line breaks and tabs."""
    if not isinstance(body, str):
        return ""
    text = _CONTROL.sub("", body.replace("\r\n", "\n"))[:_MAX_NOTES_CHARACTERS]
    return text.strip()


def version_key(version: str) -> tuple[int, int, int]:
    """Parse the stable MAJOR.MINOR.PATCH version used by image releases."""
    match = _TAG.fullmatch(f"v{version}")
    if match is None:
        raise ValueError(f"Invalid stable version: {version!r}")
    return int(match[1]), int(match[2]), int(match[3])


def _remaining_header(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("X-RateLimit-Remaining")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _fetch_page(page: int, state: GitHubApiState) -> _PageFetch:
    """Fetch one GitHub releases page body, retrying only non-rate-limit failures."""
    state.raise_if_cooling_down()
    request = Request(
        f"{_RELEASES_URL}?per_page={_PAGE_SIZE}&page={page}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PiDeck"},
    )

    def once() -> _PageFetch:
        state.raise_if_cooling_down()
        try:
            with urlopen(request, timeout=15) as response:
                return _PageFetch(response.read(_MAX_RESPONSE_BYTES + 1), _remaining_header(response))
        except HTTPError as error:
            if is_github_rate_limit(error):
                limited = github_rate_limit_error(error)
                state.save_cooldown(limited.not_before, limited.message)
                raise limited from error
            raise

    try:
        return call_with_retries(once, label=f"GitHub releases page {page}")
    except GitHubRateLimitError as error:
        state.save_cooldown(error.not_before, error.message)
        raise


def _read_page(page: int, state: GitHubApiState) -> tuple[list[object], int | None]:
    """Read one bounded GitHub API page or raise a useful error."""
    try:
        fetched = _fetch_page(page, state)
    except GitHubRateLimitError:
        raise
    except (OSError, URLError) as error:
        raise ReleaseCheckError(f"Unable to check GitHub releases: {error}") from error
    data = fetched.body
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ReleaseCheckError("GitHub release response is too large")
    try:
        releases = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseCheckError("Invalid GitHub release response") from error
    if not isinstance(releases, list):
        raise ReleaseCheckError("Unexpected GitHub release response")
    return releases, fetched.remaining


def _release_from_cache(state: GitHubApiState, advisory: str) -> StableRelease | None:
    cached = state.load_release()
    if cached is None:
        return None
    try:
        version_key(cached.version)
        notes = tuple(ReleaseNote(version, text) for version, text in cached.notes)
        for note in notes:
            version_key(note.version)
    except ValueError:
        _LOGGER.warning("Ignoring cached release with invalid versions")
        return None
    return StableRelease(cached.version, cached.tag, notes, advisory)


def _finish(
    latest: StableRelease | None,
    latest_has_assets: bool,
    notes: dict[str, str],
    state: GitHubApiState,
) -> StableRelease | None:
    if latest is not None and not latest_has_assets:
        raise ReleaseCheckError(f"Latest stable release {latest.tag} is missing update assets")
    if latest is None:
        return None
    newest = sorted(notes, key=version_key, reverse=True)[:_MAX_NOTES]
    release = replace(
        latest,
        notes=tuple(ReleaseNote(version, notes[version]) for version in newest),
    )
    state.save_release(
        release.version,
        release.tag,
        tuple((note.version, note.text) for note in release.notes),
    )
    return release


def find_latest_release(state_dir: Path | None = None) -> StableRelease | None:
    """Find the highest stable release, requiring its update assets to exist."""
    state = GitHubApiState(state_dir if state_dir is not None else default_github_api_state_dir())
    try:
        return _discover(state)
    except GitHubRateLimitError as error:
        cached = _release_from_cache(state, error.message)
        if cached is not None:
            _LOGGER.warning("%s; using cached release %s", error.message, cached.version)
            return cached
        raise ReleaseCheckError(error.message) from error


def _discover(state: GitHubApiState) -> StableRelease | None:
    """Page GitHub releases newest-first until the latest stable release is known."""
    state.raise_if_cooling_down()
    latest: StableRelease | None = None
    latest_has_assets = False
    notes: dict[str, str] = {}
    for page in range(1, _MAX_PAGES + 1):
        releases, remaining = _read_page(page, state)
        for item in releases:
            if not isinstance(item, dict):
                raise ReleaseCheckError("Invalid release in GitHub response")
            if item.get("draft") is not False or item.get("prerelease") is not False:
                continue
            tag = item.get("tag_name")
            if not isinstance(tag, str) or _TAG.fullmatch(tag) is None:
                continue
            assets = item.get("assets")
            if not isinstance(assets, list):
                raise ReleaseCheckError(f"Missing asset list for release {tag}")
            names = {
                asset["name"]
                for asset in assets
                if isinstance(asset, dict) and isinstance(asset.get("name"), str)
            }
            release = StableRelease(tag[1:], tag)
            notes[release.version] = _clean_notes(item.get("body"))
            if latest is None or version_key(release.version) > version_key(latest.version):
                latest = release
                latest_has_assets = _REQUIRED_ASSETS.issubset(names)
        short_page = len(releases) < _PAGE_SIZE
        enough_notes = len(notes) >= _MAX_NOTES
        # Newest-first: a complete latest plus enough notes means further pages are history only.
        if latest is not None and latest_has_assets and (short_page or enough_notes):
            return _finish(latest, latest_has_assets, notes, state)
        if short_page:
            return _finish(latest, latest_has_assets, notes, state)
        if remaining == 0:
            if latest is not None and latest_has_assets:
                _LOGGER.warning("GitHub rate-limit remaining is 0; stopping after page %s", page)
                return _finish(latest, latest_has_assets, notes, state)
            raise ReleaseCheckError("GitHub rate limit remaining is 0 before the latest release was found")
    raise ReleaseCheckError("Too many release pages to determine the latest version")
