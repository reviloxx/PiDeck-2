"""Controller-friendly wireless setup and masked on-screen key entry."""

from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
import logging

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.network import NetworkError, NetworkGateway, WiredLink, WirelessNetwork
from pideck.domain.keyboard_layout import DEFAULT_KEYBOARD_LAYOUT, KeyboardLayout
from pideck.domain.theme import ThemeDefinition

from .backspace_back import BACKSPACE_EDITS_TEXT
from .localization import tr
from .on_screen_keyboard import ON_SCREEN_COLUMNS, key_label, on_screen_characters, shifted
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme, icon_for_path
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)
_SUMMARY_ICON_SIZE = 64
_ROW_MARK_SIZE = 22


def _signal_bars(percent: int) -> str:
    """Return a compact 1–3 bar indicator for Wi-Fi signal strength."""
    if percent >= 66:
        return "▂▄█"
    if percent >= 33:
        return "▂▄▁"
    return "▂▁▁"


class NetworkLinkSummary(QFrame):
    """Non-focusable status card for the active link (Ethernet or Wi-Fi)."""

    def __init__(self, theme: ThemeDefinition, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._language = language
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("network_status_card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(20)
        self.icon = QLabel(self)
        self.icon.setObjectName("network_card_icon")
        self.icon.setFixedSize(_SUMMARY_ICON_SIZE, _SUMMARY_ICON_SIZE)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        self.title = QLabel(self)
        self.title.setObjectName("network_card_title")
        text.addWidget(self.title)
        self.status = QLabel(self)
        self.status.setObjectName("network_card_status")
        text.addWidget(self.status)
        self.detail = QLabel(self)
        self.detail.setObjectName("network_card_detail")
        text.addWidget(self.detail)
        layout.addLayout(text, stretch=1)

    def update_link(
        self,
        wired: WiredLink | None,
        networks: tuple[WirelessNetwork, ...],
        connected: bool,
    ) -> None:
        """Refresh icon, title, status, and optional IP detail from the latest link state."""
        if wired is not None:
            icon_key = "network_ethernet"
            self.title.setText(tr(self._language, "network_ethernet_label"))
            self.status.setText(tr(self._language, "system_info_link_connected"))
            address = wired.address or ""
            self.detail.setText(address)
            self.detail.setVisible(bool(address))
        else:
            icon_key = "network_wifi"
            active = next((network for network in networks if network.connected), None)
            if active is not None:
                self.title.setText(active.ssid)
                self.status.setText(tr(self._language, "system_info_link_connected"))
            elif connected:
                self.title.setText(tr(self._language, "wifi_connected"))
                self.status.setText(tr(self._language, "system_info_link_connected"))
            else:
                self.title.setText(tr(self._language, "network_disconnected"))
                self.status.setText(tr(self._language, "network_disconnected"))
            self.detail.clear()
            self.detail.hide()
        self.icon.setPixmap(
            icon_for_path(self._theme.icons.get(icon_key)).pixmap(
                _SUMMARY_ICON_SIZE, _SUMMARY_ICON_SIZE,
            )
        )


class WifiNetworkRow(QWidget):
    """Compact access-point row: SSID, optional lock, signal bars, connected mark."""

    def __init__(
        self,
        network: WirelessNetwork,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        if network.requires_key:
            lock = QLabel("🔒", self)
            lock.setObjectName("settings_label")
            lock.setFixedWidth(_ROW_MARK_SIZE)
            layout.addWidget(lock)
        self.label = QLabel(network.ssid, self)
        self.label.setObjectName("settings_body")
        layout.addWidget(self.label, stretch=1)
        bars = QLabel(_signal_bars(network.signal), self)
        bars.setObjectName("settings_label")
        layout.addWidget(bars)
        if network.connected:
            mark = QLabel("✓", self)
            mark.setObjectName("settings_label")
            mark.setFixedWidth(_ROW_MARK_SIZE)
            layout.addWidget(mark)


class WirelessKeyDialog(QDialog):
    """Collect a wireless key using either a physical or on-screen keyboard."""

    def __init__(
        self, theme: ThemeDefinition, language: str, parent: QWidget,
        *, title_key: str = "wifi_key", max_length: int = 64,
        keyboard_layout: KeyboardLayout = DEFAULT_KEYBOARD_LAYOUT,
    ) -> None:
        """Create a masked key editor with controller-navigable character keys."""
        super().__init__(parent)
        self.setWindowTitle(tr(language, title_key))
        self.setModal(True)
        self.setProperty(BACKSPACE_EDITS_TEXT, True)
        self._keys: list[QPushButton] = []
        self._shifted = False
        layout = QVBoxLayout(self)
        title = QLabel(tr(language, title_key), self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        self.entry = QLineEdit(self)
        self.entry.setObjectName("password_input")
        self.entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry.setMaxLength(max_length)
        self.entry.installEventFilter(self)
        layout.addWidget(self.entry)
        grid = QGridLayout()
        characters = on_screen_characters(keyboard_layout)
        self._characters = characters
        for index, character in enumerate(characters):
            button = QPushButton(key_label(character), self)
            button.setObjectName("network_key")
            button.setMinimumWidth(38)
            button.clicked.connect(lambda checked=False, position=index: self.entry.insert(self._character(position)))
            button.installEventFilter(self)
            self._keys.append(button)
            grid.addWidget(button, index // ON_SCREEN_COLUMNS, index % ON_SCREEN_COLUMNS)
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

    def _character(self, index: int) -> str:
        """Return the character typed by the key at ``index`` in the current shift state."""
        character = self._characters[index]
        return shifted(character) if self._shifted else character

    def _shift(self) -> None:
        """Toggle uppercase letters without changing punctuation."""
        self._shifted = not self._shifted
        for index, button in enumerate(self._keys[:len(self._characters)]):
            button.setText(key_label(self._character(index)))

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
        if event.key() == Qt.Key.Key_Backspace:
            self.entry.backspace()
            return True
        character_count = len(self._characters)
        key = event.key()
        target = index
        if key == Qt.Key.Key_Left:
            target = max(0, index - 1)
        elif key == Qt.Key.Key_Right:
            target = min(len(self._keys) - 1, index + 1)
        elif key == Qt.Key.Key_Up:
            if index < ON_SCREEN_COLUMNS:
                self.entry.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            target = max(0, index - ON_SCREEN_COLUMNS) if index < character_count else character_count - 1
        elif key == Qt.Key.Key_Down:
            target = min(index + ON_SCREEN_COLUMNS, character_count) if index < character_count else index
        else:
            return super().eventFilter(watched, event)
        self._keys[target].setFocus(Qt.FocusReason.OtherFocusReason)
        return True


class NetworkWindow(WallpaperDialog):
    """Show wireless networks and perform slow network operations off the UI thread."""

    operation_finished = Signal(object, object)

    def __init__(
        self, gateway: NetworkGateway, theme: ThemeDefinition, language: str,
        parent: QWidget, *, keyboard_layout: KeyboardLayout = DEFAULT_KEYBOARD_LAYOUT,
    ) -> None:
        """Build one network setup surface using an injected gateway."""
        super().__init__(parent)
        self._keyboard_layout = keyboard_layout
        self._gateway = gateway
        self._theme = theme
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-network")
        self._closed = False
        self._wired: WiredLink | None = None
        self._key_dialog: WirelessKeyDialog | None = None
        self.setWindowTitle(tr(language, "network"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "network"), "")
        self.status = frame.status
        layout = frame.content
        self.link_summary = NetworkLinkSummary(theme, language, self)
        layout.addWidget(self.link_summary)
        self.networks = QListWidget(self)
        self.networks.setObjectName("settings_applications")
        self.networks.itemActivated.connect(self._activate)
        self.networks.installEventFilter(self)
        layout.addWidget(self.networks, stretch=1)
        self.rescan = add_action_button(self, frame.footer, tr(language, "wifi_rescan"))
        self.rescan.clicked.connect(self.scan)
        self.rescan.installEventFilter(self)
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        self.back.clicked.connect(self.reject)
        self.back.installEventFilter(self)
        apply_theme(self, theme)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        self.scan()

    def _submit(self, operation: str, function: Callable[..., object], *arguments: object) -> None:
        set_status(self.status, tr(self._language, "wifi_working"))
        self.networks.setEnabled(False)
        self.rescan.setEnabled(False)
        future = self._executor.submit(function, *arguments)
        def finish(result: Future[object]) -> None:
            if not self._closed:
                self.operation_finished.emit(operation, result)

        future.add_done_callback(finish)

    def scan(self) -> None:
        """Refresh wired status and access points asynchronously."""
        def inspect() -> tuple[WiredLink | None, bool, tuple[WirelessNetwork, ...]]:
            return self._gateway.wired_link(), self._gateway.connected(), self._gateway.scan()

        self._submit("scan", inspect)

    def _finish_operation(self, operation: str, future: Future) -> None:
        if self._closed:
            return
        self.rescan.setEnabled(True)
        try:
            result = future.result()
        except NetworkError as error:
            self.networks.setEnabled(True)
            set_status(self.status, tr(self._language, "wifi_error", message=str(error)), error=True)
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception as error:
            _LOGGER.error("Unexpected network operation failure: %s", type(error).__name__)
            self.networks.setEnabled(True)
            set_status(self.status, tr(self._language, "wifi_error", message=tr(self._language, "wifi_unavailable")), error=True)
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if operation == "connect":
            set_status(self.status, "")
            self.scan()
            return
        wired, connected, networks = result
        self._wired = wired
        self.link_summary.update_link(wired, networks, connected)
        set_status(self.status, "")
        self.networks.clear()
        if wired is not None:
            self.networks.hide()
            self.networks.setEnabled(False)
            self.networks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.networks.show()
        self.networks.setEnabled(True)
        for network in networks:
            item = QListWidgetItem(self.networks)
            item.setData(Qt.ItemDataRole.UserRole, network)
            row = WifiNetworkRow(network, self._language, parent=self.networks)
            item.setSizeHint(row.sizeHint())
            self.networks.setItemWidget(item, row)
        self.networks.setFocusPolicy(Qt.FocusPolicy.StrongFocus if networks else Qt.FocusPolicy.NoFocus)
        if networks:
            self.networks.setCurrentRow(0)
            self.networks.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.rescan.setFocus(Qt.FocusReason.OtherFocusReason)

    def _activate(self, item: QListWidgetItem) -> None:
        if self._wired is not None:
            return
        network = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(network, WirelessNetwork):
            set_status(self.status, tr(self._language, "wifi_unavailable"), error=True)
            return
        if not network.supported:
            set_status(self.status, tr(self._language, "wifi_unsupported"), error=True)
            return
        if not network.requires_key:
            self._submit("connect", self._gateway.connect, network, None)
            return
        dialog = WirelessKeyDialog(
            self._theme, self._language, self, keyboard_layout=self._keyboard_layout,
        )
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
            if (
                key == Qt.Key.Key_Up
                and self.networks.isVisible()
                and self.networks.count()
                and self.networks.isEnabled()
            ):
                self.networks.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                # Never let Qt's focus chain move into an empty, invisible list selection.
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.back if watched is self.rescan else self.rescan
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, result: int) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
