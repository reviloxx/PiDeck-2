"""Safe default configuration for recovery from invalid user data."""

from pideck.domain.configuration import (
    Configuration,
    HomeScreenConfiguration,
    InputConfiguration,
    SettingsConfiguration,
)
from pideck.domain.theme import AnimationSpec, FontSpec, ThemeDefinition, TileSpec


DEFAULT_THEME = ThemeDefinition(
    identifier="default",
    name="Midnight",
    colors={
        "background": "#101214",
        "surface": "#1b1f24",
        "primary": "#63d6c5",
        "text": "#f4f7f7",
        "muted_text": "#9ca8aa",
        "focus": "#63d6c5",
        "running": "#f2b880",
        "error": "#ed7777",
    },
    fonts=FontSpec(family="DejaVu Sans", heading_size=28, body_size=18),
    icons={},
    wallpaper=None,
    tile=TileSpec(width=320, height=180, gap=24),
    animation=AnimationSpec(duration_ms=180, reduced_motion_duration_ms=0),
)


def default_configuration() -> Configuration:
    """Return a fresh safe configuration with no application commands."""
    return Configuration(
        version=1,
        applications=(),
        themes=(DEFAULT_THEME,),
        home=HomeScreenConfiguration(visible_applications=(), theme="default"),
        input=InputConfiguration(
            enabled_sources=("cec", "gamepad", "keyboard", "mouse"),
            bindings={"home": "Ctrl+Alt+H"},
        ),
        settings=SettingsConfiguration(),
    )
