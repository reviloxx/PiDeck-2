"""Tests for Linux process-group supervision."""

from pathlib import Path
import sys
import threading

import pytest

from pideck.application.ports.process import ProcessCommand, ProcessExit
from pideck.infrastructure.process.subprocess_supervisor import (
    SubprocessLaunchError,
    SubprocessSupervisor,
)


def test_supervisor_reports_child_exit() -> None:
    """A completed child produces one typed exit event."""
    supervisor = SubprocessSupervisor()
    completed = threading.Event()
    exits: list[ProcessExit] = []

    supervisor.start(
        ProcessCommand(
            executable=sys.executable,
            arguments=("-c", "raise SystemExit(7)"),
        ),
        token="finished-child",
        on_exit=lambda event: (exits.append(event), completed.set()),
    )

    assert completed.wait(2)
    assert exits[0].handle.token == "finished-child"
    assert exits[0].return_code == 7
    supervisor.close()


def test_supervisor_terminates_process_group() -> None:
    """Stopping a long-lived child terminates its dedicated process group."""
    supervisor = SubprocessSupervisor()
    completed = threading.Event()
    exits: list[ProcessExit] = []
    handle = supervisor.start(
        ProcessCommand(
            executable=sys.executable,
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        token="long-child",
        on_exit=lambda event: (exits.append(event), completed.set()),
    )

    supervisor.stop(handle, grace_period=0.1)

    assert completed.wait(2)
    assert exits[0].return_code < 0
    supervisor.close()


def test_supervisor_rejects_missing_executable() -> None:
    """An unavailable executable becomes a typed launch error."""
    supervisor = SubprocessSupervisor()

    with pytest.raises(SubprocessLaunchError):
        supervisor.start(
            ProcessCommand(executable="/definitely/missing/pideck-command"),
            token="missing-child",
            on_exit=lambda event: None,
        )

    supervisor.close()


def test_supervisor_unsets_empty_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty environment values remove inherited variables instead of clearing them."""
    import os

    monkeypatch.setenv("LIBVA_DRIVER_NAME", "nvidia")
    monkeypatch.setenv("NVD_BACKEND", "direct")
    captured: dict[str, str] = {}

    class _FakeProcess:
        pid = 4242

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(*_args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs["env"])
        return _FakeProcess()

    monkeypatch.setattr(
        "pideck.infrastructure.process.subprocess_supervisor.subprocess.Popen",
        fake_popen,
    )
    supervisor = SubprocessSupervisor()
    completed = threading.Event()
    supervisor.start(
        ProcessCommand(
            executable="kodi",
            environment={"LIBVA_DRIVER_NAME": "", "NVD_BACKEND": "", "PIDECK_KEEP": "1"},
        ),
        token="env-child",
        on_exit=lambda _event: completed.set(),
    )
    assert completed.wait(2)
    assert "LIBVA_DRIVER_NAME" not in captured
    assert "NVD_BACKEND" not in captured
    assert captured["PIDECK_KEEP"] == "1"
    assert os.environ["LIBVA_DRIVER_NAME"] == "nvidia"
    supervisor.close()
