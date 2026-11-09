"""Maintain PiDeck release-check state independently of the settings window."""

from dataclasses import dataclass
from enum import StrEnum
import logging
import threading
from typing import Callable, Mapping, Protocol


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReleaseNote:
    """Markdown release notes of one published PiDeck version (English)."""

    version: str
    text: str


class Release(Protocol):
    """Release metadata required by the update service."""

    @property
    def version(self) -> str:
        """Return the stable release version."""
        ...

    @property
    def notes(self) -> tuple[ReleaseNote, ...]:
        """Return notes of recent releases, newest first."""
        ...


class PiDeckUpdateStatus(StrEnum):
    """States of the PiDeck self-update workflow."""

    UNSUPPORTED = "unsupported"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    PREPARING = "preparing"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PiDeckUpdateInfo:
    """Current installed version and discovered update status."""

    status: PiDeckUpdateStatus
    current_version: str | None = None
    available_version: str | None = None
    message: str | None = None
    release_notes: tuple[ReleaseNote, ...] = ()
    """Notes of every release newer than the installed one, newest first."""


class PiDeckUpdateService:
    """Serialize release checks on a worker while retaining observable state."""

    def __init__(
        self,
        current_version: str | None,
        discover: Callable[[], Release | None],
        version_key: Callable[[str], tuple[int, int, int]],
        on_change: Callable[[PiDeckUpdateInfo], None],
        inspect: Callable[[], Mapping[str, str]] | None = None,
        prepare: Callable[[str], None] | None = None,
        ready: Callable[[], None] | None = None,
    ) -> None:
        """Create a service for a release image, or unsupported development install."""
        self._current_version = current_version
        self._discover = discover
        self._version_key = version_key
        self._on_change = on_change
        self._inspect = inspect
        self._prepare = prepare
        self._ready = ready
        self._lock = threading.Lock()
        self._checking = False
        self._closed = False
        self._state = PiDeckUpdateInfo(
            PiDeckUpdateStatus.UNSUPPORTED if current_version is None else PiDeckUpdateStatus.CHECKING,
            current_version,
        )

    @property
    def state(self) -> PiDeckUpdateInfo:
        """Return the most recently published immutable state."""
        with self._lock:
            return self._state

    def check_async(self) -> bool:
        """Start a check unless the installation is unsupported or one is active."""
        with self._lock:
            if self._closed or self._checking or self._current_version is None:
                return False
            self._checking = True
            self._state = PiDeckUpdateInfo(PiDeckUpdateStatus.CHECKING, self._current_version)
            state = self._state
        self._on_change(state)
        threading.Thread(target=self._check, name="pideck-release-check", daemon=True).start()
        return True

    def close(self) -> None:
        """Prevent publication of results after shutdown."""
        with self._lock:
            self._closed = True

    def prepare_async(self) -> bool:
        """Prepare the available release on a worker without blocking the UI."""
        with self._lock:
            version = self._state.available_version
            can_prepare = self._state.status in {
                PiDeckUpdateStatus.AVAILABLE, PiDeckUpdateStatus.FAILED,
            }
            if (
                self._closed or self._checking or self._prepare is None
                or not can_prepare or version is None
            ):
                return False
            self._checking = True
            notes = self._state.release_notes
            self._state = PiDeckUpdateInfo(
                PiDeckUpdateStatus.PREPARING, self._current_version, version, release_notes=notes,
            )
            state = self._state
        self._on_change(state)
        threading.Thread(
            target=self._prepare_release, args=(version, notes), name="pideck-release-prepare", daemon=True,
        ).start()
        return True

    def _publish(self, state: PiDeckUpdateInfo) -> None:
        with self._lock:
            self._checking = False
            if self._closed:
                return
            self._state = state
        self._on_change(state)

    def _prepare_release(self, version: str, notes: tuple[ReleaseNote, ...] = ()) -> None:
        assert self._prepare is not None
        try:
            self._prepare(version)
            state = PiDeckUpdateInfo(PiDeckUpdateStatus.PENDING, self._current_version, version, release_notes=notes)
        except Exception as error:
            _LOGGER.exception("PiDeck update preparation failed")
            state = PiDeckUpdateInfo(
                PiDeckUpdateStatus.FAILED, self._current_version, version, str(error), notes,
            )
        self._publish(state)

    def _check(self) -> None:
        """Discover the latest release and publish its result."""
        current = self._current_version
        assert current is not None
        previous_failure: str | None = None
        try:
            if self._ready is not None:
                self._ready()
            if self._inspect is not None:
                local = self._inspect()
                if local.get("current_version") != current:
                    raise ValueError("Running and active PiDeck versions do not match")
                if local.get("phase") in {"pending", "activating"}:
                    self._publish(PiDeckUpdateInfo(PiDeckUpdateStatus.PENDING, current, local.get("version")))
                    return
                if local.get("phase") == "failed":
                    previous_failure = local.get("message", "The previous update failed")
                elif local.get("phase") != "idle":
                    raise ValueError("Unexpected image updater state")
            release = self._discover()
            if release is None:
                state = PiDeckUpdateInfo(
                    PiDeckUpdateStatus.FAILED, current,
                    message=previous_failure or "No stable update release is available",
                )
            else:
                available = self._version_key(release.version) > self._version_key(current)
                state = PiDeckUpdateInfo(
                    PiDeckUpdateStatus.AVAILABLE if available else (
                        PiDeckUpdateStatus.FAILED if previous_failure else PiDeckUpdateStatus.UP_TO_DATE
                    ),
                    current,
                    release.version if available else None,
                    previous_failure,
                    self._newer_notes(release, current) if available else (),
                )
        except Exception as error:
            _LOGGER.exception("PiDeck release check failed")
            message = f"{previous_failure}; {error}" if previous_failure else str(error)
            state = PiDeckUpdateInfo(PiDeckUpdateStatus.FAILED, current, message=message)
        self._publish(state)

    def _newer_notes(self, release: Release, current: str) -> tuple[ReleaseNote, ...]:
        """Keep notes of versions after ``current`` up to the available release."""
        try:
            notes = tuple(getattr(release, "notes", ()))
            installed, latest = self._version_key(current), self._version_key(release.version)
            return tuple(
                note for note in notes
                if installed < self._version_key(note.version) <= latest and note.text.strip()
            )
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid PiDeck release notes")
            return ()
