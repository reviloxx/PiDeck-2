"""Controller-friendly X11 display selection with timed confirmation."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import time

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from pideck.application.ports.display import DisplayChange, DisplayError, DisplayGateway, DisplayMode, DisplayOutput
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import SettingsComboBox, apply_theme, configure_combo_box
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)


class DisplayWindow(WallpaperDialog):
    """Choose an output mode and require confirmation before persisting it."""

    operation_finished = Signal(str, object)

    def __init__(
        self, gateway: DisplayGateway, theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create display settings using an injected platform gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-display")
        self._closed = False
        self._outputs: tuple[DisplayOutput, ...] = ()
        self._pending: DisplayChange | None = None
        self._popup: tuple[QComboBox, int] | None = None
        self._notice: str | None = None
        self.setWindowTitle(tr(language, "display"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "display"), tr(language, "display_description"))
        self.status = frame.status
        layout = frame.content
        output_label = QLabel(tr(language, "display_output"), self)
        output_label.setObjectName("settings_label")
        layout.addWidget(output_label)
        self.output = SettingsComboBox(self)
        self.output.setObjectName("settings_control")
        configure_combo_box(self.output)
        layout.addWidget(self.output)
        mode_label = QLabel(tr(language, "display_mode"), self)
        mode_label.setObjectName("settings_label")
        layout.addWidget(mode_label)
        self.mode = SettingsComboBox(self)
        self.mode.setObjectName("settings_control")
        configure_combo_box(self.mode)
        layout.addWidget(self.mode)
        mode_actions = QHBoxLayout()
        mode_actions.addStretch()
        self.apply = add_action_button(self, mode_actions, tr(language, "display_apply"))
        self.keep = add_action_button(self, mode_actions, tr(language, "display_keep"))
        self.revert = add_action_button(self, mode_actions, tr(language, "display_revert"))
        layout.addLayout(mode_actions)
        self.brightness_label = QLabel(tr(language, "display_brightness", brightness=100), self)
        self.brightness_label.setObjectName("settings_label")
        layout.addWidget(self.brightness_label)
        self.brightness = QSlider(Qt.Orientation.Horizontal, self)
        self.brightness.setObjectName("system_volume")
        self.brightness.setRange(10, 100)
        self.brightness.setSingleStep(5)
        self.brightness.setPageStep(10)
        self.brightness.setValue(100)
        layout.addWidget(self.brightness)
        layout.addStretch()
        self.refresh = add_action_button(self, frame.footer, tr(language, "display_refresh"))
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        for control in (
            self.output, self.mode, self.apply, self.keep, self.revert,
            self.brightness, self.refresh, self.back,
        ):
            control.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._resolve(False))
        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(200)
        self._brightness_timer.timeout.connect(self._apply_brightness)
        self.output.currentIndexChanged.connect(self._select_output)
        self.apply.clicked.connect(self._apply)
        self.keep.clicked.connect(lambda: self._resolve(True))
        self.revert.clicked.connect(lambda: self._resolve(False))
        self.brightness.valueChanged.connect(self._brightness_changed)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        self.keep.hide()
        self.revert.hide()
        apply_theme(self, theme)
        self._refresh()

    def _submit(self, kind: str, operation: Callable[[], object]) -> None:
        set_status(self.status, tr(self._language, "wifi_working"))
        for control in (self.output, self.mode, self.apply, self.keep, self.revert, self.brightness, self.refresh):
            control.setEnabled(False)
        future = self._executor.submit(operation)

        def finish(result: Future[object]) -> None:
            if self._closed:
                if kind == "begin":
                    try:
                        change = result.result()
                        if isinstance(change, DisplayChange):
                            change.cancel()
                    except DisplayError:
                        _LOGGER.exception("Unable to revert display after closing settings")
                return
            self.operation_finished.emit(kind, result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        self._brightness_timer.stop()
        self._submit("inspect", self._gateway.inspect)

    def _sync_brightness_control(self) -> None:
        """Load persisted brightness into the slider without re-applying it."""
        try:
            value = self._gateway.get_brightness()
        except DisplayError:
            value = 100
        with QSignalBlocker(self.brightness):
            self.brightness.setValue(value)
        self.brightness_label.setText(tr(self._language, "display_brightness", brightness=value))
        self.brightness.setEnabled(True)

    def _brightness_changed(self, value: int) -> None:
        self.brightness_label.setText(tr(self._language, "display_brightness", brightness=value))
        self._brightness_timer.start()

    def _apply_brightness(self) -> None:
        value = self.brightness.value()
        self._submit("brightness", lambda: self._gateway.set_brightness(value))

    def _finish_operation(self, kind: str, future: Future[object]) -> None:
        if self._closed:
            return
        self.refresh.setEnabled(True)
        if kind in {"confirm", "cancel"}:
            self.apply.show()
            self.keep.hide()
            self.revert.hide()
        try:
            result = future.result()
        except DisplayError as error:
            set_status(self.status, tr(self._language, "display_error", message=str(error)), error=True)
            self.brightness.setEnabled(True)
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception:
            _LOGGER.exception("Unexpected display operation failure")
            set_status(self.status, tr(self._language, "display_error", message=tr(self._language, "display_unavailable")), error=True)
            self.brightness.setEnabled(True)
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if kind == "brightness":
            self._sync_brightness_control()
            set_status(self.status, "")
            self.brightness.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if kind == "begin":
            if not isinstance(result, DisplayChange):
                raise TypeError("Display gateway returned an invalid transaction")
            self._pending = result
            self.apply.hide()
            self.keep.show()
            self.revert.show()
            self.keep.setEnabled(True)
            self.revert.setEnabled(True)
            self.brightness.setEnabled(True)
            self.refresh.setEnabled(False)
            set_status(self.status, tr(self._language, "display_confirm"))
            self.revert.setFocus(Qt.FocusReason.OtherFocusReason)
            self._timer.start(max(0, int((result.deadline - time.monotonic()) * 1000)))
            return
        if kind in {"confirm", "cancel"}:
            self._notice = tr(self._language, "display_saved" if kind == "confirm" else "display_reverted")
            self._refresh()
            return
        if not isinstance(result, tuple) or not all(isinstance(item, DisplayOutput) for item in result):
            raise TypeError("Display gateway returned invalid outputs")
        self._outputs = result
        with QSignalBlocker(self.output):
            self.output.clear()
            for output in self._outputs:
                self.output.addItem(output.name, output.name)
        self._select_output(self.output.currentIndex())
        self.output.setEnabled(bool(self._outputs))
        self.mode.setEnabled(bool(self._outputs))
        self.apply.setEnabled(bool(self._outputs))
        self._sync_brightness_control()
        set_status(self.status, self._notice or "")
        self._notice = None
        if self._outputs:
            self.output.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)

    def _select_output(self, index: int) -> None:
        with QSignalBlocker(self.mode):
            self.mode.clear()
            if not 0 <= index < len(self._outputs):
                return
            output = self._outputs[index]
            for mode in output.modes:
                self.mode.addItem(f"{mode.resolution} / {mode.rate} Hz", mode)
            self.mode.setCurrentIndex(self.mode.findData(output.current))

    def _apply(self) -> None:
        output = self.output.currentData()
        mode = self.mode.currentData()
        if not isinstance(output, str) or not isinstance(mode, DisplayMode):
            set_status(self.status, tr(self._language, "display_unavailable"), error=True)
            return
        self._submit("begin", lambda: self._gateway.begin(output, mode))

    def _resolve(self, confirm: bool) -> None:
        change = self._pending
        if change is None:
            return
        self._pending = None
        self._timer.stop()
        self._submit("confirm" if confirm else "cancel", change.confirm if confirm else change.cancel)

    def handle_input(self, event: InputEvent) -> None:
        """Navigate modes, confirmation and actions with a gamepad."""
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
                if isinstance(watched, QSlider) and watched is self.brightness:
                    return False
                for group in ((self.apply, self.keep, self.revert), (self.refresh, self.back)):
                    buttons = [button for button in group if button.isEnabled() and button.isVisible()]
                    if isinstance(watched, QPushButton) and watched in buttons:
                        index = buttons.index(watched)
                        step = -1 if event.key() == Qt.Key.Key_Left else 1
                        buttons[max(0, min(len(buttons) - 1, index + step))].setFocus(Qt.FocusReason.OtherFocusReason)
                        return True
            controls: list[QWidget] = [
                control for control in (
                    self.output, self.mode, self.apply, self.keep, self.revert,
                    self.brightness, self.refresh, self.back,
                )
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
        self._timer.stop()
        self._brightness_timer.stop()
        if self._pending is not None:
            change = self._pending
            self._pending = None
            future = self._executor.submit(change.cancel)

            def report(result: Future[None]) -> None:
                try:
                    result.result()
                except DisplayError:
                    _LOGGER.exception("Unable to revert display after closing settings")

            future.add_done_callback(report)
        self._executor.shutdown(wait=False, cancel_futures=False)
