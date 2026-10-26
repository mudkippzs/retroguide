"""ROAMER catalog browse + watch-position queries (mobile binge mode)."""
from __future__ import annotations

from tvguide import repo
from helpers import add_movie, add_series


def test_catalog_series_counts(tmp_db, media_root):
    tid = add_series(tmp_db, media_root, "Show A", "sitcom", 3)
    add_series(tmp_db, media_root, "Show B", "drama", 2)
    rows = repo.catalog_series(tmp_db)
    assert len(rows) == 2
    a = next(r for r in rows if r["name"] == "Show A")
    assert a["n_episodes"] == 3 and a["n_playable"] == 3 and a["n_watched"] == 0
    # Mark one episode watched (>=90%) and one in-progress.
    eps = tmp_db.execute(
        "SELECT id FROM episodes WHERE title_id=?", (tid,)).fetchall()
    repo.set_watch(tmp_db, repo.episode_key(eps[0]["id"]), 1300.0, 1320.0)
    repo.set_watch(tmp_db, repo.episode_key(eps[1]["id"]), 100.0, 1320.0)
    a = next(r for r in repo.catalog_series(tmp_db) if r["name"] == "Show A")
    assert a["n_watched"] == 1


def test_catalog_movies_watch_state(tmp_db, media_root):
    mid = add_movie(tmp_db, media_root, "Film A")
    add_movie(tmp_db, media_root, "Film B")
    # A film whose file vanished from disk is listed but not playable.
    (media_root / "Film B.2008.mkv").unlink()
    rows = repo.catalog_movies(tmp_db)
    a = next(r for r in rows if r["name"] == "Film A")
    b = next(r for r in rows if r["name"] == "Film B")
    assert a["playable"] and not a["watched"] and a["position"] is None
    assert not b["playable"]  # missing files can't be offered for VOD
    repo.set_watch(tmp_db, repo.film_key(mid), 300.0, 6000.0)
    a = next(r for r in repo.catalog_movies(tmp_db) if r["name"] == "Film A")
    assert a["position"] == 300.0 and not a["watched"]
    repo.set_watch(tmp_db, repo.film_key(mid), 5990.0, 6000.0)
    a = next(r for r in repo.catalog_movies(tmp_db) if r["name"] == "Film A")
    assert a["watched"] and a["position"] is None  # finished items don't resume


def test_series_detail_and_play_info(tmp_db, media_root):
    tid = add_series(tmp_db, media_root, "Show A", "sitcom", 2,
                     seasons=2, eps_per_season=2)
    d = repo.series_detail(tmp_db, tid)
    assert d is not None and [s["season"] for s in d["seasons"]] == [1, 2]
    ep = d["seasons"][0]["episodes"][0]
    assert ep["playable"]
    info = repo.episode_play_info(tmp_db, ep["id"])
    assert info["key"] == f"ep:{ep['id']}"
    assert info["title"] == "Show A" and "S01E01" in info["subtitle"]
    assert repo.episode_play_info(tmp_db, 999999) is None
    assert repo.series_detail(tmp_db, 999999) is None


def test_next_episode_info_order(tmp_db, media_root):
    add_series(tmp_db, media_root, "Show A", "sitcom", 2,
               seasons=2, eps_per_season=2)
    d = repo.series_detail(tmp_db, next(
        r["id"] for r in repo.catalog_series(tmp_db)
        if r["name"] == "Show A"))
    eps = [e for s in d["seasons"] for e in s["episodes"]]
    nxt = repo.next_episode_info(tmp_db, eps[0]["id"])
    assert nxt["episode_id"] == eps[1]["id"]
    # Last episode has no successor.
    assert repo.next_episode_info(tmp_db, eps[-1]["id"]) is None


def test_film_play_info(tmp_db, media_root):
    mid = add_movie(tmp_db, media_root, "Film A", year=2001)
    info = repo.film_play_info(tmp_db, mid)
    assert info["key"] == f"film:{mid}" and info["subtitle"] == "(2001)"
    assert repo.film_play_info(tmp_db, 999999) is None
