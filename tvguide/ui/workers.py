"""QThread worker that runs catalog/schedule pipeline steps off the GUI thread."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from ..config import Config
from ..db import connect

# A task receives an emit(msg, cur, total) callback and a fresh db connection.
Task = Callable[[Callable[[str, int, int], None], object, Config], str]


class Worker(QThread):
    progress = Signal(str, int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg: Config, task: Task, label: str = ""):
        super().__init__()
        self.cfg = cfg
        self.task = task
        self.label = label

    def run(self) -> None:  # executes in the worker thread
        conn = None
        try:
            # Own connection for this thread. Schema is already applied by the
            # GUI on startup — skip init_db so we never contend on migrations.
            conn = connect()
            result = self.task(self._emit, conn, self.cfg)
            try:
                conn.commit()
            except Exception:
                pass
            self.done.emit(result or self.label)
        except Exception as exc:  # surface failure to the UI
            import logging
            import traceback
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            tb = traceback.format_exc()
            logging.getLogger("tvguide.ui").exception("%s failed", self.label)
            self.failed.emit(f"{self.label} failed: {exc}\n{tb}")
        finally:
            # Always close — an abandoned connection with an open write
            # transaction locks the whole DB until the process exits.
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _emit(self, msg: str, cur: int, total: int) -> None:
        self.progress.emit(msg, cur, total)


# -- concrete pipeline tasks ------------------------------------------------

def task_scan(emit, conn, cfg) -> str:
    from ..scan.indexer import Indexer
    stats = Indexer(conn, cfg).scan(emit)
    return f"Indexed {stats['files']} files ({stats['episodes']} eps, {stats['movies']} films)"


def task_probe(emit, conn, cfg) -> str:
    from ..scan.indexer import Indexer
    n = Indexer(conn, cfg).probe_pending(emit)
    return f"Probed {n} files"


def task_enrich(emit, conn, cfg) -> str:
    from ..enrich.enricher import Enricher
    n = Enricher(conn, cfg).run(emit)
    return f"Enriched {n} titles"


def task_schedule(emit, conn, cfg) -> str:
    from ..schedule.scheduler import Scheduler
    n = Scheduler(conn, cfg).build(emit)
    return f"Scheduled {n} programs"


def task_blurbs(emit, conn, cfg) -> str:
    from ..enrich.enricher import Enricher
    n = Enricher(conn, cfg).write_program_blurbs(emit)
    return f"Wrote {n} blurbs"


def task_add_new(emit, conn, cfg) -> str:
    """Incremental catalog update: pick up newly added files without re-probing
    or re-enriching everything. The scan skips unchanged files, probe only
    touches 'pending' rows and enrichment only runs on un-enriched titles, so
    this is a fast path when you've just dropped new media on the drives."""
    from ..scan.indexer import Indexer
    from ..enrich.enricher import Enricher
    from ..schedule.scheduler import Scheduler

    idx = Indexer(conn, cfg)
    before = conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]
    emit("Scanning for new files...", 0, 0)
    idx.scan(emit)
    after = conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]
    emit("Probing new files...", 0, 0)
    idx.probe_pending(emit)
    emit("Enriching new titles...", 0, 0)
    Enricher(conn, cfg).run(emit)
    emit("Rebuilding schedule...", 0, 0)
    n = Scheduler(conn, cfg).build(emit)
    added = max(0, after - before)
    return f"Added {added} new file(s) - {n} programs scheduled"


def task_full_setup(emit, conn, cfg) -> str:
    """Scan -> probe -> enrich -> schedule -> blurbs in one pass."""
    from ..scan.indexer import Indexer
    from ..enrich.enricher import Enricher
    from ..schedule.scheduler import Scheduler

    idx = Indexer(conn, cfg)
    emit("Scanning library...", 0, 0)
    idx.scan(emit)
    emit("Probing durations...", 0, 0)
    idx.probe_pending(emit)
    emit("Enriching metadata...", 0, 0)
    enricher = Enricher(conn, cfg)
    enricher.run(emit)
    emit("Building schedule...", 0, 0)
    n = Scheduler(conn, cfg).build(emit)
    emit("Writing guide blurbs...", 0, 0)
    blurbs = enricher.write_program_blurbs(emit)
    return f"Ready - {n} programs scheduled ({blurbs} blurbs)"
