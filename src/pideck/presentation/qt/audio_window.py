"""Controller-friendly system audio controls backed by an injected gateway."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pideck.application.ports.audio import (
    AppPcmLayout,
    AudioError,
    AudioGateway,
    AudioOutput,
    AudioState,
    HdmiAudioMode,
)
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition
from pideck.infrastructure.audio_preferences import AudioPreferencesStore

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import SettingsComboBox, apply_theme, configure_combo_box
from .wallpaper import WallpaperDialog

_LOGGER = logging.getLogger(__name__)
_LAYOUT_DEFAULT = ""


class AudioWindow(WallpaperDialog):
    """Inspect and adjust host audio without blocking the Qt event loop."""

    operation_finished = Signal(object)

    def __init__(
        self,
        gateway: AudioGateway,
        preferences: AudioPreferencesStore,
        theme: ThemeDefinition,
        language: str,
        parent: QWidget,
        applications: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Create an audio settings dialog around a platform audio gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._preferences = preferences
        self._language = language
        self._hdmi_mode = HdmiAudioMode.SURROUND
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-audio")
        self._closed = False
        self._popup_combo: SettingsComboBox | None = None
        self._popup_initial_index: int | None = None
        self._focus_before_operation: QWidget | None = None
        self._outputs: tuple[AudioOutput, ...] = ()
        self._app_layouts: dict[str, SettingsComboBox] = {}
        self.setWindowTitle(tr(language, "audio"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "audio"), tr(language, "audio_description"))
        self.status = frame.status

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("settings_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame.content.addWidget(self.scroll, stretch=1)
        page = QWidget(self.scroll)
        self.scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 14, 0)
        layout.setSpacing(14)

        mode_label = QLabel(tr(language, "audio_hdmi_mode"), page)
        mode_label.setObjectName("settings_label")
        layout.addWidget(mode_label)
        self.mode = SettingsComboBox(page)
        self.mode.setObjectName("settings_control")
        configure_combo_box(self.mode)
        self.mode.addItem(tr(language, "audio_hdmi_mode_surround"), HdmiAudioMode.SURROUND.value)
        self.mode.addItem(tr(language, "audio_hdmi_mode_bitstream"), HdmiAudioMode.BITSTREAM.value)
        self.mode.installEventFilter(self)
        layout.addWidget(self.mode)

        output_label = QLabel(tr(language, "audio_output"), page)
        output_label.setObjectName("settings_label")
        layout.addWidget(output_label)
        self.output = SettingsComboBox(page)
        self.output.setObjectName("settings_control")
        configure_combo_box(self.output)
        self.output.installEventFilter(self)
        layout.addWidget(self.output)

        self.volume_label = QLabel("", page)
        self.volume_label.setObjectName("settings_label")
        layout.addWidget(self.volume_label)
        self.volume = QSlider(Qt.Orientation.Horizontal, page)
        self.volume.setObjectName("system_volume")
        self.volume.setRange(0, 100)
        self.volume.setSingleStep(5)
        self.volume.installEventFilter(self)
        layout.addWidget(self.volume)

        if applications:
            apps_label = QLabel(tr(language, "audio_applications"), page)
            apps_label.setObjectName("settings_label")
            layout.addWidget(apps_label)
            for app_id, app_name in applications:
                app_label = QLabel(app_name, page)
                app_label.setObjectName("settings_label")
                layout.addWidget(app_label)
                combo = SettingsComboBox(page)
                combo.setObjectName("settings_control")
                configure_combo_box(combo)
                combo.addItem(tr(language, "audio_layout_default"), _LAYOUT_DEFAULT)
                combo.addItem(tr(language, "audio_layout_21"), AppPcmLayout.PCM_21.value)
                combo.addItem(tr(language, "audio_layout_51"), AppPcmLayout.PCM_51.value)
                combo.installEventFilter(self)
                combo.activated.connect(
                    lambda index, identifier=app_id, control=combo: self._apply_layout(
                        identifier, control, index,
                    )
                )
                layout.addWidget(combo)
                self._app_layouts[app_id] = combo

        layout.addStretch()

        self.refresh = add_action_button(self, frame.footer, tr(language, "audio_refresh"))
        self.refresh.installEventFilter(self)
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        self.back.installEventFilter(self)
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(200)
        self._volume_timer.timeout.connect(self._apply_volume)
        self.mode.activated.connect(self._apply_mode)
        self.output.activated.connect(self._apply_output)
        self.volume.valueChanged.connect(self._volume_changed)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        self._load_application_layouts()
        self._refresh()

    def _controls(self) -> list[QWidget]:
        controls: list[QWidget] = [self.mode, self.output, self.volume]
        controls.extend(self._app_layouts.values())
        controls.extend(control for control in (self.refresh, self.back) if control.isEnabled())
        return [control for control in controls if control.isEnabled() or control is self.volume]

    def _ensure_visible(self, control: QWidget) -> None:
        if control in (self.refresh, self.back):
            return
        self.scroll.ensureWidgetVisible(control)

    def _submit(self, operation: Callable[[], AudioState]) -> None:
        self._focus_before_operation = self.focusWidget()
        set_status(self.status, tr(self._language, "wifi_working"))
        self.mode.setEnabled(False)
        self.output.setEnabled(False)
        self.volume.setEnabled(False)
        self.refresh.setEnabled(False)
        for combo in self._app_layouts.values():
            combo.setEnabled(False)
        future = self._executor.submit(operation)

        def finish(result: Future[AudioState]) -> None:
            if not self._closed:
                self.operation_finished.emit(result)

        future.add_done_callback(finish)

    def _refresh(self) -> None:
        self._volume_timer.stop()

        def inspect() -> AudioState:
            try:
                self._hdmi_mode = self._preferences.load().hdmi_mode
            except AudioError:
                self._hdmi_mode = HdmiAudioMode.SURROUND
            return self._gateway.inspect(hdmi_mode=self._hdmi_mode)

        self._submit(inspect)

    def _load_application_layouts(self) -> None:
        try:
            saved = self._preferences.load()
        except AudioError:
            saved_layouts: dict[str, AppPcmLayout] = {}
        else:
            saved_layouts = dict(saved.applications)
        for app_id, combo in self._app_layouts.items():
            layout = saved_layouts.get(app_id)
            value = layout.value if layout is not None else _LAYOUT_DEFAULT
            with QSignalBlocker(combo):
                index = combo.findData(value)
                combo.setCurrentIndex(index if index >= 0 else 0)

    def _finish_operation(self, future: Future[AudioState]) -> None:
        if self._closed:
            return
        self.refresh.setEnabled(True)
        try:
            state = future.result()
        except AudioError as error:
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            for combo in self._app_layouts.values():
                combo.setEnabled(True)
            return
        except Exception:
            _LOGGER.exception("Unexpected audio operation failure")
            set_status(
                self.status,
                tr(self._language, "audio_error", message=tr(self._language, "audio_unavailable")),
                error=True,
            )
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            for combo in self._app_layouts.values():
                combo.setEnabled(True)
            return
        self._outputs = state.outputs
        try:
            saved = self._preferences.load()
        except AudioError as error:
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            saved_default = None
            self._hdmi_mode = HdmiAudioMode.SURROUND
        else:
            saved_default = saved.default
            self._hdmi_mode = saved.hdmi_mode
            set_status(self.status, "")
        with QSignalBlocker(self.mode), QSignalBlocker(self.output), QSignalBlocker(self.volume):
            mode_index = self.mode.findData(self._hdmi_mode.value)
            self.mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            self.output.clear()
            for output in state.outputs:
                self.output.addItem(output.description, output.identifier)
            current = (
                saved_default
                if saved_default in {output.identifier for output in state.outputs}
                else state.current
            )
            self.output.setCurrentIndex(max(0, self.output.findData(current)))
            self.volume.setValue(state.volume)
        self.volume_label.setText(tr(self._language, "audio_volume", volume=state.volume))
        self.mode.setEnabled(True)
        self.output.setEnabled(True)
        self.volume.setEnabled(True)
        for combo in self._app_layouts.values():
            combo.setEnabled(True)
        self._load_application_layouts()
        focus = self._focus_before_operation
        if focus not in self._controls():
            focus = self.mode
        focus.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_visible(focus)

    def _apply_mode(self, index: int) -> None:
        value = self.mode.itemData(index)
        if not isinstance(value, str):
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        try:
            mode = HdmiAudioMode(value)
        except ValueError:
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        self._volume_timer.stop()

        def change() -> AudioState:
            self._preferences.set_hdmi_mode(mode)
            self._hdmi_mode = mode
            current = self.output.currentData()
            if isinstance(current, str) and current:
                self._gateway.set_output(current, hdmi_mode=mode)
            else:
                self._gateway.ensure_hdmi(hdmi_mode=mode)
            return self._gateway.inspect(hdmi_mode=mode)

        self._submit(change)

    def _apply_output(self, index: int) -> None:
        identifier = self.output.itemData(index)
        if not isinstance(identifier, str) or not identifier:
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        self._volume_timer.stop()
        mode = self._hdmi_mode

        def change() -> AudioState:
            self._gateway.set_output(identifier, hdmi_mode=mode)
            self._preferences.set_default(identifier)
            return self._gateway.inspect(hdmi_mode=mode)

        self._submit(change)

    def _apply_layout(self, app_id: str, combo: SettingsComboBox, index: int) -> None:
        value = combo.itemData(index)
        if not isinstance(value, str):
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        try:
            layout = None if value == _LAYOUT_DEFAULT else AppPcmLayout(value)
            self._preferences.set_application_layout(app_id, layout)
        except (AudioError, ValueError) as error:
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            self._load_application_layouts()
            return
        set_status(self.status, "")

    def _volume_changed(self, value: int) -> None:
        self.volume_label.setText(tr(self._language, "audio_volume", volume=value))
        self._volume_timer.start()

    def _apply_volume(self) -> None:
        value = self.volume.value()
        mode = self._hdmi_mode

        def change() -> AudioState:
            self._gateway.set_volume(value)
            return self._gateway.inspect(hdmi_mode=mode)

        self._submit(change)

    def handle_input(self, event: InputEvent) -> None:
        """Forward normalized gamepad events to the focused control."""
        if not event.pressed:
            return
        if self._popup_combo is not None and self._popup_initial_index is not None:
            combo = self._popup_combo
            if event.action in (InputAction.UP, InputAction.DOWN):
                step = -1 if event.action is InputAction.UP else 1
                combo.setCurrentIndex(max(0, min(combo.count() - 1, combo.currentIndex() + step)))
            elif event.action is InputAction.ACTIVATE:
                index = combo.currentIndex()
                initial = self._popup_initial_index
                self._popup_combo = None
                self._popup_initial_index = None
                combo.hidePopup()
                if index != initial:
                    if combo is self.mode:
                        self._apply_mode(index)
                    elif combo is self.output:
                        self._apply_output(index)
                    else:
                        for app_id, control in self._app_layouts.items():
                            if control is combo:
                                self._apply_layout(app_id, control, index)
                                break
            elif event.action is InputAction.BACK:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(self._popup_initial_index)
                self._popup_combo = None
                self._popup_initial_index = None
                combo.hidePopup()
            return
        if event.action is InputAction.BACK:
            self.reject()
            return
        focus = self.focusWidget()
        if event.action is InputAction.ACTIVATE and isinstance(focus, SettingsComboBox):
            self._popup_combo = focus
            self._popup_initial_index = focus.currentIndex()
            focus.showPopup()
            return
        if event.action is InputAction.ACTIVATE and isinstance(focus, QPushButton):
            focus.click()
            return
        keys = {
            InputAction.LEFT: Qt.Key.Key_Left,
            InputAction.RIGHT: Qt.Key.Key_Right,
            InputAction.UP: Qt.Key.Key_Up,
            InputAction.DOWN: Qt.Key.Key_Down,
            InputAction.ACTIVATE: Qt.Key.Key_Return,
        }
        key = keys.get(event.action)
        if focus is not None and key is not None:
            QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Navigate between audio controls with vertical directional input."""
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            self._ensure_visible(watched)
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if isinstance(watched, SettingsComboBox) and event.key() in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Space,
            ):
                watched.showPopup()
                return True
            if watched in (self.refresh, self.back) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                target = self.refresh if event.key() == Qt.Key.Key_Left else self.back
                if target.isEnabled():
                    target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
            controls = [control for control in self._controls() if control.isEnabled()]
            if watched in controls and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                index = controls.index(watched)  # type: ignore[arg-type]
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                target = controls[max(0, min(len(controls) - 1, index + step))]
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_visible(target)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._volume_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
