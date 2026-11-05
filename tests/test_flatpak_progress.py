"""Verify flatpak CLI output is translated into overall installation progress."""

from pideck.application.ports.installation import InstallProgress, InstallStage
from pideck.infrastructure.flatpak_progress import FlatpakProgressParser

# Captured from flatpak 1.14 with LANG=C and stdout redirected to a pipe.
CAPTURED = [
    "Looking for matches?",
    "",
    "org.gnome.Chess permissions:",
    "    ipc\tfallback-x11\twayland\tdri",
    " 1.\t   \torg.gnome.Chess.Locale\tstable\ti\tflathub\t< 628.5?kB (partial)",
    " 2.\t   \torg.gnome.Chess\tstable\ti\tflathub\t< 1.6?MB",
    "Installing 1/2?",
    "Installing 1/2?                        0%  0 bytes/s",
    "Installing 1/2? ?                      4%",
    "Installing 1/2? ???????????????????? 100%",
    "Installing 2/2?",
    "Installing 2/2?                        0%  0 bytes/s",
    "Installing 2/2? ?????????             50%  1.2?MB/s  00:02",
    "Installing 2/2? ???????????????????? 100%",
    "Installing 2/2? ???????????????????? 100%",
    "Installation complete.",
]


def _feed(lines: list[str]) -> list[InstallProgress]:
    parser = FlatpakProgressParser()
    return [progress for line in lines if (progress := parser.feed(line)) is not None]


def test_operations_are_weighted_by_announced_download_size() -> None:
    """The small locale step contributes little; duplicates are suppressed."""
    progress = _feed(CAPTURED)
    assert all(item.stage is InstallStage.DOWNLOADING for item in progress)
    assert [(item.operation, item.percent) for item in progress] == [
        (1, 0), (1, 1), (1, 28), (2, 28), (2, 64), (2, 100),
    ]
    assert progress[-1] == InstallProgress(InstallStage.DOWNLOADING, 100, 2, 2)


def test_without_sizes_operations_are_weighted_equally() -> None:
    """Missing table rows fall back to equal operation weights."""
    progress = _feed([
        "Installing 1/2…", "Installing 1/2… ████ 50%", "Installing 2/2… ████ 50%",
    ])
    assert [item.percent for item in progress] == [0, 25, 75]


def test_single_operation_and_unicode_output() -> None:
    """A lone operation omits its counter; UTF-8 ellipsis and bars also parse."""
    progress = _feed(["Installing…", "Installing… █████▌ 37%  2.0 MB/s  00:10"])
    assert progress == [
        InstallProgress(InstallStage.DOWNLOADING, 0, 1, 1),
        InstallProgress(InstallStage.DOWNLOADING, 37, 1, 1),
    ]


def test_unrelated_and_malformed_lines_are_ignored() -> None:
    """Noise never produces progress or exceptions."""
    assert _feed([
        "Installation complete.", "Installing app/com.ktechpit.red/x86_64/stable",
        "Installing 3/2…", "Installing 0/2…", "error: something 50%", "",
    ]) == []
