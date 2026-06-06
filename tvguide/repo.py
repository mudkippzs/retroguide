"""Read-side queries the UI uses to render the guide and drive playback."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


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


@dataclass
class ChannelInfo:
    id: int
    slug: str
    name: str
    tagline: str
    accent: str
    logo: str | None = None


_PROG_COLS = (
    "p.id, p.channel_id, p.start_ts, p.end_ts, p.duration_sec, p.daypart, p.kind, "
    "p.display_title, p.subtitle, p.blurb, p.file_id, p.title_id, p.episode_id, "
    "m.path AS path"
)


def _to_program(row: sqlite3.Row) -> Program:
    return Program(
        id=row["id"], channel_id=row["channel_id"], start_ts=row["start_ts"],
        end_ts=row["end_ts"], duration_sec=row["duration_sec"],
        daypart=row["daypart"] or "", kind=row["kind"],
        display_title=row["display_title"] or "Untitled",
        subtitle=row["subtitle"] or "", blurb=row["blurb"] or "",
        file_id=row["file_id"], title_id=row["title_id"],
        episode_id=row["episode_id"], path=row["path"],
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


def get_program(conn: sqlite3.Connection, program_id: int) -> Program | None:
    row = conn.execute(
        f"SELECT {_PROG_COLS} FROM programs p "
        "LEFT JOIN media_files m ON m.id=p.file_id WHERE p.id=?",
        (program_id,),
    ).fetchone()
    return _to_program(row) if row else None


def has_schedule(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] > 0
