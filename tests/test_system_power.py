"""Tests for the Linux power-off adapter."""

import subprocess

import pytest

from pideck.application.power import shutdown
from pideck.domain.errors import PowerError
from pideck.infrastructure.system_power import SystemPower


def test_power_off_requests_noninteractive_system_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request shutdown without starting an interactive authentication prompt."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    SystemPower().power_off()

    assert calls == [["systemctl", "poweroff", "--no-block", "--no-ask-password"]]


def test_power_off_reports_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a rejected system power-off visible to the caller."""
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Not authorized")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(PowerError, match="Not authorized"):
        SystemPower().power_off()


@pytest.mark.parametrize("error", [OSError("systemctl unavailable"), subprocess.TimeoutExpired("systemctl", 10)])
def test_power_off_reports_execution_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Report a missing or unresponsive system power service."""
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(PowerError):
        SystemPower().power_off()


def test_shutdown_cleans_up_only_after_power_request_succeeds() -> None:
    """Keep services running if power-off fails, and clean up on success."""
    events: list[str] = []

    class FakePower:
        """Allow the power request outcome to be controlled by the test."""

        fail = True

        def power_off(self) -> None:
            events.append("power")
            if self.fail:
                raise PowerError("denied")

    power = FakePower()
    with pytest.raises(PowerError, match="denied"):
        shutdown(power, lambda: events.append("cleanup"))
    assert events == ["power"]

    power.fail = False
    shutdown(power, lambda: events.append("cleanup"))
    assert events == ["power", "power", "cleanup"]


def test_shutdown_without_power_control_only_cleans_up() -> None:
    """Retain the existing launcher-only exit behavior outside appliance mode."""
    events: list[str] = []

    shutdown(None, lambda: events.append("cleanup"))

    assert events == ["cleanup"]
