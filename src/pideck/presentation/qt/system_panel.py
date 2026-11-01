"""Shared responsive layout for fullscreen system-settings surfaces."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget


def create_system_panel(parent: QWidget, *, max_height: int | None = None) -> QVBoxLayout:
    """Create a centered panel constrained to the available space."""
    root = QHBoxLayout(parent)
    root.setContentsMargins(56, 44, 56, 44)
    panel = QFrame(parent)
    panel.setObjectName("system_settings_panel")
    panel.setMaximumWidth(1056)
    if max_height is not None:
        panel.setMaximumHeight(max_height)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    root.addWidget(panel)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(40, 34, 40, 28)
    layout.setSpacing(14)
    return layout
