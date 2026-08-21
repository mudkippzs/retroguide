"""Playability + partial-download gates."""
from __future__ import annotations

from pathlib import Path

from tvguide.util.media_ok import is_partial_path, program_playable
from tvguide.util.naming import is_excluded, is_special_episode, looks_like_anime


def test_partial_suffixes():
    assert is_partial_path("/lib/foo.mkv.part")
    assert is_partial_path("/lib/foo.crdownload")
    assert is_partial_path("/lib/.hidden.mkv")
    assert is_partial_path(Path("/lib/foo.!qB"))
    assert not is_partial_path("/lib/foo.mkv")


def test_is_excluded_partials():
    assert is_excluded(Path("/tv/show.mkv.part"), [])
    assert is_excluded(Path("/tv/show.crdownload"), ["sample"])
    assert not is_excluded(Path("/tv/Show S01E01.mkv"), ["sample"])


def test_special_episodes():
    assert is_special_episode(0, 1)
    assert is_special_episode(99, 0)
    assert is_special_episode(99, 5)
    assert is_special_episode(1, 0)
    assert not is_special_episode(1, 1)
    assert not is_special_episode(7, 12)


def test_looks_like_anime():
    assert looks_like_anime("Cowboy Bebop", genres=["Anime", "Action"])
    assert looks_like_anime("Naruto", genres=["Animation"], origin="JP")
    assert looks_like_anime("Something", path="/Anime/OVA/foo.mkv",
                            genres=["Animation"])
    assert not looks_like_anime("The Simpsons", genres=["Animation", "Comedy"])


def test_program_playable_requires_file(tmp_path):
    real = tmp_path / "ok.mkv"
    real.write_bytes(b"x" * 100)
    assert program_playable(str(real), "done", 120.0, "h264")
    assert not program_playable(str(tmp_path / "gone.mkv"), "done", 120.0, "h264")
    assert not program_playable(str(real), "error", 120.0, "h264")
    assert not program_playable(str(real), "done", 10.0, "h264")
    assert not program_playable(str(real), "done", 120.0, None, require_codec=True)
    assert not program_playable(str(real), "done", 120.0, "")
