"""Root-owned staging and crash-recoverable activation of image releases."""

from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pideck.infrastructure.pideck_releases import version_key
from pideck.infrastructure.signed_update import InvalidUpdate, unpack_bundle, verify_bundle


_LOGGER = logging.getLogger(__name__)
_ARTIFACT_LIMITS = {
    "pideck-update.json": 16 * 1024,
    "pideck-update.sig": 64,
    "pideck-update.tar": 256 * 1024 * 1024,
}
_DOWNLOAD_BASE = "https://github.com/reviloxx/PiDeck-2/releases/download"
_DOWNLOAD_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_tree(root: Path) -> None:
    """Make extracted content durable before publishing its directory."""
    for directory, _, filenames in os.walk(root, topdown=False):
        path = Path(directory)
        for name in filenames:
            with (path / name).open("rb") as stream:
                os.fsync(stream.fileno())
        _sync_directory(path)


def _remove_interrupted_directories(parent: Path, prefix: str) -> None:
    """Remove only updater-created temporary directories under a trusted parent."""
    for path in parent.iterdir():
        if not path.name.startswith(prefix):
            continue
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            raise InvalidUpdate(f"Unexpected updater temporary file: {path.name}")


class _ReleaseRedirectHandler(HTTPRedirectHandler):
    """Only allow HTTPS redirects to GitHub's release download hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _DOWNLOAD_HOSTS:
            raise InvalidUpdate("Unexpected release download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(version: str, name: str, target: Path) -> None:
    """Download a bounded fixed-name GitHub release asset."""
    limit = _ARTIFACT_LIMITS[name]
    request = Request(f"{_DOWNLOAD_BASE}/v{version}/{name}", headers={"User-Agent": "PiDeck"})
    with build_opener(_ReleaseRedirectHandler()).open(request, timeout=30) as response:
        with target.open("xb") as output:
            remaining = limit
            while True:
                data = response.read(min(1024 * 1024, remaining + 1))
                if not data:
                    break
                if len(data) > remaining:
                    raise InvalidUpdate(f"Release asset {name} is too large")
                output.write(data)
                remaining -= len(data)
            output.flush()
            os.fsync(output.fileno())


class ImageUpdateStore:
    """Stage verified releases and activate them atomically at image startup."""

    def __init__(self, root: Path, public_key: Path) -> None:
        """Use an image-owned root directory and pinned public key."""
        self.root = root
        self.public_key = public_key

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize staging, activation and readiness across updater processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".update-lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _state(self) -> dict[str, str]:
        path = self.root / "update-state.json"
        if not path.exists():
            return {"phase": "idle"}
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in state.items()):
            raise InvalidUpdate("Invalid updater state")
        if state.get("phase") not in {"idle", "pending", "activating", "failed"}:
            raise InvalidUpdate("Unknown updater state")
        return state

    def _write_state(self, state: dict[str, str]) -> None:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(state, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.root / "update-state.json")
                self._sync_root()
            finally:
                temporary.unlink(missing_ok=True)

    def _sync_root(self) -> None:
        _sync_directory(self.root)

    def _current(self) -> str:
        target = os.readlink(self.root / "current")
        if not target.startswith("releases/") or target.count("/") != 1:
            raise InvalidUpdate("Invalid active release pointer")
        version = target.removeprefix("releases/")
        version_key(version)
        if not (self.root / target).is_dir():
            raise InvalidUpdate("Active release directory is missing")
        return version

    def _switch(self, version: str) -> None:
        version_key(version)
        if not (self.root / "releases" / version).is_dir():
            raise InvalidUpdate("Release directory is missing")
        temporary = self.root / ".current-next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(f"releases/{version}")
        os.replace(temporary, self.root / "current")
        self._sync_root()

    def status(self) -> dict[str, str]:
        """Return the active version and durable update state."""
        with self._locked():
            return {**self._state(), "current_version": self._current()}

    def stage(self, version: str) -> None:
        """Download and authenticate a newer release into root-owned storage."""
        version_key(version)
        with self._locked():
            current = self._current()
            state = self._state()
            if state["phase"] in {"pending", "activating"}:
                raise InvalidUpdate("A PiDeck update is already pending")
            if version_key(version) <= version_key(current):
                raise InvalidUpdate("Update is not newer than the installed release")
            _remove_interrupted_directories(self.root, ".download-")
            staged = self.root / "staged"
            if staged.exists():
                shutil.rmtree(staged)
            with tempfile.TemporaryDirectory(prefix=".download-", dir=self.root) as temporary:
                directory = Path(temporary)
                for name in _ARTIFACT_LIMITS:
                    _download(version, name, directory / name)
                verified = verify_bundle(
                    directory / "pideck-update.json", directory / "pideck-update.sig",
                    directory / "pideck-update.tar", self.public_key, current,
                )
                if verified != version:
                    raise InvalidUpdate("Release tag does not match signed version")
                _sync_directory(directory)
                os.rename(directory, staged)
                self._sync_root()
                self._write_state({"phase": "pending", "version": version})

    def activate_at_startup(self) -> None:
        """Activate a staged release or roll back an unconfirmed earlier start."""
        with self._locked():
            state = self._state()
            if state["phase"] == "activating":
                previous = state["previous"]
                self._switch(previous)
                self._write_state({"phase": "failed", "message": "Previous update failed to start and was rolled back"})
                return
            if state["phase"] != "pending":
                return
            current = self._current()
            version = state["version"]
            staged = self.root / "staged"
            try:
                verified = verify_bundle(
                    staged / "pideck-update.json", staged / "pideck-update.sig",
                    staged / "pideck-update.tar", self.public_key, current,
                )
                if verified != version:
                    raise InvalidUpdate("Pending release does not match signed version")
                releases = self.root / "releases"
                releases.mkdir(exist_ok=True)
                _remove_interrupted_directories(releases, ".extract-")
                destination = releases / version
                if destination.exists():
                    shutil.rmtree(destination)
                with tempfile.TemporaryDirectory(prefix=".extract-", dir=releases) as temporary:
                    unpacked = Path(temporary) / "release"
                    unpack_bundle(staged / "pideck-update.tar", unpacked)
                    (unpacked / "config").mkdir()
                    unpacked.chmod(0o755)
                    _sync_tree(unpacked)
                    os.rename(unpacked, destination)
                    _sync_directory(releases)
                self._write_state({"phase": "activating", "version": version, "previous": current})
                self._switch(version)
            except (OSError, ValueError, InvalidUpdate) as error:
                _LOGGER.exception("Unable to activate PiDeck update")
                self._switch(current)
                self._write_state({"phase": "failed", "message": f"Update activation failed: {error}"})

    def mark_ready(self, version: str) -> None:
        """Confirm the newly activated application reached launcher readiness."""
        with self._locked():
            state = self._state()
            if state["phase"] == "activating":
                if state.get("version") != version or self._current() != version:
                    raise InvalidUpdate("Ready signal does not match the active release")
                self._write_state({"phase": "idle"})
                staged = self.root / "staged"
                if staged.exists():
                    shutil.rmtree(staged)
                    self._sync_root()
