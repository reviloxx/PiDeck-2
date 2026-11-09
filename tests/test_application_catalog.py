"""Verify bundled app installation and registration without package operations."""

from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from pideck.application.catalog import (
    catalog_window_modes,
    configured_application,
    effective_cec_mode,
    register_application,
    unregister_application,
)
from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.domain.configuration import ApplicationDefinition, CecMode
from pideck.domain.errors import ConfigurationError
from pideck.infrastructure.config.catalog import load_catalog
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.flatpak_installer import FlatpakInstaller, InstallationError
from tests.test_configuration import configuration_document

ROOT = Path(__file__).resolve().parents[1]


def entry(identifier: str = "youtube"):
    """Load one actual bundled catalog entry."""
    return next(item for item in load_catalog(ROOT / "assets/catalog/applications.yaml") if item.identifier == identifier)


def youtube():
    """Load the actual bundled YouTube application definition."""
    return entry().application


def test_catalog_is_launchable_in_tv_mode() -> None:
    """The catalog contains the real Flatpak ID, TV argument, label and icon."""
    app = youtube()
    assert app.identifier == "youtube"
    assert app.name == "YouTube"
    assert app.executable == "flatpak"
    assert app.arguments[0] == "run"
    assert "--env=LIBVA_DRIVER_NAME=nvidia" in app.arguments
    assert any(arg.startswith("--env=QTWEBENGINE_CHROMIUM_FLAGS=") for arg in app.arguments)
    assert app.arguments[-2:] == ("com.ktechpit.red", "--tv")
    assert app.icon is not None and (ROOT / "config" / app.icon).is_file()


def test_youtube_icon_renders_as_a_rounded_play_tile() -> None:
    """Render the bundled icon with the same tile shape and palette as other apps."""
    renderer = QSvgRenderer(str(ROOT / "assets/icons/youtube.svg"))
    assert renderer.isValid()
    image = QImage(128, 128, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(64, 15).name() == "#ed7777"
    assert image.pixelColor(32, 64).name() == "#101214"
    assert image.pixelColor(64, 64).name() == "#f4f7f7"


def test_registration_preserves_existing_choices() -> None:
    """Registration preserves settings and handles the all-visible sentinel."""
    config = YamlConfigurationParser().parse(configuration_document())
    added = register_application(config, youtube())
    assert added.home.visible_applications == (*config.home.visible_applications, "youtube")
    assert added.settings == config.settings
    assert added.applications[:-1] == config.applications
    assert added.applications[-1].cec_mode is None
    assert effective_cec_mode(added.applications[-1], (entry(),)) is CecMode.KEYBOARD
    all_visible = replace(config, home=replace(config.home, visible_applications=()))
    assert register_application(all_visible, youtube()).home.visible_applications == ()
    with pytest.raises(ConfigurationError, match="already exists"):
        register_application(added, youtube())


def test_catalog_recognizes_existing_flatpak_under_custom_identifier() -> None:
    """Custom identifiers for the same Flatpak count as the installed catalog application."""
    config = YamlConfigurationParser().parse(configuration_document())
    assert configured_application(config, entry()) is None
    custom = replace(youtube(), identifier="my-red", name="My Red")
    config = replace(config, applications=(*config.applications, custom))
    assert configured_application(config, entry()) == custom


def test_unregistration_removes_application_and_visibility() -> None:
    """Removing an application drops its tile and keeps every other choice."""
    config = register_application(YamlConfigurationParser().parse(configuration_document()), youtube())
    removed = unregister_application(config, "youtube")
    assert all(item.identifier != "youtube" for item in removed.applications)
    assert "youtube" not in removed.home.visible_applications
    assert removed.settings == config.settings
    assert unregister_application(removed, "youtube") is removed


def test_catalog_declares_window_defaults() -> None:
    """Browsers and YouTube start maximized unless a window-modes.yaml policy overrides it."""
    modes = catalog_window_modes(load_catalog(ROOT / "assets/catalog/applications.yaml"))
    assert modes["chrome"].value == "maximized"
    assert modes["brave"].value == "maximized"
    assert modes["youtube"].value == "maximized"
    assert modes["nomachine"].value == "fullscreen"


@pytest.mark.parametrize("installed", [False, True])
def test_installer_reuses_existing_or_installs_to_user_scope(
    monkeypatch: pytest.MonkeyPatch, installed: bool,
) -> None:
    """Existing system apps are reused and new apps use the verified user remote."""
    monkeypatch.setattr(FlatpakInstaller, "needs_password", staticmethod(lambda: False))
    installer = FlatpakInstaller()
    calls = []

    def run(command, password=None, on_line=None):
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
        assert ["flatpak", "install", "--user", "--assumeyes",
                "flathub", "com.ktechpit.red"] in commands
        assert commands[-1] == ["flatpak", "info", "--user", "--show-ref", "com.ktechpit.red"]


def test_installer_authentication_and_untrusted_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing prerequisite requires consent and an unexpected remote is rejected."""
    monkeypatch.setattr(FlatpakInstaller, "needs_password", staticmethod(lambda: True))
    installer = FlatpakInstaller()
    with pytest.raises(InstallationError, match="authentication"):
        installer.install(youtube())
    calls = []

    def run(command, password=None, on_line=None):
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

    def run(command, password=None, on_line=None):
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
        "install --user --assumeyes flathub com.ktechpit.red",
        "info --user --show-ref com.ktechpit.red",
    ]


def test_installer_reports_streamed_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress lines from the real subprocess boundary reach the callback in order."""
    executable = tmp_path / "flatpak"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  remotes) printf 'flathub\\thttps://dl.flathub.org/repo/\\n' ;;\n"
        "  install) printf 'Installing 1/2?\\nInstalling 1/2? ?? 50%%\\n"
        "Installing 2/2?\\nInstalling 2/2? ???? 100%%\\nInstallation complete.\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    reports: list[InstallProgress] = []
    FlatpakInstaller().install(youtube(), on_progress=reports.append)
    assert [(item.stage, item.percent) for item in reports] == [
        (InstallStage.PREPARING, None),
        (InstallStage.DOWNLOADING, None),
        (InstallStage.DOWNLOADING, 0),
        (InstallStage.DOWNLOADING, 25),
        (InstallStage.DOWNLOADING, 50),
        (InstallStage.DOWNLOADING, 100),
        (InstallStage.FINISHING, 100),
    ]


def test_installer_cancel_stops_streaming_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling while flatpak streams output terminates it and reports cancellation."""
    executable = tmp_path / "flatpak"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  remotes) printf 'flathub\\thttps://dl.flathub.org/repo/\\n' ;;\n"
        "  install) printf 'Installing?\\n'; exec sleep 30 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    installer = FlatpakInstaller()

    def cancel_once_downloading(progress: InstallProgress) -> None:
        if progress.percent == 0:
            threading.Thread(target=installer.cancel).start()

    started = time.monotonic()
    with pytest.raises(InstallationError, match="cancelled"):
        installer.install(youtube(), on_progress=cancel_once_downloading)
    assert time.monotonic() - started < 10


def test_catalog_youtube_keeps_cec_for_keyboard_forwarding() -> None:
    """YouTube receives TV remote keys from PiDeck instead of owning the adapter."""
    assert youtube().cec_mode is CecMode.KEYBOARD


def test_catalog_kodi_keeps_cec_for_keyboard_forwarding() -> None:
    """Kodi receives TV remote keys from PiDeck instead of owning the adapter."""
    assert entry("kodi").application.cec_mode is CecMode.KEYBOARD


def test_effective_cec_mode_inherits_from_matching_catalog_identifier() -> None:
    """Existing apt installs gain the catalog mode by id without rewriting pideck.yaml."""
    catalog = (entry("kodi"),)
    legacy = ApplicationDefinition("kodi", "Kodi", "kodi")
    assert effective_cec_mode(legacy, catalog) is CecMode.KEYBOARD
    explicit = replace(legacy, cec_mode=CecMode.RELEASE)
    assert effective_cec_mode(explicit, catalog) is CecMode.RELEASE
    unrelated = ApplicationDefinition("kodi-custom", "Kodi", "kodi")
    assert effective_cec_mode(unrelated, catalog) is CecMode.RELEASE


def test_effective_cec_mode_inherits_from_matching_catalog_flatpak() -> None:
    """Existing installs gain the catalog mode; explicit and unrelated apps do not."""
    catalog = (entry(),)
    legacy = replace(youtube(), identifier="my-red", cec_mode=None)
    assert effective_cec_mode(legacy, catalog) is CecMode.KEYBOARD
    explicit = replace(legacy, cec_mode=CecMode.RELEASE)
    assert effective_cec_mode(explicit, catalog) is CecMode.RELEASE
    other = ApplicationDefinition("other", "Other", "kodi")
    assert effective_cec_mode(other, catalog) is CecMode.RELEASE
    other_flatpak = replace(legacy, arguments=("run", "org.example.App"))
    assert effective_cec_mode(other_flatpak, catalog) is CecMode.RELEASE
