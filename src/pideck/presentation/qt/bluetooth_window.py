"""Controller-friendly Bluetooth discovery, pairing, and connection controls."""

from collections.abc import Callable
import logging
from threading import Event
from typing import Protocol

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QWidget,
)

from pideck.application.ports.bluetooth import BluetoothDevice, BluetoothError, BluetoothGateway, BluetoothState, PairingRequest
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .network_window import WirelessKeyDialog
from .power_dialog import ConfirmationDialog
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)


class PairingAgent(Protocol):
    """Provide user-mediated responses to pairing requests."""

    def on_request(self, callback: Callable[[PairingRequest], None]) -> None:
        """Subscribe to pairing requests."""
        ...

    def on_notice(self, callback: Callable[[PairingRequest], None]) -> None:
        """Subscribe to codes that must be displayed on screen."""
        ...

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Subscribe to cancellation by the Bluetooth service."""
        ...

    def set_target(self, path: str | None) -> None:
        """Restrict prompts to the selected device."""
        ...

    def respond(self, value: bool | str | int | None) -> None:
        """Answer or cancel a pending request."""
        ...

    def close(self) -> None:
        """Release the pairing agent."""
        ...


class BluetoothWindow(WallpaperDialog):
    """Manage Bluetooth devices without automatically authorizing pairing."""

    def __init__(
        self, gateway: BluetoothGateway, agent: PairingAgent,
        theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create a Bluetooth settings view using injected system operations."""
        super().__init__(parent)
        self._gateway = gateway
        self._agent = agent
        self._theme = theme
        self._language = language
        self._state: BluetoothState | None = None
        self._closed = False
        self._busy: str | None = None
        self._pairing_path: str | None = None
        self._pair_dialog: QDialog | None = None
        self._cancel_discovery = Event()
        self.setWindowTitle(tr(language, "bluetooth"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "bluetooth"), tr(language, "bluetooth_description"))
        self.status = frame.status
        layout = frame.content
        self.devices = QListWidget(self)
        self.devices.setObjectName("settings_applications")
        self.devices.installEventFilter(self)
        layout.addWidget(self.devices, stretch=1)
        top = QHBoxLayout()
        top.addStretch()
        self.power = self._button(top, "bluetooth_enable")
        self.scan = self._button(top, "bluetooth_scan")
        layout.addLayout(top)
        actions = QHBoxLayout()
        actions.addStretch()
        self.pair = self._button(actions, "bluetooth_pair")
        self.connect_device = self._button(actions, "bluetooth_connect")
        self.disconnect_device = self._button(actions, "bluetooth_disconnect")
        layout.addLayout(actions)
        self.refresh = self._button(frame.footer, "bluetooth_refresh")
        self.back = self._button(frame.footer, "back", back=True)
        self.devices.currentItemChanged.connect(self._selection_changed)
        self.power.clicked.connect(self._toggle_power)
        self.scan.clicked.connect(self._discover)
        self.refresh.clicked.connect(lambda: self._run("inspect", self._gateway.inspect))
        self.pair.clicked.connect(self._pair)
        self.connect_device.clicked.connect(lambda: self._device_action("connect"))
        self.disconnect_device.clicked.connect(lambda: self._device_action("disconnect"))
        self.back.clicked.connect(self.reject)
        self._agent.on_request(self._request_pairing_input)
        self._agent.on_notice(self._show_pairing_notice)
        self._agent.on_cancel(self._cancel_prompt)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        self._run("inspect", self._gateway.inspect)

    def _button(self, layout: QHBoxLayout, key: str, *, back: bool = False) -> QPushButton:
        button = add_action_button(self, layout, tr(self._language, key), back=back)
        button.installEventFilter(self)
        return button

    def _run(self, kind: str, operation: Callable[[], BluetoothState]) -> None:
        if self._busy is not None or self._closed:
            return
        self._busy = kind
        set_status(self.status, tr(self._language, "wifi_working"))
        self._set_controls()

        def execute() -> None:
            if self._closed:
                self._busy = None
                self._agent.close()
                return
            try:
                result = operation()
            except BluetoothError as error:
                if not self._closed:
                    self._show_error(str(error))
            except Exception:
                _LOGGER.exception("Unexpected Bluetooth operation failure")
                if not self._closed:
                    self._show_error(tr(self._language, "bluetooth_unavailable"))
            else:
                if not self._closed:
                    self._show_state(result)
            finally:
                self._busy = None
                if kind == "pair":
                    self._pairing_path = None
                    self._agent.set_target(None)
                if self._closed:
                    self._agent.close()
                else:
                    self._set_controls()
                    self._ensure_visible_focus()

        QTimer.singleShot(0, execute)

    def _show_state(self, state: BluetoothState) -> None:
        self._state = state
        selected = self._selected()
        with QSignalBlocker(self.devices):
            self.devices.clear()
            for device in state.devices:
                status = tr(self._language, "bluetooth_connected" if device.connected else "bluetooth_paired" if device.paired else "bluetooth_available")
                item = QListWidgetItem(f"{device.name} - {status}", self.devices)
                item.setData(Qt.ItemDataRole.UserRole, device)
                if selected is not None and device.path == selected.path:
                    self.devices.setCurrentItem(item)
            if self.devices.currentRow() < 0 and state.devices:
                self.devices.setCurrentRow(0)
        set_status(self.status, tr(self._language, "bluetooth_select" if state.powered else "bluetooth_off"))
        self.power.setText(tr(self._language, "bluetooth_disable" if state.powered else "bluetooth_enable"))
        if state.devices:
            self.devices.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.power.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_error(self, message: str) -> None:
        set_status(self.status, tr(self._language, "bluetooth_error", message=message), error=True)

    def _ensure_visible_focus(self) -> None:
        """Keep focus on an enabled control after operations disable the focused one."""
        focus = self.focusWidget()
        if focus is not None and focus.isEnabled() and focus.isVisible():
            return
        if self.devices.isEnabled() and self.devices.count():
            self.devices.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        for button in (self.power, self.scan, self.refresh, self.back):
            if button.isEnabled():
                button.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    def _selected(self) -> BluetoothDevice | None:
        item = self.devices.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, BluetoothDevice) else None

    def _selection_changed(self, *_: object) -> None:
        self._set_controls()

    def _set_controls(self) -> None:
        ready = self._busy is None and self._state is not None
        powered = ready and self._state is not None and self._state.powered
        selected = self._selected()
        self.devices.setEnabled(powered)
        self.power.setEnabled(ready)
        self.scan.setEnabled(powered)
        self.refresh.setEnabled(self._busy is None)
        self.pair.setEnabled(bool(powered and selected and not selected.paired))
        self.connect_device.setEnabled(bool(powered and selected and selected.paired and not selected.connected))
        self.disconnect_device.setEnabled(bool(powered and selected and selected.connected))

    def _toggle_power(self) -> None:
        if self._state is not None:
            enabled = not self._state.powered
            self._run("power", lambda: self._gateway.set_power(enabled))

    def _discover(self) -> None:
        self._cancel_discovery.clear()
        self._run("discover", lambda: self._gateway.discover(self._cancel_discovery))

    def _pair(self) -> None:
        device = self._selected()
        if device is None or device.paired:
            return
        self._pairing_path = device.path
        self._agent.set_target(device.path)
        self._run("pair", lambda: self._gateway.pair(device.path))

    def _device_action(self, action: str) -> None:
        device = self._selected()
        if device is None:
            return
        operation = self._gateway.connect if action == "connect" else self._gateway.disconnect
        self._run(action, lambda: operation(device.path))

    def _request_pairing_input(self, request: PairingRequest) -> None:
        if self._closed or self._pair_dialog is not None:
            self._agent.respond(None)
            return
        if request.kind in {"pin", "passkey"}:
            dialog = WirelessKeyDialog(
                self._theme, self._language, self,
                title_key="bluetooth_enter_pin" if request.kind == "pin" else "bluetooth_enter_passkey",
                max_length=16 if request.kind == "pin" else 6,
            )
            self._pair_dialog = dialog

            def finish(result: int) -> None:
                text = dialog.entry.text()
                dialog.entry.clear()
                self._pair_dialog = None
                value: str | int | None = None
                if result == QDialog.DialogCode.Accepted:
                    if request.kind == "pin" and text.isprintable() and 1 <= len(text.encode("utf-8")) <= 16:
                        value = text
                    elif request.kind == "passkey" and text.isascii() and text.isdigit() and len(text) <= 6:
                        value = int(text)
                self._agent.respond(value)
                dialog.deleteLater()

            dialog.finished.connect(finish)
            dialog.open()
            return
        if request.kind not in {"confirmation", "authorization"}:
            self._agent.respond(None)
            return
        message = tr(
            self._language,
            "bluetooth_confirm_code" if request.kind == "confirmation" else "bluetooth_authorize",
            code=request.value,
        )
        dialog = ConfirmationDialog(
            tr(self._language, "bluetooth_pair"), message, tr(self._language, "continue"),
            tr(self._language, "cancel"), self._theme, self,
        )
        self._pair_dialog = dialog

        def finish_confirmation(result: int) -> None:
            self._pair_dialog = None
            self._agent.respond(result == QDialog.DialogCode.Accepted)
            dialog.deleteLater()

        dialog.finished.connect(finish_confirmation)
        dialog.open()

    def _show_pairing_notice(self, request: PairingRequest) -> None:
        if request.kind in {"display_pin", "display_passkey"}:
            set_status(self.status, tr(self._language, "bluetooth_enter_on_device", code=request.value))

    def _cancel_prompt(self) -> None:
        if self._pair_dialog is not None:
            self._pair_dialog.reject()

    def handle_input(self, event: InputEvent) -> None:
        """Route gamepad input to the active pairing dialog or device controls."""
        if not event.pressed:
            return
        if isinstance(self._pair_dialog, ConfirmationDialog):
            self._pair_dialog.handle_input(event)
            return
        if event.action is InputAction.BACK:
            if self._pair_dialog is not None:
                self._pair_dialog.reject()
            else:
                self.reject()
            return
        focus = QApplication.focusWidget() if self._pair_dialog is not None else self.focusWidget()
        if event.action is InputAction.ACTIVATE and isinstance(focus, QPushButton):
            focus.click()
            return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
            InputAction.ACTIVATE: Qt.Key.Key_Return,
        }
        key = keys.get(event.action)
        if focus is not None and key is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Navigate between the device list and available action buttons."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            groups = [
                [button for button in group if button.isEnabled() and button.isVisible()]
                for group in (
                    (self.power, self.scan),
                    (self.pair, self.connect_device, self.disconnect_device),
                    (self.refresh, self.back),
                )
            ]
            if watched is self.devices and event.key() == Qt.Key.Key_Down and self.devices.currentRow() == self.devices.count() - 1:
                first = next((group[0] for group in groups if group), None)
                if first is not None:
                    first.setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
            for group_index, group in enumerate(groups):
                if not isinstance(watched, QPushButton) or watched not in group:
                    continue
                index = group.index(watched)
                if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    step = -1 if event.key() == Qt.Key.Key_Left else 1
                    group[max(0, min(len(group) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
                if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    step = -1 if event.key() == Qt.Key.Key_Up else 1
                    for target_index in range(group_index + step, len(groups) if step > 0 else -1, step):
                        target = groups[target_index]
                        if target:
                            target[min(index, len(target) - 1)].setFocus(Qt.FocusReason.OtherFocusReason)
                            return True
                    if step < 0 and self.devices.isEnabled() and self.devices.count():
                        self.devices.setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._cancel_discovery.set()
        self._gateway.cancel_discovery()
        self._cancel_prompt()
        self._agent.respond(None)
        if self._pairing_path is None:
            self._agent.close()
            return
        path = self._pairing_path
        self._agent.set_target(None)

        def cancel_pairing() -> None:
            try:
                self._gateway.cancel_pairing(path)
            except BluetoothError as error:
                _LOGGER.warning("Unable to cancel Bluetooth pairing: %s", error)

        QTimer.singleShot(0, cancel_pairing)
