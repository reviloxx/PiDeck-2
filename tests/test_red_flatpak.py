"""Tests for Red Flatpak permission overrides."""

from pideck.infrastructure.red_flatpak import (
    RED_FLATPAK_ID,
    configure_red_without_input_devices,
    red_input_override_command,
)


def test_red_input_override_command_keeps_gpu_without_input() -> None:
    """Override argv revokes all devices then re-adds dri and shm only."""
    assert red_input_override_command() == (
        "flatpak",
        "override",
        "--user",
        "--nodevice=all",
        "--device=dri",
        "--device=shm",
        RED_FLATPAK_ID,
    )
    assert red_input_override_command(user=False)[2] == "--system"


def test_configure_red_without_input_devices_runs_override(monkeypatch) -> None:
    """Launch preparation executes the Flatpak override without raising."""
    calls: list[tuple[str, ...]] = []

    class _Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(tuple(command))
        return _Completed()

    monkeypatch.setattr(
        "pideck.infrastructure.red_flatpak.subprocess.run",
        fake_run,
    )
    configure_red_without_input_devices()
    assert calls == [red_input_override_command()]
