"""Operating-system process supervision adapters."""

from .subprocess_supervisor import SubprocessLaunchError, SubprocessSupervisor

__all__ = ["SubprocessLaunchError", "SubprocessSupervisor"]
