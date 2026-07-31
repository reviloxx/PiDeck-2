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
