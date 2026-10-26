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
    enrich_state   TEXT DEFAULT 'pending',  -- pending|done|error
    enrich_error   TEXT,                    -- last enrichment failure reason
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
    next_order    INTEGER DEFAULT 0,        -- next abs_order cursor (wraps)
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

-- Binge-mode: named playlists of catalog references. Coarse items (series,
-- season) stay unexpanded so a playlist survives rescans and catalog growth.
CREATE TABLE IF NOT EXISTS playlists (
    id            INTEGER PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    created_at    REAL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id            INTEGER PRIMARY KEY,
    playlist_id   INTEGER NOT NULL,
    position      INTEGER NOT NULL,
    kind          TEXT NOT NULL,            -- 'film' | 'series' | 'season' | 'episode'
    title_id      INTEGER NOT NULL,
    season        INTEGER,                  -- for kind='season'
    episode_id    INTEGER,                  -- for kind='episode'
    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY(title_id) REFERENCES titles(id)
);

CREATE INDEX IF NOT EXISTS idx_playlist_items ON playlist_items(playlist_id, position);

-- Scheduling control: per-title rotation start and per-channel allow/block.
CREATE TABLE IF NOT EXISTS title_rules (
    title_id      INTEGER PRIMARY KEY,
    start_season  INTEGER,                  -- rotation starts at this season
    FOREIGN KEY(title_id) REFERENCES titles(id)
);

CREATE TABLE IF NOT EXISTS channel_rules (
    channel_slug  TEXT NOT NULL,
    title_id      INTEGER NOT NULL,
    mode          TEXT NOT NULL,            -- 'allow' | 'block'
    UNIQUE(channel_slug, title_id),
    FOREIGN KEY(title_id) REFERENCES titles(id)
);

-- ROAMER: per-item playback position for cross-device resume ('ep:<id>' /
-- 'film:<title_id>'). Written by the stream server as clients report progress.
CREATE TABLE IF NOT EXISTS watch_positions (
    key           TEXT PRIMARY KEY,         -- 'ep:<episode_id>' | 'film:<title_id>'
    position      REAL NOT NULL,
    duration      REAL NOT NULL,
    updated_at    REAL NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_data_dir()
    # Autocommit by default so a forgotten commit cannot hold a write lock
    # against the GUI / workers. Callers that need a transaction use BEGIN.
    conn = sqlite3.connect(path or DB_PATH, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=60000;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)


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
    added_enrich = False
    if "enrich_state" not in tcols:
        conn.execute(
            "ALTER TABLE titles ADD COLUMN enrich_state TEXT DEFAULT 'pending'")
        added_enrich = True
    if "enrich_error" not in tcols:
        conn.execute("ALTER TABLE titles ADD COLUMN enrich_error TEXT")
        added_enrich = True
    # Heavy enrich_state backfill only when the columns were just added.
    # Never rewrite titles on every init_db — that fought the GUI connection
    # and made Rebuild fail with "database is locked".
    row = conn.execute(
        "SELECT value FROM meta WHERE key='migrate_enrich_state_v1'"
    ).fetchone()
    if row is not None:
        return
    if added_enrich:
        conn.execute(
            "UPDATE titles SET enrich_state='done' "
            "WHERE overview IS NOT NULL AND TRIM(overview)!=''"
        )
        conn.execute(
            "UPDATE titles SET enrich_state='pending' "
            "WHERE kind='movie' AND (overview IS NULL OR TRIM(overview)='')"
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('migrate_enrich_state_v1', '1') "
        "ON CONFLICT(key) DO UPDATE SET value='1'"
    )


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
