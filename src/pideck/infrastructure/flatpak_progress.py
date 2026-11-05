"""Translate ``flatpak install`` text output into overall installation progress.

Flatpak prints a plain line-oriented format when stdout is not a terminal:
an operation table with download sizes, then ``Installing 1/2…`` followed by
repeated ``Installing 1/2… <bar> 45%`` lines. In the C locale, glib replaces
non-ASCII characters (ellipsis, bar blocks, non-breaking spaces) with ``?``,
so the patterns below are intentionally lenient. Unrecognized lines are ignored,
so a changed output format only degrades progress to indeterminate.
"""

import re

from pideck.application.ports.installation import InstallProgress, InstallStage

_OPERATION = re.compile(r"^(?:Installing|Updating)(?: (\d+)/(\d+))?(?=[^\w\s]|$)")
_PERCENT = re.compile(r"(\d{1,3})%")
_TABLE_ROW = re.compile(r"^\s*(\d+)\.\s.*<\s*([\d.]+)\W*(bytes?|kB|MB|GB|TB)\b")
_UNITS = {"byte": 1, "bytes": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


class FlatpakProgressParser:
    """Stateful parser for one ``flatpak install`` invocation."""

    def __init__(self) -> None:
        """Start without any known operations."""
        self._sizes: dict[int, float] = {}
        self._last: InstallProgress | None = None

    def feed(self, line: str) -> InstallProgress | None:
        """Consume one output line and return progress only when it changed."""
        line = line.rstrip("\r\n")
        row = _TABLE_ROW.match(line)
        if row is not None:
            self._sizes[int(row.group(1))] = float(row.group(2)) * _UNITS[row.group(3)]
            return None
        match = _OPERATION.match(line)
        if match is None:
            return None
        operation = int(match.group(1)) if match.group(1) else 1
        operations = int(match.group(2)) if match.group(2) else 1
        if not 1 <= operation <= operations:
            return None
        percents = _PERCENT.findall(line[match.end():])
        step = min(int(percents[-1]), 100) if percents else 0
        progress = InstallProgress(
            InstallStage.DOWNLOADING,
            self._overall(operation, operations, step),
            operation,
            operations,
        )
        if progress == self._last:
            return None
        self._last = progress
        return progress

    def _overall(self, operation: int, operations: int, step: int) -> int:
        """Weight operations by announced download size when every size is known."""
        known = [self._sizes[index] for index in range(1, operations + 1) if index in self._sizes]
        if len(known) == operations and sum(known) > 0:
            done = sum(known[:operation - 1]) + known[operation - 1] * step / 100
            return min(100, int(done * 100 / sum(known)))
        return min(100, ((operation - 1) * 100 + step) // operations)
