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
