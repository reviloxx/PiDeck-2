"""Controller-friendly system audio controls backed by an injected gateway."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pideck.application.ports.audio import AudioError, AudioGateway, AudioOutput, AudioState
from pideck.application.ports.input import InputAction, InputEvent
from pideck.domain.theme import ThemeDefinition
from pideck.infrastructure.audio_preferences import AudioPreferencesStore

from .localization import tr
from .system_panel import add_action_button, build_system_window, set_status
from .theme import SettingsComboBox, apply_theme, configure_combo_box
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)
_USE_DEFAULT = ""


class AudioWindow(WallpaperDialog):
    """Inspect and adjust host audio without blocking the Qt event loop."""

    operation_finished = Signal(object)

    def __init__(
        self,
        gateway: AudioGateway,
        preferences: AudioPreferencesStore,
        applications: Sequence[tuple[str, str]],
        theme: ThemeDefinition,
        language: str,
        parent: QWidget,
    ) -> None:
        """Create an audio settings dialog around a platform audio gateway."""
        super().__init__(parent)
        self._gateway = gateway
        self._preferences = preferences
        self._applications = tuple(applications)
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-audio")
        self._closed = False
        self._popup_combo: SettingsComboBox | None = None
        self._popup_initial_index: int | None = None
        self._focus_before_operation: QWidget | None = None
        self._outputs: tuple[AudioOutput, ...] = ()
        self.setWindowTitle(tr(language, "audio"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "audio"), tr(language, "audio_description"))
        self.status = frame.status
        layout = frame.content

        system_label = QLabel(tr(language, "audio_system_default"), self)
        system_label.setObjectName("settings_label")
        layout.addWidget(system_label)
        self.output = SettingsComboBox(self)
        self.output.setObjectName("settings_control")
        configure_combo_box(self.output)
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

        self._app_combos: dict[str, SettingsComboBox] = {}
        if self._applications:
            apps_label = QLabel(tr(language, "audio_per_application"), self)
            apps_label.setObjectName("settings_label")
            layout.addWidget(apps_label)
            scroll = QScrollArea(self)
            scroll.setObjectName("settings_scroll")
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            container = QWidget(scroll)
            apps_layout = QVBoxLayout(container)
            apps_layout.setContentsMargins(0, 0, 0, 0)
            apps_layout.setSpacing(10)
            for application_id, application_name in self._applications:
                label = QLabel(application_name, container)
                label.setObjectName("settings_label")
                apps_layout.addWidget(label)
                combo = SettingsComboBox(container)
                combo.setObjectName("settings_control")
                configure_combo_box(combo)
                combo.installEventFilter(self)
                combo.activated.connect(
                    lambda index, app_id=application_id, box=combo: self._apply_application(app_id, box, index)
                )
                apps_layout.addWidget(combo)
                self._app_combos[application_id] = combo
            apps_layout.addStretch()
            scroll.setWidget(container)
            layout.addWidget(scroll, stretch=1)
        else:
            layout.addStretch()

        self.refresh = add_action_button(self, frame.footer, tr(language, "audio_refresh"))
        self.refresh.installEventFilter(self)
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        self.back.installEventFilter(self)
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

    def _controls(self) -> list[QWidget]:
        controls: list[QWidget] = [self.output, self.volume]
        controls.extend(self._app_combos.values())
        controls.extend(control for control in (self.refresh, self.back) if control.isEnabled())
        return [control for control in controls if control.isEnabled() or control is self.volume]

    def _submit(self, operation: Callable[[], AudioState]) -> None:
        self._focus_before_operation = self.focusWidget()
        set_status(self.status, tr(self._language, "wifi_working"))
        self.output.setEnabled(False)
        self.volume.setEnabled(False)
        for combo in self._app_combos.values():
            combo.setEnabled(False)
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
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        except Exception:
            _LOGGER.exception("Unexpected audio operation failure")
            set_status(
                self.status,
                tr(self._language, "audio_error", message=tr(self._language, "audio_unavailable")),
                error=True,
            )
            self.refresh.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._outputs = state.outputs
        try:
            saved = self._preferences.load()
        except AudioError as error:
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            saved_default = None
            saved_apps: dict[str, str] = {}
        else:
            saved_default = saved.default
            saved_apps = saved.applications
            set_status(self.status, "")
        with QSignalBlocker(self.output), QSignalBlocker(self.volume):
            self._fill_outputs(self.output, state.outputs, include_default=False)
            current = saved_default if saved_default in {output.identifier for output in state.outputs} else state.current
            self.output.setCurrentIndex(max(0, self.output.findData(current)))
            self.volume.setValue(state.volume)
        self.volume_label.setText(tr(self._language, "audio_volume", volume=state.volume))
        for application_id, combo in self._app_combos.items():
            with QSignalBlocker(combo):
                self._fill_outputs(combo, state.outputs, include_default=True)
                selected = saved_apps.get(application_id, _USE_DEFAULT)
                if selected != _USE_DEFAULT and selected not in {output.identifier for output in state.outputs}:
                    selected = _USE_DEFAULT
                index = combo.findData(selected)
                combo.setCurrentIndex(index if index >= 0 else 0)
        self.output.setEnabled(True)
        self.volume.setEnabled(True)
        for combo in self._app_combos.values():
            combo.setEnabled(True)
        focus = self._focus_before_operation
        if focus not in self._controls():
            focus = self.output
        focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def _fill_outputs(
        self,
        combo: SettingsComboBox,
        outputs: tuple[AudioOutput, ...],
        *,
        include_default: bool,
    ) -> None:
        combo.clear()
        if include_default:
            combo.addItem(tr(self._language, "audio_use_default"), _USE_DEFAULT)
        for output in outputs:
            combo.addItem(output.description, output.identifier)

    def _apply_output(self, index: int) -> None:
        identifier = self.output.itemData(index)
        if not isinstance(identifier, str) or not identifier:
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        self._volume_timer.stop()

        def change() -> AudioState:
            self._gateway.set_output(identifier)
            self._preferences.set_default(identifier)
            return self._gateway.inspect()

        self._submit(change)

    def _apply_application(self, application_id: str, combo: SettingsComboBox, index: int) -> None:
        identifier = combo.itemData(index)
        if not isinstance(identifier, str):
            set_status(self.status, tr(self._language, "audio_unavailable"), error=True)
            return
        try:
            self._preferences.set_application(
                application_id,
                None if identifier == _USE_DEFAULT else identifier,
            )
        except AudioError as error:
            set_status(self.status, tr(self._language, "audio_error", message=str(error)), error=True)
            return
        set_status(self.status, "")

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
                    if combo is self.output:
                        self._apply_output(index)
                    else:
                        for application_id, app_combo in self._app_combos.items():
                            if app_combo is combo:
                                self._apply_application(application_id, combo, index)
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
                return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._volume_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
