"""Tests for X11 mode selection, persistent settings, and rollback watchdog."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pideck.application.ports.display import DisplayError, DisplayMode
from pideck.infrastructure import x11_display
from pideck.infrastructure.x11_display import DisplayPreferences, X11DisplayGateway


QUERY = """\
Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 8192 x 8192
HDMI-1 connected primary 1920x1080+0+0
   1920x1080     60.00*+  59.94
   1280x720      60.00
DP-1 disconnected
"""


def test_display_confirmation_timeout_is_ten_seconds() -> None:
    """Keep the watchdog confirmation interval at the requested threshold."""
    assert x11_display._TIMEOUT == 10.0


@pytest.fixture
def display(tmp_path: Path, monkeypatch):
    """Provide an isolated fake XRandR command and settings repository."""
    executable = tmp_path / "xrandr"
    executable.write_text(
        '#!/bin/sh\n'
        'echo "$*" >> "$XRANDR_LOG"\n'
        'if [ "$1" = "--query" ]; then printf "%s" "$XRANDR_QUERY"; exit 0; fi\n'
        'if [ "$XRANDR_DELAY" = "1" ] && [ "$4" = "1280x720" ]; then sleep 0.3; fi\n'
        'if [ "$XRANDR_FAIL" = "1" ] && [ "$4" = "1280x720" ]; then exit 1; fi\n'
        'exit 0\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XRANDR_LOG", str(tmp_path / "commands"))
    monkeypatch.setenv("XRANDR_QUERY", QUERY)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setattr(x11_display, "_TIMEOUT", 1.0)
    preferences = DisplayPreferences(tmp_path / "display.yaml")
    return X11DisplayGateway(preferences), preferences, tmp_path / "commands"


def test_list_modes_and_confirmed_persistence(display) -> None:
    """Only offered modes can be confirmed and later restored."""
    gateway, preferences, log = display
    output = gateway.inspect()[0]
    assert output.name == "HDMI-1"
    assert output.current == DisplayMode("1920x1080", "60.00")
    mode = DisplayMode("1280x720", "60.00")
    assert mode in output.modes
    change = gateway.begin(output.name, mode)
    change.confirm()
    assert preferences.load() == {"HDMI-1": mode}
    assert "--output HDMI-1 --mode 1280x720 --rate 60.00" in log.read_text()
    gateway.restore_saved()
    assert log.read_text().count("--output HDMI-1 --mode 1280x720 --rate 60.00") == 2


def test_cancel_and_timeout_restore_the_previous_mode(display) -> None:
    """Cancellation and expiration both roll back without persisting."""
    gateway, preferences, log = display
    mode = DisplayMode("1280x720", "60.00")
    gateway.begin("HDMI-1", mode).cancel()
    assert "--output HDMI-1 --mode 1920x1080 --rate 60.00" in log.read_text()
    change = gateway.begin("HDMI-1", mode)
    time.sleep(1.2)
    with pytest.raises(DisplayError):
        change.confirm()
    assert log.read_text().count("--output HDMI-1 --mode 1920x1080 --rate 60.00") == 2
    assert preferences.load() == {}


def test_overlapping_display_changes_are_rejected(display) -> None:
    """A second transaction cannot race the first watchdog's rollback."""
    gateway, _, _ = display
    mode = DisplayMode("1280x720", "60.00")
    first = gateway.begin("HDMI-1", mode)
    with pytest.raises(DisplayError, match="still in progress"):
        gateway.begin("HDMI-1", mode)
    first.cancel()
    gateway.begin("HDMI-1", mode).cancel()


def test_save_failure_restores_previous_display(display, monkeypatch) -> None:
    """A confirmed mode is reverted if persisting it fails."""
    gateway, preferences, log = display
    change = gateway.begin("HDMI-1", DisplayMode("1280x720", "60.00"))

    def fail(*args):
        raise DisplayError("Cannot save")

    monkeypatch.setattr(preferences, "save", fail)
    with pytest.raises(DisplayError, match="Cannot save"):
        change.confirm()
    assert "--output HDMI-1 --mode 1920x1080 --rate 60.00" in log.read_text()


def test_watchdog_reverts_after_parent_exits(display) -> None:
    """The independent watchdog restores the mode if the launcher dies."""
    _, preferences, log = display
    script = (
        "import os; from pathlib import Path; "
        "from pideck.application.ports.display import DisplayMode; "
        "from pideck.infrastructure.x11_display import X11DisplayGateway, DisplayPreferences; "
        f"g = X11DisplayGateway(DisplayPreferences(Path({str(preferences.path)!r}))); "
        "g.begin('HDMI-1', DisplayMode('1280x720', '60.00')); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=5)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "--output HDMI-1 --mode 1920x1080 --rate 60.00" in log.read_text():
            break
        time.sleep(0.01)
    else:
        pytest.fail("The watchdog did not restore the display after its parent exited")
    assert preferences.load() == {}


def test_watchdog_reverts_when_parent_dies_during_mode_change(display, monkeypatch) -> None:
    """A parent crash before the watchdog's ready response still reverts."""
    _, preferences, log = display
    monkeypatch.setenv("XRANDR_DELAY", "1")
    script = (
        "from pathlib import Path; "
        "from pideck.application.ports.display import DisplayMode; "
        "from pideck.infrastructure.x11_display import X11DisplayGateway, DisplayPreferences; "
        f"g = X11DisplayGateway(DisplayPreferences(Path({str(preferences.path)!r}))); "
        "g.begin('HDMI-1', DisplayMode('1280x720', '60.00'))"
    )
    process = subprocess.Popen([sys.executable, "-c", script])
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if log.exists() and "--output HDMI-1 --mode 1280x720 --rate 60.00" in log.read_text():
            break
        time.sleep(0.01)
    else:
        process.kill()
        process.wait(timeout=2)
        pytest.fail("The temporary mode change did not start")
    process.kill()
    process.wait(timeout=2)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "--output HDMI-1 --mode 1920x1080 --rate 60.00" in log.read_text():
            break
        time.sleep(0.01)
    else:
        pytest.fail("The watchdog did not restore the display after a crash")


def test_invalid_mode_and_wayland_do_not_apply_changes(display, monkeypatch) -> None:
    """Unsupported modes and Wayland never invoke a mode change."""
    gateway, preferences, log = display
    with pytest.raises(DisplayError, match="no longer available"):
        gateway.begin("HDMI-1", DisplayMode("1024x768", "60.00"))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with pytest.raises(DisplayError, match="X11"):
        gateway.inspect()
    monkeypatch.delenv("XDG_SESSION_TYPE")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    with pytest.raises(DisplayError, match="X11"):
        gateway.inspect()
    assert "--output" not in log.read_text()
    assert preferences.load() == {}


def test_unavailable_saved_mode_is_reported_without_applying(display) -> None:
    """A disconnected saved output is skipped and reported explicitly."""
    gateway, preferences, log = display
    preferences.save("DP-1", DisplayMode("1920x1080", "60.00"))
    with pytest.raises(DisplayError, match="DP-1"):
        gateway.restore_saved()
    text = log.read_text()
    assert "--mode" not in text
    assert "--output HDMI-1 --brightness 1.00" in text


def test_brightness_is_clamped_persisted_and_restored(display) -> None:
    """Software brightness is clamped, saved, applied to outputs, and restored."""
    gateway, preferences, log = display
    assert gateway.get_brightness() == 100
    gateway.set_brightness(5)
    assert gateway.get_brightness() == 10
    assert preferences.load_brightness() == 10
    assert "--output HDMI-1 --brightness 0.10" in log.read_text()
    gateway.set_brightness(70)
    assert gateway.get_brightness() == 70
    log.write_text("")
    gateway.restore_saved()
    assert "--output HDMI-1 --brightness 0.70" in log.read_text()


def test_legacy_display_preferences_without_brightness_default_to_full(tmp_path: Path) -> None:
    """Older display.yaml files that only store outputs remain valid."""
    path = tmp_path / "display.yaml"
    path.write_text(
        "outputs:\n  HDMI-1:\n    resolution: '1920x1080'\n    rate: '60.00'\n",
        encoding="utf-8",
    )
    preferences = DisplayPreferences(path)
    assert preferences.load() == {"HDMI-1": DisplayMode("1920x1080", "60.00")}
    assert preferences.load_brightness() == 100


def test_restore_failure_is_reported_to_settings(display) -> None:
    """Startup restoration failures are shown once and allow a later refresh."""
    gateway, preferences, _ = display
    preferences.save("DP-1", DisplayMode("1920x1080", "60.00"))
    gateway.restore_saved_async()
    assert gateway._startup_ready.wait(3)
    with pytest.raises(DisplayError, match="DP-1"):
        gateway.inspect()
    assert gateway.inspect()[0].name == "HDMI-1"


def test_failed_apply_restores_previous_mode(display, monkeypatch) -> None:
    """The watchdog restores the prior mode if the requested mode fails."""
    gateway, preferences, log = display
    monkeypatch.setenv("XRANDR_FAIL", "1")
    with pytest.raises(DisplayError, match="APPLY_FAILED"):
        gateway.begin("HDMI-1", DisplayMode("1280x720", "60.00"))
    assert "--output HDMI-1 --mode 1920x1080 --rate 60.00" in log.read_text()
    assert preferences.load() == {}


def test_reassert_active_force_reapplies_matching_mode_and_primary(display) -> None:
    """Forced reassert re-runs xrandr and moves primary onto the active output."""
    gateway, preferences, log = display
    preferences.save("HDMI-1", DisplayMode("1920x1080", "60.00"))
    preferences.save_brightness(90)
    log.write_text("")
    gateway.reassert_active(force=True)
    text = log.read_text()
    assert "--output HDMI-1 --mode 1920x1080 --rate 60.00 --primary" in text
    assert "--output HDMI-1 --brightness 0.90" in text


def test_reassert_active_without_force_only_sets_primary_when_mode_matches(display) -> None:
    """Non-forced reassert leaves a matching mode alone and only sets primary."""
    gateway, preferences, log = display
    preferences.save("HDMI-1", DisplayMode("1920x1080", "60.00"))
    log.write_text("")
    gateway.reassert_active(force=False)
    text = log.read_text()
    assert "--mode" not in text
    assert "--output HDMI-1 --primary" in text


def test_reassert_active_skips_while_change_in_progress(display) -> None:
    """Reassert must not fight an open confirmation watchdog."""
    gateway, _, log = display
    change = gateway.begin("HDMI-1", DisplayMode("1280x720", "60.00"))
    before = log.read_text()
    gateway.reassert_active(force=True)
    assert log.read_text() == before
    change.cancel()


def test_connected_names_lists_connected_outputs(display) -> None:
    """Hotplug polling reads connected output names from xrandr."""
    gateway, _, _ = display
    assert gateway.connected_names() == frozenset({"HDMI-1"})


def test_display_hotplug_monitor_recovers_on_request(display) -> None:
    """CEC-style request debounces then forces reassert and runs the after hook."""
    from pideck.infrastructure.x11_display import DisplayHotplugMonitor

    gateway, preferences, log = display
    preferences.save("HDMI-1", DisplayMode("1920x1080", "60.00"))
    after: list[str] = []
    monitor = DisplayHotplugMonitor(
        gateway, after=lambda: after.append("done"), interval=0.05, debounce=0.05,
    )
    monitor.start()
    log.write_text("")
    monitor.request()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not after:
        time.sleep(0.02)
    monitor.close()
    assert after == ["done"]
    assert "--output HDMI-1 --mode 1920x1080 --rate 60.00 --primary" in log.read_text()


def test_display_hotplug_monitor_recovers_when_outputs_change(display, monkeypatch) -> None:
    """A new connected output set schedules the same forced reassert path."""
    from pideck.infrastructure.x11_display import DisplayHotplugMonitor

    gateway, preferences, log = display
    preferences.save("HDMI-1", DisplayMode("1920x1080", "60.00"))
    after: list[str] = []
    names = iter([frozenset(), frozenset({"HDMI-1"}), frozenset({"HDMI-1"})])

    def fake_names() -> frozenset[str]:
        try:
            return next(names)
        except StopIteration:
            return frozenset({"HDMI-1"})

    monkeypatch.setattr(gateway, "connected_names", fake_names)
    monitor = DisplayHotplugMonitor(
        gateway, after=lambda: after.append("done"), interval=0.05, debounce=0.05,
    )
    monitor.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not after:
        time.sleep(0.02)
    monitor.close()
    assert after == ["done"]
    assert "--primary" in log.read_text()
