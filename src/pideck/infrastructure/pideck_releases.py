"""Discover stable PiDeck releases without changing the installed application."""

from dataclasses import dataclass, replace
import json
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

from pideck.application.pideck_updates import ReleaseNote


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


def _read_page(page: int) -> list[object]:
    """Read one bounded GitHub API page or raise a useful error."""
    request = Request(
        f"{_RELEASES_URL}?per_page={_PAGE_SIZE}&page={page}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PiDeck"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, URLError) as error:
        raise ReleaseCheckError(f"Unable to check GitHub releases: {error}") from error
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ReleaseCheckError("GitHub release response is too large")
    try:
        releases = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseCheckError("Invalid GitHub release response") from error
    if not isinstance(releases, list):
        raise ReleaseCheckError("Unexpected GitHub release response")
    return releases


def find_latest_release() -> StableRelease | None:
    """Find the highest stable release, requiring its update assets to exist."""
    latest: StableRelease | None = None
    latest_has_assets = False
    notes: dict[str, str] = {}
    for page in range(1, _MAX_PAGES + 1):
        releases = _read_page(page)
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
        if len(releases) < _PAGE_SIZE:
            if latest is not None and not latest_has_assets:
                raise ReleaseCheckError(f"Latest stable release {latest.tag} is missing update assets")
            if latest is None:
                return None
            newest = sorted(notes, key=version_key, reverse=True)[:_MAX_NOTES]
            return replace(latest, notes=tuple(ReleaseNote(version, notes[version]) for version in newest))
    raise ReleaseCheckError("Too many release pages to determine the latest version")
