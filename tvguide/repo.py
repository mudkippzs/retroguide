"""Read-side queries the UI uses to render the guide and drive playback."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

from .util.media_ok import PLAYABLE_SQL, file_exists, program_playable


@dataclass
class Program:
    id: int
    channel_id: int
    start_ts: float
    end_ts: float
    duration_sec: float
    daypart: str
    kind: str
    display_title: str
    subtitle: str
    blurb: str
    file_id: int | None
    title_id: int | None
    episode_id: int | None
    path: str | None
    playable: bool = True
    probe_state: str | None = None


@dataclass
class ChannelInfo:
    id: int
    slug: str
    name: str
    tagline: str
    accent: str
    logo: str | None = None


@dataclass
class TitleDetails:
    id: int
    kind: str
    name: str
    year: int | None
    overview: str | None
    genres: list[str]
    cast: list[str]
    rating: float | None
    poster_path: str | None
    blurb: str | None
    franchise: str | None
    tags: list[str]
    episode_name: str | None = None
    episode_overview: str | None = None


_PROG_COLS = (
    "p.id, p.channel_id, p.start_ts, p.end_ts, p.duration_sec, p.daypart, p.kind, "
    "p.display_title, p.subtitle, p.blurb, p.file_id, p.title_id, p.episode_id, "
    "m.path AS path, m.probe_state AS probe_state, m.duration_sec AS file_dur, "
    "m.video_codec AS video_codec"
)


def _to_program(row: sqlite3.Row) -> Program:
    path = row["path"]
    probe_state = row["probe_state"] if "probe_state" in row.keys() else None
    file_dur = row["file_dur"] if "file_dur" in row.keys() else None
    codec = row["video_codec"] if "video_codec" in row.keys() else None
    playable = program_playable(path, probe_state, file_dur, codec)
    # Expose a null path to callers that only check truthiness when the file
    # is known unplayable — keeps skip loops working without every call site
    # needing the new flag.
    return Program(
        id=row["id"], channel_id=row["channel_id"], start_ts=row["start_ts"],
        end_ts=row["end_ts"], duration_sec=row["duration_sec"],
        daypart=row["daypart"] or "", kind=row["kind"],
        display_title=row["display_title"] or "Untitled",
        subtitle=row["subtitle"] or "", blurb=row["blurb"] or "",
        file_id=row["file_id"], title_id=row["title_id"],
        episode_id=row["episode_id"],
        path=path if playable else None,
        playable=playable,
        probe_state=probe_state,
    )


def list_channels(conn: sqlite3.Connection) -> list[ChannelInfo]:
    rows = conn.execute(
        "SELECT id, slug, name, tagline, accent, logo FROM channels ORDER BY position"
    ).fetchall()
    return [ChannelInfo(r["id"], r["slug"], r["name"], r["tagline"] or "",
                        r["accent"] or "#36e0c8", r["logo"]) for r in rows]


def schedule_bounds(conn: sqlite3.Connection) -> tuple[float, float] | None:
    row = conn.execute("SELECT MIN(start_ts) a, MAX(end_ts) b FROM programs").fetchone()
    if row and row["a"] is not None:
        return float(row["a"]), float(row["b"])
    return None


def programs_for(conn: sqlite3.Connection, channel_id: int,
                 start_ts: float, end_ts: float) -> list[Program]:
    rows = conn.execute(
        f"SELECT {_PROG_COLS} FROM programs p "
        "LEFT JOIN media_files m ON m.id=p.file_id "
        "WHERE p.channel_id=? AND p.end_ts>? AND p.start_ts<? "
        "ORDER BY p.start_ts",
        (channel_id, start_ts, end_ts),
    ).fetchall()
    return [_to_program(r) for r in rows]


def program_at(conn: sqlite3.Connection, channel_id: int, ts: float) -> Program | None:
    row = conn.execute(
        f"SELECT {_PROG_COLS} FROM programs p "
        "LEFT JOIN media_files m ON m.id=p.file_id "
        "WHERE p.channel_id=? AND p.start_ts<=? AND p.end_ts>? "
        "ORDER BY p.start_ts LIMIT 1",
        (channel_id, ts, ts),
    ).fetchone()
    return _to_program(row) if row else None


def next_program(conn: sqlite3.Connection, channel_id: int, after_ts: float) -> Program | None:
    row = conn.execute(
        f"SELECT {_PROG_COLS} FROM programs p "
        "LEFT JOIN media_files m ON m.id=p.file_id "
        "WHERE p.channel_id=? AND p.start_ts>=? "
        "ORDER BY p.start_ts LIMIT 1",
        (channel_id, after_ts),
    ).fetchone()
    return _to_program(row) if row else None


def next_playable(conn: sqlite3.Connection, channel_id: int, after_ts: float,
                  hops: int = 40) -> Program | None:
    """Advance past unplayable slots to the next file that exists on disk."""
    cursor = after_ts
    for _ in range(hops):
        prog = next_program(conn, channel_id, cursor)
        if prog is None:
            return None
        if prog.playable and prog.path and os.path.isfile(prog.path):
            return prog
        cursor = prog.end_ts
    return None


def get_program(conn: sqlite3.Connection, program_id: int) -> Program | None:
    row = conn.execute(
        f"SELECT {_PROG_COLS} FROM programs p "
        "LEFT JOIN media_files m ON m.id=p.file_id WHERE p.id=?",
        (program_id,),
    ).fetchone()
    return _to_program(row) if row else None


def get_title_details(conn: sqlite3.Connection, title_id: int,
                      episode_id: int | None = None) -> TitleDetails | None:
    row = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        return None
    try:
        genres = json.loads(row["genres"] or "[]")
    except (json.JSONDecodeError, TypeError):
        genres = []
    try:
        cast = json.loads(row["cast_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        cast = []
    try:
        tags = json.loads(row["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        tags = []
    ep_name = ep_ov = None
    if episode_id:
        ep = conn.execute(
            "SELECT name, overview FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if ep:
            ep_name, ep_ov = ep["name"], ep["overview"]
    return TitleDetails(
        id=row["id"], kind=row["kind"], name=row["name"], year=row["year"],
        overview=row["overview"], genres=list(genres) if isinstance(genres, list) else [],
        cast=list(cast) if isinstance(cast, list) else [],
        rating=row["rating"], poster_path=row["poster_path"], blurb=row["blurb"],
        franchise=row["franchise"],
        tags=list(tags) if isinstance(tags, list) else [],
        episode_name=ep_name, episode_overview=ep_ov,
    )


def has_schedule(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] > 0


# ----------------------------------------------------------------------
# ROAMER: catalog browsing + watch positions (mobile binge mode)
# ----------------------------------------------------------------------

def episode_key(episode_id: int) -> str:
    return f"ep:{episode_id}"


def film_key(title_id: int) -> str:
    return f"film:{title_id}"


def get_watch(conn: sqlite3.Connection, key: str) -> tuple[float, float] | None:
    """(position, duration) for a watch key, or None if never started."""
    row = conn.execute(
        "SELECT position, duration FROM watch_positions WHERE key=?",
        (key,)).fetchone()
    return (float(row["position"]), float(row["duration"])) if row else None


def set_watch(conn: sqlite3.Connection, key: str, position: float,
              duration: float) -> None:
    conn.execute(
        "INSERT INTO watch_positions(key, position, duration, updated_at) "
        "VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
        "position=excluded.position, duration=excluded.duration, "
        "updated_at=excluded.updated_at",
        (key, float(position), float(duration), time.time()))


def _watched_map(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    return {r["key"]: (float(r["position"]), float(r["duration"]))
            for r in conn.execute("SELECT key, position, duration "
                                  "FROM watch_positions")}


def is_watched(pos: float | None, dur: float | None) -> bool:
    """Finished when past 90% or inside the last minute."""
    if not pos or not dur or dur <= 0:
        return False
    return pos >= 0.9 * dur or (dur - pos) <= 60


def catalog_series(conn: sqlite3.Connection) -> list[dict]:
    """All series with playable-episode and watched counts for the binge list.

    Counts are probe-based (no per-file stat) so the list endpoint stays fast
    over network mounts; existence is re-verified at play time.
    """
    watched = _watched_map(conn)
    rows = conn.execute(
        "SELECT t.id, t.name, t.year, t.bucket, t.genres, "
        "       COUNT(e.id) AS n_eps, "
        "       SUM(CASE WHEN m.id IS NOT NULL AND " + PLAYABLE_SQL +
        "           THEN 1 ELSE 0 END) AS n_play "
        "FROM titles t "
        "JOIN episodes e ON e.title_id=t.id "
        "LEFT JOIN media_files m ON m.id=e.file_id "
        "WHERE t.kind='series' "
        "GROUP BY t.id ORDER BY t.sort_name COLLATE NOCASE, t.name"
    ).fetchall()
    out = []
    for r in rows:
        n_watched = 0
        for (ep_id,) in conn.execute(
                "SELECT id FROM episodes WHERE title_id=?", (r["id"],)):
            pos, dur = watched.get(episode_key(ep_id), (None, None))
            if is_watched(pos, dur):
                n_watched += 1
        try:
            genres = json.loads(r["genres"] or "[]")
        except (json.JSONDecodeError, TypeError):
            genres = []
        out.append({
            "id": r["id"], "name": r["name"], "year": r["year"],
            "bucket": r["bucket"], "genres": genres,
            "n_episodes": r["n_eps"], "n_playable": int(r["n_play"] or 0),
            "n_watched": n_watched,
        })
    return out


def catalog_movies(conn: sqlite3.Connection) -> list[dict]:
    """All films with duration and watch state for the binge list."""
    watched = _watched_map(conn)
    rows = conn.execute(
        "SELECT t.id, t.name, t.year, t.genres, m.duration_sec, "
        "       m.probe_state, m.video_codec, m.path "
        "FROM titles t JOIN media_files m ON m.id=t.movie_file_id "
        "WHERE t.kind='movie' ORDER BY t.sort_name COLLATE NOCASE, t.name"
    ).fetchall()
    out = []
    for r in rows:
        playable = program_playable(r["path"], r["probe_state"],
                                    r["duration_sec"], r["video_codec"])
        pos, dur = watched.get(film_key(r["id"]), (None, None))
        try:
            genres = json.loads(r["genres"] or "[]")
        except (json.JSONDecodeError, TypeError):
            genres = []
        out.append({
            "id": r["id"], "name": r["name"], "year": r["year"],
            "genres": genres, "duration": r["duration_sec"],
            "playable": playable,
            "position": pos if pos and not is_watched(pos, dur) else None,
            "watched": is_watched(pos, dur),
        })
    return out


def series_detail(conn: sqlite3.Connection, title_id: int) -> dict | None:
    """Series -> seasons -> episodes with playability + resume positions."""
    t = conn.execute(
        "SELECT id, name, year, overview, genres FROM titles "
        "WHERE id=? AND kind='series'", (title_id,)).fetchone()
    if t is None:
        return None
    watched = _watched_map(conn)
    rows = conn.execute(
        "SELECT e.id, e.season, e.episode, e.name, e.overview, "
        "       m.duration_sec, m.probe_state, m.video_codec, m.path "
        "FROM episodes e LEFT JOIN media_files m ON m.id=e.file_id "
        "WHERE e.title_id=? "
        "ORDER BY CASE WHEN e.season=0 THEN 1 ELSE 0 END, e.abs_order, "
        "         e.episode", (title_id,)).fetchall()
    seasons: dict[int, list] = {}
    for r in rows:
        playable = program_playable(r["path"], r["probe_state"],
                                    r["duration_sec"], r["video_codec"])
        pos, dur = watched.get(episode_key(r["id"]), (None, None))
        seasons.setdefault(r["season"], []).append({
            "id": r["id"], "episode": r["episode"],
            "name": r["name"] or "", "overview": r["overview"] or "",
            "duration": r["duration_sec"], "playable": playable,
            "position": pos if pos and not is_watched(pos, dur) else None,
            "watched": is_watched(pos, dur),
        })
    try:
        genres = json.loads(t["genres"] or "[]")
    except (json.JSONDecodeError, TypeError):
        genres = []
    return {
        "id": t["id"], "name": t["name"], "year": t["year"],
        "overview": t["overview"] or "", "genres": genres,
        "seasons": [{"season": s, "episodes": eps}
                    for s, eps in sorted(seasons.items())],
    }


def episode_play_info(conn: sqlite3.Connection, episode_id: int) -> dict | None:
    """Resolve an episode to a playable file for VOD, or None."""
    r = conn.execute(
        "SELECT e.id, e.title_id, e.season, e.episode, e.name AS ename, "
        "       t.name AS tname, m.path, m.duration_sec, m.probe_state, "
        "       m.video_codec "
        "FROM episodes e JOIN media_files m ON m.id=e.file_id "
        "JOIN titles t ON t.id=e.title_id "
        f"WHERE e.id=? AND {PLAYABLE_SQL}", (episode_id,)).fetchone()
    if r is None or not file_exists(r["path"]):
        return None
    sub = f"S{r['season']:02d}E{r['episode']:02d}"
    if r["ename"]:
        sub += f" — {r['ename']}"
    return {"key": episode_key(r["id"]), "path": r["path"],
            "title": r["tname"], "subtitle": sub,
            "duration": float(r["duration_sec"]), "title_id": r["title_id"],
            "episode_id": r["id"]}


def film_play_info(conn: sqlite3.Connection, title_id: int) -> dict | None:
    """Resolve a film to a playable file for VOD, or None."""
    r = conn.execute(
        "SELECT t.id, t.name, t.year, m.path, m.duration_sec, m.probe_state, "
        "       m.video_codec "
        "FROM titles t JOIN media_files m ON m.id=t.movie_file_id "
        f"WHERE t.id=? AND t.kind='movie' AND {PLAYABLE_SQL}",
        (title_id,)).fetchone()
    if r is None or not file_exists(r["path"]):
        return None
    sub = f"({r['year']})" if r["year"] else ""
    return {"key": film_key(r["id"]), "path": r["path"], "title": r["name"],
            "subtitle": sub, "duration": float(r["duration_sec"]),
            "title_id": r["id"], "episode_id": None}


def next_episode_info(conn: sqlite3.Connection, episode_id: int) -> dict | None:
    """The next playable episode after this one (binge auto-advance).

    Ordering matches the desktop queue: broadcast order, specials last.
    """
    cur = conn.execute(
        "SELECT title_id, season, abs_order, episode FROM episodes "
        "WHERE id=?", (episode_id,)).fetchone()
    if cur is None:
        return None
    rows = conn.execute(
        "SELECT e.id FROM episodes e "
        "JOIN media_files m ON m.id=e.file_id "
        f"WHERE e.title_id=? AND {PLAYABLE_SQL} "
        "ORDER BY CASE WHEN e.season=0 THEN 1 ELSE 0 END, e.abs_order, "
        "         e.episode", (cur["title_id"],)).fetchall()
    ids = [r["id"] for r in rows]
    if episode_id not in ids:
        return None
    nxt = ids.index(episode_id) + 1
    while nxt < len(ids):
        info = episode_play_info(conn, ids[nxt])
        if info is not None:
            return info
        nxt += 1
    return None


def catalog_integrity(conn: sqlite3.Connection) -> dict[str, int]:
    """Counts used by health dots and `retroguide doctor`."""
    def one(q: str) -> int:
        return int(conn.execute(q).fetchone()[0])

    missing = 0
    for r in conn.execute("SELECT path FROM media_files"):
        if not r["path"] or not os.path.isfile(r["path"]):
            missing += 1
    return {
        "files": one("SELECT COUNT(*) FROM media_files"),
        "probe_error": one("SELECT COUNT(*) FROM media_files WHERE probe_state='error'"),
        "probe_pending": one("SELECT COUNT(*) FROM media_files WHERE probe_state='pending'"),
        "missing_paths": missing,
        "movies_no_overview": one(
            "SELECT COUNT(*) FROM titles WHERE kind='movie' "
            "AND (overview IS NULL OR TRIM(overview)='')"),
        "enrich_pending": one(
            "SELECT COUNT(*) FROM titles WHERE enrich_state IS NULL "
            "OR enrich_state IN ('pending','error')"),
        "programs": one("SELECT COUNT(*) FROM programs"),
        "unplayable_programs": one(
            "SELECT COUNT(*) FROM programs p LEFT JOIN media_files m ON m.id=p.file_id "
            "WHERE p.file_id IS NULL OR m.id IS NULL OR m.probe_state!='done' "
            "OR m.duration_sec IS NULL OR m.duration_sec<=60"),
    }
