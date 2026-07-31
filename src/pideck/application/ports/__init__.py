"""Interfaces required by application use cases."""

from .process import ProcessCommand, ProcessExit, ProcessHandle, ProcessSupervisor

__all__ = ["ProcessCommand", "ProcessExit", "ProcessHandle", "ProcessSupervisor"]
