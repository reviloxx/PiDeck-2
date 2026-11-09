"""Verify release Openbox config passes clicks through and can be applied."""

from pathlib import Path

import pytest

from pideck.infrastructure.session.openbox import apply_release_openbox, select_openbox_config

_SESSION = Path(__file__).resolve().parents[1] / "src/pideck/infrastructure/session"
_IMAGE_RC = (
    Path(__file__).resolve().parents[1]
    / "image/stage-pideck/00-pideck/files/openbox/rc.xml"
)


def test_openbox_rc_omits_frame_left_press_and_keeps_client() -> None:
    """Frame Left Press steals clicks; Client Left Press must remain."""
    for path in (_SESSION / "openbox-rc.xml", _IMAGE_RC):
        text = path.read_text(encoding="utf-8")
        assert 'name="Client"' in text
        assert 'name="Titlebar"' in text
        assert 'name="Close"' in text
        assert 'button="Left" action="Press"' in text
        assert 'name="Frame"' not in text
        assert "Do not bind Left Press on Frame" in text


def test_select_openbox_config_prefers_release_file() -> None:
    """The session package rc is used when present."""
    path = select_openbox_config(directory=_SESSION)
    assert path == _SESSION / "openbox-rc.xml"
    assert path.is_file()


def test_apply_release_openbox_stops_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing openbox is replaced with the release config path."""
    conf = tmp_path / "openbox-rc.xml"
    conf.write_text("<openbox_config/>\n", encoding="utf-8")
    binary = tmp_path / "openbox"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    calls: list[object] = []
    killed: list[tuple[int, int]] = []

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.openbox.shutil.which", lambda _: str(binary))

    def fake_run(args, **kwargs):
        if args[:1] == ["pgrep"] and "-ax" in args:
            return type("Result", (), {"returncode": 0, "stdout": "1111 openbox --config-file /other.xml\n"})()
        return type("Result", (), {"returncode": 0, "stdout": "4242\n"})()

    monkeypatch.setattr("pideck.infrastructure.session.openbox.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pideck.infrastructure.session.openbox.os.kill",
        lambda pid, sig: killed.append((pid, sig)) or (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.openbox.subprocess.Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    apply_release_openbox(config=conf)

    assert killed and killed[0][0] == 4242
    assert all(sig != 9 for _, sig in killed)
    assert len(calls) == 1
    assert calls[0][0][0] == [str(binary), "--config-file", str(conf)]


def test_apply_release_openbox_skips_restart_when_config_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Do not bounce openbox when it already uses the selected config."""
    conf = tmp_path / "openbox-rc.xml"
    conf.write_text("<openbox_config/>\n", encoding="utf-8")
    binary = tmp_path / "openbox"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("pideck.infrastructure.session.openbox.shutil.which", lambda _: str(binary))
    monkeypatch.setattr(
        "pideck.infrastructure.session.openbox.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": f"1111 openbox --config-file {conf}\n"}
        )(),
    )
    monkeypatch.setattr(
        "pideck.infrastructure.session.openbox.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not restart")),
    )

    apply_release_openbox(config=conf)


def test_apply_release_openbox_noop_without_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Headless validation paths do not touch the window manager."""
    conf = tmp_path / "openbox-rc.xml"
    conf.write_text("<openbox_config/>\n", encoding="utf-8")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        "pideck.infrastructure.session.openbox.shutil.which",
        lambda _: (_ for _ in ()).throw(AssertionError("should not look up openbox")),
    )

    apply_release_openbox(config=conf)
