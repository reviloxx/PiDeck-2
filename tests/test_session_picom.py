"""Verify release picom config is applied without blocking the session."""

from pathlib import Path

import pytest

from pideck.infrastructure.session.picom import apply_release_picom


def test_apply_release_picom_stops_and_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An existing picom is replaced with the release config path."""
    conf = tmp_path / "picom.conf"
    conf.write_text("vsync = true\n", encoding="utf-8")
    binary = tmp_path / "picom"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[object] = []
    killed: list[tuple[int, int]] = []

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.picom.shutil.which", lambda _: str(binary))
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "4242\n"})(),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.os.kill",
        lambda pid, sig: killed.append((pid, sig)) or (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.picom.subprocess.Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    apply_release_picom(config=conf)

    assert killed and killed[0][0] == 4242
    assert len(calls) == 1
    assert calls[0][0][0] == [str(binary), "--config", str(conf)]


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


def test_shipped_picom_conf_enables_vsync() -> None:
    """The canonical release config keeps compositing with vsync on."""
    path = Path(__file__).resolve().parents[1] / "src/pideck/infrastructure/session/picom.conf"
    text = path.read_text(encoding="utf-8")
    assert 'backend = "glx"' in text
    assert "vsync = true" in text
    assert "unredir-if-possible = false" in text
