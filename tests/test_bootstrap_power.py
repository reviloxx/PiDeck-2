"""Exercise appliance power handling through the Qt composition root."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pideck import bootstrap
from pideck.domain.errors import PowerError
from pideck.presentation.qt.launcher_window import LauncherWindow


@pytest.mark.parametrize("fail", [False, True])
def test_appliance_shutdown_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    """A shutdown signal reaches power control; failures remain visible."""
    app = QApplication.instance() or QApplication([])
    events: list[str] = []
    messages: list[str] = []

    class FakePower:
        def power_off(self) -> None:
            events.append("power")
            if fail:
                raise PowerError("denied")

    class FakeGamepad:
        def start(self, observer: object) -> None:
            pass

        def close(self) -> None:
            events.append("cleanup")

    monkeypatch.setattr(bootstrap, "SystemPower", FakePower)
    monkeypatch.setattr(bootstrap, "EvdevGamepadAdapter", FakeGamepad)
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: app)
    original_show = LauncherWindow.show_launcher
    scheduled = False

    def show_and_trigger(window: LauncherWindow) -> None:
        nonlocal scheduled
        original_show(window)
        if scheduled:
            return
        scheduled = True

        def trigger() -> None:
            window.shutdown_requested.emit()
            messages.append(window._status_label.text())
            app.quit()

        QTimer.singleShot(0, trigger)

    monkeypatch.setattr(LauncherWindow, "show_launcher", show_and_trigger)

    assert bootstrap.main(
        ["--config", str(tmp_path / "missing.yaml"), "--power-off-on-shutdown"]
    ) == 0
    assert events == (["power"] if fail else ["power", "cleanup"])
    if fail:
        assert "denied" in messages[0]
