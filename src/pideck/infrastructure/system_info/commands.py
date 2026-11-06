"""Run short diagnostic tools without a shell and tolerate their absence."""

from collections.abc import Callable
import logging
import os
import subprocess

_LOGGER = logging.getLogger(__name__)

CommandRunner = Callable[[list[str]], str | None]


def run_command(arguments: list[str], timeout: float = 5.0) -> str | None:
    """Return a diagnostic tool's standard output, or None if it is unavailable or fails."""
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError) as error:
        _LOGGER.info("Diagnostic command %s failed: %s", arguments[0], error)
        return None
    if result.returncode:
        _LOGGER.info("Diagnostic command %s exited with %s", arguments[0], result.returncode)
        return None
    return result.stdout
