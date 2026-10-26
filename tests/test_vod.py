"""ROAMER-VOD: session command construction, artifact serving, lifecycle."""
from __future__ import annotations

import time

from tvguide.stream import vod


class _FakeProc:
    pid = 4321

    def poll(self):
        return None  # still running

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _make_session(tmp_path, monkeypatch, key="ep:1", sub=(None, -1)):
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(vod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(vod, "english_subtitle", lambda _p: sub)
    # Keep encode_chain off ffprobe (and off the patched subprocess module).
    monkeypatch.setattr("tvguide.stream.media.english_audio_map",
                        lambda _p: "0:a:0")
    monkeypatch.setattr("tvguide.stream.media.english_subtitle",
                        lambda _p: sub)
    media = tmp_path / "show.mkv"
    media.write_bytes(b"0" * 64)

    class _Cfg:
        class stream:
            normalize_audio = True
            vod_dir = str(tmp_path / "vodcache")
            vod_idle_min = 45

    sess = vod.VodSession(key, str(media), 1320.0, _Cfg())
    return sess, captured["cmd"]


def test_valid_key():
    assert vod.valid_key("ep:12") and vod.valid_key("film:3")
    assert vod.valid_key("ep:12+subs")
    for bad in ("", "ep:abc", "../etc", "ep:1;rm", "foo:1", "ep:1/.."):
        assert not vod.valid_key(bad)


def test_session_builds_event_hls(tmp_path, monkeypatch):
    sess, cmd = _make_session(tmp_path, monkeypatch)
    assert "-hls_playlist_type" in cmd and "event" in cmd
    assert str(sess.dir / "v.m3u8") in cmd
    assert "webvtt" not in cmd  # no text subs -> no sidecar output
    master = (sess.dir / "index.m3u8").read_text()
    assert "EXT-X-STREAM-INF" in master and "v.m3u8" in master
    assert "EXT-X-MEDIA" not in master


def test_session_webvtt_sidecar(tmp_path, monkeypatch):
    sess, cmd = _make_session(tmp_path, monkeypatch, sub=("text", 1))
    assert sess.has_vtt
    assert "webvtt" in cmd and str(sess.dir / "s.m3u8") in cmd
    master = (sess.dir / "index.m3u8").read_text()
    assert 'TYPE=SUBTITLES' in master and 'URI="s.m3u8"' in master
    assert 'SUBTITLES="subs"' in master


def test_session_burn_variant_has_no_sidecar(tmp_path, monkeypatch):
    sess, cmd = _make_session(tmp_path, monkeypatch, key="ep:1+subs",
                              sub=("text", 0))
    assert not sess.has_vtt and "webvtt" not in cmd
    assert "subtitles=" in " ".join(cmd)  # burn-in filter instead


def test_read_file_whitelist(tmp_path, monkeypatch):
    sess, _ = _make_session(tmp_path, monkeypatch)
    (sess.dir / "seg_00001.ts").write_bytes(b"ts")
    (sess.dir / "sub_00001.vtt").write_bytes(b"vtt")
    assert sess.read_file("seg_00001.ts") == b"ts"
    assert sess.read_file("sub_00001.vtt") == b"vtt"
    assert sess.read_file("v.m3u8") is None  # not written by ffmpeg in test
    for bad in ("../config.toml", "index.m3u8", "seg_1.txt", "foo.ts",
                "seg_0000001.ts"):
        assert sess.read_file(bad) is None


def test_status_progress(tmp_path, monkeypatch):
    sess, _ = _make_session(tmp_path, monkeypatch)
    for i in range(3):
        (sess.dir / f"seg_{i:05d}.ts").write_bytes(b"ts")
    st = sess.status()
    assert st["transcoded_sec"] == 12 and st["duration"] == 1320.0
    assert not st["done"] and st["alive"]
    (sess.dir / "v.m3u8").write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
    assert sess.status()["done"]


class _FakeState:
    def __init__(self):
        self.active = 0

    def try_acquire(self):
        return True

    def release(self):
        pass

    def register(self, p):
        self.active += 1

    def unregister(self, p):
        self.active -= 1


def test_manager_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(vod, "english_subtitle", lambda _p: (None, -1))
    monkeypatch.setattr("tvguide.stream.media.english_audio_map",
                        lambda _p: "0:a:0")
    monkeypatch.setattr("tvguide.stream.media.english_subtitle",
                        lambda _p: (None, -1))
    monkeypatch.setattr(vod.subprocess, "Popen", lambda cmd, **kw: _FakeProc())
    # The fake ffmpeg writes no segments; don't wait the full first-seg window.
    monkeypatch.setattr(vod, "_FIRST_SEG_WAIT", 0.3)

    class _Cfg:
        class stream:
            normalize_audio = True
            vod_dir = str(tmp_path / "vodcache")
            vod_idle_min = 45

    media = tmp_path / "show.mkv"
    media.write_bytes(b"0" * 64)
    state = _FakeState()
    mgr = vod.VodManager(_Cfg(), state)
    sess = mgr.start("ep:1", str(media), 100.0)
    assert sess is not None and state.active == 1
    # Same key reuses the warm session.
    assert mgr.start("ep:1", str(media), 100.0) is sess
    assert mgr.get("ep:1") is sess
    # Idle reaper tears it down and frees the cap.
    sess.last_access = time.monotonic() - 999999
    mgr._stop.set()
    with mgr._lock:
        for k, s in list(mgr._sessions.items()):
            if s.idle_for > 45 * 60:
                mgr._teardown(k, s)
    assert mgr.get("ep:1") is None and state.active == 0
    assert not sess.dir.exists()
    mgr.stop_all()


def test_manager_rejects_bad_key_and_sweeps(tmp_path, monkeypatch):
    class _Cfg:
        class stream:
            normalize_audio = True
            vod_dir = str(tmp_path / "vodcache")
            vod_idle_min = 45

    stale = tmp_path / "vodcache" / "ep-9-1"
    stale.mkdir(parents=True)
    (stale / "seg_00001.ts").write_bytes(b"ts")
    mgr = vod.VodManager(_Cfg(), _FakeState())
    assert not stale.exists()  # startup sweep removed the crashed session
    assert mgr.start("ep:abc", "/x", 1.0) is None
    mgr.stop_all()
