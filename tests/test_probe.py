"""Probe must reject files without a real video stream / duration."""
from __future__ import annotations

from tvguide.scan.probe import ProbeResult, probe


def test_probe_missing_file():
    res = probe("/tmp/definitely-does-not-exist-retroguide-xyz.mkv", timeout=5)
    assert isinstance(res, ProbeResult)
    assert res.ok is False


def test_probe_rejects_empty_json(monkeypatch, tmp_path):
    fake = tmp_path / "empty.mkv"
    fake.write_bytes(b"not a video")

    class Fake:
        returncode = 0
        stdout = '{"streams": [], "format": {}}'
        stderr = ""

    monkeypatch.setattr("tvguide.scan.probe.subprocess.run", lambda *a, **k: Fake())
    res = probe(str(fake))
    assert res.ok is False
    assert res.error == "no-video-stream"


def test_probe_rejects_short_duration(monkeypatch, tmp_path):
    fake = tmp_path / "short.mkv"
    fake.write_bytes(b"x")

    class Fake:
        returncode = 0
        stdout = (
            '{"streams":[{"codec_type":"video","codec_name":"h264",'
            '"width":640,"height":360,"duration":"12.0"}],'
            '"format":{"duration":"12.0","format_name":"matroska"}}'
        )
        stderr = ""

    monkeypatch.setattr("tvguide.scan.probe.subprocess.run", lambda *a, **k: Fake())
    res = probe(str(fake))
    assert res.ok is False
    assert res.error == "bad-duration"


def test_probe_accepts_valid(monkeypatch, tmp_path):
    fake = tmp_path / "ok.mkv"
    fake.write_bytes(b"x")

    class Fake:
        returncode = 0
        stdout = (
            '{"streams":[{"codec_type":"video","codec_name":"h264",'
            '"width":1280,"height":720,"duration":"1320.0"}],'
            '"format":{"duration":"1320.0","format_name":"matroska"}}'
        )
        stderr = ""

    monkeypatch.setattr("tvguide.scan.probe.subprocess.run", lambda *a, **k: Fake())
    res = probe(str(fake))
    assert res.ok is True
    assert res.video_codec == "h264"
    assert res.duration_sec == 1320.0
