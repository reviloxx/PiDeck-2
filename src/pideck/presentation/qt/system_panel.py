"""Shared frame for fullscreen system-settings windows and confirmations.

Display, Audio, Network and Bluetooth use ``build_system_window`` so every
window has the same panel size, heading, description, status line and footer
(secondary actions followed by Back on the right).
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

SYSTEM_PANEL_MAX_HEIGHT = 720


def create_system_panel(
    parent: QWidget, *, max_width: int = 1056, max_height: int | None = None,
) -> QVBoxLayout:
    """Create a centered panel constrained to the available space."""
    root = QHBoxLayout(parent)
    root.setContentsMargins(56, 44, 56, 44)
    panel = QFrame(parent)
    panel.setObjectName("system_settings_panel")
    panel.setMaximumWidth(max_width)
    if max_height is not None:
        panel.setMaximumHeight(max_height)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    root.addWidget(panel)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(40, 34, 40, 28)
    layout.setSpacing(14)
    return layout


@dataclass(frozen=True)
class SystemWindowLayout:
    """Widgets and layouts shared by every system-settings window."""

    content: QVBoxLayout
    status: QLabel
    footer: QHBoxLayout


def build_system_window(parent: QWidget, title: str, description: str) -> SystemWindowLayout:
    """Build the common heading, description, status, content and footer areas."""
    layout = create_system_panel(parent, max_height=SYSTEM_PANEL_MAX_HEIGHT)
    heading = QLabel(title, parent)
    heading.setObjectName("settings_heading")
    layout.addWidget(heading)
    description_label = QLabel(description, parent)
    description_label.setObjectName("settings_description")
    description_label.setWordWrap(True)
    description_label.setVisible(bool(description))
    layout.addWidget(description_label)
    status = QLabel("", parent)
    status.setObjectName("system_status")
    status.setWordWrap(True)
    status.hide()
    layout.addWidget(status)
    content = QVBoxLayout()
    content.setSpacing(14)
    layout.addLayout(content, stretch=1)
    footer = QHBoxLayout()
    footer.addStretch()
    layout.addLayout(footer)
    return SystemWindowLayout(content, status, footer)


def add_action_button(parent: QWidget, layout: QHBoxLayout, text: str, *, back: bool = False) -> QPushButton:
    """Add a focusable themed action; Back uses the distinct Back style."""
    button = QPushButton(text, parent)
    button.setObjectName("settings_back" if back else "settings_footer")
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    layout.addWidget(button)
    return button


def set_status(label: QLabel, text: str, *, error: bool = False) -> None:
    """Show a status line, colored as an error when ``error`` is set, or hide it."""
    label.setText(text)
    label.setProperty("error", error)
    label.style().unpolish(label)
    label.style().polish(label)
    label.setVisible(bool(text))
