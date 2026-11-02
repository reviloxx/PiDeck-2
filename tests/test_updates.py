"""Tests for native application update detection."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pideck.domain.configuration import ApplicationDefinition
from pideck.infrastructure.platform_updates import NativeUpdateGateway
from pideck.application.ports.updates import UpdateStatus
from pideck.application.updates import ApplicationUpdateService


def test_flatpak_update_check_reports_available_update(monkeypatch) -> None:
    """Flatpak availability is detected without downloading packages."""
    responses = {
        ("flatpak", "info", "--user", "--show-ref", "tv.kodi.Kodi"): (1, ""),
        ("flatpak", "info", "--system", "--show-ref", "tv.kodi.Kodi"): (0, "app/tv.kodi.Kodi/x86_64/stable\n"),
        ("flatpak", "info", "--system", "--show-commit", "tv.kodi.Kodi"): (0, "installed-commit\n"),
        ("flatpak", "list", "--system", "--app", "--columns=ref,version:full"):
            (0, "tv.kodi.Kodi/x86_64/stable\t21.3\n"),
        ("flatpak", "remote-ls", "--system", "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"):
            (0, "tv.kodi.Kodi/x86_64/stable\t21.4 Beta\tremote-commit\n"),
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
    assert info.current_version == "21.3"
    assert info.available_version == "21.4 Beta"


@pytest.mark.parametrize(
    "app_id,name,scope,version",
    [("com.valvesoftware.SteamLink", "Steam Link", "--system", "1.4.5.326"),
     ("com.ktechpit.red", "YouTube", "--user", "10.3.0")],
)
def test_flatpak_installed_version_uses_selected_reference(
    monkeypatch, app_id: str, name: str, scope: str, version: str,
) -> None:
    """Read the selected installation, architecture, and branch rather than another ref."""
    app = ApplicationDefinition(
        name.lower(), name, "flatpak",
        ("run", scope, "--arch=x86_64", "--branch=stable", app_id, "--tv"),
    )
    ref = f"{app_id}/x86_64/stable"
    responses = {
        ("flatpak", "info", scope, "--arch=x86_64", "--show-ref", app_id, "stable"):
            (0, f"app/{ref}\n"),
        ("flatpak", "info", scope, "--arch=x86_64", "--show-commit", app_id, "stable"):
            (0, "installed-commit\n"),
        ("flatpak", "list", scope, "--app", "--columns=ref,version:full"):
            (0, f"{app_id}/aarch64/stable\twrong\n{ref}\t{version}\n"),
        ("flatpak", "remote-ls", scope, "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"):
            (0, f"{app_id}/aarch64/stable\tother\tother-commit\n"),
    }

    def run(command, **_kwargs):
        code, stdout = responses[tuple(command)]
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    info = NativeUpdateGateway().check(app)
    assert info.status is UpdateStatus.UP_TO_DATE
    assert info.current_version == version
    assert info.installed_commit is None


@pytest.mark.parametrize("version_line,metadata_status", [("-", 0), ("", 0), ("", 1)])
def test_flatpak_without_version_keeps_commit(
    monkeypatch, version_line: str, metadata_status: int,
) -> None:
    """Missing version metadata yields a labeled installed commit, including on lookup failure."""
    app_id = "com.ktechpit.red"
    ref = f"{app_id}/x86_64/stable"
    responses = {
        ("flatpak", "info", "--user", "--show-ref", app_id): (0, f"app/{ref}\n"),
        ("flatpak", "info", "--user", "--show-commit", app_id): (0, "full-commit\n"),
        ("flatpak", "list", "--user", "--app", "--columns=ref,version:full"):
            (metadata_status, f"{ref}\t{version_line}\n"),
        ("flatpak", "remote-ls", "--user", "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"):
            (0, ""),
    }
    def run(command, **_kwargs):
        code, stdout = responses[tuple(command)]
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    app = ApplicationDefinition("youtube", "YouTube", "flatpak", ("run", "--user", app_id))
    info = NativeUpdateGateway().check(app)
    assert info.current_version is None
    assert info.installed_commit == "full-commit"
    assert info.status is (UpdateStatus.UNSUPPORTED if metadata_status else UpdateStatus.UP_TO_DATE)


def test_flatpak_target_ignores_option_values_and_application_arguments() -> None:
    """A dotted branch or application argument is not mistaken for the app ID."""
    from pideck.infrastructure.platform_updates import _flatpak_id

    assert _flatpak_id(("run", "--branch", "1.2", "com.ktechpit.red", "example.com")) == "com.ktechpit.red"
    with pytest.raises(ValueError, match="missing"):
        _flatpak_id(("run", "--branch", "1.2"))


def test_legacy_steam_link_launcher_uses_matching_flatpak_metadata(monkeypatch) -> None:
    """The legacy steamlink launcher can resolve an installed Steam Link Flatpak by name."""
    app_id = "com.valvesoftware.SteamLink"
    ref = f"{app_id}/x86_64/stable"
    responses = {
        ("flatpak", "list", "--app", "--columns=application,name"): (0, f"{app_id}\tSteam Link\n"),
        ("flatpak", "info", "--user", "--show-ref", app_id): (1, ""),
        ("flatpak", "info", "--system", "--show-ref", app_id): (0, f"app/{ref}\n"),
        ("flatpak", "info", "--system", "--show-commit", app_id): (0, "full-commit\n"),
        ("flatpak", "list", "--system", "--app", "--columns=ref,version:full"): (0, f"{ref}\t1.4.5.326\n"),
        ("flatpak", "remote-ls", "--system", "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"): (0, ""),
    }
    def run(command, **_kwargs):
        code, stdout = responses[tuple(command)]
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    info = NativeUpdateGateway().check(ApplicationDefinition("steam-link", "Steam Link", "steamlink"))
    assert info.current_version == "1.4.5.326"
    assert info.status is UpdateStatus.UP_TO_DATE


def test_flatpak_update_metadata_failure_preserves_installed_version(monkeypatch) -> None:
    """Failed remote metadata must not hide a version or claim the app is up to date."""
    app_id = "com.ktechpit.red"
    ref = f"{app_id}/x86_64/stable"
    responses = {
        ("flatpak", "info", "--user", "--show-ref", app_id): (0, f"app/{ref}\n"),
        ("flatpak", "info", "--user", "--show-commit", app_id): (0, "full-commit\n"),
        ("flatpak", "list", "--user", "--app", "--columns=ref,version:full"): (0, f"{ref}\t10.3.0\n"),
        ("flatpak", "remote-ls", "--user", "--updates", "--app", "--arch=*", "--columns=ref,version:full,commit:full"): (1, ""),
    }
    def run(command, **_kwargs):
        code, stdout = responses[tuple(command)]
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="network failure")

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    info = NativeUpdateGateway().check(ApplicationDefinition("youtube", "YouTube", "flatpak", ("run", "--user", app_id)))
    assert info.status is UpdateStatus.UNSUPPORTED
    assert info.current_version == "10.3.0"
    assert info.message == "Flatpak update metadata is unavailable"


def test_flatpak_named_installation_is_selected() -> None:
    """Explicit named installations are not confused with user or default system installs."""
    from pideck.infrastructure.platform_updates import _flatpak_scope

    assert _flatpak_scope("flatpak", ("run", "--installation=other", "org.example.App"), "org.example.App") == "--installation=other"
    assert _flatpak_scope("flatpak", ("run", "--installation", "other", "org.example.App"), "org.example.App") == "--installation=other"
    with pytest.raises(ValueError, match="missing"):
        _flatpak_scope("flatpak", ("run", "--installation=", "org.example.App"), "org.example.App")


def test_apt_resolves_executable_owner_for_check_and_update(monkeypatch) -> None:
    """An executable name different from its Debian package works in both paths."""
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "flatpak":
            raise FileNotFoundError("flatpak")
        if command[0] == "dpkg-query":
            return SimpleNamespace(returncode=0, stdout="nomachine-player: /usr/NX/bin/nxplayer\n", stderr="")
        if command[0] == "apt-cache":
            return SimpleNamespace(returncode=0, stdout="  Installed: 8.0\n  Candidate: (none)\n", stderr="")
        raise AssertionError(command)

    class Process:
        stdin = None

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("pideck.infrastructure.platform_updates.shutil.which", lambda _: "/usr/NX/bin/nxplayer")
    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.Popen", lambda command, **_: commands.append(command) or Process())
    monkeypatch.setattr("pideck.infrastructure.platform_updates.threading.Thread", Thread)
    app = ApplicationDefinition("nomachine", "NoMachine", "/usr/NX/bin/nxplayer")
    gateway = NativeUpdateGateway(image_mode=True)
    info = gateway.check(app)
    assert info.current_version == "8.0"
    assert info.status is UpdateStatus.UNSUPPORTED
    gateway.start(app, None, lambda _: None)
    assert ["apt-cache", "policy", "nomachine-player"] in commands
    assert [sys.executable, "-m", "pideck.infrastructure.image_update_client", "apt", "nomachine-player"] in commands


def test_apt_rejects_ambiguous_executable_owners(monkeypatch) -> None:
    """Do not choose an arbitrary package when multiple packages own the path."""
    from pideck.infrastructure.platform_updates import _apt_package

    monkeypatch.setattr("pideck.infrastructure.platform_updates.shutil.which", lambda _: "/usr/bin/example")
    monkeypatch.setattr(
        "pideck.infrastructure.platform_updates.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="first, second: /usr/bin/example\n", stderr="",
        ),
    )
    with pytest.raises(ValueError, match="Multiple packages"):
        _apt_package("example")


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


def test_apt_shows_installed_version_when_candidate_is_unavailable(monkeypatch) -> None:
    """Missing update metadata must not hide a known installed version."""
    def run(command, **_kwargs):
        if command[0] == "flatpak":
            raise FileNotFoundError("flatpak")
        return type("Result", (), {
            "returncode": 0, "stdout": "  Installed: 1.0\n  Candidate: (none)\n", "stderr": "",
        })()

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    monkeypatch.setattr("pideck.infrastructure.platform_updates.shutil.which", lambda _: None)
    info = NativeUpdateGateway().check(ApplicationDefinition("browser", "Browser", "firefox"))
    assert info.status is UpdateStatus.UNSUPPORTED
    assert info.current_version == "1.0"


def test_apt_comparison_failure_is_not_reported_as_up_to_date(monkeypatch) -> None:
    """Invalid package versions surface a failure instead of a false success."""
    def run(command, **_kwargs):
        if command[0] == "flatpak":
            raise FileNotFoundError("flatpak")
        if command[0] == "apt-cache":
            return type("Result", (), {
                "returncode": 0, "stdout": "  Installed: 1.0\n  Candidate: invalid\n", "stderr": "",
            })()
        return type("Result", (), {"returncode": 2, "stdout": "", "stderr": "bad version"})()

    monkeypatch.setattr("pideck.infrastructure.platform_updates.subprocess.run", run)
    info = NativeUpdateGateway().check(ApplicationDefinition("browser", "Browser", "firefox"))
    assert info.status is UpdateStatus.FAILED
    assert info.current_version == "1.0"


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
