"""Binge-mode: playlist storage, expansion, shuffle and theme-random."""
from __future__ import annotations

import pytest

from tvguide.binge import playlist
from helpers import add_movie, add_series


def _series_with_specials(conn, root):
    tid = add_series(conn, root, "Show A", "sitcom", 0, seasons=2,
                     eps_per_season=3)
    # A season-0 special (e.g. a movie special) must sort after the run.
    from helpers import _add_file
    fid = _add_file(conn, root, "Show.A.Special.mkv", duration=3600.0)
    conn.execute(
        "INSERT INTO episodes(title_id,file_id,season,episode,abs_order,name,"
        "duration_sec) VALUES(?,?,0,1,6,'The Special',3600.0)", (tid, fid))
    conn.commit()
    return tid


def test_add_and_expand_series_in_order(tmp_db, media_root):
    tid = _series_with_specials(tmp_db, media_root)
    playlist.add(tmp_db, "series", tid)
    q = playlist.expand(tmp_db)
    assert len(q) == 7
    assert q[0].subtitle.startswith("S01E01")
    assert q[5].subtitle.startswith("S02E03")
    # Season-0 specials play after the regular run.
    assert q[6].subtitle.startswith("S00E01")
    assert all(qi.title_id == tid for qi in q)


def test_expand_season_filters(tmp_db, media_root):
    tid = _series_with_specials(tmp_db, media_root)
    playlist.add(tmp_db, "season", tid, season=2)
    q = playlist.expand(tmp_db)
    assert [qi.subtitle[:6] for qi in q] == ["S02E01", "S02E02", "S02E03"]


def test_expand_film_and_episode(tmp_db, media_root):
    mid = add_movie(tmp_db, media_root, "Film One", tags=["horror"])
    tid = _series_with_specials(tmp_db, media_root)
    ep_id = tmp_db.execute(
        "SELECT id FROM episodes WHERE title_id=? AND season=1 AND episode=2",
        (tid,)).fetchone()["id"]
    playlist.add(tmp_db, "film", mid)
    playlist.add(tmp_db, "episode", tid, episode_id=ep_id)
    q = playlist.expand(tmp_db)
    assert [qi.display_title for qi in q] == ["Film One", "Show A"]
    assert q[0].subtitle == "(2008)"
    assert q[1].subtitle.startswith("S01E02")


def test_expand_skips_dead_files(tmp_db, media_root):
    tid = _series_with_specials(tmp_db, media_root)
    # Delete the file behind S01E02 — expansion must skip, not stall.
    row = tmp_db.execute(
        "SELECT m.path FROM episodes e JOIN media_files m ON m.id=e.file_id "
        "WHERE e.title_id=? AND e.season=1 AND e.episode=2", (tid,)).fetchone()
    import os
    os.unlink(row["path"])
    playlist.add(tmp_db, "series", tid)
    q = playlist.expand(tmp_db)
    assert len(q) == 6
    assert "S01E02" not in [qi.subtitle for qi in q]


def test_shuffle_preserves_membership(tmp_db, media_root):
    t1 = _series_with_specials(tmp_db, media_root)
    t2 = add_series(tmp_db, media_root, "Show B", "drama", 0, seasons=1,
                    eps_per_season=2)
    m1 = add_movie(tmp_db, media_root, "Film A")
    playlist.add(tmp_db, "series", t1)
    playlist.add(tmp_db, "season", t2, season=1)
    playlist.add(tmp_db, "film", m1)
    before = sorted(i.id for i in playlist.items(tmp_db))
    playlist.shuffle(tmp_db)
    after = sorted(i.id for i in playlist.items(tmp_db))
    assert before == after


def test_move_reorders(tmp_db, media_root):
    m1 = add_movie(tmp_db, media_root, "Film A")
    m2 = add_movie(tmp_db, media_root, "Film B")
    playlist.add(tmp_db, "film", m1)
    playlist.add(tmp_db, "film", m2)
    ids = [i.id for i in playlist.items(tmp_db)]
    playlist.move(tmp_db, ids[1], -1)
    assert [i.id for i in playlist.items(tmp_db)] == [ids[1], ids[0]]


def test_theme_random_films_by_tag(tmp_db, media_root):
    for i in range(4):
        add_movie(tmp_db, media_root, f"Horror {i}", tags=["horror"])
    for i in range(3):
        add_movie(tmp_db, media_root, f"Comedy {i}", tags=["comedy"])
    n = playlist.theme_random(tmp_db, tag="horror", kind="film", n=3)
    assert n == 3
    q = playlist.expand(tmp_db)
    assert len(q) == 3
    assert all(qi.display_title.startswith("Horror") for qi in q)


def test_theme_random_episodes_by_bucket(tmp_db, media_root):
    add_series(tmp_db, media_root, "Sitcom A", "sitcom", 0, seasons=1,
               eps_per_season=4)
    add_series(tmp_db, media_root, "Drama A", "drama", 0, seasons=1,
               eps_per_season=4)
    n = playlist.theme_random(tmp_db, bucket="sitcom", kind="episode", n=2)
    assert n == 1  # only one sitcom exists; one random episode from it
    q = playlist.expand(tmp_db)
    assert len(q) == 1
    assert q[0].display_title == "Sitcom A"


def test_playheads_untouched(tmp_db, media_root):
    tid = _series_with_specials(tmp_db, media_root)
    before = tmp_db.execute(
        "SELECT next_order FROM playheads WHERE title_id=?", (tid,)).fetchone()
    playlist.add(tmp_db, "series", tid)
    playlist.expand(tmp_db)
    after = tmp_db.execute(
        "SELECT next_order FROM playheads WHERE title_id=?", (tid,)).fetchone()
    assert before["next_order"] == after["next_order"]


def test_save_load_roundtrip(tmp_db, media_root):
    m1 = add_movie(tmp_db, media_root, "Film A")
    tid = _series_with_specials(tmp_db, media_root)
    playlist.add(tmp_db, "film", m1)
    playlist.add(tmp_db, "season", tid, season=1)
    playlist.save_as(tmp_db, "Friday Night")
    playlist.clear(tmp_db)
    assert playlist.items(tmp_db) == []
    playlist.load(tmp_db, "Friday Night")
    its = playlist.items(tmp_db)
    assert len(its) == 2
    assert its[1].kind == "season" and its[1].season == 1
    saved = playlist.list_saved(tmp_db)
    assert saved[0][0] == "Friday Night" and saved[0][1] == 2
    playlist.delete_saved(tmp_db, "Friday Night")
    assert playlist.list_saved(tmp_db) == []


def test_save_rejects_queue_name(tmp_db):
    with pytest.raises(ValueError):
        playlist.save_as(tmp_db, playlist.QUEUE_NAME)


def test_next_index_sequential():
    played = {0, 1}
    assert playlist.next_index(played, 5, randomize=False, repeat=False) == 2


def test_next_index_finished_without_repeat():
    assert playlist.next_index({0, 1}, 2, randomize=False,
                               repeat=False) is None


def test_next_index_repeat_restarts_cycle():
    played = {0, 1}
    assert playlist.next_index(played, 2, randomize=False, repeat=True) == 0
    assert played == set()  # cycle reset for the next round


def test_next_index_random_picks_unplayed():
    import random
    played = {0, 1, 3}
    rng = random.Random(42)
    for _ in range(10):
        assert playlist.next_index(set(played), 5, randomize=True,
                                   repeat=False, rng=rng) in (2, 4)


def test_next_index_random_with_repeat_exhausts_then_resets():
    played = {0, 1, 2}
    nxt = playlist.next_index(played, 3, randomize=True, repeat=True)
    assert nxt in (0, 1, 2)
    assert played == set()


def test_next_index_empty_queue():
    assert playlist.next_index(set(), 0, randomize=True, repeat=True) is None
