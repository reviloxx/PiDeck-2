"""Revert a temporary X11 display mode unless its parent confirms it."""

import argparse
import select
import subprocess
import sys
import time


def main() -> int:
    """Wait for confirmation, reverting on timeout, cancellation, or parent exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--rate", required=True)
    parser.add_argument("--new-mode", required=True)
    parser.add_argument("--new-rate", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        return 2
    failure: str | None = None
    try:
        result = subprocess.run(
            ["xrandr", "--output", args.output, "--mode", args.new_mode, "--rate", args.new_rate],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        failure = str(error)
    else:
        if result.returncode:
            failure = result.stderr.strip() or "xrandr rejected the mode"
    if failure is None:
        deadline = time.monotonic() + args.timeout
        try:
            print(f"READY {deadline}", flush=True)
        except (BrokenPipeError, OSError):
            failure = "The display controller disconnected"
        else:
            readable, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
            command = sys.stdin.readline().strip() if readable else ""
            if command == "confirm" and time.monotonic() <= deadline:
                print("CONFIRMED", flush=True)
                return 0
    try:
        result = subprocess.run(
            ["xrandr", "--output", args.output, "--mode", args.mode, "--rate", args.rate],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"ROLLBACK_FAILED: {error}", flush=True)
        return 1
    if result.returncode:
        print(f"ROLLBACK_FAILED: {result.stderr.strip() or 'xrandr rejected the mode'}", flush=True)
        return 1
    if failure is not None:
        print(f"APPLY_FAILED: {failure}", flush=True)
        return 1
    print("REVERTED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
