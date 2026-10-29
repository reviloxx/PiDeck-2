"""Tests for staging and recovery of image release updates."""

from pathlib import Path
import shutil
import subprocess
import os
import threading
import sys
import tomllib

import pytest

from image.update_bundle import create_bundle
from pideck.infrastructure import image_update_store
from pideck.infrastructure import image_update_client
from pideck.infrastructure.image_update_daemon import UpdateServer
from pideck.infrastructure.image_update_store import ImageUpdateStore
from pideck.infrastructure.signed_update import InvalidUpdate


@pytest.fixture
def release_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ImageUpdateStore:
    """Create an isolated image layout and locally signed release."""
    project = tmp_path / "project"
    (project / "src/pideck").mkdir(parents=True)
    (project / "assets").mkdir()
    (project / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n')
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.1.0"\n')
    (project / "src/pideck/bootstrap.py").write_text('"""Entry point."""\n')
    (project / "assets/example.svg").write_text("<svg/>\n")
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    release = tmp_path / "release"
    create_bundle(project, release, "v1.1.0", private)
    monkeypatch.setattr(image_update_store, "_download", lambda version, name, target: shutil.copyfile(release / name, target))
    root = tmp_path / "image"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/1.0.0")
    return ImageUpdateStore(root, public)


def test_staging_activation_and_readiness(release_store: ImageUpdateStore) -> None:
    """Staging leaves the running version intact until activation and readiness."""
    release_store.stage("1.1.0")
    assert release_store.status() == {"phase": "pending", "version": "1.1.0", "current_version": "1.0.0"}
    release_store.activate_at_startup()
    assert release_store.status()["phase"] == "activating"
    assert release_store.status()["current_version"] == "1.1.0"
    assert (release_store.root / "current/python/pideck/bootstrap.py").is_file()
    release_store.mark_ready("1.1.0")
    assert release_store.status() == {"phase": "idle", "current_version": "1.1.0"}
    release_store.activate_at_startup()
    assert release_store.status()["current_version"] == "1.1.0"


def test_failed_first_start_rolls_back(release_store: ImageUpdateStore) -> None:
    """An unconfirmed activation is rolled back on the next startup."""
    release_store.stage("1.1.0")
    release_store.activate_at_startup()
    release_store.activate_at_startup()
    state = release_store.status()
    assert state["current_version"] == "1.0.0"
    assert state["phase"] == "failed"
    assert "rolled back" in state["message"]


@pytest.mark.parametrize("after_switch", [False, True])
def test_interrupted_activation_recovers(
    release_store: ImageUpdateStore, monkeypatch: pytest.MonkeyPatch, after_switch: bool
) -> None:
    """Interruption on either side of the pointer swap restores the previous release."""
    release_store.stage("1.1.0")
    original = release_store._switch

    def interrupt(version: str) -> None:
        if after_switch:
            original(version)
        raise SystemExit("simulated power loss")

    monkeypatch.setattr(release_store, "_switch", interrupt)
    with pytest.raises(SystemExit):
        release_store.activate_at_startup()
    monkeypatch.setattr(release_store, "_switch", original)
    release_store.activate_at_startup()
    assert release_store.status()["current_version"] == "1.0.0"
    assert release_store.status()["phase"] == "failed"


def test_tampering_after_staging_prevents_activation(release_store: ImageUpdateStore) -> None:
    """Activation rechecks signatures and hashes before switching versions."""
    release_store.stage("1.1.0")
    bundle = release_store.root / "staged/pideck-update.tar"
    bundle.write_bytes(bundle.read_bytes() + b"bad")
    release_store.activate_at_startup()
    state = release_store.status()
    assert state["current_version"] == "1.0.0"
    assert state["phase"] == "failed"


def test_rejects_repeated_and_older_staging(release_store: ImageUpdateStore) -> None:
    """Prevent concurrent pending operations and downgrade attempts."""
    with pytest.raises(InvalidUpdate, match="not newer"):
        release_store.stage("1.0.0")
    release_store.stage("1.1.0")
    with pytest.raises(InvalidUpdate, match="already pending"):
        release_store.stage("1.1.0")


def test_socket_staging_activation_and_confirmation(
    release_store: ImageUpdateStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise signed staging and next-start activation through the real socket protocol."""
    path = tmp_path / "service.sock"
    server = UpdateServer(path, release_store, allowed_uid=os.getuid())
    monkeypatch.setattr(image_update_client, "SOCKET_PATH", path)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        staged = image_update_client.request("stage", version="1.1.0")
        assert staged["phase"] == "pending"
        release_store.activate_at_startup()
        active = image_update_client.request("ready", version="1.1.0")
        assert active == {"phase": "idle", "current_version": "1.1.0"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_bundled_application_starts_after_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the actual signed application and bundled assets from the activated tree."""
    project = Path(__file__).resolve().parents[1]
    with (project / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    release = tmp_path / "bundle"
    create_bundle(project, release, f"v{version}", private)
    monkeypatch.setattr(image_update_store, "_download", lambda _version, name, target: shutil.copyfile(release / name, target))
    root = tmp_path / "image"
    (root / "releases/0.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/0.0.0")
    store = ImageUpdateStore(root, public)
    store.stage(version)
    store.activate_at_startup()
    script = """
from PySide6 import QtWidgets
from PySide6.QtCore import QTimer
from pideck import bootstrap
class NoGamepad:
    def start(self, observer): pass
    def close(self): pass
bootstrap.EvdevGamepadAdapter = NoGamepad
app = QtWidgets.QApplication([])
QtWidgets.QApplication = lambda _: app
QTimer.singleShot(200, app.quit)
raise SystemExit(bootstrap.main([
    '--config', CONFIG, '--asset-root', ASSET_ROOT,
]))
"""
    config = project / "image/stage-pideck/00-pideck/files/pideck.yaml"
    script = f"CONFIG = {str(config)!r}\nASSET_ROOT = {str(root / 'current/config')!r}\n" + script
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root / "current/python"), "QT_QPA_PLATFORM": "offscreen"},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
