"""Tests for root-side catalog installs behind the image update broker."""

import hashlib
import os
from pathlib import Path
import threading
import time

import pytest

from pideck.infrastructure import image_update_client, privileged_apps
from pideck.infrastructure.image_update_daemon import PackageOperations, UpdateServer
from pideck.infrastructure.image_update_store import ImageUpdateStore
from pideck.infrastructure.privileged_apps import (
    ApplicationOperations,
    ApplicationPlans,
    OperationFailed,
    Progress,
    Step,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "assets/catalog/applications.yaml"


def plans(architecture: str = "amd64", catalog: Path = CATALOG, download: Path = Path("/nonexistent")) -> ApplicationPlans:
    """Plan against a fixed architecture without touching the host."""
    return ApplicationPlans([catalog], lambda: architecture, download)


def commands(steps: list[Step]) -> list[tuple[str, ...] | None]:
    """Return the fixed commands of a plan."""
    return [step.command for step in steps]


def test_apt_install_downloads_before_changing_packages() -> None:
    """Only the download steps of an apt install can be cancelled."""
    steps = plans("arm64").install("firefox")
    assert commands(steps) == [
        ("/usr/bin/apt-get", "update"),
        ("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "--download-only", "--",
         "firefox-esr", "firefox-esr-l10n-de", "libavcodec61"),
        ("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "--",
         "firefox-esr", "firefox-esr-l10n-de", "libavcodec61"),
    ]
    assert [step.cancellable for step in steps] == [True, True, False]


def test_apt_remove_checks_and_keeps_dependencies() -> None:
    """Removal targets only the app's own packages, never shared dependencies."""
    steps = plans("arm64").remove("steam-link")
    assert steps[0].action is not None
    assert steps[1].command == ("/usr/bin/apt-get", "remove", "-y", "--autoremove", "--", "steamlink")
    assert not any(step.cancellable for step in steps)


def test_flatpak_plans_use_system_scope_and_keep_data() -> None:
    """Flatpaks are installed system-wide and removed without --delete-data."""
    install = plans().install("youtube")
    assert install[1].command == (
        "/usr/bin/flatpak", "install", "--system", "--noninteractive", "--assumeyes", "flathub", "com.ktechpit.red",
    )
    assert install[1].flatpak_progress
    remove = commands(plans().remove("youtube"))
    assert remove[0] == ("/usr/bin/flatpak", "uninstall", "--system", "--noninteractive", "--assumeyes",
                         "com.ktechpit.red")
    assert all("--delete-data" not in command for command in remove if command)


def test_deb_plan_installs_verified_download() -> None:
    """Vendor packages are installed from the checksum-verified local copy."""
    steps = plans("amd64", download=Path("/var/cache/test")).install("nomachine")
    assert [step.stage for step in steps] == ["preparing", "downloading", "installing", "finishing"]
    assert steps[2].command == (
        "/usr/bin/apt-get", "install", "-y", "--no-install-recommends", "--", "/var/cache/test/nomachine-player.deb",
    )
    assert plans().remove("nomachine")[1].command[-1] == "nomachine-player"


@pytest.mark.parametrize(("identifier", "architecture", "message"), [
    ("unknown", "amd64", "Unknown"),
    ("kodi", "riscv64", "not available"),
    ("../kodi", "amd64", "Invalid application identifier"),
    ("--help", "amd64", "Invalid application identifier"),
])
def test_plans_reject_unknown_requests(identifier: str, architecture: str, message: str) -> None:
    """Requests only name catalog applications available for this device."""
    with pytest.raises(ValueError, match=message):
        plans(architecture).install(identifier)


@pytest.mark.parametrize("spec", [
    "{method: apt, packages: [--allow-remove-essential]}",
    "{method: apt, packages: [ok], dependencies: ['a;b']}",
    "{method: flatpak, flatpak: --system}",
    "{method: deb, packages: [x], url: http://example.org/x.deb, sha256: " + "0" * 64 + "}",
    "{method: deb, packages: [x], url: https://example.org/x.deb, sha256: abc}",
    "{method: deb, packages: [x], url: https://example.org/x.deb, sha256: " + "0" * 64 + "}",
    "{method: deb, packages: [x], url: https://nomachine.com.example.org/x.deb, sha256: " + "0" * 64 + "}",
    "{method: shell, packages: [x]}",
])
def test_plans_validate_catalog_values(tmp_path: Path, spec: str) -> None:
    """Even the root-owned catalog cannot inject options or insecure downloads."""
    catalog = tmp_path / "applications.yaml"
    catalog.write_text(f"applications:\n  - id: app\n    install:\n      amd64: {spec}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        plans(catalog=catalog).install("app")


def test_plans_ignore_keys_added_by_later_releases(tmp_path: Path) -> None:
    """The image-level service keeps working with future catalog schemas."""
    catalog = tmp_path / "applications.yaml"
    catalog.write_text(
        "applications:\n  - id: app\n    future: true\n    install:\n"
        "      amd64: {method: apt, packages: [app], future: 1}\n",
        encoding="utf-8",
    )
    assert plans(catalog=catalog).install("app")[-1].command[-1] == "app"


def test_download_rejects_redirects_to_untrusted_hosts(tmp_path: Path) -> None:
    """Root never keeps a package served from outside the allowed vendor hosts."""
    source = tmp_path / "package.deb"
    source.write_bytes(b"package")
    target = tmp_path / "downloads/player.deb"
    with pytest.raises(OperationFailed, match="untrusted host"):
        privileged_apps._download(source.as_uri(), hashlib.sha256(b"package").hexdigest(), target, Progress())
    assert not target.exists()
    assert privileged_apps._trusted_download("https://web9001.nomachine.com/download/x.deb")
    assert not privileged_apps._trusted_download("http://download.nomachine.com/x.deb")


def test_removal_check_refuses_collateral_removals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing an app must not take packages outside the app's own list with it."""
    output = "Remv steamlink [1.0.16]\nRemv python3:arm64 [3.13]\n"

    def run(command, **_kwargs):
        assert command[:3] == ["/usr/bin/apt-get", "-s", "remove"]
        return type("Result", (), {"returncode": 0, "stdout": output})()

    monkeypatch.setattr(privileged_apps.subprocess, "run", run)
    with pytest.raises(OperationFailed, match="python3"):
        privileged_apps._check_removal(("steamlink",))
    output = "Remv steamlink [1.0.16]\n"
    privileged_apps._check_removal(("steamlink",))


def test_download_verifies_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a package matching the catalog checksum is kept."""
    monkeypatch.setattr(privileged_apps, "_trusted_download", lambda url: True)
    source = tmp_path / "package.deb"
    source.write_bytes(b"package")
    target = tmp_path / "downloads/player.deb"
    digest = hashlib.sha256(b"package").hexdigest()
    with pytest.raises(OperationFailed, match="checksum"):
        privileged_apps._download(source.as_uri(), "0" * 64, target, Progress())
    assert not target.exists() and not list(target.parent.iterdir())
    privileged_apps._download(source.as_uri(), digest, target, Progress())
    assert target.read_bytes() == b"package"


class StaticPlans:
    """Return prepared steps instead of reading a catalog."""

    def __init__(self, steps: list[Step]) -> None:
        self.steps = steps

    def install(self, identifier: str) -> list[Step]:
        return self.steps

    remove = install


def wait_for(operations: ApplicationOperations, token: str) -> dict:
    """Poll until an operation finishes."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = operations.status(token)
        if status["state"] != "running":
            return status
        time.sleep(0.02)
    raise AssertionError("operation did not finish")


def test_operations_run_steps_and_report_failures() -> None:
    """Steps run in order; a failing command reports its output tail."""
    guard = threading.Lock()
    seen: list[int] = []
    operations = ApplicationOperations(StaticPlans([
        Step("preparing", action=lambda progress: seen.append(progress.step)),
        Step("installing", ("/bin/sh", "-c", "echo broken; exit 3")),
    ]), guard)
    status = wait_for(operations, operations.start("install", "app"))
    assert seen == [1]
    assert status["state"] == "failed" and "broken" in status["error"]
    assert not guard.locked()
    with pytest.raises(ValueError, match="Unknown"):
        operations.status("missing")


def test_operations_cancel_only_cancellable_steps() -> None:
    """Downloads stop on request; package database changes cannot be interrupted."""
    guard = threading.Lock()
    operations = ApplicationOperations(StaticPlans([Step("downloading", ("/bin/sleep", "30"), cancellable=True)]), guard)
    token = operations.start("install", "app")
    time.sleep(0.2)
    operations.cancel(token)
    assert wait_for(operations, token)["state"] == "cancelled"
    blocked = ApplicationOperations(StaticPlans([Step("installing", ("/bin/sleep", "0.5"))]), guard)
    token = blocked.start("install", "app")
    time.sleep(0.1)
    with pytest.raises(PermissionError):
        blocked.cancel(token)
    assert wait_for(blocked, token)["state"] == "succeeded"


def test_operations_share_the_package_manager_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """An app install and a package upgrade never run at the same time."""
    guard = threading.Lock()
    operations = ApplicationOperations(StaticPlans([Step("installing", ("/bin/sleep", "0.5"))]), guard)
    token = operations.start("install", "app")
    with pytest.raises(RuntimeError, match="already running"):
        operations.start("install", "app")
    monkeypatch.setattr(
        "pideck.infrastructure.image_update_daemon.subprocess.run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": "installed"})(),
    )
    with pytest.raises(RuntimeError, match="already running"):
        PackageOperations(guard).start("apt", "kodi")
    wait_for(operations, token)


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run an isolated broker that plans against the bundled catalog."""
    root = tmp_path / "image"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/1.0.0")
    path = tmp_path / "service.sock"
    broker = UpdateServer(path, ImageUpdateStore(root), allowed_uid=os.getuid(), plans=plans("riscv64"))
    monkeypatch.setattr(image_update_client, "SOCKET_PATH", path)
    thread = threading.Thread(target=broker.serve_forever)
    thread.start()
    yield broker
    broker.shutdown()
    broker.server_close()
    thread.join()


def test_broker_advertises_and_validates_application_requests(server: UpdateServer) -> None:
    """New clients detect support; requests cannot carry package names or commands."""
    assert image_update_client.request("capabilities") == {"applications": 1, "shares": 1}
    with pytest.raises(image_update_client.ImageUpdateError, match="not available"):
        image_update_client.request("app_install", id="kodi")
    with pytest.raises(image_update_client.ImageUpdateError, match="Unsupported"):
        image_update_client.request("app_install", id="kodi", package="bash")
    with pytest.raises(image_update_client.ImageUpdateError, match="Unknown"):
        image_update_client.request("app_status", token="missing")
