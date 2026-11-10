"""Verify PiDeck installs a Kodi keymap that stops fullscreen playback on Escape."""

from pathlib import Path

from pideck.infrastructure.kodi_keymap import configure_kodi_keymap

SEED = Path(__file__).resolve().parents[1] / "assets/app-defaults/kodi/pideck.xml"


def test_installs_escape_stop_keymap(tmp_path: Path) -> None:
    """Kodi launch writes Escape → Stop under FullscreenVideo and FullscreenGame."""
    configure_kodi_keymap(home=tmp_path, seed=SEED)
    path = tmp_path / ".kodi/userdata/keymaps/pideck.xml"
    text = path.read_text(encoding="utf-8")
    assert "<FullscreenVideo>" in text
    assert "<FullscreenGame>" in text
    assert "<escape>Stop</escape>" in text


def test_overwrites_managed_keymap_and_leaves_others(tmp_path: Path) -> None:
    """PiDeck's keymap is refreshed; unrelated user keymaps stay untouched."""
    root = tmp_path / ".kodi/userdata/keymaps"
    root.mkdir(parents=True)
    managed = root / "pideck.xml"
    other = root / "custom.xml"
    managed.write_text("<keymap />\n", encoding="utf-8")
    other.write_text("<keymap><!-- keep -->\n</keymap>\n", encoding="utf-8")

    configure_kodi_keymap(home=tmp_path, seed=SEED)

    assert "<escape>Stop</escape>" in managed.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == "<keymap><!-- keep -->\n</keymap>\n"
