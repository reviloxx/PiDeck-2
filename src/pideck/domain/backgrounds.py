"""Shared launcher background styles, independent of the selected theme palette."""

from __future__ import annotations

DEFAULT_BACKGROUND = "glow"

BACKGROUND_STYLE_IDS: tuple[str, ...] = (
    "glow",
    "horizon",
    "ribbons",
    "constellation",
    "well",
)

BACKGROUND_STYLE_ID_SET = frozenset(BACKGROUND_STYLE_IDS)
