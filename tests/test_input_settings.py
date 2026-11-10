"""Tests for persisted HDMI-CEC preferences."""

from pathlib import Path

import pytest

from pideck.domain.input_settings import CecSettings
from pideck.infrastructure.input_settings import InputPreferences, InputSettingsError


def test_missing_preferences_are_initialized_once(tmp_path: Path) -> None:
    """Existing main configurations receive an independent enabled default."""
    repository = InputPreferences(tmp_path / "input.yaml")
    assert repository.load() == CecSettings()
    disabled = CecSettings(
        enabled=False, auto_connect=False, hdmi_port=2, switch_input=True, wake_tv=False,
    )
    repository.save(disabled)
    assert repository.load() == disabled


def test_legacy_preferences_default_wake_tv(tmp_path: Path) -> None:
    """Documents without wake_tv keep waking the TV on connect."""
    path = tmp_path / "input.yaml"
    path.write_text(
        "version: 1\nenabled: true\nauto_connect: true\nhdmi_port: null\nswitch_input: true\n",
        encoding="utf-8",
    )
    assert InputPreferences(path).load() == CecSettings()


@pytest.mark.parametrize("port", [0, 16, -1, True, "2"])
def test_invalid_ports_are_rejected(port: object) -> None:
    """A direct-TV port must be a non-boolean number from one through fifteen."""
    with pytest.raises(ValueError):
        CecSettings(hdmi_port=port)


@pytest.mark.parametrize(
    "document",
    [
        "version: 1\nenabled: true\n",
        "version: true\nenabled: true\nauto_connect: true\nhdmi_port: 2\nswitch_input: false\n",
        "version: 1\nenabled: yes\nauto_connect: true\nhdmi_port: 16\nswitch_input: false\n",
        "version: 1\nenabled: true\nauto_connect: true\nhdmi_port: 2\nswitch_input: false\nwake_tv: 1\n",
        "not: [valid",
    ],
)
def test_invalid_preferences_are_not_overwritten(tmp_path: Path, document: str) -> None:
    """Bad settings are reported without silently resetting the user's file."""
    path = tmp_path / "input.yaml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(InputSettingsError):
        InputPreferences(path).load()
    assert path.read_text(encoding="utf-8") == document
