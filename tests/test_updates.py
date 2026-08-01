"""Tests for native application update detection."""

from pathlib import Path

from pideck.domain.configuration import ApplicationDefinition
from pideck.infrastructure.platform_updates import NativeUpdateGateway
from pideck.application.ports.updates import UpdateStatus


def test_flatpak_update_check_reports_available_update(monkeypatch) -> None:
    """Flatpak availability is detected without downloading packages."""
    responses = {
        ("flatpak", "info", "--show-commit", "tv.kodi.Kodi"): (0, "installed-commit\n"),
        ("flatpak", "update", "--app", "--noninteractive", "--no-pull", "tv.kodi.Kodi"):
            (0, "Would update tv.kodi.Kodi\n"),
    }

    def fake_run(command, **kwargs):
        """Return controlled Flatpak command output."""
        result = type("Result", (), {})()
        result.returncode, result.stdout = responses[tuple(command)]
        result.stderr = ""
        return result

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", fake_run)
    application = ApplicationDefinition(
        identifier="kodi",
        name="Kodi",
        executable="/usr/bin/flatpak run tv.kodi.Kodi",
    )

    info = NativeUpdateGateway().check(application)

    assert info.status is UpdateStatus.AVAILABLE
    assert info.available_version is None


def test_apt_update_check_reports_candidate_version(monkeypatch) -> None:
    """Apt candidate versions produce an available update status."""
    def fake_run(command, **kwargs):
        """Return apt policy and dpkg comparison output."""
        result = type("Result", (), {})()
        if command[:3] == ["apt-cache", "policy", "firefox"]:
            result.returncode = 0
            result.stdout = (
                "firefox:\n"
                "  Installed: 1.0\n"
                "  Candidate: 2.0\n"
            )
        else:
            result.returncode = 0
            result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", fake_run)
    application = ApplicationDefinition("firefox", "Firefox", "firefox")

    info = NativeUpdateGateway().check(application)

    assert info.status is UpdateStatus.AVAILABLE
    assert info.current_version == "1.0"
    assert info.available_version == "2.0"
