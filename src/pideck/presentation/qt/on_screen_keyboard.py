"""Character sets for the controller-operated on-screen keyboard.

Keys are laid out ten per row in the order of the physical layout, so the
on-screen keyboard matches the keyboard printed on the user's hardware.
Every layout keeps all characters of the previous alphabetical keyboard, so
any existing Wi-Fi key or PIN can still be entered.
"""

from pideck.domain.keyboard_layout import KeyboardLayout


ON_SCREEN_COLUMNS = 10

_LAYOUTS: dict[KeyboardLayout, str] = {
    KeyboardLayout.GERMAN: (
        "1234567890"
        "qwertzuiop"
        "asdfghjklö"
        "yxcvbnm,.-"
        "üäß_=+!@#$"
        "%^&*()[]{}"
        ";:'\"<>/?\\|"
        "`~"
    ),
    KeyboardLayout.ENGLISH_US: (
        "1234567890"
        "qwertyuiop"
        "asdfghjkl;"
        "zxcvbnm,./"
        "-_=+!@#$%^"
        "&*()[]{}:'"
        "\"<>?\\|`~"
    ),
}


def on_screen_characters(layout: KeyboardLayout) -> str:
    """Return the on-screen key characters for ``layout`` in row-major order."""
    return _LAYOUTS[KeyboardLayout(layout)]


def key_label(character: str) -> str:
    """Return a button label that shows ``&`` instead of treating it as a mnemonic."""
    return character.replace("&", "&&")


def shifted(character: str) -> str:
    """Return the uppercase key label, keeping characters such as ß unchanged."""
    upper = character.upper()
    return upper if len(upper) == 1 else character
