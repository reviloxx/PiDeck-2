"""Asynchronous application update orchestration."""

import logging
import threading

from pideck.application.ports.updates import (
    UpdateCallback,
    UpdateGateway,
    UpdateInfo,
    UpdateStatus,
)
from pideck.domain.configuration import ApplicationDefinition

_LOGGER = logging.getLogger(__name__)


class ApplicationUpdateService:
    """Run update checks and operations without blocking the Qt event loop."""

    def __init__(self, gateway: UpdateGateway) -> None:
        """Create an update service around an injected package-manager gateway."""
        self._gateway = gateway

    def check_async(
        self,
        applications: tuple[ApplicationDefinition, ...],
        callback: UpdateCallback,
    ) -> None:
        """Check configured applications on a daemon worker thread."""
        threading.Thread(
            target=self._check_all,
            args=(applications, callback),
            name="pideck-update-check",
            daemon=True,
        ).start()

    def start_async(
        self,
        application: ApplicationDefinition,
        password: str | None,
        callback: UpdateCallback,
    ) -> None:
        """Start one update operation without blocking the settings UI."""
        callback(
            UpdateInfo(
                application.identifier,
                application.name,
                UpdateStatus.UPDATING,
            )
        )
        try:
            self._gateway.start(application, password, callback)
        except PermissionError:
            callback(
                UpdateInfo(
                    application.identifier,
                    application.name,
                    UpdateStatus.PASSWORD_REQUIRED,
                )
            )
        except Exception as error:
            _LOGGER.exception("Unable to start application update id=%s", application.identifier)
            callback(
                UpdateInfo(
                    application.identifier,
                    application.name,
                    UpdateStatus.FAILED,
                    message=str(error),
                )
            )

    def cancel(self, application_id: str) -> None:
        """Cancel the active update for one application."""
        self._gateway.cancel_by_application(application_id)

    def close(self) -> None:
        """Close the injected update gateway."""
        self._gateway.close()

    def _check_all(
        self,
        applications: tuple[ApplicationDefinition, ...],
        callback: UpdateCallback,
    ) -> None:
        """Check every configured application independently."""
        for application in applications:
            try:
                callback(self._gateway.check(application))
            except Exception as error:
                _LOGGER.exception("Update check failed id=%s", application.identifier)
                callback(
                    UpdateInfo(
                        application.identifier,
                        application.name,
                        UpdateStatus.FAILED,
                        message=str(error),
                    )
                )
