"""Stale media purge must clear every FK into media_files."""
from __future__ import annotations

from pathlib import Path

from helpers import add_series
from tvguide.scan.indexer import Indexer
from tvguide.schedule.scheduler import Scheduler


def test_purge_stale_clears_program_fk(tmp_db, media_root, cfg):
    """Vanished files referenced by the schedule must delete cleanly."""
    add_series(tmp_db, media_root, "Portlandia", "sitcom", 3)
    Scheduler(tmp_db, cfg).build()

    row = tmp_db.execute(
        "SELECT id, path FROM media_files ORDER BY id LIMIT 1"
    ).fetchone()
    assert row is not None
    fid = int(row["id"])
    Path(row["path"]).unlink()

    # Precondition: at least one scheduled airing still points at this file.
    n_prog = tmp_db.execute(
        "SELECT COUNT(*) FROM programs WHERE file_id=?", (fid,)
    ).fetchone()[0]
    assert n_prog > 0

    purged = Indexer(tmp_db, cfg)._purge_stale()
    assert purged >= 1
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM media_files WHERE id=?", (fid,)
    ).fetchone()[0] == 0
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM programs WHERE file_id=?", (fid,)
    ).fetchone()[0] == 0
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM episodes WHERE file_id=?", (fid,)
    ).fetchone()[0] == 0
