"""Ports for detecting when an external application window is visible."""

from typing import Callable, Protocol

from .process import ProcessHandle


VisibilityCallback = Callable[[ProcessHandle], None]
VisibilityTimeoutCallback = Callable[[ProcessHandle], None]


class ProcessVisibilityDetector(Protocol):
    """Detect the first visible window belonging to a managed process group."""

    def wait_until_visible(
        self,
        handle: ProcessHandle,
        on_visible: VisibilityCallback,
        on_timeout: VisibilityTimeoutCallback,
    ) -> None:
        """Monitor a process handle until its application window is visible."""
        ...

    def cancel(self, handle: ProcessHandle) -> None:
        """Cancel visibility monitoring for a process handle."""
        ...

    def close(self) -> None:
        """Stop all visibility monitoring threads."""
        ...
