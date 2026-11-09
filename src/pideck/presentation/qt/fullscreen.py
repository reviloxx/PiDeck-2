"""Present PiDeck dialogs as true fullscreen windows without Qt recentering them."""

from __future__ import annotations

from weakref import WeakKeyDictionary

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

_REASSERT_MS = 100
_pending_timers: WeakKeyDictionary[QWidget, QTimer] = WeakKeyDictionary()


def _screen_geometry(widget: QWidget):
    """Return the screen rectangle for a widget, falling back to the primary screen."""
    screen = widget.screen() or QApplication.primaryScreen()
    if screen is None:
        return None
    return screen.geometry()


def apply_fullscreen_geometry(widget: QWidget) -> None:
    """Size a widget to the full screen so QDialog skips adjustPosition."""
    geometry = _screen_geometry(widget)
    if geometry is not None:
        widget.setGeometry(geometry)


def _reassert_fullscreen(widget: QWidget) -> None:
    """Re-apply fullscreen after the window manager maps the surface."""
    _pending_timers.pop(widget, None)
    if not widget.isVisible():
        return
    apply_fullscreen_geometry(widget)
    QWidget.showFullScreen(widget)
    widget.raise_()
    widget.activateWindow()


def cancel_fullscreen_reassert(widget: QWidget) -> None:
    """Stop a pending fullscreen reassert for a widget that has hidden."""
    timer = _pending_timers.pop(widget, None)
    if timer is not None:
        timer.stop()


def present_fullscreen(widget: QWidget, *, stays_on_top: bool = False, reassert_ms: int = _REASSERT_MS) -> None:
    """Show a frameless top-level window fullscreen and reassert after map.

    Sets ``WA_Moved`` via screen geometry before ``showFullScreen`` so
    ``QDialog.showEvent`` does not center a layout-sized panel on the parent.
    """
    flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
    if stays_on_top:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    if int(widget.windowFlags()) != int(flags):
        widget.setWindowFlags(flags)
    apply_fullscreen_geometry(widget)
    QWidget.showFullScreen(widget)
    widget.raise_()
    widget.activateWindow()
    cancel_fullscreen_reassert(widget)
    if reassert_ms <= 0:
        return
    timer = QTimer(widget)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: _reassert_fullscreen(widget))
    timer.start(reassert_ms)
    _pending_timers[widget] = timer
