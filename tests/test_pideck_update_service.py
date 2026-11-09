"""Tests for background PiDeck release checks."""

import threading
from types import SimpleNamespace

from pideck.application.pideck_updates import PiDeckUpdateService, PiDeckUpdateStatus
from pideck.infrastructure.pideck_releases import version_key


def test_release_check_serializes_and_reports_available_version() -> None:
    """A second check cannot overlap and the result persists without a UI."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def discover():
        started.set()
        assert release.wait(5)
        return SimpleNamespace(version="1.10.0")

    def changed(info):
        if info.status is PiDeckUpdateStatus.AVAILABLE:
            finished.set()

    service = PiDeckUpdateService("1.9.0", discover, version_key, changed)
    assert service.check_async()
    assert started.wait(5)
    assert not service.check_async()
    release.set()
    assert finished.wait(5)
    assert service.state.available_version == "1.10.0"
    service.close()


def test_unsupported_install_does_not_check() -> None:
    """Development installations do not attempt to self-update."""
    service = PiDeckUpdateService(None, lambda: None, version_key, lambda _: None)
    assert not service.check_async()
    assert service.state.status is PiDeckUpdateStatus.UNSUPPORTED


def test_check_failure_is_reported() -> None:
    """Network errors are surfaced instead of being reported as up to date."""
    finished = threading.Event()

    def discover():
        raise OSError("offline")

    service = PiDeckUpdateService("1.0.0", discover, version_key, lambda info: finished.set())
    assert service.check_async()
    # The initial CHECKING notification is synchronous; wait for the worker result.
    while service.state.status is PiDeckUpdateStatus.CHECKING:
        assert finished.wait(5)
        finished.clear()
    assert service.state.status is PiDeckUpdateStatus.FAILED
    assert service.state.message == "offline"


def test_available_release_can_be_prepared_and_stays_pending() -> None:
    """Preparation runs in the background and publishes restart-required state."""
    available = threading.Event()
    pending = threading.Event()
    prepared = []

    def changed(info):
        if info.status is PiDeckUpdateStatus.AVAILABLE:
            available.set()
        if info.status is PiDeckUpdateStatus.PENDING:
            pending.set()

    service = PiDeckUpdateService(
        "1.0.0", lambda: SimpleNamespace(version="1.1.0"), version_key, changed,
        inspect=lambda: {"phase": "idle", "current_version": "1.0.0"},
        prepare=prepared.append,
    )
    assert service.check_async()
    assert available.wait(5)
    assert service.prepare_async()
    assert pending.wait(5)
    assert prepared == ["1.1.0"]
    assert service.state.available_version == "1.1.0"
    service.close()


def test_durable_pending_state_avoids_network_check() -> None:
    """Reopened settings can display a previously staged update without the network."""
    finished = threading.Event()

    def discover():
        raise AssertionError("pending releases should not be checked")

    service = PiDeckUpdateService(
        "1.0.0", discover, version_key,
        lambda info: finished.set() if info.status is PiDeckUpdateStatus.PENDING else None,
        inspect=lambda: {"phase": "pending", "version": "1.1.0", "current_version": "1.0.0"},
    )
    assert service.check_async()
    assert finished.wait(5)
    assert service.state.available_version == "1.1.0"
    service.close()


def test_preparation_failure_is_visible() -> None:
    """Staging failures cannot be reported as a successful pending update."""
    available, failed = threading.Event(), threading.Event()

    def changed(info):
        if info.status is PiDeckUpdateStatus.AVAILABLE:
            available.set()
        if info.status is PiDeckUpdateStatus.FAILED:
            failed.set()

    def prepare(_version):
        raise OSError("disk full")

    service = PiDeckUpdateService(
        "1.0.0", lambda: SimpleNamespace(version="1.1.0"), version_key, changed, prepare=prepare,
    )
    assert service.check_async()
    assert available.wait(5)
    assert service.prepare_async()
    assert failed.wait(5)
    assert service.state.message == "disk full"
    assert service.state.available_version == "1.1.0"
    service.close()


def test_failed_prepare_can_be_retried() -> None:
    """Retry after a staging failure re-stages without another release check."""
    available, failed, pending = threading.Event(), threading.Event(), threading.Event()
    prepared: list[str] = []

    def changed(info):
        if info.status is PiDeckUpdateStatus.AVAILABLE:
            available.set()
        elif info.status is PiDeckUpdateStatus.FAILED:
            failed.set()
        elif info.status is PiDeckUpdateStatus.PENDING:
            pending.set()

    def prepare(version: str) -> None:
        prepared.append(version)
        if len(prepared) == 1:
            raise OSError("HTTP Error 504: Gateway Timeout")

    service = PiDeckUpdateService(
        "1.0.0", lambda: SimpleNamespace(version="1.1.0"), version_key, changed, prepare=prepare,
    )
    assert service.check_async()
    assert available.wait(5)
    assert service.prepare_async()
    assert failed.wait(5)
    assert service.state.available_version == "1.1.0"
    assert service.prepare_async()
    assert pending.wait(5)
    assert prepared == ["1.1.0", "1.1.0"]
    service.close()


def test_rollback_remains_visible_when_network_is_unavailable() -> None:
    """A subsequent network error must not hide a durable rollback notice."""
    finished = threading.Event()

    def discover():
        raise OSError("offline")

    service = PiDeckUpdateService(
        "1.0.0", discover, version_key,
        lambda info: finished.set() if info.status is PiDeckUpdateStatus.FAILED else None,
        inspect=lambda: {
            "phase": "failed", "current_version": "1.0.0", "message": "Update was rolled back",
        },
    )
    assert service.check_async()
    assert finished.wait(5)
    message = service.state.message
    assert message is not None
    assert "rolled back" in message
    assert "offline" in message


def test_available_update_carries_notes_of_newer_releases() -> None:
    """Only notes after the installed version up to the available one are shown."""
    from pideck.application.pideck_updates import ReleaseNote

    finished = threading.Event()
    notes = (
        ReleaseNote("1.3.0", "- future"), ReleaseNote("1.2.0", "- latest"),
        ReleaseNote("1.1.0", "- middle"), ReleaseNote("1.0.0", "- installed"), ReleaseNote("1.1.5", " "),
    )
    service = PiDeckUpdateService(
        "1.0.0", lambda: SimpleNamespace(version="1.2.0", notes=notes), version_key,
        lambda info: finished.set() if info.status is PiDeckUpdateStatus.AVAILABLE else None,
        prepare=lambda version: None,
    )
    assert service.check_async()
    assert finished.wait(5)
    assert [note.version for note in service.state.release_notes] == ["1.2.0", "1.1.0"]
    pending = threading.Event()
    service._on_change = lambda info: pending.set() if info.status is PiDeckUpdateStatus.PENDING else None
    assert service.prepare_async()
    assert pending.wait(5)
    assert [note.version for note in service.state.release_notes] == ["1.2.0", "1.1.0"]
    service.close()


def test_releases_without_notes_still_report_updates() -> None:
    """Older release metadata without notes keeps working."""
    finished = threading.Event()
    service = PiDeckUpdateService(
        "1.0.0", lambda: SimpleNamespace(version="1.1.0"), version_key,
        lambda info: finished.set() if info.status is PiDeckUpdateStatus.AVAILABLE else None,
    )
    assert service.check_async()
    assert finished.wait(5)
    assert service.state.release_notes == ()
    service.close()


def test_cached_release_advisory_is_shown_while_rate_limited() -> None:
    """A rate-limit advisory from discovery is surfaced on an available update."""
    finished = threading.Event()
    service = PiDeckUpdateService(
        "1.0.0",
        lambda: SimpleNamespace(
            version="1.2.0",
            notes=(),
            advisory="GitHub rate limit; next check after 12:00 UTC",
        ),
        version_key,
        lambda info: finished.set() if info.status is PiDeckUpdateStatus.AVAILABLE else None,
    )
    assert service.check_async()
    assert finished.wait(5)
    assert service.state.available_version == "1.2.0"
    assert service.state.message == "GitHub rate limit; next check after 12:00 UTC"
    service.close()
