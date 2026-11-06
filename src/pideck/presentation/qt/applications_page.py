"""Settings > Applications: install, update, remove, show and order applications.

One focusable row per configured or supported application shows its home
screen state, installed version, update status and install/removal progress.
Selecting a row opens an action menu. The page only emits intents; the
composition root persists configuration and runs package operations.
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QDialog, QFrame, QScrollArea, QVBoxLayout, QWidget

from pideck.application.app_management import CatalogItemStatus
from pideck.application.application_items import (
    ApplicationAction,
    ApplicationItem,
    application_items,
    available_actions,
)
from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.application.ports.updates import UpdateInfo, UpdateStatus
from pideck.application.updates import ApplicationUpdateService
from pideck.domain.catalog import InstallState
from pideck.domain.configuration import Configuration
from pideck.domain.theme import ThemeDefinition

from .application_actions_dialog import ApplicationActionsDialog
from .install_row import install_status_text
from .localization import tr
from .power_dialog import ConfirmationDialog
from .update_rows import PasswordPrompt, UpdateRow

_STATE_TEXT = {
    InstallState.INSTALLED: "application_not_on_home",
    InstallState.NOT_INSTALLED: "application_not_installed",
    InstallState.UNAVAILABLE: "application_unavailable",
    InstallState.REQUIRES_NEWER_IMAGE: "application_requires_image",
}


class ApplicationRow(UpdateRow):
    """Render one application's home-screen, update and package state."""

    def __init__(self, item: ApplicationItem, language: str, parent: QWidget | None = None) -> None:
        """Create a row; configured applications start in the checking state."""
        super().__init__(item.key, item.name, parent)
        self._language = language
        self.item = item
        self.operation: InstallProgress | None = None
        self.removing = False
        self.action.hide()
        if item.configured:
            self.set_info(self.info)
        else:
            self._render()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Focus the row when clicked."""
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Open the action menu on a primary click."""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activation_requested.emit()
        super().mouseReleaseEvent(event)

    def set_info(self, info: UpdateInfo) -> None:
        """Render update information, keeping the row's own actions in the menu."""
        super().set_info(info)
        self.action.hide()
        self._render()

    def set_operation(self, progress: InstallProgress | None, *, removing: bool = False) -> None:
        """Show or clear install or removal progress."""
        if progress is None and self.operation is None:
            return
        self.operation, self.removing = progress, removing
        if progress is None and self.item.configured:
            self.set_info(self.info)
        else:
            self._render()

    def set_language(self, language: str) -> None:
        """Re-render all texts in the selected language."""
        self._language = language if language in {"en", "de"} else "en"
        if self.item.configured:
            super().set_language(language)
            self.action.hide()
        self._render()

    def _render(self) -> None:
        item = self.item
        if self.operation is not None:
            title = "removing_application" if self.removing else "installing_application"
            self.detail_label.show()
            self.detail_label.setText(tr(self._language, title, name=item.name))
            self.status_label.setText(install_status_text(self._language, self.operation))
            self.spinner.set_running(True)
            self.progress_bar.show()
            if self.operation.percent is None:
                self.progress_bar.setRange(0, 0)
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(max(0, min(100, self.operation.percent)))
            return
        self.progress_bar.hide()
        self.detail_label.setVisible(item.configured)
        if item.configured:
            key = "application_shown" if item.visible else "application_hidden"
            self.detail_label.setText(tr(self._language, key))
            return
        state = item.catalog.state if item.catalog is not None else InstallState.UNAVAILABLE
        self.detail_label.setText("")
        self.status_label.setText(tr(self._language, _STATE_TEXT[state]))
        self.version_label.setText("")
        self.build_label.hide()
        self.spinner.set_running(False)


class ApplicationsPage(QScrollArea):
    """Scrollable list of application rows with an action menu."""

    install_requested = Signal(str)
    """Catalog ID to install, or of the running installation to cancel."""
    remove_requested = Signal(str)
    visibility_requested = Signal(str, bool)
    move_requested = Signal(str, int)
    navigation_requested = Signal(object)
    rows_changed = Signal()
    update_status = Signal(object)

    def __init__(
        self, configuration: Configuration, statuses: tuple[CatalogItemStatus, ...], theme: ThemeDefinition,
        language: str, update_service: ApplicationUpdateService | None, parent: QWidget | None = None,
    ) -> None:
        """Create rows and start update checks for configured applications."""
        super().__init__(parent)
        self.setObjectName("settings_scroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(self)
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 12, 14, 0)
        self._layout.setSpacing(12)
        self._layout.addStretch()
        self.setWidget(content)
        self._theme = theme
        self._language = language
        self._update_service = update_service
        self._rows: dict[str, ApplicationRow] = {}
        self._infos: dict[str, UpdateInfo] = {}
        self._checked: set[str] = set()
        self._rechecking: set[str] = set()
        self._busy_id: str | None = None
        self._busy_removing = False
        self._busy_progress: InstallProgress | None = None
        self._compact = False
        self.dialog: ApplicationActionsDialog | ConfirmationDialog | None = None
        self._dialog_key: str | None = None
        self._removal_target: tuple[str, str] | None = None
        self.update_status.connect(self._handle_update_status)
        self.set_model(configuration, statuses)

    def rows(self) -> list[ApplicationRow]:
        """Return the rows in display order."""
        return list(self._rows.values())

    def row(self, key: str) -> ApplicationRow | None:
        """Return the row of a configured or catalog application."""
        return self._rows.get(key) or next(
            (row for row in self._rows.values() if row.item.catalog_id == key), None,
        )

    def set_model(self, configuration: Configuration, statuses: tuple[CatalogItemStatus, ...]) -> None:
        """Rebuild rows after a configuration or install-state change, keeping focus."""
        focused = next((row for row in self._rows.values() if row.hasFocus()), None)
        focus_keys = (focused.item.key, focused.item.catalog_id) if focused is not None else ()
        for row in self._rows.values():
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows = {}
        for item in application_items(configuration, statuses):
            row = ApplicationRow(item, self._language, self.widget())
            row.set_compact(self._compact)
            row.activation_requested.connect(lambda key=item.key: self.activate(key))
            row.navigation_requested.connect(self.navigation_requested.emit)
            row.installEventFilter(self)
            self._layout.insertWidget(self._layout.count() - 1, row)
            # Show now; layouts otherwise show new children later, which blocks restoring focus.
            row.show()
            self._rows[item.key] = row
            if item.configured and item.key in self._infos:
                row.set_info(self._infos[item.key])
        if self._dialog_key is not None and self._dialog_key not in self._rows:
            moved = next((key for key in focus_keys if key and self.row(key) is not None), None)
            self._dialog_key = self.row(moved).item.key if moved is not None else None
        self._apply_busy()
        self._check_new_applications()
        self.rows_changed.emit()
        target = next((self.row(key) for key in focus_keys if key and self.row(key) is not None), None)
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        self._refresh_dialog()

    def set_busy(self, catalog_id: str | None, *, removing: bool = False) -> None:
        """Show or clear install/removal progress on a row."""
        self._busy_id, self._busy_removing, self._busy_progress = catalog_id, removing, None
        self._apply_busy()
        self._refresh_dialog()

    def set_progress(self, catalog_id: str, progress: InstallProgress) -> None:
        """Render progress of the running install or removal."""
        if catalog_id == self._busy_id:
            self._busy_progress = progress
            self._apply_busy()

    def set_language(self, language: str) -> None:
        """Translate every row."""
        self._language = language
        for row in self._rows.values():
            row.set_language(language)

    def set_compact(self, compact: bool) -> None:
        """Stack row details on narrow screens."""
        self._compact = compact
        for row in self._rows.values():
            row.set_compact(compact)

    def activate(self, key: str) -> None:
        """Open the action menu for a row."""
        row = self._rows.get(key)
        if row is None or self.dialog is not None:
            return
        actions = self._actions(row)
        if not actions:
            return
        dialog = ApplicationActionsDialog(row.item.name, actions, self._theme, self._language, self)
        self.dialog, self._dialog_key = dialog, key
        dialog.action_chosen.connect(self._handle_action)
        dialog.finished.connect(lambda _result: self._close_dialog(dialog))
        dialog.open()

    def _actions(self, row: ApplicationRow) -> tuple[ApplicationAction, ...]:
        catalog_id = row.item.catalog_id
        if self._busy_removing and catalog_id is not None and catalog_id == self._busy_id:
            return ()
        info = row.info if row.item.configured else None
        return available_actions(
            row.item,
            count=sum(1 for other in self._rows.values() if other.item.configured),
            update_available=info is not None and info.status is UpdateStatus.AVAILABLE,
            updating=info is not None and info.status is UpdateStatus.UPDATING,
            installing=catalog_id is not None and catalog_id == self._busy_id,
            busy=self._busy_id is not None,
        )

    def _handle_action(self, value: str) -> None:
        row = self._rows.get(self._dialog_key or "")
        if row is None:
            return
        action, item = ApplicationAction(value), row.item
        if action in (ApplicationAction.MOVE_UP, ApplicationAction.MOVE_DOWN):
            self.move_requested.emit(item.key, -1 if action is ApplicationAction.MOVE_UP else 1)
        elif action in (ApplicationAction.SHOW, ApplicationAction.HIDE):
            self.visibility_requested.emit(item.key, action is ApplicationAction.SHOW)
        elif action in (ApplicationAction.INSTALL, ApplicationAction.CANCEL_INSTALL) and item.catalog_id:
            self.install_requested.emit(item.catalog_id)
        elif action in (ApplicationAction.UPDATE, ApplicationAction.CANCEL_UPDATE):
            self._activate_update(row)
        elif action is ApplicationAction.REMOVE and item.catalog_id:
            self._removal_target = (item.catalog_id, item.name)

    def _close_dialog(self, dialog: QDialog) -> None:
        if self.dialog is not dialog:
            dialog.deleteLater()
            return
        key, self.dialog = self._dialog_key, None
        dialog.deleteLater()
        removal, self._removal_target = self._removal_target, None
        if removal is not None:
            self._confirm_removal(*removal)
            return
        self._dialog_key = None
        row = self._rows.get(key or "")
        if row is not None:
            row.setFocus(Qt.FocusReason.OtherFocusReason)

    def _refresh_dialog(self) -> None:
        """Keep an open action menu consistent after a move or state change."""
        if not isinstance(self.dialog, ApplicationActionsDialog):
            return
        row = self._rows.get(self._dialog_key or "")
        actions = self._actions(row) if row is not None else ()
        if not actions:
            self.dialog.reject()
            return
        focus = self.dialog.focusWidget()
        value = focus.property("action") if focus is not None else None
        self.dialog.set_actions(actions, ApplicationAction(value) if value else None)

    def _confirm_removal(self, catalog_id: str, name: str) -> None:
        dialog = ConfirmationDialog(
            tr(self._language, "remove_confirm_title", name=name),
            tr(self._language, "remove_confirm_message", name=name),
            tr(self._language, "remove"), tr(self._language, "cancel"), self._theme, self,
        )
        self.dialog = dialog

        def finish(result: int) -> None:
            if result == QDialog.DialogCode.Accepted:
                self.remove_requested.emit(catalog_id)
            self._close_dialog(dialog)

        dialog.finished.connect(finish)
        dialog.open()

    def _apply_busy(self) -> None:
        for row in self._rows.values():
            busy = self._busy_id is not None and row.item.catalog_id == self._busy_id
            row.set_operation(
                (self._busy_progress or InstallProgress(InstallStage.PREPARING)) if busy else None,
                removing=self._busy_removing,
            )

    def _check_new_applications(self) -> None:
        configured = [row.item.application for row in self._rows.values() if row.item.configured]
        new = tuple(app for app in configured if app is not None and app.identifier not in self._checked)
        if not new:
            return
        self._checked.update(app.identifier for app in new)
        if self._update_service is not None:
            self._update_service.check_async(new, self.update_status.emit)
            return
        for app in new:
            self._handle_update_status(UpdateInfo(
                app.identifier, app.name, UpdateStatus.UNSUPPORTED, message="Update service unavailable",
            ))

    def _activate_update(self, row: ApplicationRow) -> None:
        application = row.item.application
        if self._update_service is None or application is None:
            return
        if row.info.status is UpdateStatus.UPDATING:
            self._rechecking.add(application.identifier)
            self._update_service.cancel(application.identifier)
            self._handle_update_status(UpdateInfo(application.identifier, application.name, UpdateStatus.CHECKING))
            self._update_service.check_async((application,), self.update_status.emit)
            return
        if row.info.status is not UpdateStatus.AVAILABLE:
            return
        self._handle_update_status(UpdateInfo(application.identifier, application.name, UpdateStatus.UPDATING))
        self._update_service.start_async(application, None, self.update_status.emit)

    def _handle_update_status(self, info: UpdateInfo) -> None:
        """Render update status and request a password only when required."""
        if info.status is UpdateStatus.CANCELLED and info.application_id in self._rechecking:
            return
        if info.status is not UpdateStatus.CANCELLED:
            self._rechecking.discard(info.application_id)
        self._infos[info.application_id] = info
        row = self._rows.get(info.application_id)
        if row is None:
            return
        row.set_info(info)
        application = row.item.application
        if info.status is UpdateStatus.UPDATED and self._update_service is not None and application is not None:
            self._update_service.check_async((application,), self.update_status.emit)
        if info.status is not UpdateStatus.PASSWORD_REQUIRED or application is None:
            return
        prompt = PasswordPrompt(info.application_name, self._theme, self, self._language)
        if prompt.exec() != QDialog.DialogCode.Accepted:
            self._handle_update_status(
                row.last_available_info
                or UpdateInfo(info.application_id, info.application_name, UpdateStatus.AVAILABLE)
            )
            return
        password = prompt.password()
        prompt.deleteLater()
        if self._update_service is not None:
            self._update_service.start_async(application, password, self.update_status.emit)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keep the focused row visible."""
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self.ensureWidgetVisible(watched)
        return super().eventFilter(watched, event)
