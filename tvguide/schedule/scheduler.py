"""Build the multi-channel 7-day programming grid from the catalog."""
from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from ..config import Config
from ..util.media_ok import MIN_DURATION_SEC, PLAYABLE_SQL
from ..util.naming import is_special_episode
from .dayparts import CHANNELS, DEFAULT_DURATIONS, Channel, Segment, segment_for
from .events import active_takeovers

log = logging.getLogger("tvguide.schedule")

ProgressCb = Callable[[str, int, int], None]

# Don't schedule a series with fewer than this many regular (non-special)
# playable episodes -- one-file "series" like S99E00 retrospectives just loop.
MIN_SERIES_EPS = 3
# Soft preferences (broken only when a channel would otherwise go dark).
TITLE_COOLDOWN_HOURS = 3.0
EPISODE_COOLDOWN_HOURS = 24.0
FILM_COOLDOWN_HOURS = 36.0
# Soft preference for how often one series appears per broadcast day.
MAX_TITLE_AIRS_PER_DAY = 2
# Hard ceiling: at most this many full passes through a series' episode list
# per broadcast day. Stops 5-ep docs from looping 10× on a thin niche channel.
MAX_CATALOG_PASSES_PER_DAY = 1
# Prefer at least this many series on a tag-gated channel (e.g. science) before
# leaving the rest of that bucket on generalist channels.
MIN_TAG_CHANNEL_SERIES = 6
# Tagged niche channels may loop when scarce; 1 is enough to keep them on-air.
MIN_TAGGED_POOL = 1


def _noop(msg: str, cur: int, total: int) -> None:  # pragma: no cover
    pass


class Scheduler:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn = conn
        self.cfg = cfg
        # Re-seed by ISO week so the lineup reshuffles once a week (a fresh feel
        # on Monday) while staying identical across rebuilds within that week.
        _yr, _wk, _ = datetime.now().isocalendar()
        self.rng = random.Random(20251 + _yr * 53 + _wk)
        self._series_eps: dict[int, list[sqlite3.Row]] = {}
        self._series_by_bucket: dict[str, list[sqlite3.Row]] = {}
        self._series_meta: dict[int, sqlite3.Row] = {}
        self._film_pool: list[sqlite3.Row] = []
        self._film_by_tag: dict[str, list[sqlite3.Row]] = {}
        self._film_tag_ptr: dict[str, int] = {}
        self._film_ptr: int = 0
        # title_id -> next abs_order cursor (not a list index).
        self._playheads: dict[int, int] = {}
        # title_id -> owning channel slug (exclusive home).
        self._home: dict[int, str] = {}
        # title_id -> [(start_ts, end_ts), ...] already placed.
        self._on_air: dict[int, list[tuple[float, float]]] = {}
        # Build-wide last airing timestamps for cooldown enforcement.
        self._last_title_ts: dict[int, float] = {}
        self._last_ep_ts: dict[int, float] = {}   # episode_id or film title_id
        self._last_film_ts: dict[int, float] = {}
        # (channel_slug, broadcast_date_iso, title_id) -> airings today.
        self._day_title_counts: dict[tuple[str, str, int], int] = {}
        self._takeover_cache: dict[object, dict] = {}
        # Tags that have a dedicated channel -- keep those titles off general
        # film/series pools so they don't double-air.
        self._exclusive_tags: set[str] = set()
        # Channels that program children's buckets need age-appropriate
        # pad films (no horror/war features filling in for cartoons).
        self._kids_channels: set[str] = set()
        # User scheduling rules: per-title rotation start season, and
        # per-channel allow/block lists (series only; film slots unaffected).
        self._start_seasons: dict[int, int] = {}
        self._channel_allow: dict[str, set[int]] = {}
        self._channel_block: dict[str, set[int]] = {}

    # -- setup --------------------------------------------------------------
    def seed_channels(self) -> dict[str, int]:
        ids: dict[str, int] = {}
        names = self.cfg.channel_names
        logos = self.cfg.channel_logos
        for pos, ch in enumerate(CHANNELS):
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
        self._load_rules()
        # Discover which tags own a dedicated channel.
        for ch in CHANNELS:
            for seg in ch.weekday + ch.weekend:
                if seg.tag:
                    self._exclusive_tags.add(seg.tag)
                if {"preschool", "kids_cartoon"} & set(seg.buckets):
                    self._kids_channels.add(ch.slug)

        series = self.conn.execute(
            "SELECT * FROM titles WHERE kind='series'"
        ).fetchall()
        for s in series:
            eps = self.conn.execute(
                "SELECT e.*, m.duration_sec AS file_dur, m.path AS file_path, "
                "m.probe_state, m.video_codec "
                f"FROM episodes e JOIN media_files m ON m.id=e.file_id "
                f"WHERE e.title_id=? AND e.file_id IS NOT NULL AND {PLAYABLE_SQL} "
                "ORDER BY e.abs_order",
                (s["id"],),
            ).fetchall()
            # Drop specials/bonus numbering and files that vanished from disk.
            playable = []
            for ep in eps:
                if is_special_episode(ep["season"], ep["episode"]):
                    continue
                if not ep["file_path"] or not os.path.isfile(ep["file_path"]):
                    continue
                playable.append(ep)
            # Manual override: earlier seasons leave the rotation entirely
            # (not just the starting point — wrapping must not reach them).
            start_s = self._start_seasons.get(s["id"])
            if start_s is not None:
                playable = [ep for ep in playable if ep["season"] >= start_s]
            if len(playable) < MIN_SERIES_EPS:
                continue
            self._series_eps[s["id"]] = playable
            self._series_meta[s["id"]] = s
            bucket = s["bucket"] or "drama"
            self._series_by_bucket.setdefault(bucket, []).append(s)
            ph = self.conn.execute(
                "SELECT next_order FROM playheads WHERE title_id=?", (s["id"],)
            ).fetchone()
            raw = int(ph["next_order"]) if ph else 0
            self._playheads[s["id"]] = _cursor_from_stored(raw, playable)

        for bucket, lst in self._series_by_bucket.items():
            self.rng.shuffle(lst)

        films = self.conn.execute(
            "SELECT t.*, m.duration_sec AS file_dur, m.path AS file_path, "
            "m.probe_state, m.video_codec FROM titles t "
            "JOIN media_files m ON m.id=t.movie_file_id "
            f"WHERE t.kind='movie' AND t.movie_file_id IS NOT NULL "
            f"AND {PLAYABLE_SQL}"
        ).fetchall()
        films = [f for f in films
                 if f["file_path"] and os.path.isfile(f["file_path"])]
        films = list(films)
        self.rng.shuffle(films)
        films.sort(key=lambda r: (r["rating"] or 0), reverse=True)
        self._film_pool = films
        self._film_by_tag = {}
        for f in films:
            tags = _row_tags(f)
            for t in tags:
                self._film_by_tag.setdefault(t, []).append(f)
        # Drop tagged niche pools that are too small to program without looping.
        for tag in list(self._film_by_tag):
            if len(self._film_by_tag[tag]) < MIN_TAGGED_POOL:
                log.info("tagged film pool '%s' has only %d titles — disabling",
                         tag, len(self._film_by_tag[tag]))
                del self._film_by_tag[tag]

        log.info("pools: %d series across %d buckets, %d films, tags=%s",
                 len(self._series_eps), len(self._series_by_bucket),
                 len(self._film_pool), sorted(self._film_by_tag))

    def _load_rules(self) -> None:
        """Read user scheduling rules (title_rules / channel_rules)."""
        self._start_seasons = {
            r["title_id"]: r["start_season"]
            for r in self.conn.execute(
                "SELECT title_id, start_season FROM title_rules "
                "WHERE start_season IS NOT NULL")
        }
        self._channel_allow = {}
        self._channel_block = {}
        for r in self.conn.execute(
                "SELECT channel_slug, title_id, mode FROM channel_rules"):
            target = (self._channel_allow if r["mode"] == "allow"
                      else self._channel_block)
            target.setdefault(r["channel_slug"], set()).add(r["title_id"])

    # -- exclusive channel homes -------------------------------------------
    def _assign_series(self) -> None:
        """Give every series exactly one home channel.

        Channels that declare a bucket in their dayparts share that bucket's
        roster round-robin, so sitcoms don't air on Prime + Chuckle + Banter
        at once. Tag-gated channels (science) claim matching titles first.
        """
        self._home = {}
        # tag -> channels that want it
        tag_homes: dict[str, list[str]] = {}
        # bucket -> channels that program it (non-mirror)
        bucket_homes: dict[str, list[str]] = {}
        for ch in CHANNELS:
            if ch.is_mirror:
                continue
            for seg in ch.weekday + ch.weekend:
                # Tag homes come from any daypart (series or film) so a channel
                # like Higher Learning still claims science-tagged *series*
                # even when its science-tagged slots are film-only.
                if seg.tag:
                    homes = tag_homes.setdefault(seg.tag, [])
                    if ch.slug not in homes:
                        homes.append(ch.slug)
                if seg.kind != "series":
                    continue
                for b in seg.buckets:
                    homes = bucket_homes.setdefault(b, [])
                    if ch.slug not in homes:
                        homes.append(ch.slug)

        tag_rr: dict[str, int] = {}
        bucket_rr: dict[str, int] = {}
        for series_list in self._series_by_bucket.values():
            for s in series_list:
                placed = False
                for t in _row_tags(s):
                    homes = tag_homes.get(t)
                    if not homes:
                        continue
                    idx = tag_rr.get(t, 0)
                    self._home[s["id"]] = homes[idx % len(homes)]
                    tag_rr[t] = idx + 1
                    placed = True
                    break
                if placed:
                    continue
                bucket = s["bucket"] or "drama"
                homes = list(bucket_homes.get(bucket)
                             or bucket_homes.get("drama") or [])
                if not homes:
                    continue
                # Thin tag channels (Higher Learning) starve when every short
                # science doc is tag-claimed but the rest of the documentary
                # bucket lands on Prime/Nite Owl — prefer stocking science.
                if bucket == "documentary" and "science" in homes:
                    sci_n = sum(1 for h in self._home.values() if h == "science")
                    if sci_n < MIN_TAG_CHANNEL_SERIES:
                        homes = ["science"] + [h for h in homes if h != "science"]
                idx = bucket_rr.get(bucket, 0)
                self._home[s["id"]] = homes[idx % len(homes)]
                bucket_rr[bucket] = idx + 1

        # Series confined to a tag channel that has no regular episodes in
        # that channel's buckets stay assigned; others get a home.
        for tid, slug in list(self._home.items()):
            log.debug("home %s -> %s", self._series_meta[tid]["name"], slug)

        self._apply_channel_rules(bucket_homes)

    def _apply_channel_rules(self, bucket_homes: dict[str, list[str]]) -> None:
        """User allow/block lists, applied after automatic home assignment.

        allow: pin the title to that channel (and _candidates restricts the
        channel's series roster to its allowlist). block: never air on that
        channel — re-home within the bucket if possible, else drop the title
        from rotation entirely.
        """
        for slug, tids in self._channel_allow.items():
            for tid in tids:
                if tid in self._series_meta:
                    self._home[tid] = slug
        for slug, tids in self._channel_block.items():
            for tid in tids:
                if self._home.get(tid) != slug:
                    continue
                bucket = self._series_meta[tid]["bucket"] or "drama"
                alts = [h for h in (bucket_homes.get(bucket)
                                    or bucket_homes.get("drama") or [])
                        if h != slug and tid not in self._channel_block.get(h, ())]
                if alts:
                    self._home[tid] = alts[0]
                    log.info("rule: %s blocked on %s — re-homed to %s",
                             self._series_meta[tid]["name"], slug, alts[0])
                else:
                    log.info("rule: %s blocked on %s — no other home, "
                             "dropping from rotation",
                             self._series_meta[tid]["name"], slug)
                    self._drop_series(tid)

    def _drop_series(self, tid: int) -> None:
        self._home.pop(tid, None)
        self._playheads.pop(tid, None)
        self._series_eps.pop(tid, None)
        meta = self._series_meta.pop(tid, None)
        if meta is not None:
            bucket = meta["bucket"] or "drama"
            lst = self._series_by_bucket.get(bucket)
            if lst:
                self._series_by_bucket[bucket] = [
                    s for s in lst if s["id"] != tid]

    # -- selection helpers --------------------------------------------------
    def _candidates(self, ch_slug: str, buckets: tuple[str, ...]) -> list[sqlite3.Row]:
        """Series whose exclusive home is this channel, in the requested buckets.

        User rules apply: blocked titles never air here, and a channel with an
        allowlist airs only allowlisted series.
        """
        allow = self._channel_allow.get(ch_slug)
        blocked = self._channel_block.get(ch_slug, set())
        out: list[sqlite3.Row] = []
        for b in buckets:
            for s in self._series_by_bucket.get(b, []):
                if self._home.get(s["id"]) != ch_slug:
                    continue
                if s["id"] in blocked:
                    continue
                if allow is not None and s["id"] not in allow:
                    continue
                out.append(s)
        return out

    def _index_for_cursor(self, title_id: int) -> int:
        """Index of the first playable episode at/after the abs_order cursor."""
        eps = self._series_eps.get(title_id) or []
        if not eps:
            return 0
        cursor = self._playheads.get(title_id, 0)
        for i, ep in enumerate(eps):
            if (ep["abs_order"] or 0) >= cursor:
                return i
        return 0  # wrap past end of catalog

    def _peek_episode(self, title_id: int, start_ts: float | None = None,
                      *, allow_cooling: bool = True) -> sqlite3.Row | None:
        """Next episode in broadcast order.

        When ``allow_cooling`` is False, scan forward from the playhead (and
        wrap once) for an episode outside the episode cooldown. Story order is
        preserved on advance: airing a later episode moves the cursor past it.
        """
        eps = self._series_eps.get(title_id)
        if not eps:
            return None
        start_idx = self._index_for_cursor(title_id)
        if start_ts is None or allow_cooling:
            return eps[start_idx]
        n = len(eps)
        for off in range(n):
            ep = eps[(start_idx + off) % n]
            if not self._ep_cooling(ep["id"], start_ts):
                return ep
        return None

    def _advance_playhead(self, title_id: int, ep: sqlite3.Row) -> None:
        """Move the abs_order cursor to just after ``ep``, wrapping to the min."""
        eps = self._series_eps.get(title_id)
        if not eps:
            return
        aired = int(ep["abs_order"] or 0)
        min_ao = min(int(e["abs_order"] or 0) for e in eps)
        max_ao = max(int(e["abs_order"] or 0) for e in eps)
        nxt = aired + 1
        self._playheads[title_id] = min_ao if nxt > max_ao else nxt

    def _title_cooling(self, title_id: int, start_ts: float) -> bool:
        last = self._last_title_ts.get(title_id)
        if last is None:
            return False
        return (start_ts - last) < TITLE_COOLDOWN_HOURS * 3600

    def _ep_cooling(self, ep_id: int, start_ts: float) -> bool:
        last = self._last_ep_ts.get(ep_id)
        if last is None:
            return False
        return (start_ts - last) < EPISODE_COOLDOWN_HOURS * 3600

    def _film_cooling(self, title_id: int, start_ts: float) -> bool:
        last = self._last_film_ts.get(title_id)
        if last is None:
            return False
        return (start_ts - last) < FILM_COOLDOWN_HOURS * 3600

    def _soft_day_cap(self, title_id: int) -> int:
        n = len(self._series_eps.get(title_id) or ())
        return max(1, min(MAX_TITLE_AIRS_PER_DAY, n))

    def _hard_day_cap(self, title_id: int) -> int:
        """Max airings/day: one full pass through the catalog (short docs)."""
        n = len(self._series_eps.get(title_id) or ())
        return max(1, n * MAX_CATALOG_PASSES_PER_DAY)

    def _day_cap_hit(self, ch_slug: str, day_key: str, title_id: int,
                    *, hard: bool = False) -> bool:
        limit = self._hard_day_cap(title_id) if hard else self._soft_day_cap(title_id)
        return self._day_title_counts.get((ch_slug, day_key, title_id), 0) >= limit

    def _pick_series_slot(
        self,
        cands: list[sqlite3.Row],
        start_ts: float,
        ch_slug: str,
        day_key: str,
        ch_offset: int,
        day_jitter: int,
        last_seen: dict[int, int],
        global_last: dict[int, int],
    ) -> tuple[sqlite3.Row, sqlite3.Row, float] | None:
        """Choose a series+episode for this slot.

        Tier 0: under soft day-cap, title cool, fresh episode (ideal).
        Tier 1: under soft day-cap, title cool, fresh episode (title may be warm).
        Tier 2: under hard day-cap (≤ one catalog pass/day), fresh episode.
        Never re-airs a still-cooling episode when a fresh one exists anywhere
        in the candidate set. Returns None (caller may film-fill or gap) when
        every candidate is past its hard daily pass or only has cooling eps.
        """
        if not cands:
            return None

        def _lru_key(s: sqlite3.Row) -> tuple[int, int, int, int]:
            day_count = self._day_title_counts.get((ch_slug, day_key, s["id"]), 0)
            jitter = (s["id"] * 2654435761 + ch_offset * 40503 + day_jitter) & 0xFFFF
            return (day_count, last_seen.get(s["id"], -1),
                    global_last.get(s["id"], -1), jitter)

        ordered = sorted(cands, key=_lru_key)
        tiers: list[tuple[sqlite3.Row, sqlite3.Row, float] | None] = [
            None, None, None]
        for series in ordered:
            tid = series["id"]
            if self._day_cap_hit(ch_slug, day_key, tid, hard=True):
                continue
            # Only consider a non-cooling episode — wrapping a 5-ep doc inside
            # its own cooldown is what made Higher Learning feel stuck.
            ep = self._peek_episode(tid, start_ts, allow_cooling=False)
            if ep is None:
                continue
            bucket = series["bucket"] or "drama"
            dur = self._ep_duration(ep, bucket)
            end_ts = start_ts + dur
            if self._conflicts(start_ts, end_ts, tid):
                continue
            title_cool = self._title_cooling(tid, start_ts)
            over_soft = self._day_cap_hit(ch_slug, day_key, tid, hard=False)
            cand = (series, ep, dur)
            if not over_soft and not title_cool:
                return cand
            if not over_soft and tiers[1] is None:
                tiers[1] = cand
            if tiers[2] is None:
                tiers[2] = cand
        for tier in tiers[1:]:
            if tier is not None:
                return tier
        return None

    def _conflicts(self, start_ts: float, end_ts: float, title_id: int) -> bool:
        for a, b in self._on_air.get(title_id, ()):
            if a < end_ts and b > start_ts:
                return True
        return False

    def _mark_airing(self, start_ts: float, end_ts: float, title_id: int,
                     ep_or_film_id: int | None = None, is_film: bool = False,
                     *, ch_slug: str | None = None,
                     day_key: str | None = None) -> None:
        self._on_air.setdefault(title_id, []).append((start_ts, end_ts))
        self._last_title_ts[title_id] = start_ts
        if is_film:
            self._last_film_ts[title_id] = start_ts
        elif ep_or_film_id is not None:
            self._last_ep_ts[ep_or_film_id] = start_ts
        if ch_slug is not None and day_key is not None and not is_film:
            key = (ch_slug, day_key, title_id)
            self._day_title_counts[key] = self._day_title_counts.get(key, 0) + 1

    def _seed_cooldowns_from_programs(self) -> None:
        """Carry title/episode/film cooldowns across rebuilds/auto-extend.

        Only past/current airings count — seeding from the *upcoming* grid
        (future start_ts) made every episode look perpetually "cooling" and
        starved short strands off the air after a rebuild.
        """
        now = datetime.now().timestamp()
        for r in self.conn.execute(
            "SELECT title_id, MAX(start_ts) AS ts FROM programs "
            "WHERE start_ts <= ? GROUP BY title_id",
            (now,),
        ):
            self._last_title_ts[r["title_id"]] = float(r["ts"])
        for r in self.conn.execute(
            "SELECT episode_id, MAX(start_ts) AS ts FROM programs "
            "WHERE episode_id IS NOT NULL AND start_ts <= ? "
            "GROUP BY episode_id",
            (now,),
        ):
            self._last_ep_ts[r["episode_id"]] = float(r["ts"])
        for r in self.conn.execute(
            "SELECT title_id, MAX(start_ts) AS ts FROM programs "
            "WHERE kind='movie' AND start_ts <= ? GROUP BY title_id",
            (now,),
        ):
            self._last_film_ts[r["title_id"]] = float(r["ts"])

    @staticmethod
    def _ep_duration(ep: sqlite3.Row, bucket: str) -> float:
        d = ep["file_dur"] or ep["duration_sec"]
        if d and d > MIN_DURATION_SEC:
            return float(d)
        # Unprobed/short files are filtered from pools; this is a last resort.
        return float(DEFAULT_DURATIONS.get(bucket, 30 * 60))

    @staticmethod
    def _film_duration(film: sqlite3.Row) -> float:
        d = film["file_dur"]
        if d and d > MIN_DURATION_SEC:
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
            start = start - timedelta(days=1)
        return start

    def build(self, progress: ProgressCb = _noop) -> int:
        ids = self.seed_channels()
        self._load_pools()
        self._assign_series()
        if not self._series_eps and not self._film_pool:
            progress("No playable content found", 0, 0)
            return 0

        # Carry cooldowns from the previous grid before wiping it so auto-extend
        # / rebuilds don't immediately re-air the same episodes.
        self._on_air = {}
        self._last_title_ts = {}
        self._last_ep_ts = {}
        self._last_film_ts = {}
        self._day_title_counts = {}
        self._seed_cooldowns_from_programs()

        # Explicit transaction so DELETE + inserts for a channel are batched
        # without relying on the connection's isolation_level.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("DELETE FROM programs")
            slugs = [c.slug for c in CHANNELS]
            self.conn.execute(
                f"DELETE FROM channels WHERE slug NOT IN ({','.join('?' * len(slugs))})",
                slugs,
            )
        except Exception:
            self.conn.rollback()
            raise
        start = self._start_datetime()
        end = start + timedelta(days=self.cfg.schedule.days)
        day_start = self.cfg.schedule.day_start_hour

        total_channels = len(CHANNELS)
        count = 0
        used_films: set[int] = set()
        # Build-wide LRU so channels don't all pick the same show first.
        global_last: dict[int, int] = {}
        global_slot = 0

        for ci, ch in enumerate(CHANNELS):
            if ch.is_mirror:
                continue
            channel_id = ids[ch.slug]
            cursor = start
            last_seen: dict[int, int] = {}
            slot_no = 0
            guard = 0
            while cursor < end and guard < 100000:
                guard += 1
                broadcast_day = (cursor - timedelta(hours=day_start))
                day_key = broadcast_day.date().isoformat()
                is_weekend = broadcast_day.weekday() >= 5
                # Day-of-week jitter so Mon/Thu don't mirror each other.
                day_jitter = broadcast_day.weekday() * 17
                seg = segment_for(ch, cursor.hour, is_weekend)

                placed = False
                if seg.kind == "film":
                    if seg.tag:
                        # Fresh tagged title → general feature → repeat tagged.
                        film, dur = self._pick_tagged_film(
                            seg.tag, cursor.timestamp(), used_films,
                            allow_repeat=False)
                        if film is None:
                            film, dur = self._pick_film(
                                used_movies=used_films,
                                start_ts=cursor.timestamp())
                        if film is None:
                            film, dur = self._pick_tagged_film(
                                seg.tag, cursor.timestamp(), used_films,
                                allow_repeat=True)
                        if film is not None:
                            self._place_film(channel_id, cursor, dur, seg, film)
                            used_films.add(film["id"])
                            self._mark_airing(cursor.timestamp(),
                                              cursor.timestamp() + dur,
                                              film["id"], is_film=True)
                            cursor = cursor + timedelta(seconds=dur)
                            count += 1
                            placed = True
                    else:
                        takeover = self._takeover_for(ch.slug, broadcast_day.date())
                        if takeover is not None:
                            film = takeover.next_film()
                            if film is not None and not self._film_cooling(
                                    film["id"], cursor.timestamp()):
                                dur = self._film_duration(film)
                                self._place_film(channel_id, cursor, dur, seg, film,
                                                 daypart=takeover.label)
                                used_films.add(film["id"])
                                self._mark_airing(cursor.timestamp(),
                                                  cursor.timestamp() + dur,
                                                  film["id"], is_film=True)
                                cursor = cursor + timedelta(seconds=dur)
                                count += 1
                                placed = True
                        if not placed:
                            film, dur = self._pick_film(
                                used_movies=used_films, start_ts=cursor.timestamp())
                            if film is not None:
                                self._place_film(channel_id, cursor, dur, seg, film)
                                used_films.add(film["id"])
                                if len(used_films) >= len(self._film_pool):
                                    used_films.clear()
                                    self.rng.shuffle(self._film_pool)
                                self._mark_airing(cursor.timestamp(),
                                                  cursor.timestamp() + dur,
                                                  film["id"], is_film=True)
                                cursor = cursor + timedelta(seconds=dur)
                                count += 1
                                placed = True
                else:
                    cands = self._candidates(ch.slug, seg.buckets)
                    if seg.tag:
                        tagged = [s for s in cands if seg.tag in _row_tags(s)]
                        # Prefer the tag, but don't starve a niche channel when
                        # only 2–3 short strands carry the tag.
                        if len(tagged) >= 2:
                            cands = tagged
                    pick = self._pick_series_slot(
                        cands, cursor.timestamp(), ch.slug, day_key,
                        ch.offset, day_jitter, last_seen, global_last)
                    if pick is not None:
                        series, ep, dur = pick
                        start_ts = cursor.timestamp()
                        self._advance_playhead(series["id"], ep)
                        last_seen[series["id"]] = slot_no
                        global_last[series["id"]] = global_slot
                        slot_no += 1
                        global_slot += 1
                        self._place_episode(channel_id, cursor, dur, seg,
                                            series, ep)
                        self._mark_airing(start_ts, start_ts + dur, series["id"],
                                          ep_or_film_id=ep["id"],
                                          ch_slug=ch.slug, day_key=day_key)
                        cursor = cursor + timedelta(seconds=dur)
                        count += 1
                        placed = True
                    else:
                        # Series exhausted / cooling — pad with a film rather
                        # than looping Planet Earth II for the tenth time.
                        film, dur = self._pick_pad_film(
                            ch.slug, cursor.timestamp(), used_films)
                        if film is not None:
                            self._place_film(channel_id, cursor, dur, seg, film)
                            used_films.add(film["id"])
                            self._mark_airing(cursor.timestamp(),
                                              cursor.timestamp() + dur,
                                              film["id"], is_film=True)
                            cursor = cursor + timedelta(seconds=dur)
                            count += 1
                            placed = True

                if not placed:
                    # No playable roster for this daypart at all.
                    cursor = cursor + timedelta(minutes=30)

            self.conn.commit()
            progress(f"Scheduled {ch.name}", ci + 1, total_channels)

        # Fill the +1 timeshift channels from their parent.
        for ci, ch in enumerate(CHANNELS):
            if not ch.is_mirror:
                continue
            parent_id = ids.get(ch.mirror_of)
            if parent_id is None:
                continue
            shift = ch.shift_hours * 3600.0
            self.conn.execute(
                "INSERT INTO programs(channel_id,start_ts,end_ts,duration_sec,"
                "daypart,title_id,episode_id,file_id,kind,display_title,"
                "subtitle,blurb) "
                "SELECT ?, start_ts+?, end_ts+?, duration_sec, daypart, title_id,"
                " episode_id, file_id, kind, display_title, subtitle, blurb "
                "FROM programs WHERE channel_id=?",
                (ids[ch.slug], shift, shift, parent_id),
            )
            self.conn.commit()
            progress(f"Scheduled {ch.name}", ci + 1, total_channels)

        # Persist advanced playheads (upsert so missing rows don't silently noop).
        for tid, nxt in self._playheads.items():
            self.conn.execute(
                "INSERT INTO playheads(title_id, next_order) VALUES(?, ?) "
                "ON CONFLICT(title_id) DO UPDATE SET next_order=excluded.next_order",
                (tid, nxt),
            )
        self.conn.commit()
        log.info("scheduled %d programs across %d channels", count, total_channels)
        progress("Schedule complete", total_channels, total_channels)
        return count

    def _channel_tags(self, ch_slug: str) -> list[str]:
        tags: list[str] = []
        for ch in CHANNELS:
            if ch.slug != ch_slug:
                continue
            for seg in ch.weekday + ch.weekend:
                if seg.tag and seg.tag not in tags:
                    tags.append(seg.tag)
        return tags

    def _pick_pad_film(self, ch_slug: str, start_ts: float,
                       used: set[int]) -> tuple[sqlite3.Row | None, float]:
        """Film to fill a series daypart when episodes would only wrap/repeat.

        Prefer a fresh tagged niche film, then any general feature, and only
        then repeat a lone science/standup title (avoids Cosmos×70). Kids'
        channels only get family-suitable features -- never horror/war.
        """
        kids = ch_slug in self._kids_channels
        for tag in self._channel_tags(ch_slug):
            film, dur = self._pick_tagged_film(tag, start_ts, used,
                                               allow_repeat=False)
            if film is not None and self._film_suitable(film, kids=kids):
                return film, dur
        if kids:
            # Family-tagged features first; then anything not adult-themed.
            film, dur = self._pick_film(
                used_movies=used, start_ts=start_ts,
                allow=lambda f: "family" in _row_tags(f)
                and self._film_suitable(f, kids=True))
            if film is not None:
                return film, dur
        film, dur = self._pick_film(used_movies=used, start_ts=start_ts,
                                    allow=lambda f: self._film_suitable(f, kids=kids))
        if film is not None:
            return film, dur
        for tag in self._channel_tags(ch_slug):
            film, dur = self._pick_tagged_film(tag, start_ts, used,
                                               allow_repeat=True)
            if film is not None and self._film_suitable(film, kids=kids):
                return film, dur
        return None, 0.0

    @staticmethod
    def _film_suitable(film: sqlite3.Row, *, kids: bool) -> bool:
        if not kids:
            return True
        return not ({"horror", "war"} & set(_row_tags(film)))

    def _pick_film(self, used_movies: set[int],
                   start_ts: float,
                   allow: Callable[[sqlite3.Row], bool] | None = None,
                   ) -> tuple[sqlite3.Row | None, float]:
        if not self._film_pool:
            return None, 0.0
        n = len(self._film_pool)
        fallback: tuple[sqlite3.Row, float] | None = None
        for i in range(n):
            film = self._film_pool[(self._film_ptr + i) % n]
            # Skip titles reserved for a tagged niche channel.
            tags = set(_row_tags(film))
            if tags & self._exclusive_tags:
                continue
            if film["id"] in used_movies:
                continue
            if allow is not None and not allow(film):
                continue
            dur = self._film_duration(film)
            if self._film_cooling(film["id"], start_ts):
                if fallback is None:
                    fallback = (film, dur)
                continue
            self._film_ptr = (self._film_ptr + i + 1) % n
            return film, dur
        if fallback is not None:
            # Prefer a repeat over a hole in the cinema grid.
            return fallback
        return None, 0.0

    def _pick_tagged_film(self, tag: str, start_ts: float,
                          used: set[int], *,
                          allow_repeat: bool = True) -> tuple[sqlite3.Row | None, float]:
        pool = self._film_by_tag.get(tag)
        if not pool:
            return None, 0.0
        n = len(pool)
        ptr = self._film_tag_ptr.get(tag, 0)
        fallback: tuple[sqlite3.Row, float] | None = None
        for i in range(n):
            film = pool[(ptr + i) % n]
            dur = self._film_duration(film)
            if self._film_cooling(film["id"], start_ts):
                if fallback is None:
                    fallback = (film, dur)
                continue
            self._film_tag_ptr[tag] = (ptr + i + 1) % n
            return film, dur
        if allow_repeat and fallback is not None:
            self._film_tag_ptr[tag] = (ptr + 1) % n
            return fallback
        if allow_repeat:
            # Last resort: cycle even a 1-title niche pool.
            film = pool[ptr % n]
            self._film_tag_ptr[tag] = (ptr + 1) % n
            return film, self._film_duration(film)
        return None, 0.0

    # -- row writers --------------------------------------------------------
    def _place_episode(self, channel_id, cursor, dur, seg, series, ep) -> None:
        season = ep["season"]
        episode = ep["episode"]
        subtitle = f"S{season:02d}E{episode:02d}"
        if ep["name"]:
            subtitle += f" - {ep['name']}"
        # Real provider synopsis first; the LLM teaser is only a fallback —
        # local models improvise cast/plot when context is thin.
        blurb = _trim(ep["overview"]) or ep["blurb"] or _trim(series["overview"])
        cast = _row_cast(series)
        if cast and blurb and "starring" not in blurb.lower():
            # Keep episode blurbs plot-first; cast is shown in the details panel.
            pass
        start_ts = cursor.timestamp()
        self.conn.execute(
            "INSERT INTO programs(channel_id,start_ts,end_ts,duration_sec,daypart,"
            "title_id,episode_id,file_id,kind,display_title,subtitle,blurb) "
            "VALUES(?,?,?,?,?,?,?,?, 'episode', ?,?,?)",
            (channel_id, start_ts, start_ts + dur, dur, seg.label, series["id"],
             ep["id"], ep["file_id"], series["name"], subtitle, blurb),
        )

    def _place_film(self, channel_id, cursor, dur, seg, film, daypart=None) -> None:
        cast = _row_cast(film)
        subtitle = ""
        if film["year"]:
            subtitle = f"({film['year']})"
        if cast:
            starring = ", ".join(cast[:3])
            subtitle = (subtitle + "  " if subtitle else "") + f"Starring {starring}"
        blurb = _fallback_film_blurb(film["overview"], cast) or film["blurb"]
        start_ts = cursor.timestamp()
        self.conn.execute(
            "INSERT INTO programs(channel_id,start_ts,end_ts,duration_sec,daypart,"
            "title_id,file_id,kind,display_title,subtitle,blurb) "
            "VALUES(?,?,?,?,?,?,?, 'movie', ?,?,?)",
            (channel_id, start_ts, start_ts + dur, dur, daypart or seg.label,
             film["id"], film["movie_file_id"], film["name"], subtitle, blurb),
        )

    def _takeover_for(self, slug: str, day):
        if not self.cfg.schedule.events:
            return None
        day_map = self._takeover_cache.get(day)
        if day_map is None:
            day_map = active_takeovers(self.conn, day)
            self._takeover_cache[day] = day_map
        return day_map.get(slug)


def _cursor_from_stored(raw: int, playable: list[sqlite3.Row]) -> int:
    """Interpret a stored playhead as an abs_order cursor.

    Legacy rows stored a list index into the filtered playable pool. Prefer an
    abs_order match; fall back to index-based conversion when needed.
    """
    if not playable:
        return 0
    abs_orders = [int(ep["abs_order"] or 0) for ep in playable]
    abs_set = set(abs_orders)
    min_ao = min(abs_orders)
    if raw in abs_set:
        return raw
    if 0 <= raw < len(playable):
        return abs_orders[raw]
    if raw > max(abs_orders):
        return min_ao  # past the end → wrap
    return min_ao


def _row_tags(row: sqlite3.Row) -> list[str]:
    try:
        raw = row["tags"]
    except (IndexError, KeyError):
        return []
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(t) for t in val] if isinstance(val, list) else []


def _row_cast(row: sqlite3.Row) -> list[str]:
    try:
        raw = row["cast_json"]
    except (IndexError, KeyError):
        return []
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(c) for c in val] if isinstance(val, list) else []


def _fallback_film_blurb(overview: str | None, cast: list[str]) -> str | None:
    teaser = _trim(overview)
    if not teaser and not cast:
        return None
    if cast and teaser:
        return f"Starring {', '.join(cast[:3])}. {teaser}"
    if cast:
        return f"Starring {', '.join(cast[:3])}."
    return teaser


def _trim(text: str | None, sentences: int = 2) -> str | None:
    if not text:
        return None
    parts = text.strip().split(". ")
    out = ". ".join(parts[:sentences]).strip()
    if out and not out.endswith("."):
        out += "."
    return out or None
