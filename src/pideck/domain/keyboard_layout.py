"""Keyboard layouts PiDeck can apply to the X11 session."""

from enum import StrEnum


class KeyboardLayout(StrEnum):
    """Supported XKB layouts; the value is the XKB layout name."""

    GERMAN = "de"
    ENGLISH_US = "us"


DEFAULT_KEYBOARD_LAYOUT = KeyboardLayout.GERMAN
