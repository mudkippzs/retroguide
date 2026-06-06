"""SQLite persistence layer for the catalog, enrichment and schedule."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH, ensure_data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_files (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    root          TEXT NOT NULL,            -- 'tv' or 'movie'
    size          INTEGER,
    mtime         REAL,
    duration_sec  REAL,                     -- NULL until probed
    video_codec   TEXT,
    width         INTEGER,
    height        INTEGER,
    container     TEXT,
    probe_state   TEXT DEFAULT 'pending',   -- pending|done|error
    scanned_at    REAL
);

-- A "title" is either a series or a movie.
CREATE TABLE IF NOT EXISTS titles (
    id             INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL,           -- 'series' | 'movie'
    name           TEXT NOT NULL,
    year           INTEGER,
    sort_name      TEXT,
    tmdb_id        INTEGER,
    overview       TEXT,
    genres         TEXT,                    -- json list
    bucket         TEXT,                    -- scheduling bucket (see schedule.dayparts)
    rating         REAL,
    poster_path    TEXT,
    cast_json      TEXT,                    -- json list of lead names
    runtime_hint   INTEGER,                 -- minutes (movies)
    blurb          TEXT,                    -- retro-voice teaser (movies)
    movie_file_id  INTEGER,                 -- for kind='movie'
    enriched_at    REAL,
    franchise      TEXT,                    -- e.g. 'star wars', 'mcu'
    tags           TEXT,                    -- json list: 'christmas','horror'...
    UNIQUE(kind, name, year),
    FOREIGN KEY(movie_file_id) REFERENCES media_files(id)
);

CREATE TABLE IF NOT EXISTS episodes (
    id            INTEGER PRIMARY KEY,
    title_id      INTEGER NOT NULL,
    file_id       INTEGER,
    season        INTEGER NOT NULL,
    episode       INTEGER NOT NULL,
    abs_order     INTEGER,                  -- linear ordering across seasons
    name          TEXT,                     -- episode title
    overview      TEXT,
    blurb         TEXT,                     -- retro-voice teaser
    duration_sec  REAL,
    enriched_at   REAL,
    UNIQUE(title_id, season, episode),
    FOREIGN KEY(title_id) REFERENCES titles(id),
    FOREIGN KEY(file_id) REFERENCES media_files(id)
);

-- Per-series broadcast playhead: which episode airs next.
CREATE TABLE IF NOT EXISTS playheads (
    title_id      INTEGER PRIMARY KEY,
    next_order    INTEGER DEFAULT 0,        -- index into ordered episode list
    FOREIGN KEY(title_id) REFERENCES titles(id)
);

CREATE TABLE IF NOT EXISTS channels (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    tagline       TEXT,
    accent        TEXT,                     -- hex color
    position      INTEGER,
    logo          TEXT                      -- optional path to a channel logo/bug
);

-- One scheduled program (an airing of an episode or movie).
CREATE TABLE IF NOT EXISTS programs (
    id             INTEGER PRIMARY KEY,
    channel_id     INTEGER NOT NULL,
    start_ts       REAL NOT NULL,           -- unix epoch (local-derived)
    end_ts         REAL NOT NULL,
    duration_sec   REAL NOT NULL,
    daypart        TEXT,
    title_id       INTEGER,
    episode_id     INTEGER,
    file_id        INTEGER,
    kind           TEXT,                    -- 'episode' | 'movie'
    display_title  TEXT,
    subtitle       TEXT,                    -- e.g. "S02E05 - The Storm"
    blurb          TEXT,
    FOREIGN KEY(channel_id) REFERENCES channels(id),
    FOREIGN KEY(title_id) REFERENCES titles(id),
    FOREIGN KEY(episode_id) REFERENCES episodes(id),
    FOREIGN KEY(file_id) REFERENCES media_files(id)
);

CREATE INDEX IF NOT EXISTS idx_programs_channel_start ON programs(channel_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_episodes_title ON episodes(title_id, abs_order);
CREATE INDEX IF NOT EXISTS idx_files_root ON media_files(root);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before a column existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
    if "logo" not in cols:
        conn.execute("ALTER TABLE channels ADD COLUMN logo TEXT")
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(titles)")}
    if "franchise" not in tcols:
        conn.execute("ALTER TABLE titles ADD COLUMN franchise TEXT")
    if "tags" not in tcols:
        conn.execute("ALTER TABLE titles ADD COLUMN tags TEXT")


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
