"""Enrich catalog titles with TMDB metadata and scheduling buckets."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable

from ..config import Config
from ..util.naming import heuristic_bucket, sort_name as _sort
from .llm import LLM
from .tmdb import TMDBClient
from .tvmaze import TVMazeClient

ProgressCb = Callable[[str, int, int], None]


def _noop(msg: str, cur: int, total: int) -> None:  # pragma: no cover
    pass


class Enricher:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn = conn
        self.cfg = cfg
        self.tmdb = TMDBClient(cfg.tmdb.api_key, cfg.tmdb.language)
        self.tvmaze = TVMazeClient()
        # TMDB (if a key is set) covers films + TV; otherwise TVmaze handles TV
        # for free and films lean on the local model.
        self.use_tmdb = self.tmdb.enabled
        self.llm = LLM(cfg.ollama.host, cfg.ollama.model, cfg.ollama.timeout)

    def run(self, progress: ProgressCb = _noop, limit: int | None = None,
            kind: str = "all") -> int:
        where = "WHERE enriched_at IS NULL"
        if kind in ("series", "movie"):
            where += f" AND kind='{kind}'"
        q = f"SELECT * FROM titles {where} ORDER BY id" + (
            f" LIMIT {int(limit)}" if limit else "")
        rows = self.conn.execute(q).fetchall()
        total = len(rows)
        use_llm = self.llm.available()
        for i, row in enumerate(rows):
            try:
                if row["kind"] == "movie":
                    self._enrich_movie(row, use_llm)
                else:
                    self._enrich_series(row, use_llm)
            except Exception as exc:  # keep going on individual failures
                progress(f"err {row['name'][:30]}: {exc}", i + 1, total)
            self.conn.execute(
                "UPDATE titles SET enriched_at=? WHERE id=?", (time.time(), row["id"])
            )
            if i % 10 == 0:
                self.conn.commit()
                progress(f"Enriching {row['name'][:42]}", i + 1, total)
        merged = self._merge_series_by_tmdb()
        # Tag franchises / holidays so the special-event scheduler has data.
        from .tagging import backfill as _tag_backfill
        _tag_backfill(self.conn)
        self.conn.commit()
        progress(f"Enrichment complete ({merged} dupes merged)", total, total)
        return total

    def _merge_series_by_tmdb(self) -> int:
        """Safely merge duplicate series after enrichment, by identical provider
        id OR identical canonical name (e.g. 'Andor' and 'Star Wars Andor' both
        resolve to TVmaze 'Andor'). Both signals are exact, so spin-offs like
        'The Walking Dead: Dead City' are never touched."""
        id_groups = self.conn.execute(
            "SELECT GROUP_CONCAT(id) ids FROM titles "
            "WHERE kind='series' AND tmdb_id IS NOT NULL GROUP BY tmdb_id HAVING COUNT(*)>1"
        ).fetchall()
        groups = [[int(x) for x in g["ids"].split(",")] for g in id_groups]

        # Exact-name duplicates: merge only when the non-null years agree, so
        # same-named remakes (1978 vs 2004) stay separate but Andor(2022)+
        # Andor(NULL) fold together.
        by_name: dict[str, list[tuple[int, int | None]]] = {}
        for r in self.conn.execute(
            "SELECT id, LOWER(name) nm, year FROM titles WHERE kind='series'"
        ):
            by_name.setdefault(r["nm"], []).append((r["id"], r["year"]))
        for rows in by_name.values():
            if len(rows) < 2:
                continue
            years = {y for _, y in rows if y is not None}
            if len(years) <= 1:
                groups.append([rid for rid, _ in rows])

        merged = 0
        for ids in groups:
            ids = sorted(set(ids))
            # A title may have been deleted by an earlier group; re-check.
            ids = [i for i in ids if self.conn.execute(
                "SELECT 1 FROM titles WHERE id=?", (i,)).fetchone()]
            if len(ids) < 2:
                continue
            survivor, dups = ids[0], ids[1:]
            for dup in dups:
                eps = self.conn.execute(
                    "SELECT id, season, episode FROM episodes WHERE title_id=?", (dup,)
                ).fetchall()
                for ep in eps:
                    clash = self.conn.execute(
                        "SELECT 1 FROM episodes WHERE title_id=? AND season=? AND episode=?",
                        (survivor, ep["season"], ep["episode"]),
                    ).fetchone()
                    if clash:
                        self.conn.execute("DELETE FROM programs WHERE episode_id=?", (ep["id"],))
                        self.conn.execute("DELETE FROM episodes WHERE id=?", (ep["id"],))
                    else:
                        self.conn.execute(
                            "UPDATE episodes SET title_id=? WHERE id=?", (survivor, ep["id"]))
                # Repoint any scheduled programs that referenced the dup title.
                self.conn.execute(
                    "UPDATE programs SET title_id=? WHERE title_id=?", (survivor, dup))
                self.conn.execute("DELETE FROM playheads WHERE title_id=?", (dup,))
                self.conn.execute("DELETE FROM titles WHERE id=?", (dup,))
                merged += 1
            # Re-linearize the survivor's episodes.
            eps = self.conn.execute(
                "SELECT id FROM episodes WHERE title_id=? ORDER BY season, episode",
                (survivor,),
            ).fetchall()
            for order, ep in enumerate(eps):
                self.conn.execute(
                    "UPDATE episodes SET abs_order=? WHERE id=?", (order, ep["id"]))
        self.conn.commit()
        return merged

    def _enrich_movie(self, row, use_llm: bool) -> None:
        meta = self.tmdb.movie(row["name"], row["year"]) if self.tmdb.enabled else None
        if meta:
            self.conn.execute(
                "UPDATE titles SET tmdb_id=?, overview=?, genres=?, cast_json=?, "
                "rating=?, poster_path=?, runtime_hint=COALESCE(runtime_hint,?), "
                "name=COALESCE(?, name), year=COALESCE(year,?), bucket='film' WHERE id=?",
                (meta.tmdb_id, meta.overview, json.dumps(meta.genres),
                 json.dumps(meta.cast), meta.rating, meta.poster_url,
                 meta.runtime, meta.name, meta.year, row["id"]),
            )
        # bucket already 'film'

    def _enrich_series(self, row, use_llm: bool) -> None:
        # Prefer TMDB when a key exists, else use the free TVmaze provider.
        meta = None
        provider = None
        if self.use_tmdb:
            meta = self.tmdb.tv(row["name"], row["year"])
            provider = "tmdb"
        if meta is None:
            meta = self.tvmaze.tv(row["name"], row["year"])
            provider = "tvmaze"

        genres: list[str] = []
        if meta:
            genres = meta.genres
            self.conn.execute(
                "UPDATE titles SET tmdb_id=?, overview=?, genres=?, rating=?, "
                "poster_path=?, year=COALESCE(year,?) WHERE id=?",
                (meta.tmdb_id, meta.overview, json.dumps(meta.genres), meta.rating,
                 meta.poster_url, meta.year, row["id"]),
            )
            # Rename to the canonical provider title, but tolerate the case
            # where that name already exists (a duplicate) -- the TMDB-id merge
            # below will fold them together afterwards.
            if meta.name:
                try:
                    self.conn.execute(
                        "UPDATE titles SET name=?, sort_name=? WHERE id=?",
                        (meta.name, _sort(meta.name), row["id"]))
                except sqlite3.IntegrityError:
                    pass

        bucket = heuristic_bucket(row["name"], "series", genres)
        if use_llm and (bucket in ("drama", "other") and not genres):
            llm_bucket = self.llm.classify(row["name"], meta.overview if meta else None, genres)
            if llm_bucket:
                bucket = llm_bucket
        self.conn.execute("UPDATE titles SET bucket=? WHERE id=?", (bucket, row["id"]))

        # Pull episode names/overviews from the chosen provider.
        if meta and meta.tmdb_id:
            if provider == "tvmaze":
                ep_map = self.tvmaze.tv_season_all(meta.tmdb_id)
                for (season, ep_num), info in ep_map.items():
                    self.conn.execute(
                        "UPDATE episodes SET name=COALESCE(?, name), "
                        "overview=COALESCE(?, overview) WHERE title_id=? AND "
                        "season=? AND episode=?",
                        (info["name"], info["overview"], row["id"], season, ep_num),
                    )
            else:
                seasons = [r["season"] for r in self.conn.execute(
                    "SELECT DISTINCT season FROM episodes WHERE title_id=? ORDER BY season",
                    (row["id"],),
                )]
                for season in seasons:
                    ep_map = self.tmdb.tv_season(meta.tmdb_id, season)
                    for ep_num, info in ep_map.items():
                        self.conn.execute(
                            "UPDATE episodes SET name=COALESCE(?, name), "
                            "overview=COALESCE(?, overview) WHERE title_id=? AND "
                            "season=? AND episode=?",
                            (info["name"], info["overview"], row["id"], season, ep_num),
                        )

    # -- program blurbs: retro-voice teasers for everything on the grid ----
    def write_program_blurbs(self, progress: ProgressCb = _noop) -> int:
        """Generate spoiler-free retro blurbs for each unique program, dedup by
        episode/movie, and cache them back so future weeks reuse them."""
        if not self.llm.available():
            progress("LLM unavailable", 0, 0)
            return 0

        # Unique episodes appearing in the schedule that still lack a blurb.
        eps = self.conn.execute(
            "SELECT DISTINCT e.id, e.name, e.overview, t.name AS show, t.overview AS sov "
            "FROM programs p JOIN episodes e ON e.id=p.episode_id "
            "JOIN titles t ON t.id=e.title_id "
            "WHERE p.kind='episode' AND (e.blurb IS NULL OR e.blurb='')"
        ).fetchall()
        movies = self.conn.execute(
            "SELECT DISTINCT t.id, t.name, t.year, t.cast_json, t.overview "
            "FROM programs p JOIN titles t ON t.id=p.title_id "
            "WHERE p.kind='movie' AND (t.blurb IS NULL OR t.blurb='')"
        ).fetchall()

        total = len(eps) + len(movies)
        done = 0
        for ep in eps:
            ov = ep["overview"] or ep["sov"]
            blurb = self.llm.episode_blurb(ep["show"], ep["name"], ov) or _fallback_blurb(ov, None)
            if blurb:
                self.conn.execute("UPDATE episodes SET blurb=? WHERE id=?", (blurb, ep["id"]))
                self.conn.execute(
                    "UPDATE programs SET blurb=? WHERE episode_id=?", (blurb, ep["id"]))
            done += 1
            if done % 5 == 0:
                self.conn.commit()
                progress(f"Blurb: {ep['show'][:36]}", done, total)
        for mv in movies:
            cast = json.loads(mv["cast_json"] or "[]")
            blurb = self.llm.movie_blurb(mv["name"], mv["year"], cast, mv["overview"]) \
                or _fallback_blurb(mv["overview"], mv["cast_json"])
            if blurb:
                self.conn.execute("UPDATE titles SET blurb=? WHERE id=?", (blurb, mv["id"]))
                self.conn.execute(
                    "UPDATE programs SET blurb=? WHERE title_id=? AND kind='movie'",
                    (blurb, mv["id"]))
            done += 1
            if done % 5 == 0:
                self.conn.commit()
                progress(f"Blurb: {mv['name'][:36]}", done, total)
        self.conn.commit()
        progress("Blurbs complete", total, total)
        return total

    # -- blurbs (used by scheduler, lazily, only for aired programs) --------
    def blurb_for_movie(self, title_row) -> str | None:
        if not self.llm.available():
            return _fallback_blurb(title_row["overview"], title_row["cast_json"])
        cast = json.loads(title_row["cast_json"] or "[]")
        return self.llm.movie_blurb(
            title_row["name"], title_row["year"], cast, title_row["overview"]
        ) or _fallback_blurb(title_row["overview"], title_row["cast_json"])

    def blurb_for_episode(self, show_name: str, ep_row) -> str | None:
        if not self.llm.available():
            return _fallback_blurb(ep_row["overview"], None)
        return self.llm.episode_blurb(
            show_name, ep_row["name"], ep_row["overview"]
        ) or _fallback_blurb(ep_row["overview"], None)


def _fallback_blurb(overview: str | None, cast_json: str | None) -> str | None:
    if not overview:
        return None
    text = overview.strip()
    # Trim to ~2 sentences.
    parts = text.split(". ")
    teaser = ". ".join(parts[:2]).strip()
    if not teaser.endswith("."):
        teaser += "."
    if cast_json:
        try:
            cast = json.loads(cast_json)
            if cast:
                return f"Starring {', '.join(cast[:3])}. {teaser}"
        except json.JSONDecodeError:
            pass
    return teaser
