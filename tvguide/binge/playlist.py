"""Playlist storage and expansion for binge-mode.

A playlist is an ordered list of catalog references at any granularity
(film, series, season, episode). Coarse references are expanded to concrete
playable files only at play time, so saved playlists survive rescans and
catalog growth. Expansion never touches broadcast playheads.
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
from dataclasses import dataclass

from ..util.media_ok import PLAYABLE_SQL, file_exists

QUEUE_NAME = "__queue__"

ITEM_KINDS = ("film", "series", "season", "episode")


@dataclass
class QueueItem:
    """One resolved, playable entry in an expanded queue."""
    path: str
    display_title: str
    subtitle: str
    duration_sec: float
    title_id: int
    episode_id: int | None = None


@dataclass
class Item:
    """A raw playlist_items row."""
    id: int
    kind: str
    title_id: int
    season: int | None
    episode_id: int | None
    label: str


def ensure_queue(conn: sqlite3.Connection) -> int:
    """Return the id of the reserved live-queue playlist, creating it."""
    row = conn.execute(
        "SELECT id FROM playlists WHERE name=?", (QUEUE_NAME,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO playlists(name, created_at) VALUES(?, ?)",
        (QUEUE_NAME, time.time()))
    return int(cur.lastrowid)


def items(conn: sqlite3.Connection, playlist_id: int | None = None) -> list[Item]:
    pid = playlist_id if playlist_id is not None else ensure_queue(conn)
    rows = conn.execute(
        "SELECT i.id, i.kind, i.title_id, i.season, i.episode_id, "
        "       t.name, t.kind AS tkind, e.season AS es, e.episode AS ee, "
        "       e.name AS ename "
        "FROM playlist_items i "
        "JOIN titles t ON t.id = i.title_id "
        "LEFT JOIN episodes e ON e.id = i.episode_id "
        "WHERE i.playlist_id=? ORDER BY i.position, i.id", (pid,)).fetchall()
    out: list[Item] = []
    for r in rows:
        label = _label(r["kind"], r["name"], r["season"],
                       r["es"], r["ee"], r["ename"])
        out.append(Item(r["id"], r["kind"], r["title_id"], r["season"],
                        r["episode_id"], label))
    return out


def _label(kind: str, name: str, season: int | None,
           es: int | None, ee: int | None, ename: str | None) -> str:
    if kind == "season":
        return f"{name} — Season {season}"
    if kind == "episode" and es is not None:
        base = f"{name} S{es:02d}E{ee:02d}"
        return f"{base} — {ename}" if ename else base
    return name


def add(conn: sqlite3.Connection, kind: str, title_id: int,
        season: int | None = None, episode_id: int | None = None,
        playlist_id: int | None = None) -> None:
    if kind not in ITEM_KINDS:
        raise ValueError(f"unknown item kind: {kind}")
    pid = playlist_id if playlist_id is not None else ensure_queue(conn)
    nxt = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_items "
        "WHERE playlist_id=?", (pid,)).fetchone()[0]
    conn.execute(
        "INSERT INTO playlist_items(playlist_id, position, kind, title_id, "
        "season, episode_id) VALUES(?,?,?,?,?,?)",
        (pid, nxt, kind, title_id, season, episode_id))


def remove(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM playlist_items WHERE id=?", (item_id,))


def clear(conn: sqlite3.Connection, playlist_id: int | None = None) -> None:
    pid = playlist_id if playlist_id is not None else ensure_queue(conn)
    conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))


def _renumber(conn: sqlite3.Connection, pid: int) -> None:
    rows = conn.execute(
        "SELECT id FROM playlist_items WHERE playlist_id=? "
        "ORDER BY position, id", (pid,)).fetchall()
    for pos, r in enumerate(rows):
        conn.execute("UPDATE playlist_items SET position=? WHERE id=?",
                     (pos, r["id"]))


def move(conn: sqlite3.Connection, item_id: int, delta: int,
         playlist_id: int | None = None) -> None:
    pid = playlist_id if playlist_id is not None else ensure_queue(conn)
    _renumber(conn, pid)
    rows = conn.execute(
        "SELECT id FROM playlist_items WHERE playlist_id=? "
        "ORDER BY position", (pid,)).fetchall()
    ids = [r["id"] for r in rows]
    if item_id not in ids:
        return
    i = ids.index(item_id)
    j = max(0, min(len(ids) - 1, i + delta))
    if i == j:
        return
    ids.insert(j, ids.pop(i))
    for pos, iid in enumerate(ids):
        conn.execute("UPDATE playlist_items SET position=? WHERE id=?",
                     (pos, iid))


def shuffle(conn: sqlite3.Connection, playlist_id: int | None = None) -> None:
    """Reorder items randomly. A series/season block stays intact and still
    plays in episode order — shuffle mixes the blocks, not inside them."""
    pid = playlist_id if playlist_id is not None else ensure_queue(conn)
    rows = conn.execute(
        "SELECT id FROM playlist_items WHERE playlist_id=?", (pid,)).fetchall()
    ids = [r["id"] for r in rows]
    random.shuffle(ids)
    for pos, iid in enumerate(ids):
        conn.execute("UPDATE playlist_items SET position=? WHERE id=?",
                     (pos, iid))


# ----------------------------------------------------------------------
# Expansion
# ----------------------------------------------------------------------
def _episode_queue_item(r: sqlite3.Row) -> QueueItem:
    sub = f"S{r['season']:02d}E{r['episode']:02d}"
    if r["ename"]:
        sub += f" — {r['ename']}"
    return QueueItem(r["path"], r["tname"], sub, r["duration_sec"],
                     r["title_id"], r["id"])


def _playable_episodes(conn: sqlite3.Connection, title_id: int,
                       season: int | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT e.id, e.title_id, e.season, e.episode, e.name AS ename, "
        "       m.path, m.duration_sec, t.name AS tname "
        "FROM episodes e "
        "JOIN media_files m ON m.id = e.file_id "
        "JOIN titles t ON t.id = e.title_id "
        f"WHERE e.title_id=? AND {PLAYABLE_SQL}")
    params: list = [title_id]
    if season is not None:
        sql += " AND e.season=?"
        params.append(season)
    # Broadcast order first (abs_order), season-0 specials appended after.
    sql += " ORDER BY CASE WHEN e.season=0 THEN 1 ELSE 0 END, e.abs_order, e.episode"
    return [r for r in conn.execute(sql, params).fetchall()
            if file_exists(r["path"])]


def _film_queue_item(conn: sqlite3.Connection, title_id: int) -> QueueItem | None:
    r = conn.execute(
        "SELECT t.id, t.name, t.year, m.path, m.duration_sec "
        "FROM titles t JOIN media_files m ON m.id = t.movie_file_id "
        f"WHERE t.id=? AND {PLAYABLE_SQL}", (title_id,)).fetchone()
    if not r or not file_exists(r["path"]):
        return None
    sub = f"({r['year']})" if r["year"] else ""
    return QueueItem(r["path"], r["name"], sub, r["duration_sec"], r["id"])


def expand(conn: sqlite3.Connection,
           playlist_id: int | None = None) -> list[QueueItem]:
    """Resolve the queue to concrete playable files, in order.

    Dead files and empty references are skipped silently — a binge should
    never stall on a missing episode.
    """
    out: list[QueueItem] = []
    for it in items(conn, playlist_id):
        if it.kind == "film":
            qi = _film_queue_item(conn, it.title_id)
            if qi:
                out.append(qi)
        elif it.kind == "episode":
            rows = conn.execute(
                "SELECT e.id, e.title_id, e.season, e.episode, "
                "       e.name AS ename, m.path, m.duration_sec, "
                "       t.name AS tname "
                "FROM episodes e "
                "JOIN media_files m ON m.id = e.file_id "
                "JOIN titles t ON t.id = e.title_id "
                f"WHERE e.id=? AND {PLAYABLE_SQL}", (it.episode_id,)).fetchall()
            out.extend(_episode_queue_item(r) for r in rows
                       if file_exists(r["path"]))
        else:
            season = it.season if it.kind == "season" else None
            out.extend(_episode_queue_item(r)
                       for r in _playable_episodes(conn, it.title_id, season))
    return out


def item_stats(conn: sqlite3.Connection, it: Item) -> tuple[int, float]:
    """(playable file count, total seconds) for one stored item."""
    if it.kind == "film":
        qi = _film_queue_item(conn, it.title_id)
        return (1, qi.duration_sec) if qi else (0, 0.0)
    if it.kind == "episode":
        rows = conn.execute(
            "SELECT m.path, m.duration_sec FROM episodes e "
            "JOIN media_files m ON m.id = e.file_id "
            f"WHERE e.id=? AND {PLAYABLE_SQL}", (it.episode_id,)).fetchall()
    else:
        season = it.season if it.kind == "season" else None
        rows = _playable_episodes(conn, it.title_id, season)
    alive = [r for r in rows if file_exists(r["path"])]
    return len(alive), sum(float(r["duration_sec"]) for r in alive)


# ----------------------------------------------------------------------
# Theme-random
# ----------------------------------------------------------------------
def theme_random(conn: sqlite3.Connection, *, tag: str | None = None,
                 bucket: str | None = None, kind: str = "film",
                 n: int = 5, playlist_id: int | None = None) -> int:
    """Add n random picks matching a tag and/or bucket. Returns how many
    were actually added (fewer when the pool is small)."""
    sql = "SELECT id, kind, tags, bucket FROM titles WHERE 1=1"
    params: list = []
    if kind == "film":
        sql += " AND kind='movie'"
    else:
        sql += " AND kind='series'"
    if bucket:
        sql += " AND bucket=?"
        params.append(bucket)
    rows = conn.execute(sql, params).fetchall()
    if tag:
        tag_l = tag.lower()
        rows = [r for r in rows
                if tag_l in [t.lower() for t in json.loads(r["tags"] or "[]")]]
    random.shuffle(rows)
    added = 0
    for r in rows[:n]:
        if kind == "film":
            if _film_queue_item(conn, r["id"]) is None:
                continue
            add(conn, "film", r["id"], playlist_id=playlist_id)
        else:
            eps = _playable_episodes(conn, r["id"])
            if not eps:
                continue
            pick = random.choice(eps)
            add(conn, "episode", r["id"], episode_id=pick["id"],
                playlist_id=playlist_id)
        added += 1
    return added


# ----------------------------------------------------------------------
# Named playlists
# ----------------------------------------------------------------------
def save_as(conn: sqlite3.Connection, name: str) -> None:
    """Save the live queue under a user name (replaces any same-named one)."""
    name = name.strip()
    if not name or name == QUEUE_NAME:
        raise ValueError("pick a real name for the playlist")
    qid = ensure_queue(conn)
    old = conn.execute("SELECT id FROM playlists WHERE name=?",
                       (name,)).fetchone()
    if old:
        conn.execute("DELETE FROM playlists WHERE id=?", (old["id"],))
    cur = conn.execute(
        "INSERT INTO playlists(name, created_at) VALUES(?, ?)",
        (name, time.time()))
    new_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO playlist_items(playlist_id, position, kind, title_id, "
        "season, episode_id) "
        "SELECT ?, position, kind, title_id, season, episode_id "
        "FROM playlist_items WHERE playlist_id=? ORDER BY position",
        (new_id, qid))


def load(conn: sqlite3.Connection, name: str) -> None:
    """Replace the live queue with a saved playlist."""
    row = conn.execute("SELECT id FROM playlists WHERE name=?",
                       (name,)).fetchone()
    if not row:
        raise KeyError(name)
    qid = ensure_queue(conn)
    clear(conn, qid)
    conn.execute(
        "INSERT INTO playlist_items(playlist_id, position, kind, title_id, "
        "season, episode_id) "
        "SELECT ?, position, kind, title_id, season, episode_id "
        "FROM playlist_items WHERE playlist_id=? ORDER BY position",
        (qid, row["id"]))


def list_saved(conn: sqlite3.Connection) -> list[tuple[str, int, float]]:
    """(name, item_count, created_at) for each user-saved playlist."""
    rows = conn.execute(
        "SELECT p.name, p.created_at, COUNT(i.id) AS n "
        "FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id = p.id "
        "WHERE p.name != ? GROUP BY p.id ORDER BY p.created_at DESC",
        (QUEUE_NAME,)).fetchall()
    return [(r["name"], r["n"], r["created_at"]) for r in rows]


def delete_saved(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM playlists WHERE name=? AND name != ?",
                 (name, QUEUE_NAME))
