"""Controller-friendly drive list for mounting and unmounting data drives."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.storage import Drive, StorageError, StorageGateway
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)


def format_size(size: int) -> str:
    """Format a byte count with decimal units as drive vendors label them."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


class DrivesWindow(WallpaperDialog):
    """List data drives and mount or unmount the selected one."""

    operation_finished = Signal(object)

    def __init__(
        self, gateway: StorageGateway, theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create the drive view around an injected storage gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-drives")
        self._closed = False
        self._busy = False
        self.setWindowTitle(tr(language, "drives"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "drives"), tr(language, "drives_description"))
        self.status = frame.status
        self.drives = QListWidget(self)
        self.drives.setObjectName("settings_applications")
        self.drives.installEventFilter(self)
        frame.content.addWidget(self.drives, stretch=1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.toggle = add_action_button(self, actions, tr(language, "drives_mount"))
        self.toggle.installEventFilter(self)
        frame.content.addLayout(actions)
        self.refresh = add_action_button(self, frame.footer, tr(language, "drives_refresh"))
        self.refresh.installEventFilter(self)
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        self.back.installEventFilter(self)
        self.drives.currentItemChanged.connect(self._update_controls)
        self.drives.itemActivated.connect(lambda _item: self._toggle_selected())
        self.toggle.clicked.connect(self._toggle_selected)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        if not gateway.available():
            set_status(self.status, tr(language, "drives_unsupported"), error=True)
            self.drives.setEnabled(False)
            self.toggle.setEnabled(False)
            self.refresh.setEnabled(False)
            self.back.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._refresh()

    def selected(self) -> Drive | None:
        """Return the highlighted drive, if any."""
        item = self.drives.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, Drive) else None

    def _submit(self, operation: Callable[[], tuple[tuple[Drive, ...], tuple[str, bool] | None]]) -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        set_status(self.status, tr(self._language, "wifi_working"))
        self._update_controls()
        future = self._executor.submit(operation)

        def finish(result: Future[tuple[tuple[Drive, ...], tuple[str, bool] | None]]) -> None:
            if not self._closed:
                self.operation_finished.emit(result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        self._submit(lambda: (self._gateway.list_drives(), None))

    def _toggle_selected(self) -> None:
        drive = self.selected()
        if drive is None or self._busy:
            return
        language = self._language

        def change() -> tuple[tuple[Drive, ...], tuple[str, bool] | None]:
            try:
                if drive.mounted:
                    self._gateway.unmount(drive)
                    key = "drives_removed" if drive.removable else "drives_unmounted"
                    message = tr(language, key, name=drive.label)
                else:
                    mountpoint = self._gateway.mount(drive)
                    message = tr(language, "drives_mounted", name=drive.label, path=mountpoint)
                return self._gateway.list_drives(), (message, False)
            except StorageError as error:
                drives = self._gateway.list_drives()
                return drives, (tr(language, "drives_error", message=str(error)), True)

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
        if message is not None:
            set_status(self.status, message[0], error=message[1])
        elif drives:
            set_status(self.status, "")
        else:
            set_status(self.status, tr(self._language, "drives_none"))
        self._update_controls()
        if self.drives.count():
            self.drives.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_drives(self, drives: tuple[Drive, ...]) -> None:
        previous = self.selected()
        with QSignalBlocker(self.drives):
            self.drives.clear()
            for drive in drives:
                state = (
                    tr(self._language, "drives_state_mounted", path=drive.mountpoint)
                    if drive.mounted else tr(self._language, "drives_state_unmounted")
                )
                text = f"{drive.label} · {format_size(drive.size)} · {drive.filesystem} — {state}"
                item = QListWidgetItem(text, self.drives)
                item.setData(Qt.ItemDataRole.UserRole, drive)
                if previous is not None and drive.device == previous.device:
                    self.drives.setCurrentItem(item)
            if self.drives.currentRow() < 0 and drives:
                self.drives.setCurrentRow(0)

    def _update_controls(self, *_: object) -> None:
        ready = not self._busy and self._gateway.available()
        drive = self.selected()
        self.drives.setEnabled(ready)
        self.refresh.setEnabled(ready)
        self.toggle.setEnabled(ready and drive is not None)
        key = "drives_unmount" if drive is not None and drive.mounted else "drives_mount"
        self.toggle.setText(tr(self._language, key))

    def handle_input(self, event: InputEvent) -> None:
        """Forward normalized controller input to the focused control."""
        if not event.pressed:
            return
        if event.action is InputAction.BACK:
            self.reject()
            return
        focus = self.focusWidget()
        if event.action is InputAction.ACTIVATE:
            if isinstance(focus, QPushButton):
                focus.click()
            elif focus is self.drives:
                self._toggle_selected()
            return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
        }
        key = keys.get(event.action)
        if focus is not None and key is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Move between the list, the mount action and the footer buttons."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            rows = [
                [widget for widget in row if widget.isEnabled()]
                for row in ((self.drives,), (self.toggle,), (self.refresh, self.back))
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
        self._executor.shutdown(wait=False, cancel_futures=True)
