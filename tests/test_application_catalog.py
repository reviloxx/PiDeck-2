"""Verify bundled app installation and registration without package operations."""

from dataclasses import replace
from pathlib import Path

import pytest

from pideck.application.catalog import available_applications, register_application
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config.catalog import load_catalog
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.flatpak_installer import FlatpakInstaller, InstallationError
from tests.test_configuration import configuration_document

ROOT = Path(__file__).resolve().parents[1]


def youtube():
    """Load the actual bundled application definition."""
    return load_catalog(ROOT / "assets/catalog/applications.yaml")[0]


def test_catalog_is_launchable_in_tv_mode() -> None:
    """The catalog contains the real Flatpak ID, TV argument, label and icon."""
    app = youtube()
    assert app.identifier == "youtube"
    assert app.name == "YouTube"
    assert app.executable == "flatpak"
    assert app.arguments == ("run", "com.ktechpit.red", "--tv")
    assert app.icon is not None and (ROOT / "config" / app.icon).is_file()


def test_registration_preserves_existing_choices() -> None:
    """Registration preserves settings and handles the all-visible sentinel."""
    config = YamlConfigurationParser().parse(configuration_document())
    added = register_application(config, youtube())
    assert added.home.visible_applications == (*config.home.visible_applications, "youtube")
    assert added.settings == config.settings
    assert added.applications[:-1] == config.applications
    all_visible = replace(config, home=replace(config.home, visible_applications=()))
    assert register_application(all_visible, youtube()).home.visible_applications == ()
    with pytest.raises(ConfigurationError, match="already exists"):
        register_application(added, youtube())


def test_catalog_does_not_duplicate_existing_flatpak() -> None:
    """Custom identifiers for the same Flatpak remain untouched."""
    config = YamlConfigurationParser().parse(configuration_document())
    custom = replace(youtube(), identifier="my-red", name="My Red")
    config = replace(config, applications=(*config.applications, custom))
    assert available_applications(config, (youtube(),)) == ()


@pytest.mark.parametrize("installed", [False, True])
def test_installer_reuses_existing_or_installs_to_user_scope(
    monkeypatch: pytest.MonkeyPatch, installed: bool,
) -> None:
    """Existing system apps are reused and new apps use the verified user remote."""
    monkeypatch.setattr(FlatpakInstaller, "needs_password", staticmethod(lambda: False))
    installer = FlatpakInstaller()
    calls = []

    def run(command, password=None):
        calls.append((command, password))
        if command[:2] == ["flatpak", "list"]:
            return "com.ktechpit.red\n" if installed else ""
        if command[:2] == ["flatpak", "remotes"]:
            return "flathub\thttps://dl.flathub.org/repo/\n"
        return "app/com.ktechpit.red/x86_64/stable\n"

    monkeypatch.setattr(installer, "_run", run)
    installer.install(youtube())
    commands = [command for command, _ in calls]
    if installed:
        assert len(commands) == 1
    else:
        assert ["flatpak", "install", "--user", "--noninteractive", "--assumeyes",
                "flathub", "com.ktechpit.red"] in commands
        assert commands[-1] == ["flatpak", "info", "--user", "--show-ref", "com.ktechpit.red"]


def test_installer_authentication_and_untrusted_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing prerequisite requires consent and an unexpected remote is rejected."""
    monkeypatch.setattr(FlatpakInstaller, "needs_password", staticmethod(lambda: True))
    installer = FlatpakInstaller()
    with pytest.raises(InstallationError, match="authentication"):
        installer.install(youtube())
    calls = []

    def run(command, password=None):
        calls.append((command, password))
        if command[:2] == ["flatpak", "remotes"]:
            return "flathub\thttps://example.invalid/repo/\n"
        return ""

    monkeypatch.setattr(installer, "_run", run)
    with pytest.raises(InstallationError, match="unexpected URL"):
        installer.install(youtube(), "secret")
    assert calls[0][0] == ["sudo", "-S", "-p", "", "apt-get", "install", "--no-remove", "-y", "--", "flatpak"]
    assert calls[0][1] == "secret"
    assert all(command[:2] != ["flatpak", "install"] for command, _ in calls)


def test_installer_creates_user_remote_and_propagates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing remotes are added for this user, and failed installs stay failed."""
    monkeypatch.setattr(FlatpakInstaller, "needs_password", staticmethod(lambda: False))
    installer = FlatpakInstaller()
    commands = []

    def run(command, password=None):
        commands.append(command)
        if command[:2] == ["flatpak", "install"]:
            raise InstallationError("network unavailable")
        return ""

    monkeypatch.setattr(installer, "_run", run)
    with pytest.raises(InstallationError, match="network unavailable"):
        installer.install(youtube())
    assert ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
            "https://dl.flathub.org/repo/flathub.flatpakrepo"] in commands
    assert not any(command[:2] == ["flatpak", "info"] for command in commands)


def test_installer_runs_commands_and_checks_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real subprocess boundary without installing anything on the host."""
    executable = tmp_path / "flatpak"
    log = tmp_path / "commands"
    executable.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "case \"$1\" in\n"
        "  remotes) printf 'flathub\\thttps://dl.flathub.org/repo/\\n' ;;\n"
        "  info) printf 'app/com.ktechpit.red/x86_64/stable\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    FlatpakInstaller().install(youtube())
    assert log.read_text(encoding="utf-8").splitlines() == [
        "list --app --columns=application",
        "remotes --user --columns=name,url",
        "install --user --noninteractive --assumeyes flathub com.ktechpit.red",
        "info --user --show-ref com.ktechpit.red",
    ]
