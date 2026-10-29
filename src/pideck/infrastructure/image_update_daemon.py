"""Restricted root service for image updates and installed-package upgrades."""

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import struct
import subprocess
import threading
import time
from uuid import uuid4

from pideck.infrastructure.image_update_store import ImageUpdateStore


_LOGGER = logging.getLogger(__name__)
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?")
_FLATPAK_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.[A-Za-z0-9_.-]+")
_SOCKET = Path("/run/pideck-updater/service.sock")
_ROOT = Path("/usr/lib/pideck")
_KEY = Path("/usr/share/pideck/update-public.pem")
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}


@dataclass(slots=True)
class _Operation:
    process: subprocess.Popen[bytes]
    cancelled: bool = False
    finished_at: float | None = None


class PackageOperations:
    """Run only noninteractive upgrades of already-installed packages."""

    def __init__(self) -> None:
        """Create the process registry for one daemon instance."""
        self._lock = threading.Lock()
        self._operations: dict[str, _Operation] = {}

    def start(self, kind: str, name: str) -> str:
        """Start a validated apt or system Flatpak upgrade and return its token."""
        if kind == "apt":
            if _PACKAGE.fullmatch(name) is None:
                raise ValueError("Invalid apt package name")
            check = ["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}", "--", name]
            command = ["/usr/bin/apt-get", "install", "--only-upgrade", "--no-remove", "-y", "--", name]
            expected = "installed"
        elif kind == "flatpak":
            if _FLATPAK_ID.fullmatch(name) is None:
                raise ValueError("Invalid Flatpak application ID")
            check = ["/usr/bin/flatpak", "info", "--system", "--show-ref", name]
            command = ["/usr/bin/flatpak", "update", "--system", "--noninteractive", "--assumeyes", name]
            expected = None
        else:
            raise ValueError("Unsupported package manager")
        installed = subprocess.run(check, capture_output=True, text=True, env=_ENV, timeout=30, check=False)
        if installed.returncode != 0 or (expected is not None and installed.stdout != expected):
            raise ValueError("Package is not installed")
        with self._lock:
            now = time.monotonic()
            for token, operation in tuple(self._operations.items()):
                if operation.finished_at is not None and now - operation.finished_at > 3600:
                    del self._operations[token]
            if len(self._operations) >= 128:
                raise RuntimeError("Too many uncollected package update results")
            if any(operation.process.poll() is None for operation in self._operations.values()):
                raise RuntimeError("Another package update is already running")
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=subprocess.STDOUT,
                env=_ENV,
                start_new_session=True,
            )
            token = uuid4().hex
            self._operations[token] = _Operation(process)
            threading.Thread(target=self._record_completion, args=(token, process), daemon=True).start()
            return token

    def _record_completion(self, token: str, process: subprocess.Popen[bytes]) -> None:
        process.wait()
        with self._lock:
            operation = self._operations.get(token)
            if operation is not None:
                operation.finished_at = time.monotonic()

    def status(self, token: str) -> dict[str, object]:
        """Return and remove a completed operation, or report it is running."""
        with self._lock:
            operation = self._operations.get(token)
            if operation is None:
                raise ValueError("Unknown update operation")
            code = operation.process.poll()
            if code is None:
                return {"state": "running"}
            del self._operations[token]
            if operation.cancelled:
                return {"state": "cancelled", "returncode": code}
            return {"state": "succeeded" if code == 0 else "failed", "returncode": code}

    def cancel(self, token: str) -> None:
        """Terminate a known update process group if still running."""
        with self._lock:
            operation = self._operations.get(token)
            if operation is None:
                raise ValueError("Unknown update operation")
            if operation.process.poll() is None:
                operation.cancelled = True
                os.killpg(operation.process.pid, signal.SIGTERM)


class _RequestHandler(socketserver.StreamRequestHandler):
    """Authenticate the local peer and execute a single constrained request."""

    def handle(self) -> None:
        """Process one newline-delimited JSON request."""
        server = self.server
        assert isinstance(server, UpdateServer)
        _, uid, _ = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != server.allowed_uid:
            _LOGGER.warning("Rejected updater request from uid=%d", uid)
            return
        self.connection.settimeout(30)
        try:
            line = self.rfile.readline(4097)
            if len(line) > 4096 or not line.endswith(b"\n"):
                raise ValueError("Invalid request length")
            request = json.loads(line)
            result = server.dispatch(request)
            response = {"ok": True, "result": result}
        except Exception as error:
            _LOGGER.exception("Updater request failed")
            response = {"ok": False, "error": str(error)}
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class UpdateServer(socketserver.ThreadingUnixStreamServer):
    """Serve a limited updater protocol to the provisioned appliance user."""

    daemon_threads = True

    def __init__(self, path: Path, store: ImageUpdateStore, allowed_uid: int = 1000) -> None:
        """Bind the Unix socket and initialize operations."""
        self.store = store
        self.allowed_uid = allowed_uid
        self.packages = PackageOperations()
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(str(path), _RequestHandler)
        path.chmod(0o666)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        """Bound concurrent broker connections, including slow clients."""
        if not self._slots.acquire(blocking=False):
            _LOGGER.warning("Updater connection limit reached")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: object) -> None:
        """Release the connection slot after request processing completes."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def dispatch(self, request: object) -> object:
        """Validate one protocol message and dispatch only fixed operations."""
        if not isinstance(request, dict) or not isinstance(request.get("action"), str):
            raise ValueError("Invalid updater request")
        action = request["action"]
        keys = {
            "status": {"action"},
            "stage": {"action", "version"},
            "ready": {"action", "version"},
            "package_start": {"action", "kind", "name"},
            "package_status": {"action", "token"},
            "package_cancel": {"action", "token"},
        }
        if action not in keys or set(request) != keys[action]:
            raise ValueError("Unsupported updater request")
        if any(not isinstance(value, str) or len(value) > 200 for value in request.values()):
            raise ValueError("Invalid updater request fields")
        if action == "status":
            if not (self.store.root / "current").is_symlink():
                return {"phase": "unsupported"}
            return self.store.status()
        if action == "stage":
            self.store.stage(request["version"])
            return self.store.status()
        if action == "ready":
            self.store.mark_ready(request["version"])
            return self.store.status()
        if action == "package_start":
            return {"token": self.packages.start(request["kind"], request["name"])}
        if action == "package_status":
            return self.packages.status(request["token"])
        self.packages.cancel(request["token"])
        return {}


def main() -> None:
    """Run the broker or activate a pending release before the appliance session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "activate"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = ImageUpdateStore(_ROOT, _KEY)
    if args.command == "activate":
        if (_ROOT / "current").is_symlink():
            store.activate_at_startup()
        return
    _SOCKET.parent.mkdir(parents=True, exist_ok=True)
    _SOCKET.unlink(missing_ok=True)
    with UpdateServer(_SOCKET, store) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
