"""ROAMER-VOD: on-demand HLS for the mobile binge mode.

One ffmpeg per playing item transcodes the whole file as fast as the CPU
allows (no ``-re``) into an HLS *event* playlist on disk. iOS Safari's native
player gets the real deal: a scrubber that seeks within the transcoded
portion, pause/resume for as long as the session lives, fullscreen/AirPlay,
and — when the source has a text subtitle track — a genuine WebVTT sidecar, so
CC is the native iOS subtitle menu rather than a burn-in.

Sessions are keyed by media item (``ep:<id>`` / ``film:<id>``, ``+subs`` for
the burn-in variant used with bitmap subs) so re-tapping a half-transcoded
film resumes the warm session instead of starting over. The manager shares the
global transcode cap with live HLS and reaps sessions idle beyond
``[stream].vod_idle_min``; leftovers from a crashed process are swept at
startup.

Session dirs live on disk (default ``<data dir>/vod``), never tmpfs — a
two-hour film at 720p is ~1.5GB, which has no business in RAM.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..config import DATA_DIR, Config
from .media import encode_chain, english_subtitle
from .procutil import kill_proc

log = logging.getLogger("tvguide.stream")

_KEY_RE = re.compile(r"^(ep|film):\d+(\+subs)?$")
_SEG_RE = re.compile(r"^seg_\d{1,6}\.ts$")
_SUB_RE = re.compile(r"^sub_\d{1,6}\.vtt$")
_HLS_TIME = 4               # seconds per segment, matches the live HLS path
_FIRST_SEG_WAIT = 14        # how long playlist() waits for ffmpeg's first seg

_MASTER = """#EXTM3U
{subs}#EXT-X-STREAM-INF:BANDWIDTH=1800000{subs_inf}
v.m3u8
"""
_SUB_MEDIA = ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",'
              'LANGUAGE="eng",DEFAULT=NO,AUTOSELECT=YES,URI="s.m3u8"\n')


def vod_root(cfg: Config) -> Path:
    return Path(cfg.stream.vod_dir) if cfg.stream.vod_dir else DATA_DIR / "vod"


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.match(key or ""))


class VodSession:
    """One item transcoding to an on-disk HLS event playlist."""

    def __init__(self, key: str, path: str, duration: float, cfg: Config):
        self.key = key
        self.burn_subs = key.endswith("+subs")
        self.duration = duration
        self.dir = vod_root(cfg) / f"{key.replace(':', '-')}-{int(time.time())}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.last_access = time.monotonic()
        self.has_vtt = False
        self.proc = self._spawn(path, cfg)
        self._write_master()
        log.info("vod start key=%s pid=%s \"%s\" dir=%s vtt=%s",
                 key, self.proc.pid, path, self.dir, self.has_vtt)

    def _spawn(self, path: str, cfg: Config) -> subprocess.Popen:
        kind, si = english_subtitle(path)
        burn = self.burn_subs and kind is not None
        pre, post = encode_chain(
            path, transcode=True, subs=burn, tmpdir=str(self.dir) if burn else None,
            normalize_audio=cfg.stream.normalize_audio)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *pre,
               "-i", path, *post,
               "-f", "hls", "-hls_time", str(_HLS_TIME),
               "-hls_playlist_type", "event",
               "-hls_flags", "independent_segments",
               "-hls_segment_filename", str(self.dir / "seg_%05d.ts"),
               str(self.dir / "v.m3u8")]
        # Native CC: sidecar WebVTT for text subs when we're not burning.
        if not burn and kind == "text":
            self.has_vtt = True
            cmd += ["-map", f"0:s:{si}", "-c:s", "webvtt",
                    "-f", "hls", "-hls_time", str(_HLS_TIME),
                    "-hls_playlist_type", "event",
                    "-hls_flags", "independent_segments",
                    "-hls_segment_filename", str(self.dir / "sub_%05d.vtt"),
                    str(self.dir / "s.m3u8")]
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def _write_master(self) -> None:
        subs = _SUB_MEDIA if self.has_vtt else ""
        subs_inf = ',SUBTITLES="subs"' if self.has_vtt else ""
        (self.dir / "index.m3u8").write_text(
            _MASTER.format(subs=subs, subs_inf=subs_inf))

    def touch(self) -> None:
        self.last_access = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self.last_access

    def read_file(self, name: str) -> bytes | None:
        """Serve one session artifact; names are whitelisted, never pathed."""
        if not (name in ("v.m3u8", "s.m3u8") or _SEG_RE.match(name)
                or _SUB_RE.match(name)):
            return None
        try:
            return (self.dir / name).read_bytes()
        except OSError:
            return None

    def status(self) -> dict:
        segs = len(list(self.dir.glob("seg_*.ts")))
        done = False
        try:
            done = b"#EXT-X-ENDLIST" in (self.dir / "v.m3u8").read_bytes()
        except OSError:
            pass
        return {"transcoded_sec": min(segs * _HLS_TIME, self.duration),
                "duration": self.duration, "done": done, "alive": self.alive}

    def stop(self) -> None:
        kill_proc(self.proc)
        shutil.rmtree(self.dir, ignore_errors=True)
        log.info("vod stop  key=%s pid=%s", self.key, self.proc.pid)


class VodManager:
    """Owns VOD sessions, shares the global transcode cap, reaps the idle."""

    def __init__(self, cfg: Config, state):
        self.cfg = cfg
        self.state = state            # _StreamState (shared concurrency cap)
        self._sessions: dict[str, VodSession] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sweep_stale()
        self._reaper = threading.Thread(
            target=self._reap_loop, name="retroguide-vod-reaper", daemon=True)
        self._reaper.start()

    def _sweep_stale(self) -> None:
        """Remove session dirs a previous process left behind."""
        root = vod_root(self.cfg)
        if not root.is_dir():
            return
        for d in root.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)

    def start(self, key: str, path: str, duration: float) -> VodSession | None:
        """Ensure a session for ``key``; returns None when at the stream cap."""
        if not valid_key(key):
            return None
        with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and not sess.alive:
                self._teardown(key, sess)
                sess = None
            if sess is None:
                if not self.state.try_acquire():
                    log.warning("vod busy (%d/%d) - rejecting %s",
                                self.state.active, self.state.max_streams, key)
                    return None
                try:
                    sess = VodSession(key, path, duration, self.cfg)
                except Exception:  # noqa: BLE001
                    self.state.release()
                    log.exception("vod session start failed for %s", key)
                    return None
                self.state.register(sess.proc)
                self._sessions[key] = sess
            sess.touch()
        # Wait briefly for ffmpeg's first segment so the player gets a usable
        # media playlist on its first request rather than a stall.
        deadline = time.monotonic() + _FIRST_SEG_WAIT
        while time.monotonic() < deadline:
            data = sess.read_file("v.m3u8")
            if data and b"seg_" in data:
                break
            if not sess.alive:
                break
            time.sleep(0.25)
        return sess

    def get(self, key: str) -> VodSession | None:
        with self._lock:
            sess = self._sessions.get(key)
        if sess is not None:
            sess.touch()
        return sess

    def _teardown(self, key: str, sess: VodSession) -> None:
        """Stop a session and free its registry slot + concurrency token.
        Caller must hold ``self._lock``."""
        self.state.unregister(sess.proc)
        sess.stop()
        self.state.release()
        self._sessions.pop(key, None)

    def _reap_loop(self) -> None:
        idle_sec = max(1, self.cfg.stream.vod_idle_min) * 60
        while not self._stop.wait(10):
            with self._lock:
                for key, sess in list(self._sessions.items()):
                    # A finished (fully transcoded) session costs no CPU; give
                    # it the same idle window so pause/resume still works.
                    if sess.idle_for > idle_sec:
                        log.info("vod reap key=%s (idle)", key)
                        self._teardown(key, sess)

    def stop_all(self) -> None:
        self._stop.set()
        with self._lock:
            for key, sess in list(self._sessions.items()):
                self._teardown(key, sess)
