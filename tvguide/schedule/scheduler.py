"""Build the multi-channel 7-day programming grid from the catalog."""
from __future__ import annotations

import json
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from ..config import Config
from .dayparts import CHANNELS, DEFAULT_DURATIONS, Channel, Segment, segment_for
from .events import active_takeovers

ProgressCb = Callable[[str, int, int], None]


def _noop(msg: str, cur: int, total: int) -> None:  # pragma: no cover
    pass


class Scheduler:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn = conn
        self.cfg = cfg
        self.rng = random.Random(20251)  # stable but shuffled lineups
        self._series_eps: dict[int, list[sqlite3.Row]] = {}
        self._series_by_bucket: dict[str, list[sqlite3.Row]] = {}
        self._series_meta: dict[int, sqlite3.Row] = {}
        self._film_pool: list[sqlite3.Row] = []
        self._playheads: dict[int, int] = {}
        # Each series gets a single "home" channel so a show airs on exactly
        # one channel and progresses season-by-season there.
        self._channel_by_bucket: dict[str, dict[str, list[sqlite3.Row]]] = {}
        self._channel_pool: dict[str, list[sqlite3.Row]] = {}
        # Per broadcast-day cache of active special-event takeovers.
        self._takeover_cache: dict[object, dict] = {}

    # -- setup --------------------------------------------------------------
    def seed_channels(self) -> dict[str, int]:
        ids: dict[str, int] = {}
        names = self.cfg.channel_names
        logos = self.cfg.channel_logos
        for pos, ch in enumerate(CHANNELS):
            # User overrides win over the built-in defaults; a blank override
            # falls back to the default so the field can be "unset".
            name = (names.get(ch.slug) or "").strip() or ch.name
            logo = (logos.get(ch.slug) or "").strip() or None
            self.conn.execute(
                "INSERT INTO channels(slug,name,tagline,accent,position,logo) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                "name=excluded.name, tagline=excluded.tagline, "
                "accent=excluded.accent, position=excluded.position, "
                "logo=excluded.logo",
                (ch.slug, name, ch.tagline, ch.accent, pos, logo),
            )
        self.conn.commit()
        for row in self.conn.execute("SELECT id, slug FROM channels"):
            ids[row["slug"]] = row["id"]
        return ids

    def _load_pools(self) -> None:
        # Series with at least one playable episode.
        series = self.conn.execute(
            "SELECT * FROM titles WHERE kind='series'"
        ).fetchall()
        for s in series:
            eps = self.conn.execute(
                "SELECT e.*, m.duration_sec AS file_dur "
                "FROM episodes e JOIN media_files m ON m.id=e.file_id "
                "WHERE e.title_id=? AND e.file_id IS NOT NULL "
                "ORDER BY e.abs_order",
                (s["id"],),
            ).fetchall()
            if not eps:
                continue
            self._series_eps[s["id"]] = eps
            self._series_meta[s["id"]] = s
            bucket = s["bucket"] or "drama"
            self._series_by_bucket.setdefault(bucket, []).append(s)
            ph = self.conn.execute(
                "SELECT next_order FROM playheads WHERE title_id=?", (s["id"],)
            ).fetchone()
            self._playheads[s["id"]] = ph["next_order"] if ph else 0

        # Stable-shuffle each bucket's lineup.
        for bucket, lst in self._series_by_bucket.items():
            self.rng.shuffle(lst)

        # Films with a playable file, best first.
        films = self.conn.execute(
            "SELECT t.*, m.duration_sec AS file_dur FROM titles t "
            "JOIN media_files m ON m.id=t.movie_file_id "
            "WHERE t.kind='movie' AND t.movie_file_id IS NOT NULL"
        ).fetchall()
        films = list(films)
        self.rng.shuffle(films)
        films.sort(key=lambda r: (r["rating"] or 0), reverse=True)
        self._film_pool = films

    # -- home-channel assignment -------------------------------------------
    @staticmethod
    def _channel_bucket_weights() -> dict[str, dict[str, int]]:
        """How heavily each channel leans on each bucket (segment count).

        Used to divide the series in a bucket between the channels that want
        it, proportional to need -- so an all-cartoon channel keeps the bulk
        of the cartoons while a channel that only dips into them now and then
        gets a few.
        """
        weights: dict[str, dict[str, int]] = {}
        for ch in CHANNELS:
            w: dict[str, int] = {}
            for seg in ch.weekday + ch.weekend:
                if seg.kind != "series":
                    continue
                for b in seg.buckets:
                    w[b] = w.get(b, 0) + 1
            weights[ch.slug] = w
        return weights

    def _assign_series(self) -> None:
        """Assign every series to exactly one channel.

        A given show then airs on a single channel only, which (a) stops the
        same series turning up across sister channels and (b) keeps its global
        playhead advancing season-by-season instead of being consumed by
        several channels at once.
        """
        weights = self._channel_bucket_weights()
        self._channel_by_bucket = {ch.slug: {} for ch in CHANNELS}
        self._channel_pool = {ch.slug: [] for ch in CHANNELS}

        for bucket, series_list in self._series_by_bucket.items():
            claimants = [(slug, w[bucket]) for slug, w in weights.items()
                         if w.get(bucket)]
            if not claimants:
                continue  # no channel uses this bucket; global fallback only
            counts = {slug: 0 for slug, _ in claimants}
            for s in series_list:  # already stable-shuffled
                # d'Hondt-style: give the next show to whoever is most
                # "owed" relative to its weight.
                slug = max(claimants,
                           key=lambda c: c[1] / (counts[c[0]] + 1))[0]
                counts[slug] += 1
                self._channel_by_bucket[slug].setdefault(bucket, []).append(s)
                self._channel_pool[slug].append(s)

    # -- selection helpers --------------------------------------------------
    def _candidates(self, ch_slug: str, buckets: tuple[str, ...]) -> list[sqlite3.Row]:
        by_bucket = self._channel_by_bucket.get(ch_slug, {})
        out: list[sqlite3.Row] = []
        for b in buckets:
            out.extend(by_bucket.get(b, []))
        if not out:  # nothing assigned for these buckets: use the channel's
            out = list(self._channel_pool.get(ch_slug, []))  # own pool instead
        if not out:  # truly empty channel: last-resort global pool
            for lst in self._series_by_bucket.values():
                out.extend(lst)
        return out

    def _next_episode(self, title_id: int) -> sqlite3.Row | None:
        eps = self._series_eps.get(title_id)
        if not eps:
            return None
        idx = self._playheads.get(title_id, 0) % len(eps)
        self._playheads[title_id] = (idx + 1) % len(eps)
        return eps[idx]

    @staticmethod
    def _ep_duration(ep: sqlite3.Row, bucket: str) -> float:
        d = ep["file_dur"] or ep["duration_sec"]
        if d and d > 60:
            return float(d)
        return float(DEFAULT_DURATIONS.get(bucket, 30 * 60))

    @staticmethod
    def _film_duration(film: sqlite3.Row) -> float:
        d = film["file_dur"]
        if d and d > 60:
            return float(d)
        if film["runtime_hint"]:
            return float(film["runtime_hint"] * 60)
        return float(DEFAULT_DURATIONS["film"])

    # -- build --------------------------------------------------------------
    def _start_datetime(self) -> datetime:
        now = datetime.now()
        start = now.replace(
            hour=self.cfg.schedule.day_start_hour, minute=0, second=0, microsecond=0
        )
        cfg_start = (self.cfg.schedule.start or "today").lower()
        weekdays = ["monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday"]
        if cfg_start in weekdays:
            target = weekdays.index(cfg_start)
            delta = (target - start.weekday()) % 7
            start = start + timedelta(days=delta)
        elif now.hour < self.cfg.schedule.day_start_hour:
            # Before the broadcast day begins -> still "yesterday's" grid.
            start = start - timedelta(days=1)
        return start

    def build(self, progress: ProgressCb = _noop) -> int:
        ids = self.seed_channels()
        self._load_pools()
        self._assign_series()
        if not self._series_eps and not self._film_pool:
            progress("No playable content found", 0, 0)
            return 0

        self.conn.execute("DELETE FROM programs")
        # Drop channels that no longer exist in the lineup (programs are gone now).
        slugs = [c.slug for c in CHANNELS]
        self.conn.execute(
            f"DELETE FROM channels WHERE slug NOT IN ({','.join('?' * len(slugs))})",
            slugs,
        )
        start = self._start_datetime()
        end = start + timedelta(days=self.cfg.schedule.days)
        day_start = self.cfg.schedule.day_start_hour

        total_channels = len(CHANNELS)
        count = 0
        film_ptr = 0
        used_films: set[int] = set()

        for ci, ch in enumerate(CHANNELS):
            channel_id = ids[ch.slug]
            cursor = start
            rotation: dict[str, int] = {}
            guard = 0
            while cursor < end and guard < 100000:
                guard += 1
                broadcast_day = (cursor - timedelta(hours=day_start))
                is_weekend = broadcast_day.weekday() >= 5
                seg = segment_for(ch, cursor.hour, is_weekend)

                placed = False
                if seg.kind == "film":
                    takeover = self._takeover_for(ch.slug, broadcast_day.date())
                    if takeover is not None:
                        film = takeover.next_film()
                        if film is not None:
                            dur = self._film_duration(film)
                            self._place_film(channel_id, cursor, dur, seg, film,
                                             daypart=takeover.label)
                            cursor = cursor + timedelta(seconds=dur)
                            count += 1
                            placed = True
                    if not placed:
                        film, dur = self._pick_film(used_films)
                        if film is not None:
                            self._place_film(channel_id, cursor, dur, seg, film)
                            used_films.add(film["id"])
                            if len(used_films) >= len(self._film_pool):
                                used_films.clear()
                            cursor = cursor + timedelta(seconds=dur)
                            count += 1
                            placed = True
                else:
                    cands = self._candidates(ch.slug, seg.buckets)
                    if cands:
                        # Seed each channel/segment at a different point in the
                        # lineup so sister channels don't air the same show at
                        # the same moment.
                        pos = rotation.get(seg.label, ch.offset * 7 + len(seg.label))
                        series = cands[pos % len(cands)]
                        rotation[seg.label] = pos + 1
                        ep = self._next_episode(series["id"])
                        if ep is not None:
                            bucket = series["bucket"] or "drama"
                            dur = self._ep_duration(ep, bucket)
                            self._place_episode(channel_id, cursor, dur, seg, series, ep)
                            cursor = cursor + timedelta(seconds=dur)
                            count += 1
                            placed = True

                if not placed:
                    cursor = cursor + timedelta(minutes=30)

            self.conn.commit()
            progress(f"Scheduled {ch.name}", ci + 1, total_channels)

        # Persist advanced playheads so next rebuild continues the story.
        for tid, nxt in self._playheads.items():
            self.conn.execute(
                "UPDATE playheads SET next_order=? WHERE title_id=?", (nxt, tid)
            )
        self.conn.commit()
        progress("Schedule complete", total_channels, total_channels)
        return count

    def _pick_film(self, used: set[int]) -> tuple[sqlite3.Row | None, float]:
        if not self._film_pool:
            return None, 0.0
        for film in self._film_pool:
            if film["id"] not in used:
                return film, self._film_duration(film)
        film = self._film_pool[0]
        return film, self._film_duration(film)

    # -- row writers --------------------------------------------------------
    def _place_episode(self, channel_id, cursor, dur, seg, series, ep) -> None:
        season = ep["season"]
        episode = ep["episode"]
        subtitle = f"S{season:02d}E{episode:02d}"
        if ep["name"]:
            subtitle += f" - {ep['name']}"
        blurb = ep["blurb"] or _trim(ep["overview"]) or _trim(series["overview"])
        start_ts = cursor.timestamp()
        self.conn.execute(
            "INSERT INTO programs(channel_id,start_ts,end_ts,duration_sec,daypart,"
            "title_id,episode_id,file_id,kind,display_title,subtitle,blurb) "
            "VALUES(?,?,?,?,?,?,?,?, 'episode', ?,?,?)",
            (channel_id, start_ts, start_ts + dur, dur, seg.label, series["id"],
             ep["id"], ep["file_id"], series["name"], subtitle, blurb),
        )

    def _place_film(self, channel_id, cursor, dur, seg, film, daypart=None) -> None:
        cast = json.loads(film["cast_json"] or "[]")
        subtitle = ""
        if film["year"]:
            subtitle = f"({film['year']})"
        if cast:
            subtitle = (subtitle + "  " if subtitle else "") + ", ".join(cast[:3])
        blurb = film["blurb"] or _trim(film["overview"])
        start_ts = cursor.timestamp()
        self.conn.execute(
            "INSERT INTO programs(channel_id,start_ts,end_ts,duration_sec,daypart,"
            "title_id,file_id,kind,display_title,subtitle,blurb) "
            "VALUES(?,?,?,?,?,?,?, 'movie', ?,?,?)",
            (channel_id, start_ts, start_ts + dur, dur, daypart or seg.label,
             film["id"], film["movie_file_id"], film["name"], subtitle, blurb),
        )

    def _takeover_for(self, slug: str, day):
        """Return the active special-event Takeover for this channel/day, if any."""
        if not self.cfg.schedule.events:
            return None
        day_map = self._takeover_cache.get(day)
        if day_map is None:
            day_map = active_takeovers(self.conn, day)
            self._takeover_cache[day] = day_map
        return day_map.get(slug)


def _trim(text: str | None, sentences: int = 2) -> str | None:
    if not text:
        return None
    parts = text.strip().split(". ")
    out = ". ".join(parts[:sentences]).strip()
    if out and not out.endswith("."):
        out += "."
    return out or None
