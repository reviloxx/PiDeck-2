"""Tests for catalog install models, the management service and unprivileged adapters."""

from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from pideck.application.app_management import ApplicationManagementService
from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.application.ports.packages import PackageOperationCancelled, PackageOperationError
from pideck.domain.catalog import CatalogEntry, InstallMethod, InstallSpec, InstallState, UserDefault
from pideck.domain.configuration import ApplicationDefinition, WindowMode
from pideck.domain.errors import ConfigurationError, ConfigurationValidationError
from pideck.infrastructure.config.catalog import load_catalog
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.flatpak_installer import FlatpakInstaller
from pideck.infrastructure.image_update_client import ImageUpdateError
from pideck.infrastructure.packages.installed import InstalledPackages, device_architecture
from pideck.infrastructure.packages.manager import SystemPackageManager
from pideck.infrastructure.packages.privileged import PrivilegedApplications
from pideck.infrastructure.user_defaults import UserDefaultsInstaller
from tests.test_configuration import configuration_document

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "assets/catalog/applications.yaml"
SUPPORTED = ["kodi", "youtube", "steam-link", "chromium", "chrome", "brave", "firefox", "nomachine", "retroarch"]


def catalog() -> tuple[CatalogEntry, ...]:
    """Load the bundled catalog."""
    return load_catalog(CATALOG)


def test_catalog_lists_every_supported_application_for_both_images() -> None:
    """All supported apps are installable on x86 and the Raspberry Pi, with existing icons."""
    entries = catalog()
    assert [entry.identifier for entry in entries] == SUPPORTED
    for entry in entries:
        assert set(entry.install) == {"amd64", "arm64"}, entry.identifier
        assert entry.application.icon is not None
        assert (ROOT / "config" / entry.application.icon).resolve().is_file()
        for source in (default.source for default in entry.user_defaults):
            assert source.is_file()


def test_catalog_architecture_overrides() -> None:
    """Steam Link uses Valve's Raspberry Pi package and the Flatpak on x86."""
    steam = next(entry for entry in catalog() if entry.identifier == "steam-link")
    assert steam.definition_for("amd64").arguments == ("run", "com.valvesoftware.SteamLink")
    pi = steam.definition_for("arm64")
    assert (pi.executable, pi.arguments) == ("steamlink", ())
    assert steam.spec_for("arm64").dependencies
    assert steam.spec_for("riscv64") is None
    assert steam.definition_for("riscv64") == steam.application


def test_kodi_defaults_to_german() -> None:
    """Kodi's first-run settings select the German language add-on."""
    kodi = next(entry for entry in catalog() if entry.identifier == "kodi")
    (default,) = kodi.user_defaults
    assert default.target == PurePosixPath(".kodi/userdata/guisettings.xml")
    assert "resource.language.de_de" in default.source.read_text(encoding="utf-8")


@pytest.mark.parametrize("arguments", [
    {"method": InstallMethod.FLATPAK},
    {"method": InstallMethod.FLATPAK, "flatpak": "org.example.App", "packages": ("x",)},
    {"method": InstallMethod.APT},
    {"method": InstallMethod.DEB, "packages": ("x",), "url": "https://example.org/x.deb"},
])
def test_install_spec_requires_method_fields(arguments: dict) -> None:
    """Incomplete or mixed install instructions are rejected."""
    with pytest.raises(ConfigurationValidationError):
        InstallSpec(**arguments)


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside", ""])
def test_user_default_targets_stay_in_home(target: str) -> None:
    """First-run files cannot be written outside the user's home."""
    with pytest.raises(ConfigurationValidationError):
        UserDefault(Path("/tmp/source"), PurePosixPath(target))


@pytest.mark.parametrize("install", [
    "{amd64: {method: apt, packages: [x], shell: id}}",
    "{AMD64: {method: apt, packages: [x]}}",
    "{amd64: {method: snap, packages: [x]}}",
    "[amd64]",
])
def test_loader_rejects_invalid_install_sections(tmp_path: Path, install: str) -> None:
    """Unknown keys, architectures and methods are configuration errors."""
    path = tmp_path / "applications.yaml"
    path.write_text(
        f"applications:\n  - id: app\n    name: App\n    executable: app\n    install: {install}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_catalog(path)


class FakePackages:
    """Record package-manager calls and answer state queries from sets."""

    def __init__(self, installed: set[str] = frozenset(), manageable: bool = True) -> None:
        self.installed = set(installed)
        self.manageable = manageable
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _key(spec: InstallSpec) -> str:
        return spec.flatpak or spec.packages[0]

    def is_installed(self, spec: InstallSpec) -> bool:
        return self._key(spec) in self.installed

    def can_install(self, spec: InstallSpec) -> bool:
        return self.manageable or spec.method is InstallMethod.FLATPAK

    def can_remove(self, spec: InstallSpec) -> bool:
        return self.manageable

    def needs_password(self, spec: InstallSpec) -> bool:
        return False

    def install(self, identifier, spec, on_progress, password=None) -> None:
        self.calls.append(("install", identifier))
        on_progress(InstallProgress(InstallStage.DOWNLOADING, 50))

    def uninstall(self, identifier, spec, on_progress) -> None:
        self.calls.append(("uninstall", identifier))

    def cancel(self) -> None:
        self.calls.append(("cancel", ""))


def test_service_reports_install_state_per_image() -> None:
    """States reflect packages, architecture support and the image's capabilities."""
    config = YamlConfigurationParser().parse(configuration_document())
    service = ApplicationManagementService(catalog(), FakePackages({"kodi"}, manageable=False), "amd64")
    states = {status.identifier: status for status in service.statuses(config)}
    assert states["kodi"].state is InstallState.INSTALLED and not states["kodi"].removable
    assert states["youtube"].state is InstallState.NOT_INSTALLED
    assert states["chrome"].state is InstallState.REQUIRES_NEWER_IMAGE
    other = ApplicationManagementService(catalog(), FakePackages(), "riscv64")
    assert other.status(config, "kodi").state is InstallState.UNAVAILABLE


def test_service_registers_architecture_definition_and_unregisters_custom_entries() -> None:
    """Installed apps join the home screen; removals also drop custom entries for the same Flatpak."""
    config = YamlConfigurationParser().parse(configuration_document())
    packages = FakePackages()
    service = ApplicationManagementService(catalog(), packages, "arm64")
    reports: list[InstallProgress] = []
    service.install("steam-link", reports.append)
    assert packages.calls == [("install", "steam-link")] and reports[0].percent == 50
    added = service.registered(config, "steam-link")
    steam = added.applications[-1]
    assert (steam.identifier, steam.executable) == ("steam-link", "steamlink")
    assert service.registered(added, "steam-link") is added
    browser = service.registered(config, "brave").applications[-1]
    assert browser.window_mode is WindowMode.MAXIMIZED and browser.cec_mode is None
    custom = replace(service.registered(config, "youtube").applications[-1], identifier="my-red")
    with_custom = replace(config, applications=(*config.applications, custom))
    removed = service.unregistered(with_custom, "youtube")
    assert all(item.identifier != "my-red" for item in removed.applications)
    assert service.unregistered(config, "youtube") is config
    with pytest.raises(PackageOperationError, match="Unknown"):
        service.uninstall("unknown", reports.append)


def test_installed_packages_reads_dpkg_and_flatpak_state(tmp_path: Path) -> None:
    """Fully installed Debian packages and deployed Flatpaks count as installed."""
    status = tmp_path / "status"
    status.write_text(
        "Package: kodi\nStatus: install ok installed\n\n"
        "Package: retroarch\nStatus: deinstall ok config-files\n\n"
        "Package: libretro-mgba\nStatus: install ok installed\n",
        encoding="utf-8",
    )
    system, user = tmp_path / "system", tmp_path / "user"
    (system / "app/com.ktechpit.red/current/active").mkdir(parents=True)
    (user / "app/org.example.App/current/active").mkdir(parents=True)
    installed = InstalledPackages(status, system, user)
    assert installed.is_installed(InstallSpec(InstallMethod.APT, ("kodi",)))
    assert not installed.is_installed(InstallSpec(InstallMethod.APT, ("retroarch", "libretro-mgba")))
    assert installed.flatpak_scope("com.ktechpit.red") == "system"
    assert installed.flatpak_scope("org.example.App") == "user"
    assert not installed.is_installed(InstallSpec(InstallMethod.FLATPAK, flatpak="org.other.App"))
    assert not InstalledPackages(tmp_path / "missing").debian_installed(["kodi"])


@pytest.mark.parametrize(("machine", "expected"), [("x86_64", "amd64"), ("aarch64", "arm64"), ("riscv64", "riscv64")])
def test_device_architecture_uses_debian_names(machine: str, expected: str) -> None:
    """Catalog keys use Debian architecture names."""
    assert device_architecture(machine) == expected


class FakeBroker:
    """Script image-service responses for the privileged client."""

    def __init__(self, statuses: list[dict], supported: bool = True) -> None:
        self.statuses = statuses
        self.supported_flag = supported
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, action: str, **fields):
        self.requests.append((action, fields))
        if action == "capabilities":
            if not self.supported_flag:
                raise ImageUpdateError("Unsupported updater request")
            return {"applications": 1}
        if action in ("app_install", "app_remove"):
            return {"token": "t"}
        if action == "app_cancel":
            raise ImageUpdateError("cannot stop now")
        return self.statuses.pop(0)


def test_privileged_client_reports_progress_and_results() -> None:
    """Service progress is translated; failures and cancellation become port errors."""
    broker = FakeBroker([
        {"state": "running", "stage": "downloading", "step": 2, "steps": 3, "percent": 40},
        {"state": "running", "stage": "installing", "step": 3, "steps": 3, "percent": None},
        {"state": "succeeded"},
        {"state": "failed", "error": "apt failed"},
        {"state": "cancelled"},
    ])
    client = PrivilegedApplications(broker, poll_interval=0)
    assert client.supported()
    reports: list[InstallProgress] = []
    client.install("chrome", reports.append)
    assert broker.requests[1] == ("app_install", {"id": "chrome"})
    assert reports == [
        InstallProgress(InstallStage.PREPARING),
        InstallProgress(InstallStage.DOWNLOADING, 40, 2, 3),
        InstallProgress(InstallStage.INSTALLING, None, 3, 3),
    ]
    with pytest.raises(PackageOperationError, match="apt failed"):
        client.remove("chrome", reports.append)
    with pytest.raises(PackageOperationCancelled):
        client.install("chrome", reports.append)
    assert not PrivilegedApplications(FakeBroker([], supported=False)).supported()


def test_privileged_cancel_maps_refusal_to_permission_error() -> None:
    """A refused cancellation is reported like other deferred cancellations."""
    client = PrivilegedApplications(FakeBroker([]))
    client.cancel()
    client._token = "t"
    with pytest.raises(PermissionError, match="cannot stop"):
        client.cancel()


class FakeUserFlatpak:
    """Record user Flatpak operations."""

    calls: list[tuple[str, str]] = []

    def install_ref(self, app_id, password=None, on_progress=None) -> None:
        self.calls.append(("install", app_id))

    def uninstall(self, app_id, on_progress=None) -> None:
        self.calls.append(("uninstall", app_id))

    def cancel(self) -> None:
        self.calls.append(("cancel", ""))


def test_package_manager_routes_by_image_capability(tmp_path: Path) -> None:
    """New images use the service; old images fall back to user Flatpaks only."""
    user = tmp_path / "user"
    (user / "app/com.ktechpit.red/current/active").mkdir(parents=True)
    installed = InstalledPackages(tmp_path / "status", tmp_path / "system", user)
    red = InstallSpec(InstallMethod.FLATPAK, flatpak="com.ktechpit.red")
    chrome = InstallSpec(InstallMethod.APT, ("google-chrome-stable",))
    FakeUserFlatpak.calls = []
    old = SystemPackageManager(PrivilegedApplications(FakeBroker([], supported=False)), installed, FakeUserFlatpak)
    assert old.can_install(red) and not old.can_install(chrome)
    assert old.can_remove(red) and not old.can_remove(chrome)
    old.install("youtube", red, lambda _: None)
    old.uninstall("youtube", red, lambda _: None)
    assert FakeUserFlatpak.calls == [("install", "com.ktechpit.red"), ("uninstall", "com.ktechpit.red")]
    with pytest.raises(PackageOperationError, match="newer PiDeck image"):
        old.install("chrome", chrome, lambda _: None)
    broker = FakeBroker([{"state": "succeeded"}, {"state": "succeeded"}])
    new = SystemPackageManager(PrivilegedApplications(broker, poll_interval=0), installed, FakeUserFlatpak)
    assert new.can_install(chrome) and not new.needs_password(red)
    new.install("chrome", chrome, lambda _: None)
    new.uninstall("chrome", chrome, lambda _: None)
    assert [action for action, _ in broker.requests if action.startswith("app_")] == [
        "app_install", "app_status", "app_remove", "app_status",
    ]


def test_user_flatpak_uninstall_keeps_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """User Flatpak removal never passes --delete-data."""
    installer = FlatpakInstaller()
    commands: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", lambda command, *args, **kwargs: commands.append(command) or "")
    installer.uninstall("com.ktechpit.red")
    assert commands == [
        ["flatpak", "uninstall", "--user", "--noninteractive", "--assumeyes", "com.ktechpit.red"],
        ["flatpak", "uninstall", "--user", "--unused", "--noninteractive", "--assumeyes"],
    ]
    assert not any("--delete-data" in command for command in commands)


def test_user_defaults_are_created_only_when_absent(tmp_path: Path) -> None:
    """First-run files never overwrite settings the application already wrote."""
    source = tmp_path / "guisettings.xml"
    source.write_text("german", encoding="utf-8")
    entry = CatalogEntry(
        ApplicationDefinition("kodi", "Kodi", "kodi"),
        user_defaults=(UserDefault(source, PurePosixPath(".kodi/userdata/guisettings.xml")),),
    )
    home = tmp_path / "home"
    installer = UserDefaultsInstaller(home)
    installer.apply(entry)
    target = home / ".kodi/userdata/guisettings.xml"
    assert target.read_text(encoding="utf-8") == "german"
    target.write_text("user choice", encoding="utf-8")
    installer.apply(entry)
    assert target.read_text(encoding="utf-8") == "user choice"
    source.unlink()
    target.unlink()
    installer.apply(entry)
    assert not target.exists()


def test_new_configurations_default_to_german_without_migrating_saved_ones() -> None:
    """Fresh installs start in German; saved settings keep their language."""
    from pideck.infrastructure.config.defaults import default_configuration

    assert default_configuration().settings.language == "de"
    document = configuration_document()
    document["settings"].pop("language", None)
    assert YamlConfigurationParser().parse(document).settings.language == "en"
