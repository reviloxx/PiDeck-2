"""Process supervision ports used by application session orchestration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ProcessCommand:
    """Describe one executable invocation without shell interpolation."""

    executable: str
    arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] | None = None
    working_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class ProcessHandle:
    """Identify a supervised process group."""

    token: str
    pid: int
    process_group_id: int


@dataclass(frozen=True, slots=True)
class ProcessExit:
    """Describe the observed exit of a supervised process group."""

    handle: ProcessHandle
    return_code: int


ProcessExitCallback = Callable[[ProcessExit], None]


class ProcessSupervisor(Protocol):
    """Start, monitor, and stop external application process groups."""

    def start(
        self,
        command: ProcessCommand,
        token: str,
        on_exit: ProcessExitCallback,
    ) -> ProcessHandle:
        """Start a command in its own process group."""
        ...

    def stop(self, handle: ProcessHandle, grace_period: float = 3.0) -> None:
        """Request graceful termination and escalate after the grace period."""
        ...

    def close(self) -> None:
        """Stop all remaining process groups and release supervisor resources."""
        ...
