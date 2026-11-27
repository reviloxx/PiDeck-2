"""Tests for shipping and extracting uBlock Origin Lite for YouTube TV."""

from pathlib import Path
import zipfile

from pideck.infrastructure.ublock_lite import ensure_ublock_origin_lite, shipped_extension_zip

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_ublock_zip_is_present() -> None:
    """The release assets include a Chromium uBOL zip."""
    path = shipped_extension_zip(ROOT / "assets")
    assert path is not None and path.is_file()
    with zipfile.ZipFile(path) as archive:
        assert "manifest.json" in archive.namelist()


def test_ensure_ublock_extracts_once(tmp_path: Path) -> None:
    """The zip is extracted into the user config tree and reused afterwards."""
    home = tmp_path / "home"
    home.mkdir()
    first = ensure_ublock_origin_lite(ROOT / "assets", home)
    assert first is not None
    assert (first / "manifest.json").is_file()
    stamp = first / ".pideck-version"
    assert stamp.is_file()
    mtime = stamp.stat().st_mtime_ns
    second = ensure_ublock_origin_lite(ROOT / "assets", home)
    assert second == first
    assert stamp.stat().st_mtime_ns == mtime
