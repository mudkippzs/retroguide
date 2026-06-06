"""Live HLS for Apple devices (iOS/macOS Safari won't play our open-ended
progressive MP4 stream).

Design: one ffmpeg per channel, transcoding the *currently airing* program
(input-seeked to the live wall-clock offset, paced with ``-re``) into a
sliding-window HLS playlist. ffmpeg finalizes the playlist with ``#EXT-X-ENDLIST``
when the program ends, which fires the browser's ``ended`` event; the page then
reloads and the manager rebuilds the session for whatever is now airing.

This deliberately avoids ffmpeg's concat demuxer and ``append_list`` -- both
proved fragile across a heterogeneous library (differing codecs/audio layouts
stall or corrupt the playlist). Every ffmpeg here only ever touches one file.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from ..config import Config
from ..db import connect
from .. import repo
from .procutil import kill_proc

log = logging.getLogger("tvguide.stream")

_SEG_RE = re.compile(r"^seg_\d{1,6}\.ts$")
_IDLE_SEC = 30          # tear down a channel nobody is watching
_FIRST_SEG_WAIT = 14    # how long /index.m3u8 waits for ffmpeg's first segment


def _now_program(slug: str):
    """(program, offset_seconds) for the live moment, or (None, 0)."""
    conn = connect()
    try:
        ch = next((c for c in repo.list_channels(conn) if c.slug == slug), None)
        if ch is None:
            return None, 0.0
        now = time.time()
        prog = repo.program_at(conn, ch.id, now)
        if prog is None or not prog.path:
            return None, 0.0
        return prog, max(0.0, now - prog.start_ts)
    finally:
        conn.close()


class HlsSession:
    """One ffmpeg transcoding the current program of a channel to HLS."""

    def __init__(self, slug: str, program, offset: float, cfg: Config):
        self.slug = slug
        self.program_id = program.id
        self.cfg = cfg
        self.dir = Path(tempfile.mkdtemp(prefix=f"rg-hls-{slug}-"))
        self.playlist = self.dir / "index.m3u8"
        self.last_access = time.monotonic()
        self.proc = self._spawn(program.path, offset)
        log.info("hls start ch=%s pid=%s offset=%ds \"%s\" dir=%s",
                 slug, self.proc.pid, int(offset), program.display_title, self.dir)

    def _spawn(self, path: str, offset: float):
        import subprocess
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if offset > 1:
            cmd += ["-ss", f"{offset:.3f}"]
        cmd += [
            "-re", "-i", path,
            "-map", "0:v:0", "-map", "0:a:0?", "-sn",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-vf", "scale=-2:min(720\\,ih)", "-g", "48", "-keyint_min", "48",
            "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", "4", "-hls_list_size", "6",
            # No omit_endlist: ffmpeg writes #EXT-X-ENDLIST when the program
            # ends, which the page uses as the cue to roll to the next show.
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", str(self.dir / "seg_%05d.ts"),
            str(self.playlist),
        ]
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def touch(self) -> None:
        self.last_access = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self.last_access

    def read_playlist(self) -> bytes | None:
        try:
            return self.playlist.read_bytes()
        except OSError:
            return None

    def read_segment(self, name: str) -> bytes | None:
        if not _SEG_RE.match(name):
            return None
        try:
            return (self.dir / name).read_bytes()
        except OSError:
            return None

    def stop(self) -> None:
        kill_proc(self.proc)
        shutil.rmtree(self.dir, ignore_errors=True)
        log.info("hls stop  ch=%s pid=%s", self.slug, self.proc.pid)


class HlsManager:
    """Owns per-channel HLS sessions, shares the global transcode cap, and
    reaps idle/dead sessions."""

    def __init__(self, cfg: Config, state):
        self.cfg = cfg
        self.state = state            # _StreamState (shared concurrency cap)
        self._sessions: dict[str, HlsSession] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper = threading.Thread(
            target=self._reap_loop, name="retroguide-hls-reaper", daemon=True)
        self._reaper.start()

    def playlist(self, slug: str) -> bytes | None:
        """Ensure a session for the current program and return its playlist.
        Returns None for off-air / busy / not-ready (the client retries)."""
        prog, offset = _now_program(slug)
        if prog is None:
            self._drop(slug)
            return None

        with self._lock:
            sess = self._sessions.get(slug)
            if sess is not None and (not sess.alive or sess.program_id != prog.id):
                self._teardown(slug, sess)
                sess = None
            if sess is None:
                if not self.state.try_acquire():
                    log.warning("hls busy (%d/%d) - rejecting %s",
                                self.state.active, self.state.max_streams, slug)
                    return None
                try:
                    sess = HlsSession(slug, prog, offset, self.cfg)
                except Exception:  # noqa: BLE001
                    self.state.release()
                    log.exception("hls session start failed for %s", slug)
                    return None
                self.state.register(sess.proc)
                self._sessions[slug] = sess
            sess.touch()

        # Wait briefly for ffmpeg's first segment so the player gets a usable
        # playlist on the first request rather than a 404.
        deadline = time.monotonic() + _FIRST_SEG_WAIT
        while time.monotonic() < deadline:
            data = sess.read_playlist()
            if data and b"seg_" in data:
                return data
            if not sess.alive:
                break
            time.sleep(0.25)
        return sess.read_playlist()

    def segment(self, slug: str, name: str) -> bytes | None:
        with self._lock:
            sess = self._sessions.get(slug)
        if sess is None:
            return None
        sess.touch()
        return sess.read_segment(name)

    def _teardown(self, slug: str, sess: HlsSession) -> None:
        """Stop a session and free its registry slot + concurrency token.
        Caller must hold ``self._lock``."""
        self.state.unregister(sess.proc)
        sess.stop()
        self.state.release()
        self._sessions.pop(slug, None)

    def _drop(self, slug: str) -> None:
        with self._lock:
            sess = self._sessions.get(slug)
            if sess is not None:
                self._teardown(slug, sess)

    def _reap_loop(self) -> None:
        while not self._stop.wait(5):
            with self._lock:
                for slug, sess in list(self._sessions.items()):
                    if not sess.alive or sess.idle_for > _IDLE_SEC:
                        reason = "dead" if not sess.alive else "idle"
                        log.info("hls reap ch=%s (%s)", slug, reason)
                        self._teardown(slug, sess)

    def stop_all(self) -> None:
        self._stop.set()
        with self._lock:
            for slug, sess in list(self._sessions.items()):
                self._teardown(slug, sess)
