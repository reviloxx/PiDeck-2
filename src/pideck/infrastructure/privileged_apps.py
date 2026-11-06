"""Root-side install and removal of catalog applications for the update broker.

The broker accepts only a catalog application identifier. Package names,
Flatpak IDs and download URLs are read from the root-owned catalog of the
active PiDeck release, never from the request. This module is installed with
the image, so it ignores catalog keys it does not use; that keeps it working
with catalogs shipped by later application releases.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
from _thread import LockType
import time
from typing import Any
import urllib.parse
import urllib.request
from uuid import uuid4

import yaml

from pideck.infrastructure.flatpak_progress import FlatpakProgressParser

_LOGGER = logging.getLogger(__name__)
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*")
_FLATPAK_ID = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+)+")
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_URL = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/%+-]+\.deb")
# Release bundles are not publisher-authenticated, so the image itself limits
# where root may download vendor packages from, including after redirects.
DEB_HOSTS = ("nomachine.com",)
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
_APT = "/usr/bin/apt-get"
_FLATPAK = "/usr/bin/flatpak"
_FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
_APT_INSTALL = (_APT, "install", "-y", "--no-install-recommends")
CATALOG_PATHS = (
    Path("/usr/lib/pideck/current/assets/catalog/applications.yaml"),
    Path("/usr/share/pideck/assets/catalog/applications.yaml"),
)
DOWNLOAD_DIR = Path("/var/cache/pideck/downloads")


class OperationFailed(Exception):
    """A plan step failed; the message is reported to the client."""


@dataclass(frozen=True, slots=True)
class Step:
    """One plan step: a fixed command or an in-process action.

    Steps that change the package database are not cancellable because
    interrupting dpkg can leave the system in an inconsistent state.
    """

    stage: str
    command: tuple[str, ...] | None = None
    action: Callable[["Progress"], None] | None = None
    cancellable: bool = False
    flatpak_progress: bool = False


@dataclass(slots=True)
class Progress:
    """Mutable progress shared between a running plan and status requests."""

    step: int = 0
    steps: int = 0
    stage: str = "preparing"
    percent: int | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


def debian_architecture() -> str:
    """Return the running system's Debian architecture name."""
    result = subprocess.run(
        ["/usr/bin/dpkg", "--print-architecture"], capture_output=True, text=True, env=_ENV, timeout=10, check=True,
    )
    return result.stdout.strip()


class ApplicationPlans:
    """Translate a catalog identifier into a fixed install or removal plan."""

    def __init__(
        self, catalog_paths: Sequence[Path] = CATALOG_PATHS,
        architecture: Callable[[], str] = debian_architecture,
        download_dir: Path = DOWNLOAD_DIR,
    ) -> None:
        """Use the first existing root-owned catalog."""
        self._catalog_paths = tuple(catalog_paths)
        self._architecture = architecture
        self._download_dir = download_dir

    def install(self, identifier: str) -> list[Step]:
        """Return the steps that install an application."""
        method, spec = self._spec(identifier)
        if method == "flatpak":
            return [
                Step("preparing", (_FLATPAK, "remote-add", "--system", "--if-not-exists", "flathub", _FLATHUB)),
                Step("downloading", (_FLATPAK, "install", "--system", "--noninteractive", "--assumeyes",
                                     "flathub", spec["flatpak"]), cancellable=True, flatpak_progress=True),
            ]
        if method == "apt":
            packages = (*spec["packages"], *spec["dependencies"])
            return [
                Step("preparing", (_APT, "update"), cancellable=True),
                Step("downloading", (*_APT_INSTALL, "--download-only", "--", *packages), cancellable=True),
                Step("installing", (*_APT_INSTALL, "--", *packages)),
            ]
        package = spec["packages"][0]
        target = self._download_dir / f"{package}.deb"
        return [
            Step("preparing", (_APT, "update"), cancellable=True),
            Step("downloading", action=lambda progress: _download(spec["url"], spec["sha256"], target, progress),
                 cancellable=True),
            Step("installing", (*_APT_INSTALL, "--", str(target))),
            Step("finishing", action=lambda _progress: target.unlink(missing_ok=True)),
        ]

    def remove(self, identifier: str) -> list[Step]:
        """Return the steps that remove an application but keep user data."""
        method, spec = self._spec(identifier)
        if method == "flatpak":
            return [
                Step("removing", (_FLATPAK, "uninstall", "--system", "--noninteractive", "--assumeyes",
                                  spec["flatpak"])),
                Step("finishing", (_FLATPAK, "uninstall", "--system", "--unused", "--noninteractive", "--assumeyes")),
            ]
        packages = tuple(spec["packages"])
        return [
            Step("preparing", action=lambda _progress: _check_removal(packages)),
            Step("removing", (_APT, "remove", "-y", "--autoremove", "--", *packages)),
        ]

    def _spec(self, identifier: str) -> tuple[str, dict[str, Any]]:
        """Find and strictly validate the current architecture's instructions."""
        if _IDENTIFIER.fullmatch(identifier) is None:
            raise ValueError("Invalid application identifier")
        catalog = self._load()
        entry = next(
            (item for item in catalog if isinstance(item, dict) and item.get("id") == identifier), None,
        )
        if entry is None:
            raise ValueError("Unknown application")
        install = entry.get("install")
        spec = install.get(self._architecture()) if isinstance(install, dict) else None
        if not isinstance(spec, dict):
            raise ValueError("Application is not available for this device")
        method = spec.get("method")
        packages = _string_list(spec.get("packages", []))
        dependencies = _string_list(spec.get("dependencies", []))
        if not all(_PACKAGE.fullmatch(name) for name in (*packages, *dependencies)):
            raise ValueError("Invalid package name in catalog")
        if method == "flatpak":
            flatpak = spec.get("flatpak")
            if not isinstance(flatpak, str) or _FLATPAK_ID.fullmatch(flatpak) is None:
                raise ValueError("Invalid Flatpak ID in catalog")
            return method, {"flatpak": flatpak}
        if method == "apt" and packages:
            return method, {"packages": packages, "dependencies": dependencies}
        if method == "deb" and len(packages) == 1:
            url, sha256 = spec.get("url"), spec.get("sha256")
            if not isinstance(url, str) or _URL.fullmatch(url) is None or not _trusted_download(url):
                raise ValueError("Invalid download URL in catalog")
            if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
                raise ValueError("Invalid SHA-256 in catalog")
            return method, {"packages": packages, "url": url, "sha256": sha256}
        raise ValueError("Unsupported install method in catalog")

    def _load(self) -> list[Any]:
        for path in self._catalog_paths:
            if path.is_file():
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("applications"), list):
                    raise ValueError("Invalid application catalog")
                return data["applications"]
        raise ValueError("Application catalog is missing")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Invalid package list in catalog")
    return list(value)


def _check_removal(packages: tuple[str, ...]) -> None:
    """Refuse removals that would take other packages with them."""
    result = subprocess.run(
        [_APT, "-s", "remove", "--", *packages], capture_output=True, text=True, env=_ENV, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise OperationFailed(result.stdout.strip()[-1000:] or "Unable to plan the removal")
    removed = {line.split()[1].split(":")[0] for line in result.stdout.splitlines() if line.startswith("Remv ")}
    extra = sorted(removed.difference(packages))
    if extra:
        raise OperationFailed(f"Removing would also remove: {', '.join(extra)}")


def _trusted_download(url: str) -> bool:
    """Return whether a URL uses HTTPS on one of the image's allowed vendor hosts."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == item or host.endswith("." + item) for item in DEB_HOSTS)


def _download(url: str, sha256: str, target: Path, progress: Progress) -> None:
    """Download a package over HTTPS into a root-only directory and verify it."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
            if not _trusted_download(response.geturl()):
                raise OperationFailed("The package download was redirected to an untrusted host")
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while chunk := response.read(1 << 16):
                if progress.cancelled.is_set():
                    raise OperationFailed("cancelled")
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if total:
                    progress.percent = min(99, received * 100 // total)
        if digest.hexdigest() != sha256:
            raise OperationFailed("Downloaded package checksum does not match the catalog")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(slots=True)
class _Operation:
    progress: Progress
    process: subprocess.Popen[str] | None = None
    cancellable: bool = True
    state: str = "running"
    error: str | None = None
    finished_at: float | None = None


class ApplicationOperations:
    """Run one install or removal plan at a time and report its progress."""

    def __init__(self, plans: ApplicationPlans, guard: LockType) -> None:
        """Share ``guard`` with other package-manager users to avoid dpkg lock conflicts."""
        self._plans = plans
        self._guard = guard
        self._lock = threading.Lock()
        self._operations: dict[str, _Operation] = {}

    def start(self, kind: str, identifier: str) -> str:
        """Validate the request, build its plan and start it in the background."""
        steps = self._plans.install(identifier) if kind == "install" else self._plans.remove(identifier)
        with self._lock:
            now = time.monotonic()
            for token, operation in tuple(self._operations.items()):
                if operation.finished_at is not None and now - operation.finished_at > 3600:
                    del self._operations[token]
            if len(self._operations) >= 32:
                raise RuntimeError("Too many uncollected application operations")
            if not self._guard.acquire(blocking=False):
                raise RuntimeError("Another package operation is already running")
            token = uuid4().hex
            operation = _Operation(Progress(steps=len(steps)))
            self._operations[token] = operation
        _LOGGER.info("Application %s started id=%s", kind, identifier)
        threading.Thread(target=self._run, args=(operation, steps), daemon=True).start()
        return token

    def status(self, token: str) -> dict[str, object]:
        """Report progress, and forget an operation once its result is collected."""
        with self._lock:
            operation = self._operations.get(token)
            if operation is None:
                raise ValueError("Unknown application operation")
            progress = operation.progress
            result: dict[str, object] = {
                "state": operation.state, "step": progress.step, "steps": progress.steps,
                "stage": progress.stage, "percent": progress.percent,
            }
            if operation.error is not None:
                result["error"] = operation.error
            if operation.state != "running":
                del self._operations[token]
            return result

    def cancel(self, token: str) -> None:
        """Stop a cancellable step; package-database changes always run to completion."""
        with self._lock:
            operation = self._operations.get(token)
            if operation is None:
                raise ValueError("Unknown application operation")
            if operation.state != "running":
                return
            if not operation.cancellable:
                raise PermissionError("The system is changing packages and cannot stop now")
            operation.progress.cancelled.set()
            if operation.process is not None and operation.process.poll() is None:
                os.killpg(operation.process.pid, signal.SIGTERM)

    def _run(self, operation: _Operation, steps: list[Step]) -> None:
        state, error = "succeeded", None
        try:
            for index, step in enumerate(steps, start=1):
                with self._lock:
                    if operation.progress.cancelled.is_set():
                        state = "cancelled"
                        break
                    operation.cancellable = step.cancellable
                    operation.progress.step = index
                    operation.progress.stage = step.stage
                    operation.progress.percent = None
                self._run_step(operation, step)
                if operation.progress.cancelled.is_set():
                    state = "cancelled"
                    break
        except OperationFailed as failure:
            state, error = ("cancelled", None) if operation.progress.cancelled.is_set() else ("failed", str(failure))
        except Exception as failure:  # noqa: BLE001 - reported to the client instead of crashing the broker
            _LOGGER.exception("Application operation failed")
            state, error = "failed", str(failure) or type(failure).__name__
        finally:
            with self._lock:
                operation.state, operation.error = state, error
                operation.process = None
                operation.finished_at = time.monotonic()
            self._guard.release()
            _LOGGER.info("Application operation finished state=%s", state)

    def _run_step(self, operation: _Operation, step: Step) -> None:
        if step.action is not None:
            step.action(operation.progress)
            return
        assert step.command is not None
        with self._lock:
            if operation.progress.cancelled.is_set():
                return
            process = subprocess.Popen(
                list(step.command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=_ENV, start_new_session=True,
            )
            operation.process = process
        parser = FlatpakProgressParser() if step.flatpak_progress else None
        tail: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            tail = [*tail[-19:], line.rstrip()]
            if parser is not None and (parsed := parser.feed(line)) is not None:
                operation.progress.percent = parsed.percent
        process.stdout.close()
        if process.wait() != 0:
            raise OperationFailed("\n".join(tail)[-1000:] or f"{Path(step.command[0]).name} failed")
