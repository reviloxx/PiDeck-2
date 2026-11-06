"""Tests for the Qt controller that runs application installs off the UI thread."""

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pideck.application.app_management import ApplicationManagementService
from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.application.ports.packages import PackageOperationCancelled, PackageOperationError
from pideck.infrastructure.config.catalog import load_catalog
from pideck.presentation.qt.application_operations import ApplicationOperationsController

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Create one offscreen Qt application."""
    return QApplication.instance() or QApplication([])


class ScriptedPackages:
    """Fail, cancel or succeed according to the requested application."""

    def __init__(self) -> None:
        self.cancelled = 0

    def is_installed(self, spec) -> bool:
        return False

    def can_install(self, spec) -> bool:
        return True

    def can_remove(self, spec) -> bool:
        return True

    def needs_password(self, spec) -> bool:
        return False

    def install(self, identifier, spec, on_progress, password=None) -> None:
        on_progress(InstallProgress(InstallStage.DOWNLOADING, 10))
        if identifier == "chrome":
            raise PackageOperationError("network unavailable")
        if identifier == "brave":
            raise PackageOperationCancelled("cancelled")

    def uninstall(self, identifier, spec, on_progress) -> None:
        on_progress(InstallProgress(InstallStage.REMOVING))

    def cancel(self) -> None:
        self.cancelled += 1


def run_until(application: QApplication, condition) -> None:
    """Process queued signals until ``condition`` holds."""
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline
        application.processEvents()
        time.sleep(0.01)


def test_controller_reports_progress_results_and_cancellation(application: QApplication) -> None:
    """Results arrive on the Qt thread and the controller accepts the next operation."""
    packages = ScriptedPackages()
    service = ApplicationManagementService(load_catalog(ROOT / "assets/catalog/applications.yaml"), packages, "amd64")
    controller = ApplicationOperationsController(service)
    progress, finished, cancelled = [], [], []
    controller.progress.connect(lambda *args: progress.append(args))
    controller.finished.connect(lambda *args: finished.append(args))
    controller.cancelled.connect(cancelled.append)

    controller.start_install("youtube")
    assert controller.active_id == "youtube"
    with pytest.raises(RuntimeError):
        controller.start_remove("kodi")
    run_until(application, lambda: finished)
    assert finished == [("youtube", False, None)]
    assert progress[0] == ("youtube", InstallProgress(InstallStage.DOWNLOADING, 10))
    assert controller.active_id is None and controller.last_progress is None

    controller.start_install("chrome")
    run_until(application, lambda: len(finished) == 2)
    assert finished[-1] == ("chrome", False, "network unavailable")

    controller.start_install("brave")
    controller.cancel()
    run_until(application, lambda: cancelled)
    assert cancelled == ["brave"] and packages.cancelled == 1

    controller.start_remove("kodi")
    assert controller.removing
    controller.cancel()
    run_until(application, lambda: len(finished) == 3)
    assert finished[-1] == ("kodi", True, None) and packages.cancelled == 1
