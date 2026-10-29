"""Maintain PiDeck release-check state independently of the settings window."""

from dataclasses import dataclass
from enum import StrEnum
import logging
import threading
from typing import Callable, Mapping, Protocol


_LOGGER = logging.getLogger(__name__)


class Release(Protocol):
    """Release metadata required by the update service."""

    @property
    def version(self) -> str:
        """Return the stable release version."""
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
            if (
                self._closed or self._checking or self._prepare is None
                or self._state.status is not PiDeckUpdateStatus.AVAILABLE or version is None
            ):
                return False
            self._checking = True
            self._state = PiDeckUpdateInfo(PiDeckUpdateStatus.PREPARING, self._current_version, version)
            state = self._state
        self._on_change(state)
        threading.Thread(target=self._prepare_release, args=(version,), name="pideck-release-prepare", daemon=True).start()
        return True

    def _publish(self, state: PiDeckUpdateInfo) -> None:
        with self._lock:
            self._checking = False
            if self._closed:
                return
            self._state = state
        self._on_change(state)

    def _prepare_release(self, version: str) -> None:
        assert self._prepare is not None
        try:
            self._prepare(version)
            state = PiDeckUpdateInfo(PiDeckUpdateStatus.PENDING, self._current_version, version)
        except Exception as error:
            _LOGGER.exception("PiDeck update preparation failed")
            state = PiDeckUpdateInfo(PiDeckUpdateStatus.FAILED, self._current_version, message=str(error))
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
                )
        except Exception as error:
            _LOGGER.exception("PiDeck release check failed")
            message = f"{previous_failure}; {error}" if previous_failure else str(error)
            state = PiDeckUpdateInfo(PiDeckUpdateStatus.FAILED, current, message=message)
        self._publish(state)
