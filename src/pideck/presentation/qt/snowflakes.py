"""Lightweight falling-snow overlay for the launcher home screen."""

from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from pideck.domain.snowflakes import DEFAULT_SNOWFLAKES, snowflakes_active

_FLAKE_COUNT = 48
_INTERVAL_MS = 33
_ALPHA = 180


@dataclass(slots=True)
class _Flake:
    x: float
    y: float
    size: float
    speed: float
    drift: float
    phase: float


class SnowflakeOverlay(QWidget):
    """Paint soft drifting flakes above the wallpaper without stealing input."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a full-bleed, mouse-transparent particle layer."""
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("snowflake_overlay")
        self._mode = DEFAULT_SNOWFLAKES
        self._enabled = False
        self._flakes: list[_Flake] = []
        self._rng = Random(20241201)
        self._color = QColor(255, 255, 255, _ALPHA)
        self._timer = QTimer(self)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    @property
    def mode(self) -> str:
        """Return the configured snowflakes mode."""
        return self._mode

    @property
    def active(self) -> bool:
        """Return whether flakes are currently animating."""
        return self._timer.isActive()

    def set_mode(self, mode: str) -> None:
        """Store the mode and start or stop animation based on season policy."""
        self._mode = mode
        self.sync()

    def set_color(self, color: str) -> None:
        """Tint flakes toward a theme color while keeping them mostly white."""
        tint = QColor(color)
        if not tint.isValid():
            tint = QColor(255, 255, 255)
        mixed = QColor(
            (tint.red() + 255 * 3) // 4,
            (tint.green() + 255 * 3) // 4,
            (tint.blue() + 255 * 3) // 4,
            _ALPHA,
        )
        self._color = mixed
        if self.isVisible():
            self.update()

    def sync(self) -> None:
        """Re-evaluate seasonal policy and match timer visibility to the parent."""
        should_run = snowflakes_active(self._mode) and self._enabled
        if should_run:
            if not self._flakes:
                self._seed_flakes()
            starting = not self._timer.isActive()
            self.show()
            if starting:
                self._timer.start()
                self.lower()
        else:
            self._timer.stop()
            self.hide()

    def set_launcher_visible(self, visible: bool) -> None:
        """Pause animation while the launcher is hidden for an app session."""
        self._enabled = visible
        self.sync()

    def resizeEvent(self, event: object) -> None:
        """Keep flakes distributed when the screen geometry changes."""
        super().resizeEvent(event)
        if self._flakes and self.width() > 0 and self.height() > 0:
            for flake in self._flakes:
                flake.x %= max(self.width(), 1)
                flake.y %= max(self.height(), 1)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw each flake as a soft filled ellipse."""
        del event
        if not self._flakes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        for flake in self._flakes:
            radius = flake.size
            painter.drawEllipse(
                int(flake.x - radius),
                int(flake.y - radius),
                max(1, int(radius * 2)),
                max(1, int(radius * 2)),
            )

    def _seed_flakes(self) -> None:
        width = max(self.width(), 1)
        height = max(self.height(), 1)
        self._flakes = [
            _Flake(
                x=self._rng.uniform(0, width),
                y=self._rng.uniform(0, height),
                size=self._rng.uniform(1.5, 4.5),
                speed=self._rng.uniform(0.6, 2.2),
                drift=self._rng.uniform(-0.45, 0.45),
                phase=self._rng.uniform(0, 6.28),
            )
            for _ in range(_FLAKE_COUNT)
        ]

    def _advance(self) -> None:
        if not snowflakes_active(self._mode):
            self.sync()
            return
        width = max(self.width(), 1)
        height = max(self.height(), 1)
        for flake in self._flakes:
            flake.phase += 0.04
            flake.y += flake.speed
            flake.x += flake.drift + 0.35 * math.sin(flake.phase)
            if flake.y - flake.size > height:
                flake.y = -flake.size
                flake.x = self._rng.uniform(0, width)
            if flake.x < -flake.size:
                flake.x = width + flake.size
            elif flake.x > width + flake.size:
                flake.x = -flake.size
        self.update()
