"""Discover stable PiDeck releases without changing the installed application."""

from dataclasses import dataclass
import json
import re
from urllib.error import URLError
from urllib.request import Request, urlopen


_RELEASES_URL = "https://api.github.com/repos/reviloxx/PiDeck-2/releases"
_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_PAGES = 20
_PAGE_SIZE = 100
_REQUIRED_ASSETS = frozenset({"pideck-update.tar", "pideck-update.json", "pideck-update.sig"})


class ReleaseCheckError(Exception):
    """A release check could not establish reliable availability."""


@dataclass(frozen=True, slots=True)
class StableRelease:
    """A stable release whose update assets are present."""

    version: str
    tag: str


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
            if latest is None or version_key(release.version) > version_key(latest.version):
                latest = release
                latest_has_assets = _REQUIRED_ASSETS.issubset(names)
        if len(releases) < _PAGE_SIZE:
            if latest is not None and not latest_has_assets:
                raise ReleaseCheckError(f"Latest stable release {latest.tag} is missing update assets")
            return latest
    raise ReleaseCheckError("Too many release pages to determine the latest version")
