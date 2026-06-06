"""A tiny HTTP server that streams the *currently airing* program of a channel
to any browser on the LAN.

The desktop player stays the source of truth for the schedule; this just lets
you tune in from a phone/laptop/TV without the server's screen. Each channel
behaves like a real broadcast: you join at the wall-clock offset (you miss the
start), and when a program ends the page auto-advances to the next one.

Implementation notes
---------------------
* No extra dependencies -- stdlib ``http.server`` + ``ffmpeg`` (already a
  project dependency for probing).
* The video endpoint shells out to ffmpeg, input-seeks to the live offset and
  pipes a fragmented MP4 (``frag_keyframe+empty_moov``) straight to the
  response, which every modern browser plays progressively with no JS shim.
* Transcoding to H.264/AAC is on by default so HEVC/10-bit files (which
  browsers can't decode) still play. Flip ``[stream].transcode = false`` to
  remux instead when your library is already browser-friendly.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ..config import Config
from ..db import connect
from .. import repo
from . import web
from .hls import HlsManager
from .procutil import kill_proc

log = logging.getLogger("tvguide.stream")

_SEG_RE = re.compile(r"^seg_\d{1,6}\.ts$")

# How long a single response write may block on a stalled/half-open client
# before we tear the stream down (and reap its ffmpeg). Generous enough to
# survive normal buffering pauses; bounded so a slept iPad can't pin a thread.
_WRITE_TIMEOUT_SEC = 120


class _StreamState:
    """Shared, thread-safe accounting for live transcodes: a concurrency cap
    plus a registry so every ffmpeg can be reaped on shutdown."""

    def __init__(self, max_streams: int):
        self.max_streams = max(1, int(max_streams))
        self._sem = threading.BoundedSemaphore(self.max_streams)
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            pass

    def register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._procs)

    def kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            kill_proc(proc)
        if procs:
            log.info("reaped %d inflight transcode(s) on shutdown", len(procs))


def lan_ip() -> str:
    """Best-effort primary LAN address (no traffic is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


_audio_map_cache: dict[str, str] = {}
_audio_map_lock = threading.Lock()


def _english_audio_map(path: str) -> str:
    """Pick an English audio stream (``0:a:N``) when one is tagged, else the
    first. Cached per path so we don't run an ffprobe on every tune-in."""
    with _audio_map_lock:
        cached = _audio_map_cache.get(path)
    if cached is not None:
        return cached

    result = "0:a:0"
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index:stream_tags=language",
             "-of", "json", path],
            capture_output=True, text=True, timeout=15)
        streams = json.loads(out.stdout or "{}").get("streams", [])
        for i, s in enumerate(streams):
            lang = ((s.get("tags") or {}).get("language") or "").lower()
            if lang in ("eng", "en", "english"):
                result = f"0:a:{i}"
                break
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe audio map failed for %s: %s", path, exc)
    with _audio_map_lock:
        _audio_map_cache[path] = result
    return result


def _ffmpeg_cmd(path: str, offset: float, duration: float, transcode: bool) -> list[str]:
    # Input-seek (-ss before -i) is fast and frame-accurate enough for TV.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if offset > 1:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", path]
    if duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-map", "0:v:0", "-map", _english_audio_map(path) + "?", "-sn"]
    if transcode:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-vf", "scale=-2:min(720\\,ih)", "-g", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        ]
    else:
        # Copy video (assumes browser-friendly H.264); audio still -> aac.
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    cmd += [
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    return cmd


def _now_program(conn, slug: str):
    """Return (channel, program, offset, remaining) for the live moment."""
    channel = next((c for c in repo.list_channels(conn) if c.slug == slug), None)
    if channel is None:
        return None, None, 0.0, 0.0
    now = time.time()
    prog = repo.program_at(conn, channel.id, now)
    if prog is None:
        return channel, None, 0.0, 0.0
    offset = max(0.0, now - prog.start_ts)
    remaining = max(0.0, prog.end_ts - now)
    return channel, prog, offset, remaining


def _prog_dict(prog) -> dict | None:
    if prog is None:
        return None
    return {
        "id": prog.id,
        "title": prog.display_title,
        "subtitle": prog.subtitle,
        "blurb": prog.blurb,
        "daypart": prog.daypart,
        "kind": prog.kind,
        "start": prog.start_ts,
        "end": prog.end_ts,
        "duration": prog.duration_sec,
    }


def _make_handler(cfg: Config, state: _StreamState, hls: HlsManager):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 -> body is delimited by connection close, so we can stream
        # an unknown-length response without chunked encoding.
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):  # route access log to our logger
            log.debug("%s %s", self.address_string(), fmt % args)

        # -- helpers -------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, obj) -> None:
            self._send(200, json.dumps(obj).encode(), "application/json")

        # -- routing -------------------------------------------------------
        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._app()
                if path == "/api/now":
                    return self._api_now()
                if path == "/api/grid":
                    return self._api_grid()
                if path.startswith("/api/schedule/"):
                    return self._api_schedule(path[len("/api/schedule/"):])
                if path.startswith("/logo/"):
                    return self._logo(path[len("/logo/"):])
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2 and parts[0] == "c":
                    slug = parts[1]
                    if len(parts) == 2:
                        return self._app(slug)
                    if parts[2] == "now.json":
                        return self._now_json(slug)
                    if parts[2] == "live.mp4":
                        return self._live(slug)
                    if parts[2] == "index.m3u8":
                        return self._hls_playlist(slug)
                    if _SEG_RE.match(parts[2]):
                        return self._hls_segment(slug, parts[2])
                self._send(404, b"Not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - never let a worker thread die silently
                log.exception("request handler error for %s", self.path)

        def _app(self, slug: str | None = None):
            ui = cfg.ui
            page = web.render_app(slug, era=ui.era, crt=ui.crt, bw=ui.bw,
                                  hls=cfg.stream.hls)
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def _api_now(self):
            conn = connect()
            try:
                now = time.time()
                channels = []
                for c in repo.list_channels(conn):
                    prog = repo.program_at(conn, c.id, now)
                    after = prog.end_ts if prog else now
                    nxt = repo.next_program(conn, c.id, after)
                    channels.append({
                        "slug": c.slug, "name": c.name, "accent": c.accent,
                        "tagline": c.tagline, "logo": bool(c.logo),
                        "now": _prog_dict(prog), "next": _prog_dict(nxt),
                    })
            finally:
                conn.close()
            self._send_json({"server_now": now, "channels": channels})

        def _logo(self, slug: str):
            slug = slug.strip("/").split("?")[0]
            conn = connect()
            try:
                ch = next(
                    (c for c in repo.list_channels(conn) if c.slug == slug), None)
            finally:
                conn.close()
            if ch is None or not ch.logo or not os.path.isfile(ch.logo):
                return self._send(404, b"No logo", "text/plain")
            ctype = mimetypes.guess_type(ch.logo)[0] or "image/png"
            try:
                with open(ch.logo, "rb") as fh:
                    self._send(200, fh.read(), ctype)
            except OSError:
                self._send(404, b"No logo", "text/plain")

        def _api_grid(self):
            """All channels' programs over a window, for the timeline EPG."""
            conn = connect()
            try:
                now = time.time()
                origin = now - 3600          # 1h of history
                end = now + 12 * 3600        # 12h horizon
                channels = []
                for c in repo.list_channels(conn):
                    progs = repo.programs_for(conn, c.id, origin, end)
                    channels.append({
                        "slug": c.slug, "name": c.name, "accent": c.accent,
                        "logo": bool(c.logo),
                        "programs": [_prog_dict(p) for p in progs],
                    })
            finally:
                conn.close()
            self._send_json({"server_now": now, "origin": origin, "end": end,
                             "channels": channels})

        def _api_schedule(self, slug: str):
            slug = slug.strip("/")
            conn = connect()
            try:
                channel = next(
                    (c for c in repo.list_channels(conn) if c.slug == slug), None)
                if channel is None:
                    return self._send(404, b"No such channel", "text/plain")
                now = time.time()
                progs = repo.programs_for(
                    conn, channel.id, now - 2 * 3600, now + 18 * 3600)
                items = [_prog_dict(p) for p in progs]
            finally:
                conn.close()
            self._send_json({
                "server_now": now, "slug": channel.slug, "name": channel.name,
                "accent": channel.accent, "tagline": channel.tagline,
                "programs": items,
            })

        def _now_json(self, slug: str):
            conn = connect()
            try:
                _, prog, offset, remaining = _now_program(conn, slug)
            finally:
                conn.close()
            data = {"title": "", "subtitle": "", "remaining": 0}
            if prog is not None:
                data = {
                    "title": prog.display_title,
                    "subtitle": prog.subtitle,
                    "remaining": int(remaining),
                    "offset": int(offset),
                }
            self._send_json(data)

        def _hls_playlist(self, slug: str):
            data = hls.playlist(slug)
            if data is None:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Retry-After", "3")
                self.send_header("Content-Length", "9")
                self.end_headers()
                try:
                    self.wfile.write(b"not ready")
                except OSError:
                    pass
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def _hls_segment(self, slug: str, name: str):
            data = hls.segment(slug, name)
            if data is None:
                return self._send(404, b"gone", "text/plain")
            self._send(200, data, "video/mp2t")

        def _live(self, slug: str):
            conn = connect()
            try:
                _, prog, offset, remaining = _now_program(conn, slug)
            finally:
                conn.close()
            if prog is None or not prog.path:
                # Nothing on now: short response, the page retries shortly.
                return self._send(503, b"Off air", "text/plain")

            client = self.address_string()
            # Cap concurrent transcodes so a misbehaving/looping client (e.g.
            # iOS retrying an unsupported stream) can't overrun the host.
            if not state.try_acquire():
                log.warning("stream busy (%d/%d) - rejecting %s for %s",
                            state.active, state.max_streams, client, slug)
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Retry-After", "3")
                self.send_header("Content-Length", "11")
                self.end_headers()
                try:
                    self.wfile.write(b"server busy")
                except OSError:
                    pass
                return

            cmd = _ffmpeg_cmd(prog.path, offset, remaining, cfg.stream.transcode)
            # start_new_session => its own process group, so _kill_proc can take
            # down ffmpeg and any helper it spawned in one shot (no orphans).
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, start_new_session=True)
            state.register(proc)
            log.info("stream start client=%s ch=%s pid=%s offset=%ds \"%s\" (%d/%d active)",
                     client, slug, proc.pid, int(offset), prog.display_title,
                     state.active, state.max_streams)
            # A stalled/half-open client must not pin this thread + ffmpeg
            # forever; a blocked write will raise after the timeout.
            try:
                self.connection.settimeout(_WRITE_TIMEOUT_SEC)
            except OSError:
                pass

            sent = 0
            t0 = time.monotonic()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.end_headers()
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass  # viewer left / changed channel / went to sleep
            except OSError as exc:
                log.debug("stream write error client=%s ch=%s: %s", client, slug, exc)
            finally:
                rc = proc.poll()
                kill_proc(proc)
                try:
                    proc.stdout.close()
                except OSError:
                    pass
                state.unregister(proc)
                state.release()
                log.info("stream end   client=%s ch=%s pid=%s sent=%.1fMB %.0fs rc=%s (%d active)",
                         client, slug, proc.pid, sent / 1e6,
                         time.monotonic() - t0, rc, state.active)

    return Handler


class StreamServer:
    """Run the LAN stream server in a background daemon thread."""

    def __init__(self, cfg: Config, port: int | None = None):
        self.cfg = cfg
        self.port = port or cfg.stream.port
        self.bind = cfg.stream.bind
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state = _StreamState(cfg.stream.max_streams)
        self._hls = HlsManager(cfg, self._state)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def active_streams(self) -> int:
        return self._state.active

    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}/"

    def start(self) -> str:
        if self.running:
            return self.url()
        handler = _make_handler(self.cfg, self._state, self._hls)
        self._httpd = ThreadingHTTPServer((self.bind, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="retroguide-stream", daemon=True)
        self._thread.start()
        url = self.url()
        log.info("stream server up on %s:%s (%s) max_streams=%d transcode=%s",
                 self.bind, self.port, url, self.cfg.stream.max_streams,
                 self.cfg.stream.transcode)
        return url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        # Tear down HLS sessions, then reap any inflight transcodes.
        self._hls.stop_all()
        self._state.kill_all()
        self._thread = None
        log.info("stream server stopped")
