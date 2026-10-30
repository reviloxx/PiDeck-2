"""Controller-friendly wireless setup and masked on-screen key entry."""

from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
import logging
import string

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.network import NetworkError, NetworkGateway, WirelessNetwork
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .theme import apply_theme


_LOGGER = logging.getLogger(__name__)


class WirelessKeyDialog(QDialog):
    """Collect a wireless key using either a physical or on-screen keyboard."""

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget) -> None:
        """Create a masked key editor with controller-navigable character keys."""
        super().__init__(parent)
        self.setWindowTitle(tr(language, "wifi_key"))
        self.setModal(True)
        self._keys: list[QPushButton] = []
        self._shifted = False
        layout = QVBoxLayout(self)
        title = QLabel(tr(language, "wifi_key"), self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        self.entry = QLineEdit(self)
        self.entry.setObjectName("password_input")
        self.entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry.setMaxLength(64)
        self.entry.installEventFilter(self)
        layout.addWidget(self.entry)
        grid = QGridLayout()
        characters = string.ascii_lowercase + string.digits + "-_=+!@#$%^&*()[]{};:'\",.<>/?\\|`~"
        self._characters = characters
        for index, character in enumerate(characters):
            button = QPushButton(character, self)
            button.setObjectName("network_key")
            button.setMinimumWidth(38)
            button.clicked.connect(lambda checked=False, item=button: self.entry.insert(item.text()))
            button.installEventFilter(self)
            self._keys.append(button)
            grid.addWidget(button, index // 10, index % 10)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        for label, action in (
            (tr(language, "keyboard_shift"), self._shift),
            (tr(language, "keyboard_space"), lambda: self.entry.insert(" ")),
            (tr(language, "keyboard_delete"), self.entry.backspace),
            (tr(language, "cancel"), self.reject),
            (tr(language, "continue"), self.accept),
        ):
            button = QPushButton(label, self)
            button.setObjectName("settings_footer")
            button.clicked.connect(action)
            button.installEventFilter(self)
            self._keys.append(button)
            actions.addWidget(button)
        layout.addLayout(actions)
        apply_theme(self, theme)
        self._keys[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _shift(self) -> None:
        """Toggle uppercase letters without changing punctuation."""
        self._shifted = not self._shifted
        for button, character in zip(self._keys, self._characters):
            button.setText(character.upper() if self._shifted else character)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Move focus predictably between the on-screen keys."""
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        if watched is self.entry:
            if event.key() == Qt.Key.Key_Down:
                self._keys[0].setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept()
                return True
            return super().eventFilter(watched, event)
        if watched not in self._keys:
            return super().eventFilter(watched, event)
        index = self._keys.index(watched)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._keys[index].click()
            return True
        character_count = len(self._characters)
        key = event.key()
        target = index
        if key == Qt.Key.Key_Left:
            target = max(0, index - 1)
        elif key == Qt.Key.Key_Right:
            target = min(len(self._keys) - 1, index + 1)
        elif key == Qt.Key.Key_Up:
            if index < 10:
                self.entry.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            target = max(0, index - 10) if index < character_count else character_count - 1
        elif key == Qt.Key.Key_Down:
            target = min(index + 10, character_count) if index < character_count else index
        else:
            return super().eventFilter(watched, event)
        self._keys[target].setFocus(Qt.FocusReason.OtherFocusReason)
        return True


class NetworkWindow(QDialog):
    """Show wireless networks and perform slow network operations off the UI thread."""

    operation_finished = Signal(object, object)

    def __init__(
        self, gateway: NetworkGateway, theme: ThemeDefinition, language: str,
        parent: QWidget,
    ) -> None:
        """Build one network setup surface using an injected gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._theme = theme
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-network")
        self._closed = False
        self._key_dialog: WirelessKeyDialog | None = None
        self.setWindowTitle(tr(language, "network"))
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(56, 44, 56, 44)
        title = QLabel(tr(language, "network"), self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        description = QLabel(tr(language, "wifi_description"), self)
        description.setObjectName("settings_description")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel("", self)
        self.status.setObjectName("settings_description")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.networks = QListWidget(self)
        self.networks.setObjectName("settings_applications")
        self.networks.itemActivated.connect(self._activate)
        self.networks.installEventFilter(self)
        layout.addWidget(self.networks, stretch=1)
        actions = QHBoxLayout()
        self.rescan = QPushButton(tr(language, "wifi_rescan"), self)
        self.rescan.setObjectName("settings_footer")
        self.rescan.clicked.connect(self.scan)
        self.rescan.installEventFilter(self)
        self.back = QPushButton(tr(language, "back"), self)
        self.back.setObjectName("settings_back")
        self.back.clicked.connect(self.reject)
        self.back.installEventFilter(self)
        actions.addWidget(self.rescan)
        actions.addWidget(self.back)
        layout.addLayout(actions)
        apply_theme(self, theme)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        self.scan()

    def _submit(self, operation: str, function: Callable[..., object], *arguments: object) -> None:
        self.status.setText(tr(self._language, "wifi_working"))
        self.networks.setEnabled(False)
        self.rescan.setEnabled(False)
        future = self._executor.submit(function, *arguments)
        def finish(result: Future[object]) -> None:
            if not self._closed:
                self.operation_finished.emit(operation, result)

        future.add_done_callback(finish)

    def scan(self) -> None:
        """Refresh access points asynchronously."""
        self._submit("scan", self._gateway.scan)

    def _finish_operation(self, operation: str, future: Future) -> None:
        if self._closed:
            return
        self.networks.setEnabled(True)
        self.rescan.setEnabled(True)
        try:
            result = future.result()
        except NetworkError as error:
            self.status.setText(tr(self._language, "wifi_error", message=str(error)))
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception as error:
            _LOGGER.error("Unexpected network operation failure: %s", type(error).__name__)
            self.status.setText(tr(self._language, "wifi_error", message=tr(self._language, "wifi_unavailable")))
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if operation == "connect":
            self.status.setText(tr(self._language, "wifi_connected"))
            return
        self.networks.clear()
        for network in result:
            text = f"{network.ssid}  ({network.signal}%)"
            if network.connected:
                text += f" - {tr(self._language, 'wifi_connected')}"
            item = QListWidgetItem(text, self.networks)
            item.setData(Qt.ItemDataRole.UserRole, network)
        self.status.setText(tr(self._language, "wifi_select" if result else "wifi_empty"))
        if result:
            self.networks.setCurrentRow(0)
            self.networks.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)

    def _activate(self, item: QListWidgetItem) -> None:
        network = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(network, WirelessNetwork):
            self.status.setText(tr(self._language, "wifi_unavailable"))
            return
        if not network.supported:
            self.status.setText(tr(self._language, "wifi_unsupported"))
            return
        if not network.requires_key:
            self._submit("connect", self._gateway.connect, network, None)
            return
        dialog = WirelessKeyDialog(self._theme, self._language, self)
        self._key_dialog = dialog

        def finish(result: int) -> None:
            if result == QDialog.DialogCode.Accepted:
                key = dialog.entry.text()
                dialog.entry.clear()
                self._submit("connect", self._gateway.connect, network, key)
            else:
                dialog.entry.clear()
            self._key_dialog = None

        dialog.finished.connect(finish)
        dialog.open()

    def handle_input(self, event: InputEvent) -> None:
        """Route controller actions to the currently focused Qt widget."""
        if not event.pressed:
            return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
            InputAction.ACTIVATE: Qt.Key.Key_Return, InputAction.BACK: Qt.Key.Key_Escape,
        }
        if event.action is InputAction.BACK:
            if self._key_dialog is not None:
                self._key_dialog.reject()
            else:
                self.reject()
            return
        key = keys.get(event.action)
        focus = QApplication.focusWidget()
        if event.action is InputAction.ACTIVATE:
            if isinstance(focus, QPushButton):
                focus.click()
                return
            if focus is self.networks and self.networks.currentItem() is not None:
                self._activate(self.networks.currentItem())
                return
        if key is not None and focus is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Move between network results and actions with directional input."""
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        key = event.key()
        if watched is self.networks and key == Qt.Key.Key_Down:
            if self.networks.currentRow() == self.networks.count() - 1:
                self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        if watched in (self.rescan, self.back):
            if key == Qt.Key.Key_Up and self.networks.count():
                self.networks.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.back if watched is self.rescan else self.rescan
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, result: int) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
