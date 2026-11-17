"""Verify release picom config is selected and applied without blocking the session."""

from pathlib import Path
import signal

import pytest

from pideck.infrastructure.session.picom import (
    POST_MAP_REFRESH_DELAYS_MS,
    apply_release_picom,
    pause_picom_for_client,
    post_map_refresh_followup_delay_ms,
    resume_picom_after_client,
    select_picom_config,
)

_SESSION = Path(__file__).resolve().parents[1] / "src/pideck/infrastructure/session"


def test_post_map_picom_refresh_schedule_spans_fifteen_seconds() -> None:
    """Boot recovery retries at 0.5s, 2s, 5s, and 15s after the launcher maps."""
    assert POST_MAP_REFRESH_DELAYS_MS == (500, 2000, 5000, 15000)
    assert post_map_refresh_followup_delay_ms(0) == 1500
    assert post_map_refresh_followup_delay_ms(1) == 3000
    assert post_map_refresh_followup_delay_ms(2) == 10000
    assert post_map_refresh_followup_delay_ms(3) is None
    assert post_map_refresh_followup_delay_ms(-1) is None


def test_select_picom_config_uses_nvidia_profile() -> None:
    """NVIDIA hosts get the GLX vsync config."""
    path = select_picom_config(
        directory=_SESSION,
        nvidia_present=lambda: True,
        virtual_machine=lambda: True,
    )
    assert path.name == "picom-nvidia.conf"
    text = path.read_text(encoding="utf-8")
    assert 'backend = "glx"' in text
    assert "vsync = true" in text


def test_select_picom_config_uses_vm_profile_without_nvidia() -> None:
    """VMs without NVIDIA keep the pre-0.5.24 safe compositor path."""
    path = select_picom_config(
        directory=_SESSION,
        nvidia_present=lambda: False,
        virtual_machine=lambda: True,
    )
    assert path.name == "picom-vm.conf"
    text = path.read_text(encoding="utf-8")
    assert 'backend = "xrender"' in text
    assert "vsync = false" in text


def test_select_picom_config_default_enables_vsync_without_glx() -> None:
    """Physical non-NVIDIA hosts vsync through xrender."""
    path = select_picom_config(
        directory=_SESSION,
        nvidia_present=lambda: False,
        virtual_machine=lambda: False,
    )
    assert path.name == "picom.conf"
    text = path.read_text(encoding="utf-8")
    assert 'backend = "xrender"' in text
    assert "vsync = true" in text


def test_apply_release_picom_stops_and_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An existing picom is replaced with the selected config path."""
    conf = tmp_path / "picom-vm.conf"
    conf.write_text("vsync = false\n", encoding="utf-8")
    binary = tmp_path / "picom"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[object] = []
    killed: list[tuple[int, int]] = []
    live = {"pids": [4242]}

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))

    def fake_run(args, **kwargs):
        if args[:1] == ["pgrep"] and "-ax" in args:
            return type("Result", (), {"returncode": 0, "stdout": "1111 picom --config /other.conf\n"})()
        if live["pids"]:
            return type("Result", (), {"returncode": 0, "stdout": "\n".join(str(p) for p in live["pids"]) + "\n"})()
        return type("Result", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda pid: pid in live["pids"],
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda pid, sig: (killed.append((pid, sig)), live["pids"].clear()),
    )

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        live["pids"] = [9001]

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.Popen", fake_popen)

    apply_release_picom(config=conf)

    assert killed and killed[0][0] == 4242
    assert all(sig != 9 for _, sig in killed)
    assert len(calls) == 1
    assert calls[0][0][0] == [str(binary), "--config", str(conf)]


def test_apply_release_picom_skips_restart_when_config_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Do not bounce picom when it already uses the selected config."""
    conf = tmp_path / "picom-vm.conf"
    conf.write_text("vsync = false\n", encoding="utf-8")
    binary = tmp_path / "picom"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": f"1111 picom --config {conf}\n"}
        )(),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not restart")),
    )

    apply_release_picom(config=conf)


def test_apply_release_picom_force_restarts_when_config_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """force=True replaces a matching picom (cold-start black composite recovery)."""
    conf = tmp_path / "picom-nvidia.conf"
    conf.write_text('backend = "glx"\n', encoding="utf-8")
    binary = tmp_path / "picom"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[object] = []
    killed: list[tuple[int, int]] = []
    live = {"pids": [1111]}

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))

    def fake_run(args, **kwargs):
        if args[:1] == ["pgrep"] and "-ax" in args:
            return type(
                "Result", (), {"returncode": 0, "stdout": f"1111 picom --config {conf}\n"}
            )()
        if live["pids"]:
            return type("Result", (), {"returncode": 0, "stdout": "\n".join(str(p) for p in live["pids"]) + "\n"})()
        return type("Result", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda pid: pid in live["pids"],
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda pid, sig: (killed.append((pid, sig)), live["pids"].clear()),
    )

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        live["pids"] = [9001]

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.Popen", fake_popen)

    apply_release_picom(config=conf, force=True)

    assert killed and killed[0][0] == 1111
    assert len(calls) == 1
    assert calls[0][0][0] == [str(binary), "--config", str(conf)]


def test_apply_release_picom_retries_when_first_start_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A zombie-only session gets a second start attempt after the first exits."""
    conf = tmp_path / "picom-nvidia.conf"
    conf.write_text('backend = "glx"\n', encoding="utf-8")
    binary = tmp_path / "picom"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[object] = []
    live = {"pids": []}

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.run",
        lambda *args, **kwargs: (
            type("Result", (), {"returncode": 0, "stdout": "\n".join(str(p) for p in live["pids"]) + "\n"})()
            if live["pids"]
            else type("Result", (), {"returncode": 1, "stdout": ""})()
        ),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda pid: pid in live["pids"],
    )

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return
        live["pids"] = [9002]

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.Popen", fake_popen)
    clock = {"t": 0.0}
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.time.monotonic",
        lambda: clock["t"],
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.time.sleep",
        lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
    )

    apply_release_picom(config=conf, force=True)

    assert len(calls) == 2


def test_apply_release_picom_noop_without_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing picom leaves the session alone."""
    conf = tmp_path / "picom.conf"
    conf.write_text("vsync = true\n", encoding="utf-8")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")),
    )

    apply_release_picom(config=conf)


def test_apply_release_picom_noop_without_display(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Headless validation paths do not touch the compositor."""
    conf = tmp_path / "picom.conf"
    conf.write_text("vsync = true\n", encoding="utf-8")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.shutil.which",
        lambda _: (_ for _ in ()).throw(AssertionError("should not look up picom")),
    )

    apply_release_picom(config=conf)


def test_pause_picom_for_client_stops_running_compositor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming clients need picom stopped so the window is not redirected."""
    live = {"pids": [4242]}
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.run",
        lambda *args, **kwargs: (
            type("Result", (), {"returncode": 0, "stdout": "4242\n"})()
            if live["pids"]
            else type("Result", (), {"returncode": 1, "stdout": ""})()
        ),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda pid: pid in live["pids"],
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda pid, sig: (killed.append((pid, sig)), live["pids"].clear()),
    )

    pause_picom_for_client()

    assert killed == [(4242, signal.SIGTERM)]
    assert not live["pids"]


def test_pause_picom_for_client_noop_when_already_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not signal anything when picom is already down."""
    monkeypatch.setattr("pideck.infrastructure.session.picom.live_picom_running", lambda: False)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not kill")),
    )

    pause_picom_for_client()


def test_resume_picom_after_client_force_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaving Steam Link must bring release picom back even if it was stopped."""
    calls: list[object] = []

    def fake_apply(*, force: bool = False, **_kwargs) -> None:
        calls.append(force)

    monkeypatch.setattr("pideck.infrastructure.session.picom.apply_release_picom", fake_apply)

    resume_picom_after_client()

    assert calls == [True]
