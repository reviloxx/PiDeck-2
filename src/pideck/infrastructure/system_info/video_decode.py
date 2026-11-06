"""Report hardware video decoding support through VDPAU and VA-API tools."""

import re

from pideck.domain.system_info import VideoDecodeInfo

from .commands import CommandRunner

_FAMILIES = (
    ("HEVC", "HEVC"), ("H264", "H.264"), ("VP9", "VP9"), ("VP8", "VP8"), ("AV1", "AV1"),
    ("VC1", "VC-1"), ("MPEG4", "MPEG-4"), ("MPEG2", "MPEG-2"), ("MPEG1", "MPEG-1"), ("JPEG", "JPEG"),
)
_ORDER = [label for _, label in _FAMILIES]


def codec_family(profile: str) -> str | None:
    """Map a VDPAU or VA-API profile name to a codec family label."""
    name = profile.upper().removeprefix("VAPROFILE")
    for prefix, label in _FAMILIES:
        if name.startswith(prefix):
            return label
    return None


def _sorted(families: set[str]) -> tuple[str, ...]:
    return tuple(sorted(families, key=_ORDER.index))


def parse_vdpauinfo(text: str) -> VideoDecodeInfo:
    """Parse the driver string and decoder table of ``vdpauinfo``."""
    driver = None
    match = re.search(r"^Information string:\s*(.+?)\s*$", text, re.MULTILINE)
    if match:
        driver = " ".join(match.group(1).split())
        # Drop the build date that follows the version number.
        driver = re.sub(r"^(.*?\d+\.\d+(?:\.\d+)*)\s.*$", r"\1", driver)
    families: set[str] = set()
    _, _, table = text.partition("Decoder capabilities:")
    # The decoder table ends at the next section heading such as "Output surface:".
    table = re.split(r"^\S[^\n]*:\s*$", table, maxsplit=1, flags=re.MULTILINE)[0]
    for line in table.splitlines():
        fields = line.split()
        if len(fields) < 2 or "not supported" in line or not fields[1].isdigit():
            continue
        family = codec_family(fields[0])
        if family:
            families.add(family)
    return VideoDecodeInfo("VDPAU", driver, _sorted(families), bool(families))


def parse_vainfo(text: str) -> VideoDecodeInfo:
    """Parse the driver string and decode (VLD) entrypoints of ``vainfo``."""
    driver = None
    match = re.search(r"Driver version:\s*(.+?)\s*$", text, re.MULTILINE)
    if match:
        driver = match.group(1)
    families: set[str] = set()
    for profile in re.findall(r"^\s*(VAProfile\w+)\s*:\s*VAEntrypointVLD\b", text, re.MULTILINE):
        family = codec_family(profile)
        if family:
            families.add(family)
    return VideoDecodeInfo("VA-API", driver, _sorted(families), bool(families))


def read_video_decoding(run: CommandRunner) -> tuple[VideoDecodeInfo, ...]:
    """Query both APIs; an unavailable tool or driver yields an unavailable entry."""
    results = []
    for api, command, parser in (
        ("VDPAU", ["vdpauinfo"], parse_vdpauinfo),
        ("VA-API", ["vainfo"], parse_vainfo),
    ):
        output = run(command)
        results.append(parser(output) if output else VideoDecodeInfo(api, None, (), False))
    return tuple(results)
