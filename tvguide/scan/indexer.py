"""Walk the library roots and build the SQLite catalog."""
from __future__ import annotations

import hashlib
import logging
import os
import re as _re
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from ..config import Config
from ..util.media_ok import is_partial_path
from ..util.naming import (
    has_episode_marker,
    heuristic_bucket,
    is_excluded,
    is_special_episode,
    looks_like_video,
    parse_media,
    series_title_from_name,
    sort_name,
)
from .probe import probe

log = logging.getLogger("tvguide.scan")

ProgressCb = Callable[[str, int, int], None]  # message, current, total


def _noop(msg: str, cur: int, total: int) -> None:  # pragma: no cover
    pass

# Folders that group episodes but aren't the show itself.
_SEASON_PREFIX = _re.compile(
    r"^(season|series|book|saison|staffel|stagione|temporada|seizoen|sezon|"
    r"sezona|specials?|extras?|vol(ume)?|disc|cd|part|chapter|s\d{1,2})\b", _re.I)
# Foreign "<n>ª Temporada" / "<n> Staffel" style season folders.
_SEASON_NUM_WORD = _re.compile(
    r"\b\d{1,3}\s*[\u00aa\u00ba\u00b0ao]?\s*"
    r"(temporada|staffel|stagione|saison|seizoen|sezon|sezona|series|season)\b", _re.I)
# Bare numeric folders ("10", "1)", "01.").
_SEASON_NUMERIC = _re.compile(r"^\s*\d{1,3}\s*[\u00aa\u00ba\u00b0ao.)\-]*\s*$")


def _is_season_folder(name: str) -> bool:
    n = name.strip()
    return bool(_SEASON_PREFIX.match(n) or _SEASON_NUM_WORD.search(n)
                or _SEASON_NUMERIC.match(n))


def _good_title(title: str) -> bool:
    t = (title or "").strip()
    return len(t) >= 3 and any(c.isalpha() for c in t)


def walk_videos(roots: list[str], cfg: Config) -> Iterator[Path]:
    exts = [e.lower() for e in cfg.library.video_extensions]
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            dp = Path(dirpath)
            # Prune excluded directories in place.
            dirnames[:] = [
                d for d in dirnames
                if not is_excluded(dp / d, cfg.library.exclude_patterns)
            ]
            for fn in filenames:
                fp = dp / fn
                if not looks_like_video(fp, exts):
                    continue
                if is_excluded(fp, cfg.library.exclude_patterns):
                    continue
                yield fp


class Indexer:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn = conn
        self.cfg = cfg
        self._series_cache: dict[str, int] = {}
        self._movie_cache: dict[tuple[str, int | None], int] = {}

    # -- title resolution ---------------------------------------------------
    def _find_or_create_series(self, name: str, year: int | None) -> int:
        name = (self.cfg.title_aliases or {}).get(name, name)
        key = sort_name(name)
        if key in self._series_cache:
            return self._series_cache[key]
        row = self.conn.execute(
            "SELECT id FROM titles WHERE kind='series' AND sort_name=?", (key,)
        ).fetchone()
        if row:
            self._series_cache[key] = row["id"]
            return row["id"]
        bucket = heuristic_bucket(name, "series")
        cur = self.conn.execute(
            "INSERT INTO titles(kind,name,year,sort_name,bucket) VALUES('series',?,?,?,?)",
            (name, year, key, bucket),
        )
        tid = cur.lastrowid
        self._series_cache[key] = tid
        return tid

    def _find_or_create_movie(self, name: str, year: int | None, file_id: int) -> int:
        key = (sort_name(name), year)
        if key in self._movie_cache:
            return self._movie_cache[key]
        row = self.conn.execute(
            "SELECT id FROM titles WHERE kind='movie' AND sort_name=? AND "
            "(year IS ? OR year=?)",
            (key[0], year, year),
        ).fetchone()
        if row:
            self._movie_cache[key] = row["id"]
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO titles(kind,name,year,sort_name,bucket,movie_file_id) "
            "VALUES('movie',?,?,?, 'film', ?)",
            (name, year, key[0], file_id),
        )
        tid = cur.lastrowid
        self._movie_cache[key] = tid
        return tid

    # -- main scan ----------------------------------------------------------
    def scan(self, progress: ProgressCb = _noop) -> dict:
        cfg = self.cfg
        min_bytes = cfg.library.min_file_mb * 1024 * 1024
        stats = {"files": 0, "episodes": 0, "movies": 0, "skipped": 0}

        roots = [("tv", r) for r in cfg.library.tv_roots] + [
            ("movie", r) for r in cfg.library.movie_roots
        ]

        progress("Discovering files...", 0, 0)
        # Materialize the file list first so we can show progress.
        discovered: list[tuple[str, Path]] = []
        for root_kind, root in roots:
            for fp in walk_videos([root], cfg):
                discovered.append((root_kind, fp))
        total = len(discovered)
        progress(f"Found {total} media files", 0, total)

        for i, (root_kind, fp) in enumerate(discovered):
            try:
                st = fp.stat()
            except OSError:
                stats["skipped"] += 1
                continue
            if st.st_size < min_bytes:
                stats["skipped"] += 1
                continue

            file_id = self._upsert_file(fp, root_kind, st)
            self._ingest_file(file_id, fp, root_kind, stats)

            if i % 200 == 0:
                self.conn.commit()
                progress(f"Indexing {fp.name[:48]}", i + 1, total)

        self.conn.commit()
        purged = self._purge_stale(seen_paths={str(fp) for _, fp in discovered})
        stats["purged"] = purged
        self._finalize_orders()
        self._apply_bucket_overrides()
        progress("Scan complete", total, total)
        return stats

    def _purge_stale(self, seen_paths: set[str] | None = None) -> int:
        """Remove catalog rows for files that vanished or are partial downloads.

        Only deletes when the path is gone from disk (or is a partial). Offline
        mounts must not wipe the catalog — an unreachable root leaves rows alone.
        """
        rows = self.conn.execute("SELECT id, path FROM media_files").fetchall()
        gone: list[int] = []
        for r in rows:
            path = r["path"]
            if is_partial_path(path) or not Path(path).is_file():
                gone.append(r["id"])
        if not gone:
            return 0
        affected_titles: set[int] = set()
        for fid in gone:
            for r in self.conn.execute(
                    "SELECT title_id FROM episodes WHERE file_id=?", (fid,)):
                affected_titles.add(r["title_id"])
            for r in self.conn.execute(
                    "SELECT id FROM titles WHERE movie_file_id=?", (fid,)):
                affected_titles.add(r["id"])
            # Clear every FK that references media_files before the DELETE.
            # programs keeps a copy of file_id for playback; leaving it set
            # raises IntegrityError under PRAGMA foreign_keys=ON.
            self.conn.execute("DELETE FROM programs WHERE file_id=?", (fid,))
            self.conn.execute("UPDATE episodes SET file_id=NULL WHERE file_id=?", (fid,))
            self.conn.execute(
                "UPDATE titles SET movie_file_id=NULL WHERE movie_file_id=?", (fid,))
            self.conn.execute("DELETE FROM media_files WHERE id=?", (fid,))
        self._purge_ghost_titles(affected_titles)
        self.conn.commit()
        log.info("purged %d stale/missing media_files", len(gone))
        return len(gone)

    def _purge_ghost_titles(self, title_ids: set[int]) -> None:
        """Drop titles that no longer own a single file.

        Episode metadata is kept while the title still has other files (it
        carries enriched names/overviews for when the missing file returns);
        a title with nothing playable left is deleted outright so deleted
        shows don't linger in the UI as 0-episode ghosts.
        """
        for tid in title_ids:
            n_eps = self.conn.execute(
                "SELECT COUNT(*) c FROM episodes "
                "WHERE title_id=? AND file_id IS NOT NULL", (tid,),
            ).fetchone()["c"]
            has_film = self.conn.execute(
                "SELECT 1 FROM titles WHERE id=? AND movie_file_id IS NOT NULL",
                (tid,),
            ).fetchone()
            if n_eps or has_film:
                continue
            self.conn.execute("DELETE FROM episodes WHERE title_id=?", (tid,))
            self.conn.execute("DELETE FROM playheads WHERE title_id=?", (tid,))
            self.conn.execute("DELETE FROM programs WHERE title_id=?", (tid,))
            self.conn.execute("DELETE FROM titles WHERE id=?", (tid,))
            log.info("purged ghost title id=%d (no files remain)", tid)

    def reindex_from_db(self, progress: ProgressCb = _noop) -> dict:
        """Rebuild titles/episodes from already-discovered files (no network
        walk, no reprobe). Used to re-apply improved parsing/dedup quickly."""
        stats = {"files": 0, "episodes": 0, "movies": 0, "skipped": 0}
        # Keep broadcast progress across reindex so shows don't restart at S01E01.
        saved_playheads = {
            (r["name"] or "").strip().lower(): int(r["next_order"])
            for r in self.conn.execute(
                "SELECT t.name, p.next_order FROM playheads p "
                "JOIN titles t ON t.id=p.title_id WHERE t.kind='series'"
            )
            if (r["name"] or "").strip()
        }
        # Keep enrichment (tags, buckets, blurbs…) too: a parser-fix reindex
        # must not force a full network re-enrich.
        saved_titles = {
            r["sort_name"]: dict(r)
            for r in self.conn.execute(
                "SELECT sort_name, bucket, tags, overview, genres, cast_json, "
                "rating, blurb, tmdb_id, poster_path, runtime_hint, year, "
                "enrich_state FROM titles"
            )
            if r["sort_name"]
        }
        # Keep episode-level enrichment (name/overview/blurb) too — it is
        # expensive API data and a reindex must not silently drop it.
        saved_episodes = {
            (self._meta_key(r["tname"]), r["season"], r["episode"]):
                (r["name"], r["overview"], r["blurb"])
            for r in self.conn.execute(
                "SELECT e.season, e.episode, e.name, e.overview, e.blurb, "
                "t.name AS tname FROM episodes e "
                "JOIN titles t ON t.id = e.title_id"
            )
        }
        self.conn.execute("DELETE FROM programs")
        self.conn.execute("DELETE FROM playheads")
        self.conn.execute("DELETE FROM episodes")
        self.conn.execute("DELETE FROM titles")
        self.conn.commit()
        self._series_cache.clear()
        self._movie_cache.clear()
        rows = self.conn.execute(
            "SELECT id, path, root FROM media_files ORDER BY path"
        ).fetchall()
        total = len(rows)
        for i, row in enumerate(rows):
            self._ingest_file(row["id"], Path(row["path"]), row["root"], stats)
            if i % 300 == 0:
                self.conn.commit()
                progress("Reindexing catalog", i + 1, total)
        self.conn.commit()
        self._finalize_orders()
        self._restore_playheads(saved_playheads)
        self._restore_title_meta(saved_titles)
        self._restore_episode_meta(saved_episodes)
        self._apply_bucket_overrides()
        progress("Reindex complete", total, total)
        return stats

    @staticmethod
    def _meta_key(name: str) -> str:
        """Punctuation-insensitive match key: a rebuild may drop the colons a
        provider added ("Star Trek: TNG" -> "Star Trek TNG")."""
        return _re.sub(r"[^a-z0-9]+", "", (name or "").lower())

    def _restore_title_meta(self, saved: dict[str, dict]) -> None:
        """Re-apply enrichment fields to rebuilt titles, matched by sort_name."""
        if not saved:
            return
        by_key = {self._meta_key(k): v for k, v in saved.items()}
        for row in self.conn.execute("SELECT id, sort_name FROM titles"):
            meta = saved.get(row["sort_name"]) or by_key.get(
                self._meta_key(row["sort_name"]))
            if not meta:
                continue
            self.conn.execute(
                "UPDATE titles SET bucket=?, tags=?, overview=?, genres=?, "
                "cast_json=?, rating=?, blurb=?, tmdb_id=?, poster_path=?, "
                "runtime_hint=COALESCE(runtime_hint, ?), "
                "year=COALESCE(year, ?), enrich_state=? WHERE id=?",
                (meta["bucket"], meta["tags"], meta["overview"], meta["genres"],
                 meta["cast_json"], meta["rating"], meta["blurb"],
                 meta["tmdb_id"], meta["poster_path"], meta["runtime_hint"],
                 meta["year"], meta["enrich_state"], row["id"]),
            )
        self.conn.commit()

    def _restore_episode_meta(self, saved: dict[tuple[str, int, int], tuple]) -> None:
        """Re-apply episode name/overview/blurb to rebuilt episodes, matched
        by (punctuation-insensitive title key, season, episode)."""
        if not saved:
            return
        restored = 0
        for row in self.conn.execute(
                "SELECT e.id, e.season, e.episode, t.name AS tname "
                "FROM episodes e JOIN titles t ON t.id = e.title_id"):
            meta = saved.get(
                (self._meta_key(row["tname"]), row["season"], row["episode"]))
            if not meta:
                continue
            name, overview, blurb = meta
            self.conn.execute(
                "UPDATE episodes SET name=COALESCE(?, name), "
                "overview=COALESCE(?, overview), blurb=COALESCE(?, blurb) "
                "WHERE id=?",
                (name, overview, blurb, row["id"]),
            )
            restored += 1
        self.conn.commit()
        log.info("reindex: restored enrichment for %d episodes", restored)

    def _apply_bucket_overrides(self) -> None:
        """Config-declared bucket fixes win over heuristics and enrichers."""
        from ..util.naming import sort_name
        for name, bucket in (self.cfg.bucket_overrides or {}).items():
            self.conn.execute(
                "UPDATE titles SET bucket=? WHERE sort_name=?",
                (bucket, sort_name(name)),
            )
        self.conn.commit()

    def _restore_playheads(self, saved: dict[str, int]) -> None:
        """Remap saved abs_order cursors onto rebuilt series by title name."""
        if not saved:
            return
        by_key = {self._meta_key(k): v for k, v in saved.items()}
        for row in self.conn.execute(
            "SELECT id, name FROM titles WHERE kind='series'"
        ):
            key = (row["name"] or "").strip().lower()
            cursor = saved.get(key)
            if cursor is None:
                cursor = by_key.get(self._meta_key(row["name"]))
            if cursor is None:
                continue
            # Clamp to a still-valid abs_order when the catalog shrank/grew.
            bounds = self.conn.execute(
                "SELECT MIN(abs_order) AS lo, MAX(abs_order) AS hi "
                "FROM episodes WHERE title_id=?",
                (row["id"],),
            ).fetchone()
            if bounds is None or bounds["lo"] is None:
                continue
            lo, hi = int(bounds["lo"]), int(bounds["hi"])
            if cursor > hi:
                cursor = lo
            elif cursor < lo:
                cursor = lo
            self.conn.execute(
                "INSERT INTO playheads(title_id, next_order) VALUES(?, ?) "
                "ON CONFLICT(title_id) DO UPDATE SET "
                "next_order=excluded.next_order",
                (row["id"], cursor),
            )
        self.conn.commit()

    def _series_name(self, fp: Path, root_kind: str) -> str:
        """Identify a series by the nearest meaningful folder to the file.

        Walking up from the file and skipping season/book/specials containers
        lands on the show (or release) folder, avoiding both over-grouping into
        a category folder ("TV Show Overflow") and season folders ("Book One").
        """
        roots = (self.cfg.library.tv_roots if root_kind == "tv"
                 else self.cfg.library.movie_roots)
        rel = None
        for r in roots:
            try:
                rel = fp.relative_to(Path(r))
                break
            except ValueError:
                continue
        if rel is None:
            return series_title_from_name(fp.stem)

        ancestors = list(rel.parts[:-1])  # folders under root, top -> immediate
        for folder in reversed(ancestors):       # closest to the file first
            if _is_season_folder(folder):
                continue
            title = series_title_from_name(folder)
            if _good_title(title):
                return title
        return series_title_from_name(fp.stem)

    def _ingest_file(self, file_id: int, fp: Path, root_kind: str, stats: dict) -> None:
        # Decide expected type from the drive + an explicit SxxExx marker so
        # guessit doesn't read stray numbers in movie titles as episodes.
        marker = has_episode_marker(str(fp))
        if marker:
            expected = "episode"
        elif root_kind == "movie":
            expected = "movie"
        else:
            expected = None
        parsed = parse_media(fp, expected)

        if marker:
            kind = "episode"
        elif root_kind == "movie":
            kind = "movie"
        elif parsed.episode is not None:
            kind = "episode"
        elif parsed.kind == "movie":
            kind = "movie"
        else:
            kind = "episode"

        if kind == "episode" and parsed.episode is not None:
            # Bonus/special numbering (S00, S99E00, E00) stays out of the
            # normal playhead -- attach under the series but mark via season.
            series_name = self._series_name(fp, root_kind) or parsed.title
            # Fold "Show: Superfan Episodes" style bonus folders back into the
            # parent show when the filename itself is a real episode marker.
            if ":" in series_name or " - " in series_name:
                base = series_name.split(":")[0].split(" - ")[0].strip()
                if base and len(base) >= 3 and not is_special_episode(
                        parsed.season, parsed.episode):
                    # Keep the folder title for true spin-offs; only collapse
                    # when the file is a regular SxxExx under a "Superfan"/
                    # "Extended" style packaging folder.
                    low = series_name.lower()
                    if any(t in low for t in ("superfan", "extended cut",
                                              "extended edition", "bonus")):
                        series_name = base
            tid = self._find_or_create_series(series_name, parsed.year)
            self._upsert_episode(tid, file_id, parsed)
            stats["episodes"] += 1
        elif kind == "episode" and root_kind == "tv":
            # TV-root file without a parseable episode number: keep as a
            # series special (S00) rather than inventing a movie title.
            series_name = self._series_name(fp, root_kind) or parsed.title
            tid = self._find_or_create_series(series_name, parsed.year)
            # Synthesize a special slot from a *stable* filename digest so it
            # doesn't collide with real episodes, stays out of regular
            # rotation, and -- unlike hash(), which is randomized per process --
            # produces the same number on every rescan instead of piling up
            # duplicate rows.
            digest = hashlib.md5(fp.name.encode()).hexdigest()
            ep_num = int(digest[:8], 16) % 9000 + 100
            class _P:
                season, episode, episode_title = 0, ep_num, parsed.title
            self._upsert_episode(tid, file_id, _P())
            stats["episodes"] += 1
        else:
            tid = self._find_or_create_movie(parsed.title, parsed.year, file_id)
            self.conn.execute(
                "UPDATE titles SET movie_file_id=? WHERE id=? AND movie_file_id IS NULL",
                (file_id, tid),
            )
            stats["movies"] += 1
        stats["files"] += 1

    def _upsert_file(self, fp: Path, root_kind: str, st: os.stat_result) -> int:
        row = self.conn.execute(
            "SELECT id, mtime, size FROM media_files WHERE path=?", (str(fp),)
        ).fetchone()
        now = time.time()
        if row:
            if row["mtime"] != st.st_mtime or row["size"] != st.st_size:
                self.conn.execute(
                    "UPDATE media_files SET size=?, mtime=?, probe_state='pending', "
                    "scanned_at=? WHERE id=?",
                    (st.st_size, st.st_mtime, now, row["id"]),
                )
            else:
                self.conn.execute(
                    "UPDATE media_files SET scanned_at=? WHERE id=?", (now, row["id"])
                )
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO media_files(path,root,size,mtime,scanned_at,probe_state) "
            "VALUES(?,?,?,?,?, 'pending')",
            (str(fp), root_kind, st.st_size, st.st_mtime, now),
        )
        return cur.lastrowid

    def _upsert_episode(self, title_id: int, file_id: int, parsed) -> None:
        # Preserve season 0 (specials) -- `or 1` would silently promote bonus
        # content into the regular rotation.
        season = parsed.season if parsed.season is not None else 1
        episode = parsed.episode
        self.conn.execute(
            "INSERT INTO episodes(title_id,file_id,season,episode,name) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(title_id,season,episode) DO UPDATE SET "
            "file_id=excluded.file_id, "
            "name=COALESCE(episodes.name, excluded.name)",
            (title_id, file_id, season, episode, parsed.episode_title),
        )

    def _finalize_orders(self) -> None:
        """Assign a linear abs_order to each series' episodes (season, episode),
        and normalize 'seasons' that are actually years (e.g. Looney Tunes
        shorts grouped by year -> a single clean, sequential season)."""
        series = self.conn.execute(
            "SELECT id FROM titles WHERE kind='series'"
        ).fetchall()
        for s in series:
            eps = self.conn.execute(
                "SELECT id, season FROM episodes WHERE title_id=? ORDER BY season, episode",
                (s["id"],),
            ).fetchall()
            year_seasons = bool(eps) and min(e["season"] for e in eps) >= 1900
            for order, ep in enumerate(eps):
                if year_seasons:
                    self.conn.execute(
                        "UPDATE episodes SET abs_order=?, season=1, episode=? WHERE id=?",
                        (order, order + 1, ep["id"]),
                    )
                else:
                    self.conn.execute(
                        "UPDATE episodes SET abs_order=? WHERE id=?", (order, ep["id"])
                    )
            self.conn.execute(
                "INSERT OR IGNORE INTO playheads(title_id, next_order) VALUES(?, 0)",
                (s["id"],),
            )
        self.conn.commit()

    # -- probing (slow, network bound) -------------------------------------
    def probe_pending(self, progress: ProgressCb = _noop, limit: int | None = None) -> int:
        rows = self.conn.execute(
            "SELECT id, path FROM media_files WHERE probe_state='pending'"
            + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()
        total = len(rows)
        done = 0
        for i, row in enumerate(rows):
            res = probe(row["path"])
            if res.ok:
                self.conn.execute(
                    "UPDATE media_files SET duration_sec=?, video_codec=?, width=?, "
                    "height=?, container=?, probe_state='done' WHERE id=?",
                    (res.duration_sec, res.video_codec, res.width, res.height,
                     res.container, row["id"]),
                )
                # Propagate duration to episode/movie rows.
                self.conn.execute(
                    "UPDATE episodes SET duration_sec=? WHERE file_id=?",
                    (res.duration_sec, row["id"]),
                )
                if res.duration_sec:
                    self.conn.execute(
                        "UPDATE titles SET runtime_hint=? WHERE movie_file_id=? "
                        "AND runtime_hint IS NULL",
                        (int(res.duration_sec // 60), row["id"]),
                    )
                done += 1
            else:
                self.conn.execute(
                    "UPDATE media_files SET probe_state='error', duration_sec=NULL, "
                    "video_codec=NULL WHERE id=?", (row["id"],)
                )
                # Unlink so the scheduler never treats this as playable.
                self.conn.execute(
                    "UPDATE episodes SET file_id=NULL WHERE file_id=?", (row["id"],))
                self.conn.execute(
                    "UPDATE titles SET movie_file_id=NULL WHERE movie_file_id=?",
                    (row["id"],))
                log.warning("probe failed for %s: %s", row["path"], res.error)
            if i % 25 == 0:
                self.conn.commit()
                progress(f"Probing {Path(row['path']).name[:48]}", i + 1, total)
        self.conn.commit()
        progress("Probe complete", total, total)
        return done
