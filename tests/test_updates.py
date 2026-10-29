"""Tests for native application update detection."""

from pathlib import Path
import sys

import pytest

from pideck.domain.configuration import ApplicationDefinition
from pideck.infrastructure.platform_updates import NativeUpdateGateway
from pideck.application.ports.updates import UpdateStatus
from pideck.application.updates import ApplicationUpdateService


def test_flatpak_update_check_reports_available_update(monkeypatch) -> None:
    """Flatpak availability is detected without downloading packages."""
    responses = {
        ("flatpak", "info", "--show-commit", "tv.kodi.Kodi"): (0, "installed-commit\n"),
        ("flatpak", "remote-ls", "--updates", "--app", "--columns=application,version,commit"):
            (0, "tv.kodi.Kodi 21.4 remote-commit\n"),
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
    assert info.available_version == "21.4"


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


def test_apt_updates_work_when_flatpak_is_not_installed(monkeypatch) -> None:
    """Image installations without Flatpak can still inspect apt applications."""
    def run(command, **_kwargs):
        if command[0] == "flatpak":
            raise FileNotFoundError("flatpak")
        if command[:3] == ["apt-cache", "policy", "firefox-esr"]:
            return type("Result", (), {
                "returncode": 0, "stdout": "  Installed: 1.0\n  Candidate: 2.0\n", "stderr": "",
            })()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    info = NativeUpdateGateway(image_mode=True).check(ApplicationDefinition("browser", "Browser", "firefox-esr"))
    assert info.status is UpdateStatus.AVAILABLE


def test_image_apt_update_uses_passwordless_broker(monkeypatch) -> None:
    """Image-mode apt updates never invoke sudo or request a password."""
    commands = []

    class Process:
        stdin = None

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    def popen(command, **_kwargs):
        commands.append(command)
        return Process()

    monkeypatch.setattr("pideck.infrastructure.platform_updates._flatpak_target", lambda *_: None)
    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.Popen", popen)
    monkeypatch.setattr("pideck.infrastructure.platform_updates.threading.Thread", Thread)
    app = ApplicationDefinition("browser", "Browser", "firefox-esr")
    NativeUpdateGateway(image_mode=True).start(app, None, lambda _: None)
    assert commands == [[sys.executable, "-m", "pideck.infrastructure.image_update_client", "apt", "firefox-esr"]]


def test_image_flatpak_scope_honors_explicit_launcher_options(monkeypatch) -> None:
    """Explicit system or user Flatpak installs are not confused with app arguments."""
    from pideck.infrastructure.platform_updates import _flatpak_uses_user_scope

    monkeypatch.setattr(
        "pideck.infrastructure.platform_updates.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    assert not _flatpak_uses_user_scope("flatpak", ("run", "--system", "org.example.App"), "org.example.App")
    assert _flatpak_uses_user_scope("flatpak", ("run", "--user", "org.example.App"), "org.example.App")
    assert _flatpak_uses_user_scope("flatpak", ("run", "org.example.App", "--system"), "org.example.App")


def test_image_permission_failures_never_request_password() -> None:
    """Image-mode failures are visible without falling back to a password prompt."""
    class Gateway:
        def start(self, application, password, callback):
            raise PermissionError("broker denied access")

    states = []
    service = ApplicationUpdateService(Gateway(), allow_password_prompt=False)
    service.start_async(ApplicationDefinition("browser", "Browser", "firefox"), None, states.append)
    assert states[-1].status is UpdateStatus.FAILED
    assert states[-1].message == "broker denied access"


@pytest.mark.parametrize("user_install", [True, False])
def test_image_flatpak_updates_use_correct_scope(monkeypatch, user_install: bool) -> None:
    """User Flatpaks run unprivileged; system Flatpaks use the broker."""
    commands = []

    class Process:
        stdin = None

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("pideck.infrastructure.platform_updates._flatpak_target", lambda *_: "org.example.App")
    monkeypatch.setattr("pideck.infrastructure.platform_updates._flatpak_uses_user_scope", lambda *_: user_install)
    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.Popen", lambda command, **_: commands.append(command) or Process())
    monkeypatch.setattr("pideck.infrastructure.platform_updates.threading.Thread", Thread)
    app = ApplicationDefinition("app", "App", "flatpak run org.example.App")
    NativeUpdateGateway(image_mode=True).start(app, None, lambda _: None)
    if user_install:
        assert commands == [["flatpak", "update", "--user", "--noninteractive", "--assumeyes", "org.example.App"]]
    else:
        assert commands == [[sys.executable, "-m", "pideck.infrastructure.image_update_client", "flatpak", "org.example.App"]]
