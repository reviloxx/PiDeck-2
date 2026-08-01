"""Operating-system process supervision adapters."""

from .subprocess_supervisor import SubprocessLaunchError, SubprocessSupervisor
from .x11_visibility import X11WindowVisibilityDetector

__all__ = [
	"SubprocessLaunchError",
	"SubprocessSupervisor",
	"X11WindowVisibilityDetector",
]
