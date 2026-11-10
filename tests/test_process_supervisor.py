"""Tests for Linux process-group supervision."""

import signal
import sys
import threading

import pytest

from pideck.application.ports.process import ProcessCommand, ProcessExit
from pideck.infrastructure.process import subprocess_supervisor as supervisor_module
from pideck.infrastructure.process.subprocess_supervisor import (
    SubprocessLaunchError,
    SubprocessSupervisor,
    _flatpak_app_id,
    _is_steamlink_orphan,
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


def test_flatpak_app_id_extracted_from_run_command() -> None:
    """Flatpak run commands expose the application id for orphan teardown."""
    command = ProcessCommand("flatpak", ("run", "--branch=stable", "com.valvesoftware.SteamLink"))
    assert _flatpak_app_id(command) == "com.valvesoftware.SteamLink"
    assert _flatpak_app_id(ProcessCommand("steamlink")) is None


def test_steamlink_orphan_cmdline_detection() -> None:
    """Steam Link launcher and runtime command lines are recognized."""
    assert _is_steamlink_orphan(b"/usr/bin/steamlink\x00")
    assert _is_steamlink_orphan(b"/home/pideck/.local/share/SteamLink/bin/steamlink\x00")
    assert not _is_steamlink_orphan(b"/usr/bin/kodi\x00")


def test_stop_runs_flatpak_kill_for_flatpak_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping a Flatpak session also asks Flatpak to kill the app id."""
    kills: list[list[str]] = []
    released = threading.Event()

    class _FakeProcess:
        pid = 4242

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                if released.wait(timeout):
                    return -15
                raise __import__("subprocess").TimeoutExpired(cmd="fake", timeout=timeout)
            released.wait(2)
            return -15

    monkeypatch.setattr(
        supervisor_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(supervisor_module.os, "killpg", lambda *_args, **_kwargs: released.set())

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        kills.append(list(command))
        return None

    monkeypatch.setattr(supervisor_module.subprocess, "run", fake_run)
    supervisor = SubprocessSupervisor()
    completed = threading.Event()
    handle = supervisor.start(
        ProcessCommand("flatpak", ("run", "com.valvesoftware.SteamLink")),
        token="steam-flatpak",
        on_exit=lambda _event: completed.set(),
    )
    supervisor.stop(handle, grace_period=0.05)
    assert completed.wait(2)
    assert ["flatpak", "kill", "com.valvesoftware.SteamLink"] in kills
    supervisor.close()


def test_stop_signals_steamlink_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native steamlink stop also signals leftover processes outside the group."""
    signaled: list[tuple[int, int]] = []
    released = threading.Event()

    class _FakeProcess:
        pid = 4242

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                released.wait(timeout)
                return 0
            released.wait(2)
            return 0

    monkeypatch.setattr(
        supervisor_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(supervisor_module.os, "killpg", lambda *_args, **_kwargs: released.set())
    monkeypatch.setattr(
        SubprocessSupervisor,
        "_signal_matching_processes",
        classmethod(
            lambda cls, matches, termination_signal: signaled.append(
                (1 if matches(b"/usr/bin/steamlink\x00") else 0, termination_signal)
            )
        ),
    )
    supervisor = SubprocessSupervisor()
    completed = threading.Event()
    handle = supervisor.start(
        ProcessCommand("steamlink"),
        token="steam-native",
        on_exit=lambda _event: completed.set(),
    )
    supervisor.stop(handle, grace_period=0.05)
    assert completed.wait(2)
    assert signaled == [(1, signal.SIGTERM)]
    supervisor.close()
