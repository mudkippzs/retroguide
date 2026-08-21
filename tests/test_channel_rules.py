"""Scheduling control: rotation start season + channel allow/block rules."""
from __future__ import annotations

from helpers import add_series
from tvguide.schedule.rules import (
    get_start_season,
    rules_for_title,
    set_channel_rule,
    set_start_season,
)
from tvguide.schedule.scheduler import Scheduler


def _series_airings(conn, title_id: int):
    return conn.execute(
        "SELECT e.season, e.episode, c.slug "
        "FROM programs p "
        "JOIN episodes e ON e.id = p.episode_id "
        "JOIN channels c ON c.id = p.channel_id "
        "WHERE p.title_id=? AND c.slug NOT LIKE '%1' "
        "ORDER BY p.start_ts", (title_id,)).fetchall()


def test_start_season_override_skips_earlier_seasons(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Late Show", "sitcom", 0,
                     seasons=3, eps_per_season=3)
    set_start_season(tmp_db, tid, 3)
    assert get_start_season(tmp_db, tid) == 3
    Scheduler(tmp_db, cfg).build()
    airings = _series_airings(tmp_db, tid)
    assert airings, "expected the show to air"
    assert {r["season"] for r in airings} == {3}


def test_start_season_clear_restores_full_rotation(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Full Show", "sitcom", 0,
                     seasons=2, eps_per_season=3)
    set_start_season(tmp_db, tid, 2)
    set_start_season(tmp_db, tid, None)
    assert get_start_season(tmp_db, tid) is None
    Scheduler(tmp_db, cfg).build()
    seasons = {r["season"] for r in _series_airings(tmp_db, tid)}
    assert 1 in seasons


def test_block_removes_show_from_channel(tmp_db, media_root, cfg):
    for i in range(6):
        add_series(tmp_db, media_root, f"Sitcom {i}", "sitcom", 8)
    target = add_series(tmp_db, media_root, "Banned Show", "sitcom", 8)
    Scheduler(tmp_db, cfg).build()
    homes = {r["slug"] for r in _series_airings(tmp_db, target)}
    assert homes, "expected the show to air before the block"
    for slug in homes:
        set_channel_rule(tmp_db, target, slug, "block")
    Scheduler(tmp_db, cfg).build()
    after = {r["slug"] for r in _series_airings(tmp_db, target)}
    assert not (after & homes), "blocked show still airs on a blocked channel"


def test_allowlist_pins_and_restricts_channel(tmp_db, media_root, cfg):
    for i in range(6):
        add_series(tmp_db, media_root, f"Sitcom {i}", "sitcom", 8)
    pinned = add_series(tmp_db, media_root, "Only Show", "sitcom", 8)
    set_channel_rule(tmp_db, pinned, "chuckle", "allow")
    assert rules_for_title(tmp_db, pinned) == {"chuckle": "allow"}
    Scheduler(tmp_db, cfg).build()
    # The pinned show airs only on chuckle...
    assert {r["slug"] for r in _series_airings(tmp_db, pinned)} == {"chuckle"}
    # ...and chuckle' series slots air nothing else.
    others = tmp_db.execute(
        "SELECT DISTINCT p.title_id FROM programs p "
        "JOIN channels c ON c.id=p.channel_id "
        "WHERE c.slug='chuckle' AND p.kind='episode' AND p.title_id != ?",
        (pinned,)).fetchall()
    assert others == []


def test_clear_channel_rule(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Ruled Show", "sitcom", 8)
    set_channel_rule(tmp_db, tid, "chuckle", "block")
    set_channel_rule(tmp_db, tid, "chuckle", None)
    assert rules_for_title(tmp_db, tid) == {}
