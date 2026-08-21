"""Schedule invariants: exclusive homes, cooldowns, playable-only pools."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from helpers import add_movie, add_series
from tvguide.schedule.scheduler import (
    MAX_TITLE_AIRS_PER_DAY,
    MIN_SERIES_EPS,
    Scheduler,
)
from tvguide.scan.indexer import Indexer


def _airings(conn):
    return conn.execute(
        "SELECT p.title_id, p.episode_id, p.file_id, p.start_ts, p.end_ts, "
        "p.kind, c.slug, t.name "
        "FROM programs p JOIN channels c ON c.id=p.channel_id "
        "JOIN titles t ON t.id=p.title_id "
        "WHERE c.slug NOT LIKE '%1' "  # ignore +1 mirrors
        "ORDER BY p.start_ts"
    ).fetchall()


def test_excludes_probe_error_films(tmp_db, media_root, cfg):
    add_movie(tmp_db, media_root, "Good Film", probe_state="done",
              overview="A fine film.", cast=["A", "B"])
    add_movie(tmp_db, media_root, "The X-Files I Want To Believe",
              probe_state="error")
    n = Scheduler(tmp_db, cfg).build()
    assert n > 0
    names = {r["name"] for r in _airings(tmp_db) if r["kind"] == "movie"}
    assert "Good Film" in names
    assert "The X-Files I Want To Believe" not in names


def test_excludes_one_episode_special_series(tmp_db, media_root, cfg):
    # Regular sitcom with enough episodes.
    add_series(tmp_db, media_root, "The Office", "sitcom", 6)
    # Malformed Superfan retrospective: single S99E00-style special.
    tid = add_series(tmp_db, media_root, "The Office Superfan", "sitcom", 1,
                     season=99, start_ep=0)
    # Force the single ep to look like S99E00.
    tmp_db.execute(
        "UPDATE episodes SET season=99, episode=0 WHERE title_id=?", (tid,))
    tmp_db.commit()
    Scheduler(tmp_db, cfg).build()
    names = {r["name"] for r in _airings(tmp_db) if r["kind"] == "episode"}
    assert "The Office" in names
    assert "The Office Superfan" not in names


def test_series_has_exclusive_home(tmp_db, media_root, cfg):
    # Enough sitcoms that chuckles/banter/prime all get some.
    for i in range(12):
        add_series(tmp_db, media_root, f"Sitcom {i}", "sitcom", 8)
    Scheduler(tmp_db, cfg).build()
    by_title: dict[int, set[str]] = defaultdict(set)
    for r in _airings(tmp_db):
        if r["kind"] != "episode":
            continue
        by_title[r["title_id"]].add(r["slug"])
    # No primary channel should share a title with another primary.
    for tid, slugs in by_title.items():
        assert len(slugs) == 1, f"title {tid} on {slugs}"


def test_no_simulcast_overlap(tmp_db, media_root, cfg):
    for i in range(8):
        add_series(tmp_db, media_root, f"Toon {i}", "adult_animation", 10)
    Scheduler(tmp_db, cfg).build()
    rows = [r for r in _airings(tmp_db) if r["kind"] == "episode"]
    # For every pair of airings of the same title, windows must not overlap.
    by_title: dict[int, list] = defaultdict(list)
    for r in rows:
        by_title[r["title_id"]].append(r)
    for tid, airings in by_title.items():
        airings.sort(key=lambda r: r["start_ts"])
        for a, b in zip(airings, airings[1:]):
            assert a["end_ts"] <= b["start_ts"] + 0.01, (
                f"overlap for {tid}: {a['slug']}@{a['start_ts']} vs "
                f"{b['slug']}@{b['start_ts']}"
            )


def test_anime_channel_is_pure(tmp_db, media_root, cfg):
    add_series(tmp_db, media_root, "Cowboy Bebop", "anime", 8)
    add_series(tmp_db, media_root, "Naruto", "anime", 8)
    add_series(tmp_db, media_root, "Bleach", "anime", 8)
    add_series(tmp_db, media_root, "The Simpsons", "adult_animation", 8)
    add_series(tmp_db, media_root, "Stargate", "action_adventure", 8)
    add_series(tmp_db, media_root, "Avatar", "kids_cartoon", 8)
    Scheduler(tmp_db, cfg).build()
    nihon = [r for r in _airings(tmp_db) if r["slug"] == "nihon"]
    assert nihon, "Tokyo Toke should have programming when anime exists"
    names = {r["name"] for r in nihon}
    assert "The Simpsons" not in names
    assert "Stargate" not in names
    assert "Avatar" not in names
    assert names <= {"Cowboy Bebop", "Naruto", "Bleach"}


def test_prefers_episode_cooldown_but_fills_gaps(tmp_db, media_root, cfg):
    # Tiny library: channel stays continuous even when cool-downs can't be met.
    add_series(tmp_db, media_root, "Spaced", "sitcom", MIN_SERIES_EPS)
    Scheduler(tmp_db, cfg).build()
    airings = [r for r in _airings(tmp_db) if r["name"] == "Spaced"]
    assert airings, "thin sitcom channel must still get programming"
    # Prefer diversity when possible: with 3 episodes over 2 days, we should
    # see more than one distinct episode id used.
    ep_ids = {r["episode_id"] for r in airings}
    assert len(ep_ids) >= 2


def test_film_blurb_and_cast_subtitle(tmp_db, media_root, cfg):
    add_movie(tmp_db, media_root, "Hot Fuzz", overview="A London cop moves to a village.",
              cast=["Simon Pegg", "Nick Frost"], tags=[])
    Scheduler(tmp_db, cfg).build()
    row = tmp_db.execute(
        "SELECT subtitle, blurb FROM programs WHERE kind='movie' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert "Starring" in (row["subtitle"] or "")
    assert "Simon Pegg" in (row["subtitle"] or "")
    assert row["blurb"]  # overview fallback


def test_series_starts_at_s01e01_and_advances(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Futurama", "adult_animation", 0,
                     seasons=2, eps_per_season=6)
    Scheduler(tmp_db, cfg).build()
    airings = [r for r in _airings(tmp_db) if r["title_id"] == tid]
    assert airings
    first = tmp_db.execute(
        "SELECT season, episode, abs_order FROM episodes WHERE id=?",
        (airings[0]["episode_id"],),
    ).fetchone()
    assert (first["season"], first["episode"]) == (1, 1)
    assert first["abs_order"] == 0

    # Chronological airings for this title should walk abs_order forward
    # (wrapping only after the full playable run).
    orders = []
    for r in airings:
        ep = tmp_db.execute(
            "SELECT abs_order FROM episodes WHERE id=?", (r["episode_id"],)
        ).fetchone()
        orders.append(ep["abs_order"])
    # First stretch before any wrap must be strictly increasing by 1.
    wrap_at = next((i for i in range(1, len(orders)) if orders[i] < orders[i - 1]),
                   len(orders))
    assert orders[:wrap_at] == list(range(wrap_at))


def test_playhead_persists_across_rebuilds(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Futurama", "adult_animation", 0,
                     seasons=3, eps_per_season=8)
    Scheduler(tmp_db, cfg).build()
    ph1 = tmp_db.execute(
        "SELECT next_order FROM playheads WHERE title_id=?", (tid,)
    ).fetchone()["next_order"]
    assert ph1 > 0

    last_ep = tmp_db.execute(
        "SELECT e.abs_order FROM programs p "
        "JOIN episodes e ON e.id=p.episode_id "
        "WHERE p.title_id=? ORDER BY p.start_ts DESC LIMIT 1",
        (tid,),
    ).fetchone()
    # Cursor sits just after the last aired abs_order (or wrapped to 0).
    n_eps = 3 * 8
    expected = 0 if last_ep["abs_order"] >= n_eps - 1 else last_ep["abs_order"] + 1
    assert ph1 == expected

    Scheduler(tmp_db, cfg).build()
    first_after = tmp_db.execute(
        "SELECT e.abs_order FROM programs p "
        "JOIN episodes e ON e.id=p.episode_id "
        "WHERE p.title_id=? ORDER BY p.start_ts ASC LIMIT 1",
        (tid,),
    ).fetchone()
    assert first_after["abs_order"] == ph1


def test_day_cap_rotates_titles(tmp_db, media_root, cfg):
    for i in range(6):
        add_series(tmp_db, media_root, f"Toon {i}", "adult_animation", 12)
    Scheduler(tmp_db, cfg).build()
    rows = [r for r in _airings(tmp_db) if r["kind"] == "episode"]
    assert rows
    # Group by home channel + broadcast day (day_start_hour=6 default).
    by_ch_day: dict[tuple[str, str], dict[int, int]] = defaultdict(
        lambda: defaultdict(int))
    for r in rows:
        day = (datetime.fromtimestamp(r["start_ts"]) - timedelta(hours=6)
               ).date().isoformat()
        by_ch_day[(r["slug"], day)][r["title_id"]] += 1
    # With several series on a channel, airings should rotate: the busiest
    # title on a day should not hog the whole grid.
    rotated = 0
    for (_slug, _day), counts in by_ch_day.items():
        if len(counts) < 3:
            continue
        rotated += 1
        busiest = max(counts.values())
        total = sum(counts.values())
        assert busiest <= max(MAX_TITLE_AIRS_PER_DAY * 4, total // len(counts) + 3)
    assert rotated >= 1


def test_short_science_docs_do_not_loop_all_day(tmp_db, media_root, cfg):
    """Higher Learning used to air the same 5-ep strands ~50×/week."""
    # Three short science docs + filler films + a longer untagged documentary.
    for name in ("Planet Earth II", "Solar System", "Wonders"):
        add_series(tmp_db, media_root, name, "documentary", 5,
                   tags=["science"], duration=3600.0)
    add_series(tmp_db, media_root, "Vietnam", "documentary", 8,
               duration=3600.0)
    for i in range(6):
        add_movie(tmp_db, media_root, f"Doc Film {i}", duration=5400.0,
                  tags=["science"] if i < 2 else [], overview="A film.")
    cfg.schedule.days = 2
    Scheduler(tmp_db, cfg).build()
    rows = [r for r in _airings(tmp_db)
            if r["slug"] == "science" and r["kind"] == "episode"]
    assert rows, "science channel should still schedule documentaries"
    from collections import Counter
    from datetime import datetime, timedelta
    by_day_title: dict[tuple[str, str], int] = Counter()
    for r in rows:
        day = (datetime.fromtimestamp(r["start_ts"]) - timedelta(hours=6)
               ).date().isoformat()
        by_day_title[(day, r["name"])] += 1
    # One catalog pass/day ⇒ a 5-ep show airs at most 5 times that day.
    for (day, name), n in by_day_title.items():
        assert n <= 5, f"{name} aired {n}× on {day}"
    # Distinct episodes for Planet Earth II should cover the catalog once,
    # not hammer a single episode.
    pe = [r for r in rows if r["name"] == "Planet Earth II"]
    if pe:
        assert len({r["episode_id"] for r in pe}) >= 3


def test_reindex_preserves_playhead(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Futurama", "adult_animation", 10,
                     playhead=4)
    Indexer(tmp_db, cfg).reindex_from_db()
    row = tmp_db.execute(
        "SELECT p.next_order FROM playheads p "
        "JOIN titles t ON t.id=p.title_id WHERE t.name='Futurama'"
    ).fetchone()
    assert row is not None
    assert row["next_order"] == 4
    # Old title id may have changed; playhead must not reset to 0.
    assert tid  # keep fixture usage obvious for linters
