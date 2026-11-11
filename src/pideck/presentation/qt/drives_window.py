"""Controller-friendly drive list for mounting, unmounting and LAN sharing."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from pideck.application.drive_items import DriveAction, available_actions
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.sharing import ShareError, ShareGateway
from pideck.application.ports.storage import Drive, StorageError, StorageGateway
from pideck.domain.theme import ThemeDefinition

from pideck.infrastructure.samba_shares import smb_url

from .drive_actions_dialog import DriveActionsDialog, format_size
from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme, icon_for_path
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)
_STATUS_ICON_SIZE = 24


class _UnavailableShares:
    """Share gateway used when the image does not support Samba exports."""

    def available(self) -> bool:
        return False

    def is_shared(self, uuid: str | None) -> bool:
        return False

    def share_name(self, drive: Drive) -> str:
        return drive.label

    def share(self, drive: Drive) -> str:
        raise ShareError("Network sharing requires a newer PiDeck image")

    def unshare(self, drive: Drive) -> None:
        return None

    def release(self, drive: Drive) -> None:
        return None

    def restore_shared(self) -> None:
        return None

    def ensure_preferred(self, drive: Drive) -> str | None:
        return None


class DriveRowWidget(QWidget):
    """List row with drive identity text and mount/share status icons."""

    def __init__(
        self,
        text: str,
        *,
        mounted: bool,
        shared: bool,
        mounted_label: str,
        shared_label: str,
        theme: ThemeDefinition,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(10)
        self.label = QLabel(text, self)
        self.label.setObjectName("settings_body")
        layout.addWidget(self.label, stretch=1)
        self.mounted_icon = QLabel(self)
        self.mounted_icon.setObjectName("drive_status_icon")
        self.mounted_icon.setFixedSize(_STATUS_ICON_SIZE, _STATUS_ICON_SIZE)
        self.shared_icon = QLabel(self)
        self.shared_icon.setObjectName("drive_status_icon")
        self.shared_icon.setFixedSize(_STATUS_ICON_SIZE, _STATUS_ICON_SIZE)
        layout.addWidget(self.mounted_icon)
        layout.addWidget(self.shared_icon)
        self._apply_status(
            mounted=mounted,
            shared=shared,
            mounted_label=mounted_label,
            shared_label=shared_label,
            theme=theme,
        )

    def _apply_status(
        self,
        *,
        mounted: bool,
        shared: bool,
        mounted_label: str,
        shared_label: str,
        theme: ThemeDefinition,
    ) -> None:
        if mounted:
            self.mounted_icon.setPixmap(
                icon_for_path(theme.icons.get("drive_mounted")).pixmap(
                    _STATUS_ICON_SIZE, _STATUS_ICON_SIZE
                )
            )
            self.mounted_icon.setAccessibleName(mounted_label)
            self.mounted_icon.show()
        else:
            self.mounted_icon.hide()
            self.mounted_icon.clear()
            self.mounted_icon.setAccessibleName("")
        if shared:
            self.shared_icon.setPixmap(
                icon_for_path(theme.icons.get("drive_shared")).pixmap(
                    _STATUS_ICON_SIZE, _STATUS_ICON_SIZE
                )
            )
            self.shared_icon.setAccessibleName(shared_label)
            self.shared_icon.show()
        else:
            self.shared_icon.hide()
            self.shared_icon.clear()
            self.shared_icon.setAccessibleName("")


class DrivesWindow(WallpaperDialog):
    """List data drives and open a per-drive options menu."""

    operation_finished = Signal(object)

    def __init__(
        self,
        gateway: StorageGateway,
        theme: ThemeDefinition,
        language: str,
        parent: QWidget,
        shares: ShareGateway | None = None,
    ) -> None:
        """Create the drive view around injected storage and share gateways."""
        super().__init__(parent)
        self._gateway = gateway
        self._shares: ShareGateway = shares if shares is not None else _UnavailableShares()
        self._theme = theme
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-drives")
        self._closed = False
        self._busy = False
        self.dialog: DriveActionsDialog | None = None
        self._dialog_device: str | None = None
        self.setWindowTitle(tr(language, "drives"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "drives"), tr(language, "drives_description"))
        self.status = frame.status
        self.drives = QListWidget(self)
        self.drives.setObjectName("settings_applications")
        self.drives.installEventFilter(self)
        frame.content.addWidget(self.drives, stretch=1)
        self.refresh = add_action_button(self, frame.footer, tr(language, "drives_refresh"))
        self.refresh.installEventFilter(self)
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        self.back.installEventFilter(self)
        self.drives.currentItemChanged.connect(self._update_controls)
        # Single click opens options (same as Applications); Enter/double-click too.
        self.drives.itemClicked.connect(lambda _item: self.activate_selected())
        self.drives.itemActivated.connect(lambda _item: self.activate_selected())
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        if not gateway.available():
            set_status(self.status, tr(language, "drives_unsupported"), error=True)
            self.drives.setEnabled(False)
            self.refresh.setEnabled(False)
            self.back.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._refresh()

    def selected(self) -> Drive | None:
        """Return the highlighted drive, if any."""
        item = self.drives.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, Drive) else None

    def activate_selected(self) -> None:
        """Open the options menu for the highlighted drive."""
        drive = self.selected()
        if drive is None or self._busy or self.dialog is not None:
            return
        shared = self._shares.is_shared(drive.uuid)
        actions = available_actions(
            drive,
            busy=self._busy,
            share_available=self._shares.available(),
            shared=shared,
        )
        if not actions:
            return
        share_name = self._shares.share_name(drive) if shared else None
        share_url = smb_url(share_name) if share_name else None
        dialog = DriveActionsDialog(
            drive,
            actions,
            self._theme,
            self._language,
            self,
            shared=shared,
            share_name=share_name,
            share_url=share_url,
        )
        self.dialog, self._dialog_device = dialog, drive.device
        dialog.action_chosen.connect(self._handle_action)
        dialog.finished.connect(lambda _result: self._close_dialog(dialog))
        # showFullScreen so the menu appears above this already-modal window.
        dialog.showFullScreen()
        dialog.raise_()
        dialog.activateWindow()

    def _handle_action(self, action: str) -> None:
        drive = self.selected()
        if drive is None or drive.device != self._dialog_device:
            return
        if action == DriveAction.MOUNT:
            self._mount(drive)
        elif action == DriveAction.UNMOUNT:
            self._unmount(drive)
        elif action == DriveAction.SHARE:
            self._share(drive)
        elif action == DriveAction.STOP_SHARING:
            self._unshare(drive)

    def _close_dialog(self, dialog: DriveActionsDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None
            self._dialog_device = None
        self.activateWindow()
        if self.drives.count():
            self.drives.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)

    def _submit(self, operation: Callable[[], tuple[tuple[Drive, ...], tuple[str, bool] | None]]) -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        set_status(self.status, tr(self._language, "wifi_working"))
        self._update_controls()
        self._refresh_dialog()
        future = self._executor.submit(operation)

        def finish(result: Future[tuple[tuple[Drive, ...], tuple[str, bool] | None]]) -> None:
            if not self._closed:
                self.operation_finished.emit(result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        self._submit(lambda: (self._gateway.list_drives(), None))

    def _mount(self, drive: Drive) -> None:
        language = self._language
        shares = self._shares

        def change() -> tuple[tuple[Drive, ...], tuple[str, bool] | None]:
            try:
                mountpoint = self._gateway.mount(drive)
                message = tr(language, "drives_mounted", name=drive.label, path=mountpoint)
                mounted = next(
                    (item for item in self._gateway.list_drives() if item.device == drive.device),
                    None,
                )
                if mounted is not None:
                    try:
                        url = shares.ensure_preferred(mounted)
                        if url:
                            message = tr(
                                language, "drives_mounted_shared",
                                name=drive.label, path=mountpoint, url=url,
                            )
                    except ShareError as error:
                        _LOGGER.warning("Unable to restore share after mount: %s", error)
                return self._gateway.list_drives(), (message, False)
            except StorageError as error:
                return self._gateway.list_drives(), (tr(language, "drives_error", message=str(error)), True)

        self._submit(change)

    def _unmount(self, drive: Drive) -> None:
        language = self._language
        shares = self._shares

        def change() -> tuple[tuple[Drive, ...], tuple[str, bool] | None]:
            try:
                if shares.is_shared(drive.uuid):
                    shares.release(drive)
                self._gateway.unmount(drive)
                key = "drives_removed" if drive.removable else "drives_unmounted"
                message = tr(language, key, name=drive.label)
                return self._gateway.list_drives(), (message, False)
            except (StorageError, ShareError) as error:
                return self._gateway.list_drives(), (tr(language, "drives_error", message=str(error)), True)

        self._submit(change)

    def _share(self, drive: Drive) -> None:
        language = self._language
        shares = self._shares

        def change() -> tuple[tuple[Drive, ...], tuple[str, bool] | None]:
            try:
                current = next(
                    (item for item in self._gateway.list_drives() if item.device == drive.device),
                    drive,
                )
                url = shares.share(current)
                message = tr(language, "drives_shared", name=drive.label, url=url)
                return self._gateway.list_drives(), (message, False)
            except (StorageError, ShareError) as error:
                return self._gateway.list_drives(), (tr(language, "drives_share_error", message=str(error)), True)

        self._submit(change)

    def _unshare(self, drive: Drive) -> None:
        language = self._language
        shares = self._shares

        def change() -> tuple[tuple[Drive, ...], tuple[str, bool] | None]:
            try:
                shares.unshare(drive)
                message = tr(language, "drives_unshared", name=drive.label)
                return self._gateway.list_drives(), (message, False)
            except ShareError as error:
                return self._gateway.list_drives(), (tr(language, "drives_share_error", message=str(error)), True)

        self._submit(change)

    def _finish_operation(self, future: Future[tuple[tuple[Drive, ...], tuple[str, bool] | None]]) -> None:
        if self._closed:
            return
        self._busy = False
        try:
            drives, message = future.result()
        except StorageError as error:
            drives, message = (), (tr(self._language, "drives_error", message=str(error)), True)
        except Exception:
            _LOGGER.exception("Unexpected drive operation failure")
            drives, message = (), (tr(self._language, "drives_error", message=tr(self._language, "drives_unavailable")), True)
        self._show_drives(drives)
        self._update_controls()
        self._refresh_dialog()
        if message is not None and message[1]:
            if self.dialog is not None:
                self.dialog.set_error(message[0])
                set_status(self.status, "")
            else:
                set_status(self.status, message[0], error=True)
        if self.dialog is None:
            if self.drives.count():
                self.drives.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_drives(self, drives: tuple[Drive, ...]) -> None:
        previous = self.selected()
        with QSignalBlocker(self.drives):
            self.drives.clear()
            for drive in drives:
                text = f"{drive.label} · {format_size(drive.size)} · {drive.filesystem}"
                shared = drive.mounted and self._shares.is_shared(drive.uuid)
                mounted_label = (
                    tr(self._language, "drives_state_mounted", path=drive.mountpoint or "")
                    if drive.mounted
                    else ""
                )
                shared_label = (
                    tr(self._language, "drives_state_shared", name=self._shares.share_name(drive))
                    if shared
                    else ""
                )
                item = QListWidgetItem(text, self.drives)
                item.setData(Qt.ItemDataRole.UserRole, drive)
                row = DriveRowWidget(
                    text,
                    mounted=drive.mounted,
                    shared=shared,
                    mounted_label=mounted_label,
                    shared_label=shared_label,
                    theme=self._theme,
                    parent=self.drives,
                )
                item.setSizeHint(row.sizeHint())
                self.drives.setItemWidget(item, row)
                if previous is not None and drive.device == previous.device:
                    self.drives.setCurrentItem(item)
            if self.drives.currentRow() < 0 and drives:
                self.drives.setCurrentRow(0)

    def _update_controls(self, *_: object) -> None:
        ready = not self._busy and self._gateway.available()
        self.drives.setEnabled(ready)
        self.refresh.setEnabled(ready)
        if not self._busy and self._gateway.available():
            self._sync_status_for_selection()

    def _sync_status_for_selection(self) -> None:
        """Keep the list status free of per-drive share details."""
        if self.drives.count() == 0:
            set_status(self.status, tr(self._language, "drives_none"))
            return
        set_status(self.status, "")

    def _refresh_dialog(self) -> None:
        dialog = self.dialog
        if dialog is None or self._dialog_device is None:
            return
        drive = next(
            (item for item in (
                self.selected(),
            ) if item is not None and item.device == self._dialog_device),
            None,
        )
        if drive is None:
            for row in range(self.drives.count()):
                value = self.drives.item(row).data(Qt.ItemDataRole.UserRole)
                if isinstance(value, Drive) and value.device == self._dialog_device:
                    drive = value
                    break
        if drive is None:
            dialog.reject()
            return
        shared = self._shares.is_shared(drive.uuid)
        # Keep showing actions for the current state while a worker runs; disable via set_busy.
        actions = available_actions(
            drive,
            busy=False,
            share_available=self._shares.available(),
            shared=shared,
        )
        if not actions:
            dialog.reject()
            return
        focus: DriveAction | None = None
        focused = dialog.focusWidget()
        if isinstance(focused, QPushButton):
            value = focused.property("action")
            if value in {action.value for action in DriveAction}:
                focus = DriveAction(str(value))
        share_name = self._shares.share_name(drive) if shared else None
        share_url = smb_url(share_name) if share_name else None
        dialog.set_details(drive, shared=shared, share_name=share_name, share_url=share_url)
        dialog.set_actions(actions, focus=focus)
        dialog.set_busy(self._busy)

    def handle_input(self, event: InputEvent) -> None:
        """Forward normalized controller input to the focused control or menu."""
        if not event.pressed:
            return
        if self.dialog is not None:
            self.dialog.handle_input(event)
            return
        if event.action is InputAction.BACK:
            self.reject()
            return
        focus = self.focusWidget()
        if event.action is InputAction.ACTIVATE:
            if isinstance(focus, QPushButton):
                focus.click()
            elif focus is self.drives:
                self.activate_selected()
            return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
        }
        key = keys.get(event.action)
        if focus is not None and key is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Move between the list and the footer buttons."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            rows = [
                [widget for widget in row if widget.isEnabled()]
                for row in ((self.drives,), (self.refresh, self.back))
            ]
            rows = [row for row in rows if row]
            if watched is self.drives and key == Qt.Key.Key_Down and self.drives.currentRow() < self.drives.count() - 1:
                return super().eventFilter(watched, event)
            if watched is self.drives and key == Qt.Key.Key_Up:
                return super().eventFilter(watched, event)
            for row_index, row in enumerate(rows):
                if watched not in row:
                    continue
                index = row.index(watched)
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and isinstance(watched, QPushButton):
                    step = -1 if key == Qt.Key.Key_Left else 1
                    row[max(0, min(len(row) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    target_index = max(0, min(len(rows) - 1, row_index + (-1 if key == Qt.Key.Key_Up else 1)))
                    target = rows[target_index]
                    target[min(index, len(target) - 1)].setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        if self.dialog is not None:
            self.dialog.reject()
        self._executor.shutdown(wait=False, cancel_futures=True)
