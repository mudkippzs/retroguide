"""Regression tests: season parsing, stable specials, ghosts, overrides."""
from __future__ import annotations

from pathlib import Path

from helpers import add_movie, add_series
from tvguide.scan.indexer import Indexer
from tvguide.util.naming import folder_season_hint, parse_media


def _mk(path: Path, kb: int = 2048) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * 1024 * kb)
    return path


def _scan_cfg(cfg, media_root):
    cfg.library.tv_roots = [str(media_root)]
    cfg.library.movie_roots = [str(media_root)]
    cfg.library.min_file_mb = 0
    return cfg


def test_season_episode_words_in_filename_beat_boxset_folder():
    p = Path("/lib/Star Trek Season 1, 2, 3, 4, 5, 6 & 7 Boxset/Season 2/"
             "Star Trek Season 2 Episode 01 - The Child.avi")
    r = parse_media(p, "episode")
    assert (r.season, r.episode) == (2, 1)


def test_folder_season_hint_for_episode_only_filename():
    p = Path("/lib/Show Boxset/Season 5/Show Episode 03 - Title.avi")
    r = parse_media(p, "episode")
    assert (r.season, r.episode) == (5, 3)
    # Ambiguous multi-season folders are skipped.
    assert folder_season_hint(Path("/lib/Show Season 1, 2 & 3/x.avi")) is None
    assert folder_season_hint(p) == 5


def test_unmarked_tv_file_is_stable_special(tmp_db, media_root, cfg):
    """Movie-length TV-root files become season-0 specials, once, stably."""
    _scan_cfg(cfg, media_root)
    _mk(media_root / "Futurama S01-S07 + 4 Movies" /
        "Futurama S05M01 Bender's Big Score (2007).mp4")
    Indexer(tmp_db, cfg).scan()
    # A second scan (fresh Indexer, as in a new process) must not duplicate.
    Indexer(tmp_db, cfg).scan()
    rows = tmp_db.execute(
        "SELECT season, episode FROM episodes e JOIN titles t ON t.id=e.title_id "
        "WHERE t.name LIKE 'Futurama%'").fetchall()
    assert len(rows) == 1
    assert rows[0]["season"] == 0  # special: excluded from regular rotation


def test_min_file_mb_floor(tmp_db, media_root, cfg):
    _scan_cfg(cfg, media_root)
    _mk(media_root / "Small Show" / "Small Show S01E01 Pilot.mp4", kb=2048)
    cfg.library.min_file_mb = 50
    Indexer(tmp_db, cfg).scan()
    assert tmp_db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    cfg.library.min_file_mb = 1
    Indexer(tmp_db, cfg).scan()
    assert tmp_db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


def test_purge_removes_ghost_title(tmp_db, media_root, cfg):
    """A title whose last file vanished is deleted, not left as a ghost."""
    add_series(tmp_db, media_root, "Boston Legal", "drama", 3)
    tid = tmp_db.execute(
        "SELECT id FROM titles WHERE name='Boston Legal'").fetchone()["id"]
    for r in tmp_db.execute("SELECT path FROM media_files").fetchall():
        Path(r["path"]).unlink()
    Indexer(tmp_db, cfg)._purge_stale()
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM titles WHERE id=?", (tid,)).fetchone()[0] == 0
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM episodes WHERE title_id=?", (tid,)).fetchone()[0] == 0


def test_purge_keeps_title_with_surviving_files(tmp_db, media_root, cfg):
    add_series(tmp_db, media_root, "Portlandia", "sitcom", 3)
    tid = tmp_db.execute(
        "SELECT id FROM titles WHERE name='Portlandia'").fetchone()["id"]
    first = tmp_db.execute(
        "SELECT path FROM media_files LIMIT 1").fetchone()["path"]
    Path(first).unlink()
    Indexer(tmp_db, cfg)._purge_stale()
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM titles WHERE id=?", (tid,)).fetchone()[0] == 1


def test_reindex_preserves_enrichment(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Spaced", "sitcom", 5)
    tmp_db.execute(
        "UPDATE titles SET tags='[\"cult\"]', overview='o', rating=8.4, "
        "blurb='b', enrich_state='done' WHERE id=?", (tid,))
    tmp_db.commit()
    Indexer(tmp_db, cfg).reindex_from_db()
    row = tmp_db.execute(
        "SELECT tags, overview, rating, blurb, bucket, enrich_state "
        "FROM titles WHERE name='Spaced'").fetchone()
    assert row is not None
    assert row["tags"] == '["cult"]' and row["overview"] == "o"
    assert row["rating"] == 8.4 and row["blurb"] == "b"
    assert row["bucket"] == "sitcom" and row["enrich_state"] == "done"


def test_reindex_preserves_episode_overviews(tmp_db, media_root, cfg):
    tid = add_series(tmp_db, media_root, "Spaced", "sitcom", 3)
    tmp_db.execute(
        "UPDATE episodes SET overview='Tim and Daisy pretend to be a couple.', "
        "blurb='bb' WHERE title_id=? AND season=1 AND episode=1", (tid,))
    tmp_db.commit()
    Indexer(tmp_db, cfg).reindex_from_db()
    row = tmp_db.execute(
        "SELECT e.overview, e.blurb, e.name FROM episodes e "
        "JOIN titles t ON t.id=e.title_id "
        "WHERE t.name='Spaced' AND e.season=1 AND e.episode=1").fetchone()
    assert row is not None
    assert row["overview"] == "Tim and Daisy pretend to be a couple."
    assert row["blurb"] == "bb"
    assert row["name"]  # episode name survived too


def test_bucket_overrides_applied_on_scan(tmp_db, media_root, cfg):
    _scan_cfg(cfg, media_root)
    cfg.bucket_overrides = {"Father Ted BoxSet": "sitcom"}
    _mk(media_root / "Father Ted BoxSet" / "Father Ted BoxSet S01E01 x.mp4")
    Indexer(tmp_db, cfg).scan()
    row = tmp_db.execute(
        "SELECT bucket FROM titles WHERE name='Father Ted BoxSet'").fetchone()
    assert row["bucket"] == "sitcom"


def test_kids_channel_never_pads_with_horror(tmp_db, media_root, cfg):
    """ToonWorld pad-fills must be family-suitable (no horror/war)."""
    # One tiny kids series so the roster exhausts and pad-films fire.
    add_series(tmp_db, media_root, "Tiny Toon", "kids_cartoon", 3,
               duration=660.0)
    add_movie(tmp_db, media_root, "Family Film", tags=["family"])
    add_movie(tmp_db, media_root, "Gorefest", tags=["horror"])
    add_movie(tmp_db, media_root, "War Film", tags=["war"])
    from tvguide.schedule.scheduler import Scheduler
    cfg.schedule.days = 2
    Scheduler(tmp_db, cfg).build()
    rows = tmp_db.execute(
        "SELECT DISTINCT p.display_title FROM programs p "
        "JOIN channels c ON c.id=p.channel_id "
        "WHERE c.slug='toonworld' AND p.kind='movie'").fetchall()
    aired = {r["display_title"] for r in rows}
    assert aired, "expected pad films on toonworld"
    assert "Gorefest" not in aired
    assert "War Film" not in aired
