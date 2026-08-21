"""User scheduling rules: rotation start season and channel allow/block.

Rules are read by the Scheduler at pool-load time, so changes take effect
on the next schedule rebuild — no rebuild is triggered implicitly.
"""
from __future__ import annotations

import sqlite3


def set_start_season(conn: sqlite3.Connection, title_id: int,
                     season: int | None) -> None:
    """Pin a series' rotation to start at ``season`` (None clears)."""
    if season is None:
        conn.execute("DELETE FROM title_rules WHERE title_id=?", (title_id,))
    else:
        conn.execute(
            "INSERT INTO title_rules(title_id, start_season) VALUES(?, ?) "
            "ON CONFLICT(title_id) DO UPDATE SET start_season=excluded.start_season",
            (title_id, season))


def get_start_season(conn: sqlite3.Connection, title_id: int) -> int | None:
    row = conn.execute(
        "SELECT start_season FROM title_rules WHERE title_id=?",
        (title_id,)).fetchone()
    return row["start_season"] if row else None


def set_channel_rule(conn: sqlite3.Connection, title_id: int,
                     channel_slug: str, mode: str | None) -> None:
    """'allow' pins the title to the channel (and restricts the channel's
    series roster to allowlisted titles); 'block' keeps it off the channel;
    None clears the rule."""
    if mode is None:
        conn.execute(
            "DELETE FROM channel_rules WHERE channel_slug=? AND title_id=?",
            (channel_slug, title_id))
        return
    if mode not in ("allow", "block"):
        raise ValueError(f"unknown rule mode: {mode}")
    conn.execute(
        "INSERT INTO channel_rules(channel_slug, title_id, mode) "
        "VALUES(?, ?, ?) ON CONFLICT(channel_slug, title_id) "
        "DO UPDATE SET mode=excluded.mode",
        (channel_slug, title_id, mode))


def rules_for_title(conn: sqlite3.Connection,
                    title_id: int) -> dict[str, str]:
    """channel_slug -> mode for every rule on this title."""
    return {
        r["channel_slug"]: r["mode"]
        for r in conn.execute(
            "SELECT channel_slug, mode FROM channel_rules WHERE title_id=?",
            (title_id,))
    }
