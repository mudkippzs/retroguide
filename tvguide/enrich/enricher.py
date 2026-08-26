"""Enrich catalog titles with TMDB/TVmaze metadata and scheduling buckets."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from types import SimpleNamespace

from rapidfuzz import fuzz

from ..config import Config
from ..util.naming import heuristic_bucket, looks_like_anime, sort_name as _sort
from .llm import LLM
from .tmdb import TMDBClient
from .tvmaze import TVMazeClient

log = logging.getLogger("tvguide.enrich")

ProgressCb = Callable[[str, int, int], None]

# Minimum fuzzy score before we accept a provider match / rename.
_MATCH_MIN = 72


def _noop(msg: str, cur: int, total: int) -> None:  # pragma: no cover
    pass


class Enricher:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn = conn
        self.cfg = cfg
        self.tmdb = TMDBClient(cfg.tmdb.api_key, cfg.tmdb.language)
        self.tvmaze = TVMazeClient()
        self.use_tmdb = self.tmdb.enabled
        self.llm = LLM(cfg.ollama.host, cfg.ollama.model, cfg.ollama.timeout)

    def run(self, progress: ProgressCb = _noop, limit: int | None = None,
            kind: str = "all", force: bool = False) -> int:
        # Pending + previous errors retry; successful enrichments stay done
        # unless force=True (e.g. re-pull after enabling TMDB).
        if force:
            where = "WHERE 1=1"
        else:
            where = ("WHERE (enrich_state IS NULL OR enrich_state='' "
                     "OR enrich_state='pending' OR enrich_state='error' "
                     "OR enriched_at IS NULL)")
        if kind in ("series", "movie"):
            where += f" AND kind='{kind}'"
        q = f"SELECT * FROM titles {where} ORDER BY id" + (
            f" LIMIT {int(limit)}" if limit else "")
        rows = self.conn.execute(q).fetchall()
        total = len(rows)
        use_llm = self.llm.available()
        done = 0
        for i, row in enumerate(rows):
            try:
                if row["kind"] == "movie":
                    ok = self._enrich_movie(row, use_llm)
                else:
                    ok = self._enrich_series(row, use_llm)
                if ok:
                    self.conn.execute(
                        "UPDATE titles SET enriched_at=?, enrich_state='done', "
                        "enrich_error=NULL WHERE id=?",
                        (time.time(), row["id"]),
                    )
                    done += 1
                else:
                    self.conn.execute(
                        "UPDATE titles SET enrich_state='error', "
                        "enrich_error=? WHERE id=?",
                        ("no metadata", row["id"]),
                    )
            except Exception as exc:  # keep going on individual failures
                log.warning("enrich failed for %s: %s", row["name"], exc)
                progress(f"err {row['name'][:30]}: {exc}", i + 1, total)
                self.conn.execute(
                    "UPDATE titles SET enrich_state='error', enrich_error=? WHERE id=?",
                    (str(exc)[:200], row["id"]),
                )
            if i % 10 == 0:
                self.conn.commit()
                progress(f"Enriching {row['name'][:42]}", i + 1, total)
        merged = self._merge_series_by_tmdb()
        self._backfill_episode_overviews(progress)
        from .tagging import backfill as _tag_backfill
        _tag_backfill(self.conn)
        # Config-declared bucket fixes win over heuristic/LLM classification.
        from ..util.naming import sort_name as _sn
        for name, bucket in (self.cfg.bucket_overrides or {}).items():
            self.conn.execute(
                "UPDATE titles SET bucket=? WHERE sort_name=?",
                (bucket, _sn(name)),
            )
        self.conn.commit()
        progress(f"Enrichment complete ({merged} dupes merged)", total, total)
        return done

    def _merge_series_by_tmdb(self) -> int:
        """Safely merge duplicate series after enrichment."""
        id_groups = self.conn.execute(
            "SELECT GROUP_CONCAT(id) ids FROM titles "
            "WHERE kind='series' AND tmdb_id IS NOT NULL GROUP BY tmdb_id HAVING COUNT(*)>1"
        ).fetchall()
        groups = [[int(x) for x in g["ids"].split(",")] for g in id_groups]

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
                self.conn.execute(
                    "UPDATE programs SET title_id=? WHERE title_id=?", (survivor, dup))
                self.conn.execute("DELETE FROM playheads WHERE title_id=?", (dup,))
                self.conn.execute("DELETE FROM titles WHERE id=?", (dup,))
                merged += 1
            eps = self.conn.execute(
                "SELECT id FROM episodes WHERE title_id=? ORDER BY season, episode",
                (survivor,),
            ).fetchall()
            for order, ep in enumerate(eps):
                self.conn.execute(
                    "UPDATE episodes SET abs_order=? WHERE id=?", (order, ep["id"]))
        self.conn.commit()
        return merged

    def _name_ok(self, local: str, remote: str | None) -> bool:
        if not remote:
            return False
        score = fuzz.token_set_ratio(_sort(local), _sort(remote))
        return score >= _MATCH_MIN

    @staticmethod
    def _year_ok(local_year: int | None, remote_year: int | None) -> bool:
        """Reject matches whose year contradicts a known local year — the
        classic poison case is a same-named remake/revival (Scrubs 2001 vs
        Scrubs 2026). Unknown years on either side can't contradict."""
        if not local_year or not remote_year:
            return True
        return abs(int(local_year) - int(remote_year)) <= 2

    def _enrich_movie(self, row, use_llm: bool) -> bool:
        """Return True when useful metadata was written."""
        meta = self.tmdb.movie(row["name"], row["year"]) if self.tmdb.enabled else None
        if meta and not self._name_ok(row["name"], meta.name):
            log.info("rejecting low-confidence TMDB movie match %r -> %r",
                     row["name"], meta.name)
            meta = None
        elif meta and not self._year_ok(row["year"], meta.year):
            log.info("rejecting year-conflict TMDB movie match %r (%s) -> %r (%s)",
                     row["name"], row["year"], meta.name, meta.year)
            meta = None

        wrote = False
        if meta:
            self.conn.execute(
                "UPDATE titles SET tmdb_id=?, overview=?, genres=?, cast_json=?, "
                "rating=?, poster_path=?, runtime_hint=COALESCE(runtime_hint,?), "
                "name=COALESCE(?, name), year=COALESCE(year,?), bucket='film' WHERE id=?",
                (meta.tmdb_id, meta.overview, json.dumps(meta.genres),
                 json.dumps(meta.cast), meta.rating, meta.poster_url,
                 meta.runtime, meta.name, meta.year, row["id"]),
            )
            wrote = bool(meta.overview or meta.cast)
        elif use_llm:
            # Keyless fallback: local model invents a short synopsis + cast guess
            # from the filename so the guide is never blank for films.
            overview = self.llm.movie_overview(row["name"], row["year"])
            if overview:
                self.conn.execute(
                    "UPDATE titles SET overview=?, bucket='film' WHERE id=?",
                    (overview, row["id"]),
                )
                wrote = True
            blurb = self.llm.movie_blurb(row["name"], row["year"], [], overview)
            if blurb:
                self.conn.execute(
                    "UPDATE titles SET blurb=? WHERE id=?", (blurb, row["id"]))
                wrote = True
        return wrote

    def _enrich_series(self, row, use_llm: bool) -> bool:
        meta = None
        provider = None
        if self.use_tmdb:
            meta = self.tmdb.tv(row["name"], row["year"])
            provider = "tmdb"
        if meta is None:
            meta = self.tvmaze.tv(row["name"], row["year"])
            provider = "tvmaze"
        if meta and not self._name_ok(row["name"], meta.name):
            log.info("rejecting low-confidence %s match %r -> %r",
                     provider, row["name"], meta.name)
            meta = None
            provider = None
        elif meta and not self._year_ok(row["year"], meta.year):
            log.info("rejecting year-conflict %s match %r (%s) -> %r (%s)",
                     provider, row["name"], row["year"], meta.name, meta.year)
            meta = None
            provider = None

        genres: list[str] = []
        origin = None
        wrote = False
        if meta:
            genres = meta.genres
            origin = getattr(meta, "origin", None)
            self.conn.execute(
                "UPDATE titles SET tmdb_id=?, overview=?, genres=?, cast_json=?, "
                "rating=?, poster_path=?, year=COALESCE(year,?) WHERE id=?",
                (meta.tmdb_id, meta.overview, json.dumps(meta.genres),
                 json.dumps(meta.cast or []), meta.rating,
                 meta.poster_url, meta.year, row["id"]),
            )
            wrote = True
            if meta.name:
                try:
                    self.conn.execute(
                        "UPDATE titles SET name=?, sort_name=? WHERE id=?",
                        (meta.name, _sort(meta.name), row["id"]))
                except sqlite3.IntegrityError:
                    pass

        canon = (meta.name if meta and meta.name else row["name"])
        bucket = heuristic_bucket(canon, "series", genres, origin=origin)
        if looks_like_anime(canon, genres=genres, origin=origin):
            bucket = "anime"
        # Re-classify ambiguous animation via LLM when genres are thin.
        if use_llm and (bucket in ("drama", "other", "kids_cartoon")
                        and (not genres or "animation" in {g.lower() for g in genres})):
            llm_bucket = self.llm.classify(canon, meta.overview if meta else None, genres)
            if llm_bucket:
                bucket = llm_bucket
        self.conn.execute("UPDATE titles SET bucket=? WHERE id=?", (bucket, row["id"]))
        self._enrich_episodes(row["id"], canon, row["year"], meta, provider)
        return wrote or bool(bucket)

    def _enrich_episodes(self, title_id: int, name: str, year: int | None,
                         meta, provider: str | None) -> None:
        """Fill episode names/overviews. Prefer TVmaze's full episode list."""
        ep_map: dict[tuple[int, int], dict] = {}
        if provider == "tvmaze" and meta and meta.tmdb_id:
            ep_map = self.tvmaze.tv_season_all(meta.tmdb_id)
        else:
            # Show meta may be from TMDB; still prefer TVmaze for per-episode copy.
            maze = self.tvmaze.tv(name, year)
            if maze and maze.tmdb_id and self._name_ok(name, maze.name):
                ep_map = self.tvmaze.tv_season_all(maze.tmdb_id)
            elif meta and meta.tmdb_id and provider == "tmdb":
                for season in self.conn.execute(
                    "SELECT DISTINCT season FROM episodes WHERE title_id=? "
                    "ORDER BY season",
                    (title_id,),
                ):
                    for ep_num, info in self.tmdb.tv_season(
                            meta.tmdb_id, season["season"]).items():
                        ep_map[(season["season"], ep_num)] = info

        # TVmaze often has episode names but no summaries for older shows;
        # top up missing overviews from TMDB's season data when we can.
        # NB: meta.tmdb_id is a *TVmaze* id when provider == "tvmaze"; the
        # titles table always stores the TMDB id, so read it from there.
        row = self.conn.execute(
            "SELECT tmdb_id FROM titles WHERE id=?", (title_id,)).fetchone()
        tmdb_id = row["tmdb_id"] if row else None
        if tmdb_id and any(not i.get("overview") for i in ep_map.values()):
            for season in {s for s, _ in ep_map}:
                for ep_num, info in self.tmdb.tv_season(tmdb_id, season).items():
                    cur = ep_map.get((season, ep_num))
                    if cur is None:
                        ep_map[(season, ep_num)] = info
                    elif not cur.get("overview") and info.get("overview"):
                        cur["overview"] = info["overview"]

        for (season, ep_num), info in ep_map.items():
            self.conn.execute(
                "UPDATE episodes SET name=COALESCE(?, name), "
                "overview=COALESCE(?, overview), enriched_at=? "
                "WHERE title_id=? AND season=? AND episode=?",
                (info.get("name"), info.get("overview"), time.time(),
                 title_id, season, ep_num),
            )

    def _backfill_episode_overviews(self, progress: ProgressCb = _noop) -> int:
        """Fill episode names/overviews for series whose title enrichment is
        already done but whose episodes are missing copy (e.g. after a
        reindex, or an earlier run without provider access)."""
        rows = self.conn.execute(
            "SELECT DISTINCT t.id, t.name, t.year, t.tmdb_id FROM titles t "
            "JOIN episodes e ON e.title_id = t.id "
            "WHERE t.kind='series' AND (e.overview IS NULL OR e.overview='')"
        ).fetchall()
        filled = 0
        for i, row in enumerate(rows):
            try:
                before = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM episodes "
                    "WHERE title_id=? AND overview IS NOT NULL AND overview!=''",
                    (row["id"],)).fetchone()["n"]
                meta = None
                provider = None
                if row["tmdb_id"]:
                    meta = SimpleNamespace(tmdb_id=row["tmdb_id"],
                                           name=row["name"])
                    provider = "tmdb"
                self._enrich_episodes(row["id"], row["name"], row["year"],
                                      meta, provider)
                after = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM episodes "
                    "WHERE title_id=? AND overview IS NOT NULL AND overview!=''",
                    (row["id"],)).fetchone()["n"]
                filled += max(0, after - before)
            except Exception as exc:
                log.warning("episode backfill failed for %s: %s",
                            row["name"], exc)
            if i % 5 == 0:
                self.conn.commit()
                progress(f"Episode overviews: {row['name'][:36]}",
                         i + 1, len(rows))
        self.conn.commit()
        log.info("episode overview backfill: %d episodes across %d series",
                 filled, len(rows))
        return filled

    # -- program blurbs ----------------------------------------------------
    def write_program_blurbs(self, progress: ProgressCb = _noop) -> int:
        """Generate spoiler-free retro blurbs; fall back to overview+cast offline."""
        use_llm = self.llm.available()
        if not use_llm:
            progress("LLM unavailable — writing fallback blurbs", 0, 0)

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
            blurb = None
            if use_llm:
                blurb = self.llm.episode_blurb(ep["show"], ep["name"], ov)
            blurb = blurb or _fallback_blurb(ov, None)
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
            blurb = None
            if use_llm:
                blurb = self.llm.movie_blurb(mv["name"], mv["year"], cast, mv["overview"])
            blurb = blurb or _fallback_blurb(mv["overview"], mv["cast_json"])
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
    if not overview and not cast_json:
        return None
    teaser = ""
    if overview:
        text = overview.strip()
        parts = text.split(". ")
        teaser = ". ".join(parts[:2]).strip()
        if teaser and not teaser.endswith("."):
            teaser += "."
    if cast_json:
        try:
            cast = json.loads(cast_json)
            if cast:
                if teaser:
                    return f"Starring {', '.join(cast[:3])}. {teaser}"
                return f"Starring {', '.join(cast[:3])}."
        except json.JSONDecodeError:
            pass
    return teaser or None
