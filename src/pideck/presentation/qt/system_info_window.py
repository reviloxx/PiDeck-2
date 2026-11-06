"""Read-only, controller-friendly System information window."""

from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.system_info import SystemInfoGateway
from pideck.domain.system_info import SystemInfo
from pideck.domain.theme import ThemeDefinition

from .localization import tr
from .system_info_format import Section, system_info_sections
from .system_panel import add_action_button, build_system_window, set_status
from .theme import apply_theme
from .wallpaper import WallpaperDialog


_LOGGER = logging.getLogger(__name__)
_SCROLL_STEP = 80
_KEY_WIDTH = 340


class SystemInfoWindow(WallpaperDialog):
    """Show hardware, driver and software facts collected off the UI thread."""

    operation_finished = Signal(object)

    def __init__(
        self, gateway: SystemInfoGateway, theme: ThemeDefinition, language: str, parent: QWidget,
    ) -> None:
        """Create the window and start collecting the first snapshot."""
        super().__init__(parent)
        self._gateway = gateway
        self._language = language
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pideck-sysinfo")
        self._closed = False
        self.setWindowTitle(tr(language, "system_info"))
        self.setModal(True)
        frame = build_system_window(self, tr(language, "system_info"), tr(language, "system_info_description"))
        self.status = frame.status
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("settings_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame.content.addWidget(self.scroll, stretch=1)
        self.refresh = add_action_button(self, frame.footer, tr(language, "system_info_refresh"))
        self.back = add_action_button(self, frame.footer, tr(language, "back"), back=True)
        for button in (self.refresh, self.back):
            button.installEventFilter(self)
        self.refresh.clicked.connect(self._refresh)
        self.back.clicked.connect(self.reject)
        self.operation_finished.connect(self._finish_operation)
        self.finished.connect(self._finish)
        apply_theme(self, theme)
        self._refresh()

    def _refresh(self) -> None:
        set_status(self.status, tr(self._language, "system_info_loading"))
        self.refresh.setEnabled(False)
        self.back.setFocus(Qt.FocusReason.OtherFocusReason)
        future = self._executor.submit(self._gateway.inspect)

        def finish(result: Future[SystemInfo]) -> None:
            if not self._closed:
                self.operation_finished.emit(result)

        future.add_done_callback(finish)

    def _finish_operation(self, future: Future[SystemInfo]) -> None:
        if self._closed:
            return
        self.refresh.setEnabled(True)
        try:
            info = future.result()
        except Exception:
            _LOGGER.exception("Unable to collect system information")
            set_status(self.status, tr(self._language, "system_info_error"), error=True)
            return
        set_status(self.status, "")
        self._show_sections(system_info_sections(info, self._language))

    def _show_sections(self, sections: tuple[Section, ...]) -> None:
        position = self.scroll.verticalScrollBar().value()
        page = QWidget(self.scroll)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 14, 0)
        # One grid for all sections keeps the value column aligned.
        grid = QGridLayout()
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, _KEY_WIDTH)
        row = 0
        for title, rows in sections:
            heading = QLabel(title, page)
            heading.setObjectName("settings_kicker")
            if row:
                grid.setRowMinimumHeight(row, 12)
                row += 1
            grid.addWidget(heading, row, 0, 1, 2)
            row += 1
            for label, value in rows:
                key = QLabel(label, page)
                key.setObjectName("settings_hint")
                key.setWordWrap(True)
                key.setMaximumWidth(_KEY_WIDTH)
                grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
                text = QLabel(value, page)
                text.setObjectName("system_info_value")
                text.setWordWrap(True)
                grid.addWidget(text, row, 1, Qt.AlignmentFlag.AlignTop)
                row += 1
        outer.addLayout(grid)
        outer.addStretch()
        self.scroll.setWidget(page)
        self.scroll.verticalScrollBar().setValue(position)

    def _scroll_by(self, step: int) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + step)

    def handle_input(self, event: InputEvent) -> None:
        """Scroll with up/down, switch buttons with left/right and close with Back."""
        if not event.pressed:
            return
        if event.action is InputAction.BACK:
            self.reject()
        elif event.action in (InputAction.UP, InputAction.DOWN):
            self._scroll_by(-_SCROLL_STEP if event.action is InputAction.UP else _SCROLL_STEP)
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            self._focus_button(left=event.action is InputAction.LEFT)
        elif event.action is InputAction.ACTIVATE:
            focus = self.focusWidget()
            if focus in (self.refresh, self.back) and focus.isEnabled():
                focus.click()

    def _focus_button(self, *, left: bool) -> None:
        target = self.refresh if left and self.refresh.isEnabled() else self.back
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Scroll the content with vertical keys while focus stays on the footer."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            steps = {
                Qt.Key.Key_Up: -_SCROLL_STEP, Qt.Key.Key_Down: _SCROLL_STEP,
                Qt.Key.Key_PageUp: -self.scroll.viewport().height(),
                Qt.Key.Key_PageDown: self.scroll.viewport().height(),
            }
            if event.key() in steps:
                self._scroll_by(steps[Qt.Key(event.key())])
                return True
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self._focus_button(left=event.key() == Qt.Key.Key_Left)
                return True
        return super().eventFilter(watched, event)

    def _finish(self, _result: int) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
