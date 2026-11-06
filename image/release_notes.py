#!/usr/bin/env python3
"""Extract one version's release notes from CHANGELOG.md for a GitHub release."""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
_HEADING = re.compile(r"^## (v\d+\.\d+\.\d+)\s*$")


def release_notes(changelog: str, tag: str) -> str:
    """Return the body of the ``## <tag>`` section, without its heading."""
    lines: list[str] | None = None
    for line in changelog.splitlines():
        heading = _HEADING.match(line)
        if heading is not None:
            if lines is not None:
                break
            if heading[1] == tag:
                lines = []
            continue
        if lines is not None:
            lines.append(line)
    notes = "\n".join(lines or []).strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md has no release notes for {tag}")
    return notes + "\n"


def main() -> int:
    """Write a tag's release notes to a file for ``gh release create --notes-file``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        notes = release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), args.tag)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
