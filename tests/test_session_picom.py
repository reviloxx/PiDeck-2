"""Verify release picom config is selected and applied without blocking the session."""

from pathlib import Path

import pytest

from pideck.infrastructure.session.picom import apply_release_picom, select_picom_config

_SESSION = Path(__file__).resolve().parents[1] / "src/pideck/infrastructure/session"


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

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))

    def fake_run(args, **kwargs):
        if args[:1] == ["pgrep"] and "-ax" in args:
            return type("Result", (), {"returncode": 0, "stdout": f"1111 picom --config /other.conf\n"})()
        return type("Result", (), {"returncode": 0, "stdout": "4242\n"})()

    monkeypatch.setattr("pideck.infrastructure.session.picom.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom._pid_is_alive",
        lambda pid: pid == 4242 and not killed,
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

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
