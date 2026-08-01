"""Tests for normalized Linux gamepad input."""

from types import SimpleNamespace

from pideck.application.ports.input import InputAction, InputEvent
from pideck.infrastructure.input_gamepad import EvdevGamepadAdapter


def test_gamepad_maps_dpad_and_buttons() -> None:
    """Standard evdev codes become normalized PiDeck actions."""
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=-1)) is InputAction.LEFT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=16, value=1)) is InputAction.RIGHT
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=-1)) is InputAction.UP
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=3, code=17, value=1)) is InputAction.DOWN
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=1)) is InputAction.ACTIVATE
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=305, value=1)) is InputAction.BACK
    assert EvdevGamepadAdapter._map_event(SimpleNamespace(type=1, code=304, value=0)) is None


def test_gamepad_event_is_source_tagged() -> None:
    """Normalized events identify their physical source."""
    event = InputEvent(InputAction.ACTIVATE, "gamepad")

    assert event.source == "gamepad"
    assert event.pressed is True


def test_gamepad_detection_accepts_integer_capability_codes() -> None:
    """Real evdev devices may expose capabilities as integer code lists."""
    device = SimpleNamespace(
        name="Generic USB Controller",
        capabilities=lambda: {1: [304, 305], 3: [16, 17]},
    )

    assert EvdevGamepadAdapter._is_gamepad(device) is True
