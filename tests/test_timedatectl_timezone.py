"""Tests for timedatectl-backed host timezone changes."""

from pathlib import Path
import subprocess

import pytest

from pideck.application.ports.timezone import TimezoneError
from pideck.infrastructure.timedatectl_timezone import TimedatectlTimezoneGateway


class Runner:
    """Record timedatectl invocations for assertions."""

    def __init__(self, outputs: dict[tuple[str, ...], subprocess.CompletedProcess[str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.outputs = outputs or {}

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        key = tuple(command[1:])
        if key in self.outputs:
            return self.outputs[key]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_current_timezone_uses_timedatectl_when_available() -> None:
    """Prefer timedatectl show output over the legacy timezone file."""
    runner = Runner(
        {
            ("show", "-p", "Timezone", "--value"): subprocess.CompletedProcess(
                [], 0, stdout="Europe/Berlin\n", stderr="",
            ),
        }
    )
    gateway = TimedatectlTimezoneGateway(
        runner=runner,
        which=lambda _name: "/usr/bin/timedatectl",
        zones=lambda: {"Europe/Berlin", "UTC"},
        timezone_file=Path("/missing"),
    )

    assert gateway.current_timezone() == "Europe/Berlin"


def test_current_timezone_falls_back_to_timezone_file(tmp_path: Path) -> None:
    """Read /etc/timezone when timedatectl is unavailable."""
    path = tmp_path / "timezone"
    path.write_text("America/New_York\n", encoding="utf-8")
    gateway = TimedatectlTimezoneGateway(
        runner=Runner(),
        which=lambda _name: None,
        zones=lambda: {"America/New_York"},
        timezone_file=path,
    )

    assert gateway.current_timezone() == "America/New_York"


def test_set_timezone_invokes_timedatectl() -> None:
    """Apply a validated IANA timezone through timedatectl."""
    runner = Runner()
    gateway = TimedatectlTimezoneGateway(
        runner=runner,
        which=lambda _name: "/usr/bin/timedatectl",
        zones=lambda: {"Europe/Berlin", "UTC"},
    )

    gateway.set_timezone("UTC")

    assert runner.calls == [["/usr/bin/timedatectl", "set-timezone", "UTC"]]


def test_set_timezone_rejects_unknown_and_failed_commands() -> None:
    """Unknown zones and rejected timedatectl calls surface as TimezoneError."""
    gateway = TimedatectlTimezoneGateway(
        runner=Runner(
            {
                ("set-timezone", "UTC"): subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="Authentication failed",
                ),
            }
        ),
        which=lambda _name: "/usr/bin/timedatectl",
        zones=lambda: {"UTC"},
    )

    with pytest.raises(TimezoneError, match="Unknown timezone"):
        gateway.set_timezone("Europe/Berlin")
    with pytest.raises(TimezoneError, match="Authentication failed"):
        gateway.set_timezone("UTC")
