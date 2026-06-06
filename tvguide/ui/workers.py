"""QThread worker that runs catalog/schedule pipeline steps off the GUI thread."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from ..config import Config
from ..db import connect, init_db

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
        try:
            conn = connect()          # own connection for this thread
            init_db(conn)
            result = self.task(self._emit, conn, self.cfg)
            conn.close()
            self.done.emit(result or self.label)
        except Exception as exc:  # surface failure to the UI
            import traceback
            self.failed.emit(f"{self.label} failed: {exc}\n{traceback.format_exc()}")

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


def task_full_setup(emit, conn, cfg) -> str:
    """Scan -> probe -> enrich -> schedule in one pass with phased progress."""
    from ..scan.indexer import Indexer
    from ..enrich.enricher import Enricher
    from ..schedule.scheduler import Scheduler

    idx = Indexer(conn, cfg)
    emit("Scanning library...", 0, 0)
    idx.scan(emit)
    emit("Probing durations...", 0, 0)
    idx.probe_pending(emit)
    emit("Enriching metadata...", 0, 0)
    Enricher(conn, cfg).run(emit)
    emit("Building schedule...", 0, 0)
    n = Scheduler(conn, cfg).build(emit)
    return f"Ready - {n} programs scheduled"
