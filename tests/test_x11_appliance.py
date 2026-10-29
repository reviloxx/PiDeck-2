"""Opt-in X11 integration checks for the appliance session."""

import os
from pathlib import Path
import select
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import pytest

from pideck.application.session import ApplicationSessionService, LaunchStatus, SessionState
from pideck.domain.configuration import ApplicationDefinition, ApplicationProfile
from pideck.infrastructure.process import SubprocessSupervisor
from pideck.infrastructure.process.x11_visibility import X11WindowVisibilityDetector

pytestmark = pytest.mark.skipif(
    os.environ.get("PIDECK_RUN_X11_TESTS") != "1",
    reason="Requires an isolated X11 display with openbox and wmctrl",
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def isolated_display(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Run the tests on an isolated Xvfb server, never the user's display."""
    executable = os.environ.get("PIDECK_XVFB_BIN") or shutil.which("Xvfb")
    if not executable:
        pytest.fail("Xvfb is required for the opt-in X11 tests")
    home = tmp_path_factory.mktemp("x11-home")
    runtime = home / "runtime"
    runtime.mkdir(mode=0o700)
    read_fd, write_fd = os.pipe()
    server = subprocess.Popen(
        [executable, "-displayfd", str(write_fd), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
        pass_fds=(write_fd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.close(write_fd)
    patch = pytest.MonkeyPatch()
    try:
        ready, _, _ = select.select([read_fd], [], [], 10)
        if not ready:
            pytest.fail("Xvfb did not provide a display number")
        display = os.read(read_fd, 32).decode().strip()
        if not display.isdigit():
            pytest.fail(f"Invalid Xvfb display number: {display!r}")
        patch.setenv("DISPLAY", f":{display}")
        patch.setenv("QT_QPA_PLATFORM", "xcb")
        patch.setenv("HOME", str(home))
        patch.setenv("XDG_CONFIG_HOME", str(home / "config"))
        patch.setenv("XDG_DATA_HOME", str(home / "data"))
        patch.setenv("XDG_CACHE_HOME", str(home / "cache"))
        patch.setenv("XDG_RUNTIME_DIR", str(runtime))
        subprocess.run(["xdpyinfo"], stdout=subprocess.DEVNULL, check=True, timeout=5)
        yield
    finally:
        patch.undo()
        os.close(read_fd)
        try:
            os.killpg(server.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        server.wait(timeout=5)


class SessionObserver:
    """Record actual external-window and process lifecycle events."""

    def __init__(self) -> None:
        self.visible_event = threading.Event()
        self.finished_event = threading.Event()
        self.return_code: int | None = None

    def started(self, application: ApplicationDefinition, profile: ApplicationProfile | None) -> None:
        """Accept the process-start event."""

    def visible(self, application: ApplicationDefinition) -> None:
        """Record X11 readiness."""
        self.visible_event.set()

    def visibility_timeout(self, application: ApplicationDefinition) -> None:
        """Record a timeout as a test failure through the missing visible event."""

    def finished(self, application: ApplicationDefinition, return_code: int) -> None:
        """Record the external application's exit."""
        self.return_code = return_code
        self.finished_event.set()

    def failed(self, application: ApplicationDefinition, message: str) -> None:
        """Record a launch failure as a test failure through the missing events."""


@pytest.fixture
def window_manager() -> Iterator[None]:
    """Run a dedicated openbox instance for the lifecycle test."""
    process = subprocess.Popen(
        ["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = process.stderr.read().decode(errors="replace") if process.stderr else ""
                pytest.fail(f"openbox exited ({process.returncode}): {error}")
            if subprocess.run(["wmctrl", "-m"], capture_output=True).returncode == 0:
                break
            time.sleep(0.1)
        else:
            pytest.fail("openbox did not become ready")
        yield
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()


def test_real_x11_window_is_detected_and_returns_to_launcher(window_manager: None) -> None:
    """Detect a genuine child window, then observe its clean exit."""
    observer = SessionObserver()
    session = ApplicationSessionService(
        SubprocessSupervisor(), observer, X11WindowVisibilityDetector(timeout=8)
    )
    child = (
        "from PySide6.QtCore import QTimer; "
        "from PySide6.QtWidgets import QApplication, QWidget; "
        "app=QApplication([]); window=QWidget(); window.setWindowTitle('PiDeck test child'); "
        "window.resize(300,200); window.show(); QTimer.singleShot(3000, app.quit); "
        "app.exec()"
    )
    definition = ApplicationDefinition(
        identifier="test-child",
        name="Test child",
        executable=sys.executable,
        arguments=("-c", child),
    )
    try:
        result = session.request_launch(definition)
        assert result.status is LaunchStatus.STARTED
        assert observer.visible_event.wait(8), "The child window was not detected"
        assert observer.finished_event.wait(8), "The child did not exit"
        assert observer.return_code == 0
        assert session.snapshot.state is SessionState.IDLE
    finally:
        session.close()


def test_image_xsession_starts_the_launcher(tmp_path: Path) -> None:
    """Start the actual X session script and find the fullscreen PiDeck window."""
    source = ROOT / "image/stage-pideck/00-pideck/files/pideck-xsession"
    script = tmp_path / "xsession"
    script.write_text(
        source.read_text()
        .replace("/usr/bin/python3", shlex.quote(sys.executable))
        .replace("/usr/share/pideck/config", shlex.quote(str(ROOT / "config")))
    )
    env = {
        **os.environ,
        "PIDECK_CONFIG": str(ROOT / "image/stage-pideck/00-pideck/files/pideck.yaml"),
        "PYTHONPATH": str(ROOT / "src"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    error_log = tmp_path / "session.log"
    with error_log.open("wb") as error_stream:
        process = subprocess.Popen(
            ["sh", str(script)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=error_stream,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(f"PiDeck X session exited: {error_log.read_text(errors='replace')}")
                result = subprocess.run(
                    ["wmctrl", "-lGpx"], capture_output=True, text=True, check=False
                )
                if result.returncode == 0 and any(
                    "PiDeck" in line and f" {process.pid} " in line
                    for line in result.stdout.splitlines()
                ):
                    break
                time.sleep(0.1)
            else:
                pytest.fail("The PiDeck window did not appear in the X11 window list")
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
