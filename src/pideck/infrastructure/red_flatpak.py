"""Keep YouTube (Red) from reading gamepads while PiDeck injects keys."""

from __future__ import annotations

import logging
import subprocess

_LOGGER = logging.getLogger(__name__)

RED_FLATPAK_ID = "com.ktechpit.red"


def red_input_override_command(*, user: bool = True) -> tuple[str, ...]:
    """Return the Flatpak override argv that keeps GPU but drops raw input devices."""
    scope = "--user" if user else "--system"
    return (
        "flatpak",
        "override",
        scope,
        "--nodevice=all",
        "--device=dri",
        "--device=shm",
        RED_FLATPAK_ID,
    )


def configure_red_without_input_devices(*, user: bool = True) -> None:
    """Idempotently revoke Red's ``devices=all`` so native gamepad cannot double with keys.

    Failures are logged and never raised: launch should still proceed with key
    forwarding even if Flatpak is missing on a developer host.
    """
    command = red_input_override_command(user=user)
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _LOGGER.warning("Unable to override Red Flatpak permissions: %s", error)
        return
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace").strip()
        _LOGGER.warning(
            "Red Flatpak override failed code=%s detail=%s",
            completed.returncode,
            detail or "(no output)",
        )
        return
    _LOGGER.info("Configured Red Flatpak without input devices command=%s", " ".join(command))
