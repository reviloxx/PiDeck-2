"""Tests for X11 visibility of sandboxed and native applications."""

import subprocess
import threading
import time

import pytest

from pideck.application.ports.process import ProcessHandle
from pideck.domain.configuration import WindowMode
from pideck.infrastructure.process import x11_visibility
from pideck.infrastructure.process.x11_visibility import X11WindowVisibilityDetector


@pytest.mark.parametrize(("window_pid", "client_pid", "visible"), [(0, 552, True), (2, 552, True), (0, 999, False)])
def test_window_uses_server_pid_when_client_property_is_wrong(
    monkeypatch: pytest.MonkeyPatch, window_pid: int, client_pid: int, visible: bool,
) -> None:
    """Match the server-reported host PID rather than a misleading client PID."""
    handle = ProcessHandle(token="app", pid=537, process_group_id=537)
    monkeypatch.setattr(x11_visibility, "_process_group_ids", lambda group: {552})
    monkeypatch.setattr(x11_visibility, "_window_client_pid", lambda window_id: client_pid)
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, f"0x0080000c 0 {window_pid} 0 0 1280 800 app.App pideck App\n", "",
        ),
    )
    assert x11_visibility.X11WindowVisibilityDetector._has_visible_window(handle) is visible


def test_native_window_does_not_need_xres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve direct PID matching for ordinary X11 windows."""
    handle = ProcessHandle(token="app", pid=552, process_group_id=552)
    monkeypatch.setattr(x11_visibility, "_process_group_ids", lambda group: {552})
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "0x0080000c 0 552 0 0 1280 800 app.App pideck App\n", "",
        ),
    )
    monkeypatch.setattr(x11_visibility, "_window_client_pid", lambda window_id: pytest.fail("unnecessary query"))
    assert x11_visibility.X11WindowVisibilityDetector._has_visible_window(handle)


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ("WM_TRANSIENT_FOR: not found.\n_NET_WM_WINDOW_TYPE = _NET_WM_WINDOW_TYPE_NORMAL", True),
        ("WM_TRANSIENT_FOR(WINDOW): window id # 0x123\n_NET_WM_WINDOW_TYPE = _NET_WM_WINDOW_TYPE_DIALOG", False),
        ("WM_TRANSIENT_FOR: not found.\n_NET_WM_WINDOW_TYPE = _NET_WM_WINDOW_TYPE_SPLASH", False),
        (None, False),
    ],
)
def test_main_window_selection(
    monkeypatch: pytest.MonkeyPatch, properties: str | None, expected: bool,
) -> None:
    """Dialog, splash and unavailable windows are never maximized."""
    monkeypatch.setattr(x11_visibility, "_window_properties", lambda window_id: properties)
    assert x11_visibility._is_main_window("0x123") is expected


def test_fullscreen_is_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """An application's existing fullscreen state is preserved."""
    monkeypatch.setattr(
        x11_visibility, "_window_properties", lambda window_id: "_NET_WM_STATE_FULLSCREEN",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("fullscreen was changed"))
    x11_visibility._maximize_window("0x123", threading.Event())


def test_maximization_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A maximize request is followed by confirmation of both EWMH states."""
    properties = iter([
        "",
        "_NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ",
    ])
    monkeypatch.setattr(x11_visibility, "_window_properties", lambda window_id: next(properties))
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    x11_visibility._maximize_window("0x123", threading.Event())
    assert commands == [["wmctrl", "-ir", "0x123", "-b", "add,maximized_vert,maximized_horz"]]


def test_managed_mode_is_reapplied_after_short_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Browsers that resize after map get a second maximize attempt."""
    calls: list[str] = []

    def maximize(window_id: str, cancel: threading.Event) -> None:
        del cancel
        calls.append(window_id)

    monkeypatch.setattr(x11_visibility, "_maximize_window", maximize)
    X11WindowVisibilityDetector._apply_window_mode("0xabc", WindowMode.MAXIMIZED, threading.Event())
    assert calls == ["0xabc", "0xabc"]


def test_fullscreen_request_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fullscreen request is followed by confirmation of the EWMH state."""
    properties = iter(["", "_NET_WM_STATE_FULLSCREEN"])
    monkeypatch.setattr(x11_visibility, "_window_properties", lambda window_id: next(properties))
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    x11_visibility._fullscreen_window("0x123", threading.Event())
    assert commands == [["wmctrl", "-ir", "0x123", "-b", "add,fullscreen"]]


def test_fullscreen_request_skips_already_fullscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already-fullscreen window is left unchanged."""
    monkeypatch.setattr(
        x11_visibility, "_window_properties", lambda window_id: "_NET_WM_STATE_FULLSCREEN",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("fullscreen was changed"))
    x11_visibility._fullscreen_window("0x123", threading.Event())


def test_timeout_still_maximizes_when_a_window_appears_late(monkeypatch: pytest.MonkeyPatch) -> None:
    """Managed modes apply maximize on a last-chance lookup instead of giving up."""
    handle = ProcessHandle(token="brave", pid=1, process_group_id=1)
    visible = threading.Event()
    timed_out = threading.Event()
    maximized: list[str] = []

    def find(watch_handle: ProcessHandle, *, main_only: bool = False) -> str | None:
        del watch_handle
        if main_only:
            return None
        return "0xabc"

    monkeypatch.setattr(X11WindowVisibilityDetector, "_find_visible_window", staticmethod(find))
    monkeypatch.setattr(
        X11WindowVisibilityDetector,
        "_apply_window_mode",
        staticmethod(lambda window_id, mode, cancel: maximized.append(f"{window_id}:{mode}")),
    )
    detector = X11WindowVisibilityDetector(timeout=0.05, poll_interval=0.01)
    detector.wait_until_visible(
        handle,
        lambda _handle: visible.set(),
        lambda _handle: timed_out.set(),
        WindowMode.MAXIMIZED,
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not visible.is_set() and not timed_out.is_set():
        time.sleep(0.01)
    assert visible.is_set()
    assert not timed_out.is_set()
    assert maximized == ["0xabc:maximized"]
    detector.close()
