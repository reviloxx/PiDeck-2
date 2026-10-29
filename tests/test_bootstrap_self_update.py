"""Exercise the image self-update path through the Qt composition root."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pideck import bootstrap
from pideck.application.pideck_updates import PiDeckUpdateStatus
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.infrastructure.pideck_releases import StableRelease
from pideck.presentation.qt.launcher_window import LauncherWindow
from pideck.presentation.qt.settings_window import SettingsWindow


def test_image_update_runs_through_settings_without_blocking_qt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup checks, settings activation and staged state are wired together."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: app)
    monkeypatch.setenv("PIDECK_IMAGE", "1")
    monkeypatch.setenv("PIDECK_RELEASE_VERSION", "1.0.0")
    calls = []

    def request(action, **fields):
        calls.append((action, fields))
        if action == "stage":
            return {"phase": "pending", "version": fields["version"], "current_version": "1.0.0"}
        return {"phase": "idle", "current_version": "1.0.0"}

    class FakeGateway:
        def __init__(self, *, image_mode):
            assert image_mode

        def check(self, application):
            return UpdateInfo(application.identifier, application.name, UpdateStatus.UNSUPPORTED)

        def close(self):
            pass

    class FakeGamepad:
        def start(self, observer):
            pass

        def close(self):
            pass

    monkeypatch.setattr(bootstrap, "image_update_request", request)
    monkeypatch.setattr(bootstrap, "find_latest_release", lambda: StableRelease("1.1.0", "v1.1.0"))
    monkeypatch.setattr(bootstrap, "NativeUpdateGateway", FakeGateway)
    monkeypatch.setattr(bootstrap, "EvdevGamepadAdapter", FakeGamepad)
    windows = []
    original_init = SettingsWindow.__init__

    def capture_window(window, *args, **kwargs):
        original_init(window, *args, **kwargs)
        windows.append(window)

    monkeypatch.setattr(SettingsWindow, "__init__", capture_window)
    original_show = LauncherWindow.show_launcher

    def show_and_open(window):
        original_show(window)
        QTimer.singleShot(0, window.settings_requested.emit)

    monkeypatch.setattr(LauncherWindow, "show_launcher", show_and_open)
    completed = []
    timer = QTimer()
    timer.setInterval(10)

    def observe():
        if not windows or windows[0]._pideck_row is None:
            return
        row = windows[0]._pideck_row
        if row._pideck_info.status is PiDeckUpdateStatus.AVAILABLE:
            windows[0].pideck_update_requested.emit()
        elif row._pideck_info.status is PiDeckUpdateStatus.PENDING:
            completed.append(True)
            timer.stop()
            app.quit()

    timer.timeout.connect(observe)
    timer.start()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(app.quit)
    deadline.start(5000)
    assert bootstrap.main(["--config", str(tmp_path / "missing.yaml")]) == 0
    timer.stop()
    deadline.stop()
    if windows:
        window = windows[0]
        launcher = window.parentWidget()
        window.blockSignals(True)
        window.close()
        if launcher is not None:
            launcher.close()
            launcher.deleteLater()
        app.processEvents()
    assert completed == [True]
    assert [action for action, _ in calls[:2]] == ["ready", "status"]
    assert any(action == "status" for action, _ in calls)
    assert any(action == "stage" and fields["version"] == "1.1.0" for action, fields in calls)
    assert any(timer.interval() == 6 * 60 * 60 * 1000 for timer in app.findChildren(QTimer))
