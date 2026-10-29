"""Client for the restricted image update service."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
from typing import Any


SOCKET_PATH = Path("/run/pideck-updater/service.sock")


class ImageUpdateError(Exception):
    """The privileged image update service rejected or failed an operation."""


def request(action: str, *, timeout: float = 30, **fields: str) -> dict[str, Any]:
    """Send a bounded request to the image service and return its result."""
    data = json.dumps({"action": action, **fields}).encode("utf-8") + b"\n"
    if len(data) > 4096:
        raise ImageUpdateError("Update request is too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(SOCKET_PATH))
            connection.sendall(data)
            with connection.makefile("rb") as stream:
                line = stream.readline(16385)
    except OSError as error:
        raise ImageUpdateError(f"Image update service unavailable: {error}") from error
    if len(line) > 16384 or not line.endswith(b"\n"):
        raise ImageUpdateError("Invalid image update service response")
    try:
        response = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageUpdateError("Invalid image update service response") from error
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise ImageUpdateError("Invalid image update service response")
    if not response["ok"]:
        raise ImageUpdateError(str(response.get("error", "Image update failed")))
    result = response.get("result")
    if not isinstance(result, dict):
        raise ImageUpdateError("Invalid image update service result")
    return result


def _package_update(kind: str, name: str) -> int:
    """Wait for a privileged package update, forwarding cancellation."""
    cancelled = threading.Event()

    def cancel(_signum: int, _frame: object) -> None:
        cancelled.set()

    signal.signal(signal.SIGTERM, cancel)
    token = request("package_start", kind=kind, name=name).get("token")
    if not isinstance(token, str):
        raise ImageUpdateError("Invalid package update token")
    sent_cancel = False
    while True:
        if cancelled.is_set() and not sent_cancel:
            request("package_cancel", token=token)
            sent_cancel = True
        result = request("package_status", token=token)
        state = result.get("state")
        if state == "running":
            cancelled.wait(0.5) if not sent_cancel else threading.Event().wait(0.5)
            continue
        if state == "succeeded":
            return 0
        if state == "cancelled":
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)
            return 1
        if state == "failed":
            raise ImageUpdateError(f"Package update failed (exit {result.get('returncode')})")
        raise ImageUpdateError("Invalid package update status")


def main() -> int:
    """Run one broker-backed package update for the existing update gateway."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("apt", "flatpak"))
    parser.add_argument("name")
    arguments = parser.parse_args()
    try:
        return _package_update(arguments.kind, arguments.name)
    except ImageUpdateError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
