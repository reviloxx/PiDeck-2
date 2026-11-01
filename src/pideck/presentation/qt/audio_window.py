"""Controller-friendly system audio controls backed by an injected gateway."""

from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from pideck.application.ports.audio import AudioError, AudioGateway, AudioState
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_panel import create_system_panel
from .theme import apply_theme
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)


class AudioWindow(WallpaperDialog):
    """Inspect and adjust host audio without blocking the Qt event loop."""

    operation_finished = Signal(object)

    def __init__(
        self, gateway: AudioGateway, theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create an audio settings dialog around a platform audio gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-audio")
        self._closed = False
        self._popup_initial_index: int | None = None
        self._focus_before_operation: QWidget | None = None
        self.setWindowTitle(tr(language, "audio"))
        self.setModal(True)
        layout = create_system_panel(self, max_height=600)
        title = QLabel(tr(language, "audio"), self)
        title.setObjectName("settings_heading")
        layout.addWidget(title)
        self.status = QLabel("", self)
        self.status.setObjectName("settings_description")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        output_label = QLabel(tr(language, "audio_output"), self)
        output_label.setObjectName("settings_label")
        layout.addWidget(output_label)
        self.output = QComboBox(self)
        self.output.setObjectName("settings_control")
        self.output.installEventFilter(self)
        layout.addWidget(self.output)
        self.volume_label = QLabel("", self)
        self.volume_label.setObjectName("settings_label")
        layout.addWidget(self.volume_label)
        self.volume = QSlider(Qt.Orientation.Horizontal, self)
        self.volume.setObjectName("system_volume")
        self.volume.setRange(0, 100)
        self.volume.setSingleStep(5)
        self.volume.installEventFilter(self)
        layout.addWidget(self.volume)
        layout.addStretch()
        self.refresh = QPushButton(tr(language, "audio_refresh"), self)
        self.refresh.setObjectName("settings_footer")
        self.refresh.installEventFilter(self)
        self.back = QPushButton(tr(language, "back"), self)
        self.back.setObjectName("settings_back")
        self.back.installEventFilter(self)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.refresh)
        actions.addWidget(self.back)
        layout.addLayout(actions)
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(200)
        self._volume_timer.timeout.connect(self._apply_volume)
        self.output.activated.connect(self._apply_output)
        self.volume.valueChanged.connect(self._volume_changed)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        self._refresh()

    def _submit(self, operation: Callable[[], AudioState]) -> None:
        self._focus_before_operation = self.focusWidget()
        self.status.setText(tr(self._language, "wifi_working"))
        self.output.setEnabled(False)
        self.volume.setEnabled(False)
        self.refresh.setEnabled(False)
        future = self._executor.submit(operation)

        def finish(result: Future[AudioState]) -> None:
            if not self._closed:
                self.operation_finished.emit(result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        self._volume_timer.stop()
        self._submit(self._gateway.inspect)

    def _finish_operation(self, future: Future[AudioState]) -> None:
        if self._closed:
            return
        self.refresh.setEnabled(True)
        try:
            state = future.result()
        except AudioError as error:
            self.status.setText(tr(self._language, "audio_error", message=str(error)))
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception:
            _LOGGER.exception("Unexpected audio operation failure")
            self.status.setText(tr(self._language, "audio_error", message=tr(self._language, "audio_unavailable")))
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        with QSignalBlocker(self.output), QSignalBlocker(self.volume):
            self.output.clear()
            for output in state.outputs:
                self.output.addItem(output.description, output.identifier)
            self.output.setCurrentIndex(self.output.findData(state.current))
            self.volume.setValue(state.volume)
        self.volume_label.setText(tr(self._language, "audio_volume", volume=state.volume))
        self.status.clear()
        self.output.setEnabled(True)
        self.volume.setEnabled(True)
        focus = self._focus_before_operation
        if focus not in (self.output, self.volume):
            focus = self.output
        focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_output(self, index: int) -> None:
        identifier = self.output.itemData(index)
        if type(identifier) is not int:
            self.status.setText(tr(self._language, "audio_unavailable"))
            return
        self._volume_timer.stop()

        def change() -> AudioState:
            self._gateway.set_output(identifier)
            return self._gateway.inspect()

        self._submit(change)

    def _volume_changed(self, value: int) -> None:
        self.volume_label.setText(tr(self._language, "audio_volume", volume=value))
        self._volume_timer.start()

    def _apply_volume(self) -> None:
        value = self.volume.value()

        def change() -> AudioState:
            self._gateway.set_volume(value)
            return self._gateway.inspect()

        self._submit(change)

    def handle_input(self, event: InputEvent) -> None:
        """Forward normalized gamepad events to the focused control."""
        if not event.pressed:
            return
        if self._popup_initial_index is not None:
            if event.action in (InputAction.UP, InputAction.DOWN):
                step = -1 if event.action is InputAction.UP else 1
                self.output.setCurrentIndex(max(0, min(self.output.count() - 1, self.output.currentIndex() + step)))
            elif event.action is InputAction.ACTIVATE:
                index = self.output.currentIndex()
                initial = self._popup_initial_index
                self._popup_initial_index = None
                self.output.hidePopup()
                if index != initial:
                    self._apply_output(index)
            elif event.action is InputAction.BACK:
                with QSignalBlocker(self.output):
                    self.output.setCurrentIndex(self._popup_initial_index)
                self._popup_initial_index = None
                self.output.hidePopup()
            return
        if event.action is InputAction.BACK:
            self.reject()
            return
        focus = self.focusWidget()
        if event.action is InputAction.ACTIVATE and focus is self.output:
            self._popup_initial_index = self.output.currentIndex()
            self.output.showPopup()
            return
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
        """Navigate between audio controls with vertical directional input."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if watched is self.output and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space,
            ):
                self.output.showPopup()
                return True
            if watched in (self.refresh, self.back) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.refresh if event.key() == Qt.Key.Key_Left else self.back
                if target.isEnabled():
                    target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            controls: list[QObject] = [control for control in (self.output, self.volume, self.refresh, self.back) if control.isEnabled()]
            if watched in controls and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                index = controls.index(watched)
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                target = controls[max(0, min(len(controls) - 1, index + step))]
                if isinstance(target, QWidget):
                    target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._volume_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
