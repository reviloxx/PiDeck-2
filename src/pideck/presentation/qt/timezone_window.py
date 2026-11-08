"""Controller-friendly host timezone selection with region and city controls."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.timezone import TimezoneError, TimezoneGateway
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import SettingsComboBox, apply_theme, configure_combo_box
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)


def split_timezone(timezone: str) -> tuple[str, str]:
    """Split an IANA identifier into a region and city label."""
    if "/" not in timezone:
        return timezone, timezone
    region, city = timezone.split("/", 1)
    return region, city


class TimezoneWindow(WallpaperDialog):
    """Choose a host timezone without blocking the Qt event loop."""

    operation_finished = Signal(str, object)

    def __init__(
        self, gateway: TimezoneGateway, theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create timezone settings using an injected platform gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-timezone")
        self._closed = False
        self._by_region: dict[str, list[str]] = {}
        self._current = ""
        self._popup: tuple[QComboBox, int] | None = None
        self._notice: str | None = None
        self.setWindowTitle(tr(language, "timezone"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "timezone"), tr(language, "timezone_description"))
        self.status = frame.status
        layout = frame.content
        region_label = QLabel(tr(language, "timezone_region"), self)
        region_label.setObjectName("settings_label")
        layout.addWidget(region_label)
        self.region = SettingsComboBox(self)
        self.region.setObjectName("settings_control")
        configure_combo_box(self.region)
        layout.addWidget(self.region)
        city_label = QLabel(tr(language, "timezone_city"), self)
        city_label.setObjectName("settings_label")
        layout.addWidget(city_label)
        self.city = SettingsComboBox(self)
        self.city.setObjectName("settings_control")
        configure_combo_box(self.city)
        layout.addWidget(self.city)
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self.apply = add_action_button(self, apply_row, tr(language, "timezone_apply"))
        layout.addLayout(apply_row)
        layout.addStretch()
        self.refresh = add_action_button(self, frame.footer, tr(language, "timezone_refresh"))
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        for control in (self.region, self.city, self.apply, self.refresh, self.back):
            control.installEventFilter(self)
        self.region.currentIndexChanged.connect(self._select_region)
        self.apply.clicked.connect(self._apply)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        self._refresh()

    def _submit(self, kind: str, operation: Callable[[], object]) -> None:
        set_status(self.status, tr(self._language, "wifi_working"))
        for control in (self.region, self.city, self.apply, self.refresh):
            control.setEnabled(False)
        future = self._executor.submit(operation)

        def finish(result: Future[object]) -> None:
            if not self._closed:
                self.operation_finished.emit(kind, result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        def load() -> tuple[str, tuple[str, ...]]:
            return self._gateway.current_timezone(), self._gateway.list_timezones()

        self._submit("inspect", load)

    def _finish_operation(self, kind: str, future: Future[object]) -> None:
        if self._closed:
            return
        self.refresh.setEnabled(True)
        try:
            result = future.result()
        except TimezoneError as error:
            set_status(self.status, tr(self._language, "timezone_error", message=str(error)), error=True)
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception:
            _LOGGER.exception("Unexpected timezone operation failure")
            set_status(
                self.status,
                tr(self._language, "timezone_error", message=tr(self._language, "timezone_unavailable")),
                error=True,
            )
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if kind == "apply":
            self._notice = tr(self._language, "timezone_saved")
            self._refresh()
            return
        if not isinstance(result, tuple) or len(result) != 2:
            raise TypeError("Timezone gateway returned an invalid inspect result")
        current, zones = result
        if not isinstance(current, str) or not isinstance(zones, tuple):
            raise TypeError("Timezone gateway returned an invalid inspect result")
        self._current = current
        self._by_region = {}
        for zone in zones:
            if not isinstance(zone, str):
                raise TypeError("Timezone gateway returned an invalid inspect result")
            region, _city = split_timezone(zone)
            self._by_region.setdefault(region, []).append(zone)
        with QSignalBlocker(self.region):
            self.region.clear()
            for region in sorted(self._by_region):
                self.region.addItem(region, region)
        current_region, _current_city = split_timezone(current)
        region_index = self.region.findData(current_region)
        if region_index < 0 and self.region.count():
            region_index = 0
        if region_index >= 0:
            self.region.setCurrentIndex(region_index)
            self._select_region(region_index, preferred_timezone=current)
        else:
            with QSignalBlocker(self.city):
                self.city.clear()
        enabled = bool(self._by_region)
        self.region.setEnabled(enabled)
        self.city.setEnabled(enabled)
        self.apply.setEnabled(enabled)
        set_status(self.status, self._notice or "")
        self._notice = None
        if enabled:
            self.region.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)

    def _select_region(self, index: int, preferred_timezone: str | None = None) -> None:
        with QSignalBlocker(self.city):
            self.city.clear()
            region = self.region.itemData(index)
            if not isinstance(region, str):
                return
            for timezone in self._by_region.get(region, []):
                _region, city = split_timezone(timezone)
                self.city.addItem(city.replace("_", " "), timezone)
            selected = preferred_timezone or self._current
            city_index = self.city.findData(selected)
            if city_index >= 0:
                self.city.setCurrentIndex(city_index)

    def _apply(self) -> None:
        timezone = self.city.currentData()
        if not isinstance(timezone, str) or not timezone:
            set_status(self.status, tr(self._language, "timezone_unavailable"), error=True)
            return
        self._submit("apply", lambda: self._gateway.set_timezone(timezone))

    def handle_input(self, event: InputEvent) -> None:
        """Navigate region, city and actions with a gamepad."""
        if not event.pressed:
            return
        if self._popup is not None:
            combo, initial = self._popup
            if event.action in (InputAction.UP, InputAction.DOWN):
                step = -1 if event.action is InputAction.UP else 1
                combo.setCurrentIndex(max(0, min(combo.count() - 1, combo.currentIndex() + step)))
            elif event.action in (InputAction.ACTIVATE, InputAction.BACK):
                if event.action is InputAction.BACK:
                    combo.setCurrentIndex(initial)
                self._popup = None
                combo.hidePopup()
            return
        if event.action is InputAction.BACK:
            self.reject()
            return
        focus = self.focusWidget()
        if event.action is InputAction.ACTIVATE:
            if isinstance(focus, QComboBox):
                self._popup = (focus, focus.currentIndex())
                focus.showPopup()
                return
            if isinstance(focus, QPushButton):
                focus.click()
                return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left, InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up, InputAction.DOWN: Qt.Key.Key_Down,
        }
        key = keys.get(event.action)
        if focus is not None and key is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Navigate available controls vertically with the keyboard or gamepad."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if isinstance(watched, QComboBox) and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space,
            ):
                watched.showPopup()
                return True
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                buttons = [button for button in (self.refresh, self.back) if button.isEnabled() and button.isVisible()]
                if isinstance(watched, QPushButton) and watched in buttons:
                    index = buttons.index(watched)
                    step = -1 if event.key() == Qt.Key.Key_Left else 1
                    buttons[max(0, min(len(buttons) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
            controls: list[QWidget] = [
                control
                for control in (self.region, self.city, self.apply, self.refresh, self.back)
                if control.isEnabled() and control.isVisible()
            ]
            if isinstance(watched, QWidget) and watched in controls and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                index = controls.index(watched)
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                controls[max(0, min(len(controls) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)
