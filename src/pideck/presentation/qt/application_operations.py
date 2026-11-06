"""Run catalog installs and removals off the Qt thread and report on it.

The controller owns the single running operation. Package work runs in a
worker thread; progress and results are delivered as Qt signals on the
thread that owns the controller, so slots may update widgets directly.
"""

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal

from pideck.application.app_management import ApplicationManagementService
from pideck.application.ports.installation import InstallProgress
from pideck.application.ports.packages import PackageOperationCancelled, PackageOperationError

_LOGGER = logging.getLogger(__name__)


class ApplicationOperationsController(QObject):
    """Start, cancel and observe one application install or removal at a time."""

    progress = Signal(str, object)
    """``(application_id, InstallProgress)``"""
    finished = Signal(str, bool, object)
    """``(application_id, removing, error)``; ``error`` is ``None`` on success."""
    cancelled = Signal(str)

    _worker_progress = Signal(str, object)
    _worker_done = Signal(str, bool, object, bool)

    def __init__(self, service: ApplicationManagementService, parent: QObject | None = None) -> None:
        """Bind the controller to the application management service."""
        super().__init__(parent)
        self._service = service
        self._active_id: str | None = None
        self._removing = False
        self._last_progress: InstallProgress | None = None
        self._worker_progress.connect(self._handle_progress, Qt.ConnectionType.QueuedConnection)
        self._worker_done.connect(self._handle_done, Qt.ConnectionType.QueuedConnection)

    @property
    def active_id(self) -> str | None:
        """Return the application of the running operation, if any."""
        return self._active_id

    @property
    def removing(self) -> bool:
        """Return whether the running operation is a removal."""
        return self._removing

    @property
    def last_progress(self) -> InstallProgress | None:
        """Return the most recent progress of the running operation."""
        return self._last_progress

    def start_install(self, application_id: str, password: str | None = None) -> None:
        """Install an application in the background."""
        self._start(application_id, False, lambda report: self._service.install(application_id, report, password))

    def start_remove(self, application_id: str) -> None:
        """Remove an application in the background, keeping its user data."""
        self._start(application_id, True, lambda report: self._service.uninstall(application_id, report))

    def cancel(self) -> None:
        """Cancel the running install; raises ``PermissionError`` if it cannot stop now."""
        if self._active_id is not None and not self._removing:
            self._service.cancel()

    def _start(
        self, application_id: str, removing: bool,
        operation: Callable[[Callable[[InstallProgress], None]], None],
    ) -> None:
        if self._active_id is not None:
            raise RuntimeError("Another application operation is already running")
        self._active_id, self._removing, self._last_progress = application_id, removing, None

        def run() -> None:
            try:
                operation(lambda progress: self._worker_progress.emit(application_id, progress))
            except PackageOperationCancelled:
                self._worker_done.emit(application_id, removing, None, True)
                return
            except PackageOperationError as error:
                self._worker_done.emit(application_id, removing, str(error), False)
                return
            except Exception:
                _LOGGER.exception("Unexpected application operation failure")
                self._worker_done.emit(application_id, removing, "Unexpected application operation failure", False)
                return
            self._worker_done.emit(application_id, removing, None, False)

        threading.Thread(target=run, name="pideck-application-operation", daemon=True).start()

    def _handle_progress(self, application_id: str, progress: InstallProgress) -> None:
        if application_id == self._active_id:
            self._last_progress = progress
            self.progress.emit(application_id, progress)

    def _handle_done(self, application_id: str, removing: bool, error: str | None, cancelled: bool) -> None:
        self._active_id, self._removing, self._last_progress = None, False, None
        if cancelled:
            _LOGGER.info("Application installation cancelled application=%s", application_id)
            self.cancelled.emit(application_id)
            return
        if error is not None:
            _LOGGER.warning("Application %s failed application=%s: %s",
                            "removal" if removing else "installation", application_id, error)
        self.finished.emit(application_id, removing, error)
