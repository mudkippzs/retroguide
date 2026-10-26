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
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..db import connect
from .. import repo
from ..binge import playlist
from . import web
from .hls import HlsManager
from .media import encode_chain, english_audio_map, english_subtitle
from .procutil import kill_proc
from .vod import VodManager, valid_key

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


def _english_audio_map(path: str) -> str:
    return english_audio_map(path)


def _ffmpeg_cmd(path: str, offset: float, duration: float, transcode: bool,
                subs: bool = False, tmpdir: str | None = None,
                normalize_audio: bool = True) -> list[str]:
    # Input-seek (-ss before -i) is fast and frame-accurate enough for TV.
    pre, post = encode_chain(path, transcode, subs, tmpdir,
                             normalize_audio=normalize_audio)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *pre]
    if offset > 1:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", path]
    if duration > 0:
        # When subs are burned we use -copyts (so libass stays in sync), which
        # makes -t measure against absolute timestamps -- use -to(end) instead.
        cmd += ["-to", f"{offset + duration:.3f}"] if pre else ["-t", f"{duration:.3f}"]
    cmd += post
    cmd += [
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    return cmd


def _now_program(conn, slug: str):
    """Return (channel, program, offset, remaining) for the live moment.

    Advances past unplayable/missing files so the stream never starts ffmpeg
    on a dead path.
    """
    channel = next((c for c in repo.list_channels(conn) if c.slug == slug), None)
    if channel is None:
        return None, None, 0.0, 0.0
    now = time.time()
    prog = repo.program_at(conn, channel.id, now)
    if prog is not None and not prog.playable:
        prog = repo.next_playable(conn, channel.id, prog.end_ts)
    if prog is None or not prog.path or not os.path.isfile(prog.path):
        return channel, None, 0.0, 0.0
    offset = max(0.0, now - prog.start_ts) if prog.start_ts <= now else 0.0
    remaining = max(0.0, prog.end_ts - now) if prog.end_ts > now else prog.duration_sec
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
        "playable": bool(getattr(prog, "playable", True)),
    }


def _make_handler(cfg: Config, state: _StreamState, hls: HlsManager,
                  vod: VodManager):
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

        def _send_json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _read_json(self) -> dict | None:
            """Parse a JSON POST body; sends 400 and returns None on junk."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > 1_000_000:
                self._send_json({"error": "bad body"}, 400)
                return None
            try:
                data = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"error": "bad json"}, 400)
                return None
            if not isinstance(data, dict):
                self._send_json({"error": "bad json"}, 400)
                return None
            return data

        # -- routing -------------------------------------------------------
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            subs = parse_qs(parsed.query).get("subs", ["0"])[0] == "1"
            try:
                if path in ("/", "/index.html"):
                    return self._app()
                if path == "/api/now":
                    return self._api_now()
                if path == "/api/grid":
                    return self._api_grid()
                if path.startswith("/api/schedule/"):
                    return self._api_schedule(path[len("/api/schedule/"):])
                if path.startswith("/api/program/"):
                    return self._api_program(path[len("/api/program/"):])
                if path.startswith("/logo/"):
                    return self._logo(path[len("/logo/"):])
                if path == "/api/health":
                    return self._api_health()
                if path == "/api/catalog":
                    return self._api_catalog()
                if path.startswith("/api/catalog/series/"):
                    return self._api_series(path[len("/api/catalog/series/"):])
                if path == "/api/queue":
                    return self._api_queue()
                if path == "/api/queue/saved":
                    return self._api_queue_saved()
                if path.startswith("/api/watch/"):
                    return self._api_watch_get(path[len("/api/watch/"):])
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2 and parts[0] == "c":
                    slug = parts[1]
                    if len(parts) == 2:
                        return self._app(slug)
                    if parts[2] == "now.json":
                        return self._now_json(slug)
                    if parts[2] == "live.mp4":
                        return self._live(slug, subs)
                    if parts[2] == "index.m3u8":
                        return self._hls_playlist(slug, subs)
                    if _SEG_RE.match(parts[2]):
                        return self._hls_segment(slug, parts[2])
                if len(parts) == 3 and parts[0] == "v":
                    return self._vod_file(parts[1], parts[2])
                self._send(404, b"Not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - never let a worker thread die silently
                log.exception("request handler error for %s", self.path)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/vod/play":
                    return self._api_vod_play()
                if path == "/api/watch":
                    return self._api_watch_set()
                if path.startswith("/api/queue/"):
                    return self._api_queue_action(
                        path[len("/api/queue/"):])
                self._send(404, b"Not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001
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

        def _api_program(self, raw_id: str):
            """Title/episode details for the guide detail panel."""
            try:
                pid = int(raw_id.strip("/"))
            except ValueError:
                return self._send(404, b"Bad id", "text/plain")
            conn = connect()
            try:
                prog = repo.get_program(conn, pid)
                if prog is None or not prog.title_id:
                    return self._send(404, b"Not found", "text/plain")
                details = repo.get_title_details(
                    conn, prog.title_id, prog.episode_id)
            finally:
                conn.close()
            if details is None:
                return self._send(404, b"Not found", "text/plain")
            self._send_json({
                "id": prog.id,
                "title": prog.display_title,
                "subtitle": prog.subtitle,
                "blurb": prog.blurb or details.blurb,
                "overview": details.episode_overview or details.overview,
                "cast": details.cast,
                "genres": details.genres,
                "year": details.year,
                "rating": details.rating,
                "poster": details.poster_path,
                "franchise": details.franchise,
                "tags": details.tags,
                "playable": prog.playable,
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

        # -- ROAMER: health / catalog / queue / vod / watch -----------------
        def _api_health(self):
            conn = connect()
            try:
                bounds = repo.schedule_bounds(conn)
            finally:
                conn.close()
            self._send_json({
                "ok": True,
                "active_streams": state.active,
                "max_streams": state.max_streams,
                "schedule_end": bounds[1] if bounds else None,
                "server_now": time.time(),
            })

        def _api_catalog(self):
            conn = connect()
            try:
                data = {"series": repo.catalog_series(conn),
                        "movies": repo.catalog_movies(conn)}
            finally:
                conn.close()
            self._send_json(data)

        def _api_series(self, raw_id: str):
            try:
                tid = int(raw_id.strip("/"))
            except ValueError:
                return self._send(404, b"Bad id", "text/plain")
            conn = connect()
            try:
                detail = repo.series_detail(conn, tid)
            finally:
                conn.close()
            if detail is None:
                return self._send(404, b"Not found", "text/plain")
            self._send_json(detail)

        @staticmethod
        def _queue_item_dict(it) -> dict:
            return {"id": it.id, "kind": it.kind, "title_id": it.title_id,
                    "season": it.season, "episode_id": it.episode_id,
                    "label": it.label}

        @staticmethod
        def _expanded_dict(qi) -> dict:
            key = (repo.episode_key(qi.episode_id) if qi.episode_id
                   else repo.film_key(qi.title_id))
            return {"key": key, "title": qi.display_title,
                    "subtitle": qi.subtitle, "duration": qi.duration_sec}

        def _api_queue(self):
            conn = connect()
            try:
                items = []
                for it in playlist.items(conn):
                    n, secs = playlist.item_stats(conn, it)
                    d = self._queue_item_dict(it)
                    d.update({"n_files": n, "seconds": secs})
                    items.append(d)
                expanded = [self._expanded_dict(qi)
                            for qi in playlist.expand(conn)]
            finally:
                conn.close()
            self._send_json({"items": items, "expanded": expanded})

        def _api_queue_saved(self):
            conn = connect()
            try:
                saved = [{"name": n, "items": c, "created_at": ts}
                         for n, c, ts in playlist.list_saved(conn)]
            finally:
                conn.close()
            self._send_json({"saved": saved})

        def _api_queue_action(self, action: str):
            body = self._read_json()
            if body is None:
                return
            action = action.strip("/")
            conn = connect()
            try:
                if action == "add":
                    playlist.add(conn, str(body.get("kind", "")),
                                 int(body["title_id"]),
                                 season=body.get("season"),
                                 episode_id=body.get("episode_id"))
                elif action == "remove":
                    playlist.remove(conn, int(body["item_id"]))
                elif action == "move":
                    playlist.move(conn, int(body["item_id"]),
                                  int(body.get("delta", 0)))
                elif action == "shuffle":
                    playlist.shuffle(conn)
                elif action == "clear":
                    playlist.clear(conn)
                elif action == "save":
                    playlist.save_as(conn, str(body.get("name", "")))
                elif action == "load":
                    playlist.load(conn, str(body.get("name", "")))
                elif action == "delete":
                    playlist.delete_saved(conn, str(body.get("name", "")))
                elif action == "theme-random":
                    added = playlist.theme_random(
                        conn, tag=body.get("tag"), bucket=body.get("bucket"),
                        kind=body.get("kind", "film"),
                        n=int(body.get("n", 5)))
                    return self._send_json({"added": added})
                elif action == "next":
                    return self._api_queue_next(conn, body)
                else:
                    return self._send_json({"error": "unknown action"}, 404)
                conn.commit()
            except (KeyError, ValueError, TypeError) as exc:
                return self._send_json({"error": str(exc) or "bad request"},
                                       400)
            finally:
                conn.close()
            self._send_json({"ok": True})

        def _api_queue_next(self, conn, body: dict):
            """Server-side repeat/random advance so the logic stays in one
            place (binge.playlist.next_index)."""
            expanded = playlist.expand(conn)
            played = {int(i) for i in body.get("played", [])
                      if str(i).lstrip("-").isdigit()}
            idx = playlist.next_index(
                played, len(expanded),
                randomize=bool(body.get("random")),
                repeat=bool(body.get("repeat")))
            item = self._expanded_dict(expanded[idx]) if idx is not None else None
            return self._send_json({"index": idx, "item": item})

        def _api_vod_play(self):
            body = self._read_json()
            if body is None:
                return
            conn = connect()
            try:
                if body.get("episode_id") is not None:
                    info = repo.episode_play_info(conn, int(body["episode_id"]))
                elif body.get("title_id") is not None:
                    info = repo.film_play_info(conn, int(body["title_id"]))
                else:
                    return self._send_json({"error": "episode_id or title_id "
                                            "required"}, 400)
                if info is None:
                    return self._send_json({"error": "not playable"}, 404)
                kind, _si = english_subtitle(info["path"])
                burn = bool(body.get("subs")) and kind is not None
                key = info["key"] + ("+subs" if burn else "")
                sess = vod.start(key, info["path"], info["duration"])
                if sess is None:
                    return self._send_json({"error": "server busy"}, 503)
                pos_dur = repo.get_watch(conn, info["key"])
                position = None
                if pos_dur and not repo.is_watched(*pos_dur):
                    position = pos_dur[0]
                nxt = (repo.next_episode_info(conn, info["episode_id"])
                       if info["episode_id"] else None)
            finally:
                conn.close()
            self._send_json({
                "key": key,
                "url": f"/v/{key}/index.m3u8",
                "status_url": f"/v/{key}/status",
                "title": info["title"], "subtitle": info["subtitle"],
                "duration": info["duration"],
                "position": position,
                # native = real iOS CC menu via WebVTT; burn = bitmap subs
                # burned into a +subs variant; none = no subtitles on file.
                "subs": "burn" if burn else ("native" if sess.has_vtt
                                             else ("bitmap" if kind == "bitmap"
                                                   else "none")),
                "next": ({"episode_id": nxt["episode_id"],
                          "title": nxt["title"], "subtitle": nxt["subtitle"]}
                         if nxt else None),
            })

        def _vod_file(self, key: str, name: str):
            sess = vod.get(key) if valid_key(key) else None
            if sess is None:
                return self._send(404, b"gone", "text/plain")
            if name == "index.m3u8":
                try:
                    data = (sess.dir / "index.m3u8").read_bytes()
                except OSError:
                    data = None
                ctype = "application/vnd.apple.mpegurl"
            elif name == "status":
                return self._send_json(sess.status())
            else:
                data = sess.read_file(name)
                ctype = ("application/vnd.apple.mpegurl" if name.endswith(".m3u8")
                         else "text/vtt" if name.endswith(".vtt")
                         else "video/mp2t")
            if data is None:
                return self._send(404, b"gone", "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control",
                             "no-cache" if name.endswith(".m3u8")
                             else "max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def _api_watch_get(self, key: str):
            key = key.strip("/")
            if not valid_key(key):
                return self._send_json({"error": "bad key"}, 400)
            conn = connect()
            try:
                pos = repo.get_watch(conn, key)
            finally:
                conn.close()
            self._send_json({"position": pos[0], "duration": pos[1]}
                            if pos else {"position": None})

        def _api_watch_set(self):
            body = self._read_json()
            if body is None:
                return
            key = str(body.get("key", ""))
            if not valid_key(key):
                return self._send_json({"error": "bad key"}, 400)
            try:
                position = float(body["position"])
                duration = float(body["duration"])
            except (KeyError, ValueError, TypeError):
                return self._send_json({"error": "position/duration required"},
                                       400)
            conn = connect()
            try:
                repo.set_watch(conn, key, position, duration)
            finally:
                conn.close()
            self._send_json({"ok": True})

        def _hls_playlist(self, slug: str, subs: bool = False):
            data = hls.playlist(slug, subs)
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

        def _live(self, slug: str, subs: bool = False):
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

            # Subtitles are burned in, which needs a scratch dir (for a clean
            # symlink so libass never has to escape the real path).
            subdir = tempfile.mkdtemp(prefix="rg-sub-") if subs else None
            cmd = _ffmpeg_cmd(prog.path, offset, remaining,
                              cfg.stream.transcode, subs=subs, tmpdir=subdir,
                              normalize_audio=cfg.stream.normalize_audio)
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
                if subdir:
                    shutil.rmtree(subdir, ignore_errors=True)
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
        self._vod = VodManager(cfg, self._state)

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
        handler = _make_handler(self.cfg, self._state, self._hls, self._vod)
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
        # Tear down HLS + VOD sessions, then reap any inflight transcodes.
        self._hls.stop_all()
        self._vod.stop_all()
        self._state.kill_all()
        self._thread = None
        log.info("stream server stopped")
