"""Client for catalog application installs performed by the restricted root service."""

from collections.abc import Callable
import logging
import threading
from typing import Any

from pideck.application.ports.installation import InstallProgress, InstallProgressCallback, InstallStage
from pideck.application.ports.packages import PackageOperationCancelled, PackageOperationError
from pideck.infrastructure.image_update_client import ImageUpdateError, request as broker_request

_LOGGER = logging.getLogger(__name__)
_STAGES = {stage.value: stage for stage in InstallStage}

Request = Callable[..., dict[str, Any]]


class PrivilegedApplications:
    """Ask the image's update service to install or remove a catalog application."""

    def __init__(self, request: Request = broker_request, poll_interval: float = 0.5) -> None:
        """Use the image service socket; ``request`` is injectable for tests."""
        self._request = request
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._token: str | None = None
        self._supported: bool | None = None
        self._stop = threading.Event()

    def supported(self) -> bool:
        """Return whether the running image's service supports application installs.

        Only a positive answer is cached, so a service that was briefly
        restarting is detected on the next check.
        """
        if not self._supported:
            try:
                self._supported = self._request("capabilities", timeout=5).get("applications") == 1
            except ImageUpdateError as error:
                _LOGGER.info("Privileged application installs unavailable: %s", error)
                self._supported = False
        return bool(self._supported)

    def install(self, identifier: str, on_progress: InstallProgressCallback) -> None:
        """Install a catalog application and wait for the result."""
        self._run("app_install", identifier, on_progress)

    def remove(self, identifier: str, on_progress: InstallProgressCallback) -> None:
        """Remove a catalog application and wait for the result."""
        self._run("app_remove", identifier, on_progress)

    def cancel(self) -> None:
        """Ask the service to stop; raises ``PermissionError`` while packages change."""
        with self._lock:
            token = self._token
        if token is None:
            return
        try:
            self._request("app_cancel", token=token)
        except ImageUpdateError as error:
            raise PermissionError(str(error)) from error

    def _run(self, action: str, identifier: str, on_progress: InstallProgressCallback) -> None:
        on_progress(InstallProgress(InstallStage.PREPARING))
        try:
            token = self._request(action, id=identifier).get("token")
        except ImageUpdateError as error:
            raise PackageOperationError(str(error)) from error
        if not isinstance(token, str):
            raise PackageOperationError("Invalid response from the system service")
        with self._lock:
            self._token = token
        try:
            self._wait(token, on_progress)
        finally:
            with self._lock:
                self._token = None

    def _wait(self, token: str, on_progress: InstallProgressCallback) -> None:
        last: InstallProgress | None = None
        while True:
            try:
                result = self._request("app_status", token=token)
            except ImageUpdateError as error:
                raise PackageOperationError(str(error)) from error
            state = result.get("state")
            if state == "succeeded":
                return
            if state == "cancelled":
                raise PackageOperationCancelled("Operation cancelled")
            if state == "failed":
                raise PackageOperationError(str(result.get("error") or "The package operation failed"))
            if state != "running":
                raise PackageOperationError("Invalid response from the system service")
            progress = _progress(result)
            if progress != last:
                on_progress(progress)
                last = progress
            self._stop.wait(self._poll_interval)


def _progress(result: dict[str, Any]) -> InstallProgress:
    """Convert a service status into the shared progress model."""
    stage = _STAGES.get(str(result.get("stage")), InstallStage.PREPARING)
    percent = result.get("percent")
    step, steps = result.get("step"), result.get("steps")
    return InstallProgress(
        stage,
        percent if type(percent) is int else None,
        step if type(step) is int else None,
        steps if type(steps) is int else None,
    )
