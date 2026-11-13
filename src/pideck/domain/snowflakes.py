"""Home-screen snowflake mode and seasonal automatic window."""

from __future__ import annotations

from datetime import date

DEFAULT_SNOWFLAKES = "automatic"

SNOWFLAKES_MODES: tuple[str, ...] = (
    "off",
    "on",
    "automatic",
)

SNOWFLAKES_MODE_SET = frozenset(SNOWFLAKES_MODES)


def is_snowflakes_season(today: date) -> bool:
    """Return whether ``today`` falls in the holiday snow window.

    The automatic window is 1 December through 6 January (inclusive).
    """
    return today.month == 12 or (today.month == 1 and today.day <= 6)


def snowflakes_active(mode: str, today: date | None = None) -> bool:
    """Resolve whether snowflakes should animate for ``mode`` on ``today``."""
    if mode == "on":
        return True
    if mode == "off":
        return False
    if mode == "automatic":
        return is_snowflakes_season(today if today is not None else date.today())
    return False
