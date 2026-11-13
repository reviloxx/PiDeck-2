"""Tests for staging and recovery of image release updates."""

from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import os
import threading
import sys
import tomllib

import pytest
from urllib.error import HTTPError

from image.update_bundle import create_bundle
from pideck.infrastructure import image_update_store
from pideck.infrastructure import image_update_client
from pideck.infrastructure.image_update_daemon import UpdateServer
from pideck.infrastructure.image_update_store import ImageUpdateStore
from pideck.infrastructure.image_update_bundle import InvalidUpdate


@pytest.fixture
def release_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ImageUpdateStore:
    """Create an isolated image layout and locally built release."""
    project = tmp_path / "project"
    (project / "src/pideck").mkdir(parents=True)
    (project / "assets/host/etc/modprobe.d").mkdir(parents=True)
    (project / "assets/host/etc/udev/rules.d").mkdir(parents=True)
    (project / "assets/host/usr/lib/pideck").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n')
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.1.0"\n')
    (project / "src/pideck/bootstrap.py").write_text('"""Entry point."""\n')
    (project / "assets/example.svg").write_text("<svg/>\n")
    (
        project / "assets/host/etc/modprobe.d/pideck-blacklist-hid-nintendo.conf"
    ).write_text("blacklist hid-nintendo\n")
    (
        project / "assets/host/etc/udev/rules.d/99-pideck-switch-pro-hidraw.rules"
    ).write_text(
        'KERNEL=="hidraw*", RUN+="/usr/lib/pideck/pideck-switch-pro-led /dev/%k 1"\n'
    )
    led = project / "assets/host/usr/lib/pideck/pideck-switch-pro-led"
    led.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    led.chmod(0o755)
    release = tmp_path / "release"
    create_bundle(project, release, "v1.1.0")
    monkeypatch.setattr(image_update_store, "_download", lambda version, name, target: shutil.copyfile(release / name, target))
    root = tmp_path / "image"
    host_root = tmp_path / "host"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/1.0.0")
    return ImageUpdateStore(root, host_root=host_root)


def test_staging_activation_and_readiness(
    release_store: ImageUpdateStore, tmp_path: Path,
) -> None:
    """Staging leaves the running version intact until activation and readiness."""
    release_store.stage("1.1.0")
    assert release_store.status() == {"phase": "pending", "version": "1.1.0", "current_version": "1.0.0"}
    release_store.activate_at_startup()
    assert release_store.status()["phase"] == "activating"
    assert release_store.status()["current_version"] == "1.1.0"
    assert (release_store.root / "current/python/pideck/bootstrap.py").is_file()
    blacklist = (
        tmp_path / "host/etc/modprobe.d/pideck-blacklist-hid-nintendo.conf"
    )
    assert blacklist.is_file()
    assert "blacklist hid-nintendo" in blacklist.read_text()
    assert (
        tmp_path / "host/etc/udev/rules.d/99-pideck-switch-pro-hidraw.rules"
    ).is_file()
    led_helper = tmp_path / "host/usr/lib/pideck/pideck-switch-pro-led"
    assert led_helper.is_file()
    assert led_helper.stat().st_mode & 0o111
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
    """Activation rechecks hashes before switching versions."""
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


def test_download_retries_transient_gateway_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single CDN 504 does not fail a release asset download."""
    target = tmp_path / "pideck-update.json"
    payload = b'{"format":1}'
    attempts = {"count": 0}

    class Response:
        def __init__(self) -> None:
            self._remaining = payload

        def read(self, size: int) -> bytes:
            data, self._remaining = self._remaining[:size], self._remaining[size:]
            return data

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Opener:
        def open(self, _request, timeout=30):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise HTTPError(
                    "https://github.com/reviloxx/PiDeck-2/releases/download/v1.1.0/pideck-update.json",
                    504, "Gateway Timeout", EmailMessage(), BytesIO(),
                )
            return Response()

    monkeypatch.setattr(image_update_store, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr("pideck.infrastructure.http_retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr("pideck.infrastructure.http_retry.random.uniform", lambda _a, _b: 0.0)
    image_update_store._download("1.1.0", "pideck-update.json", target)
    assert target.read_bytes() == payload
    assert attempts["count"] == 2


def test_socket_staging_activation_and_confirmation(
    release_store: ImageUpdateStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise staging and next-start activation through the real socket protocol."""
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
    """Run the actual application and bundled assets from the activated tree."""
    project = Path(__file__).resolve().parents[1]
    with (project / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    release = tmp_path / "bundle"
    create_bundle(project, release, f"v{version}")
    monkeypatch.setattr(image_update_store, "_download", lambda _version, name, target: shutil.copyfile(release / name, target))
    root = tmp_path / "image"
    host_root = tmp_path / "host"
    (root / "releases/0.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/0.0.0")
    store = ImageUpdateStore(root, host_root=host_root)
    store.stage(version)
    store.activate_at_startup()
    assert (
        host_root / "etc/modprobe.d/pideck-blacklist-hid-nintendo.conf"
    ).is_file()
    script = """
from PySide6 import QtWidgets
from PySide6.QtCore import QTimer
from pideck import bootstrap
class NoGamepad:
    presence_changed = None
    device_lost = None
    def start(self, observer): pass
    def close(self): pass
    def connected_gamepad_count(self): return 0
    def connected_keyboard_count(self): return 0
    def set_exclusive(self, exclusive): pass
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
