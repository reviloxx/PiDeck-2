"""Tests for the image updater's local privilege boundary."""

import os
from pathlib import Path
import socket
import threading

import pytest

from pideck.infrastructure import image_update_client, image_update_daemon
from pideck.infrastructure.image_update_daemon import PackageOperations, UpdateServer
from pideck.infrastructure.image_update_store import ImageUpdateStore


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run an isolated broker socket for the current test user."""
    root = tmp_path / "image"
    (root / "releases/1.0.0").mkdir(parents=True)
    (root / "current").symlink_to("releases/1.0.0")
    path = tmp_path / "service.sock"
    broker = UpdateServer(path, ImageUpdateStore(root, tmp_path / "public.pem"), allowed_uid=os.getuid())
    monkeypatch.setattr(image_update_client, "SOCKET_PATH", path)
    thread = threading.Thread(target=broker.serve_forever)
    thread.start()
    yield broker
    broker.shutdown()
    broker.server_close()
    thread.join()


def test_authenticated_local_status(server: UpdateServer) -> None:
    """The allowed UID can obtain the current image state through the socket."""
    assert image_update_client.request("status") == {"phase": "idle", "current_version": "1.0.0"}


def test_rejects_unknown_commands_and_extra_fields(server: UpdateServer) -> None:
    """The client cannot send paths, extra arguments or arbitrary commands."""
    with pytest.raises(image_update_client.ImageUpdateError, match="Unsupported"):
        image_update_client.request("shell", command="id")
    with pytest.raises(image_update_client.ImageUpdateError, match="Unsupported"):
        image_update_client.request("status", path="/etc/passwd")


def test_other_uids_cannot_use_service(server: UpdateServer) -> None:
    """Socket filesystem access alone does not grant protocol access."""
    server.allowed_uid = os.getuid() + 1
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(str(image_update_client.SOCKET_PATH))
        connection.sendall(b'{"action":"status"}\n')
        try:
            response = connection.recv(1024)
        except ConnectionResetError:
            response = b""
    assert response == b""


@pytest.mark.parametrize("kind,name", [
    ("apt", "--allow-remove-essential"),
    ("apt", "valid;id"),
    ("apt", "../etc/passwd"),
    ("flatpak", "--system"),
    ("flatpak", "org.example.App;id"),
    ("shell", "id"),
])
def test_package_requests_cannot_supply_commands(kind: str, name: str) -> None:
    """The broker rejects command syntax before invoking package managers."""
    with pytest.raises(ValueError):
        PackageOperations().start(kind, name)


def test_apt_requires_an_installed_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing package cannot be installed using the upgrade operation."""
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "not-installed"})()

    monkeypatch.setattr(image_update_daemon.subprocess, "run", run)
    with pytest.raises(ValueError, match="not installed"):
        PackageOperations().start("apt", "example")
    assert commands == [["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}", "--", "example"]]


def test_apt_upgrade_uses_fixed_noninteractive_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorized apt updates cannot install a requested missing package or pass options."""
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "installed"})()

    class Process:
        pid = 12345

        def poll(self):
            return None

        def wait(self):
            return 0

    def popen(command, **kwargs):
        commands.append(command)
        assert kwargs["env"]["DEBIAN_FRONTEND"] == "noninteractive"
        assert kwargs["start_new_session"] is True
        return Process()

    monkeypatch.setattr(image_update_daemon.subprocess, "run", run)
    monkeypatch.setattr(image_update_daemon.subprocess, "Popen", popen)
    token = PackageOperations().start("apt", "firefox-esr")
    assert token
    assert commands[1] == [
        "/usr/bin/apt-get", "install", "--only-upgrade", "--no-remove", "-y", "--", "firefox-esr"
    ]
