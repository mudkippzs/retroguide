"""Stream encode_chain: audio normalization wiring."""
from __future__ import annotations

from tvguide.stream.media import encode_chain
from tvguide.util.audio_norm import AUDIO_NORM


def test_encode_chain_includes_dynaudnorm(tmp_path, monkeypatch):
    media = tmp_path / "show.mkv"
    media.write_bytes(b"0" * 64)
    monkeypatch.setattr("tvguide.stream.media.english_audio_map",
                        lambda _p: "0:a:0")
    monkeypatch.setattr("tvguide.stream.media.english_subtitle",
                        lambda _p: (None, -1))
    _pre, post = encode_chain(str(media), transcode=True, subs=False,
                              tmpdir=None, normalize_audio=True)
    assert "-af" in post
    assert AUDIO_NORM in post


def test_encode_chain_can_disable_norm(tmp_path, monkeypatch):
    media = tmp_path / "show.mkv"
    media.write_bytes(b"0" * 64)
    monkeypatch.setattr("tvguide.stream.media.english_audio_map",
                        lambda _p: "0:a:0")
    monkeypatch.setattr("tvguide.stream.media.english_subtitle",
                        lambda _p: (None, -1))
    _pre, post = encode_chain(str(media), transcode=True, subs=False,
                              tmpdir=None, normalize_audio=False)
    assert "-af" not in post
