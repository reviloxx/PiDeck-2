"""Tests for the Linux power-off adapter."""

import subprocess

import pytest

from pideck.application.power import hibernate, shutdown, suspend
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


def test_suspend_requests_noninteractive_system_suspend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request suspend without starting an interactive authentication prompt."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    SystemPower().suspend()

    assert calls == [["systemctl", "suspend", "--no-block", "--no-ask-password"]]


def test_hibernate_requests_noninteractive_system_hibernate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request hibernate without starting an interactive authentication prompt."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    SystemPower().hibernate()

    assert calls == [["systemctl", "hibernate", "--no-block", "--no-ask-password"]]


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


def test_suspend_keeps_session_and_reports_failure() -> None:
    """Suspend must not tear down the session; failures stay visible."""
    events: list[str] = []

    class FakePower:
        fail = True

        def suspend(self) -> None:
            events.append("suspend")
            if self.fail:
                raise PowerError("denied")

        def power_off(self) -> None:
            raise AssertionError("power_off must not run")

    power = FakePower()
    with pytest.raises(PowerError, match="denied"):
        suspend(power)
    assert events == ["suspend"]

    power.fail = False
    suspend(power)
    assert events == ["suspend", "suspend"]


def test_hibernate_keeps_session_on_success() -> None:
    """Hibernate keeps the process so a successful resume can continue."""
    events: list[str] = []

    class FakePower:
        def hibernate(self) -> None:
            events.append("hibernate")

        def power_off(self) -> None:
            raise AssertionError("power_off must not run")

    hibernate(FakePower())
    assert events == ["hibernate"]


def test_restart_is_noninteractive_and_reports_failure(monkeypatch) -> None:
    """Restart requests use systemd and report authorization failures."""
    events: list[str] = []
    def run(command, **kwargs):
        events.append(" ".join(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    SystemPower().restart()
    assert events == ["systemctl reboot --no-block --no-ask-password"]

    def reject(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Not authorized")

    monkeypatch.setattr(subprocess, "run", reject)
    with pytest.raises(PowerError, match="Not authorized"):
        SystemPower().restart()
