"""Central logging configuration for RetroGuide.

One rotating log file under the app data dir plus a console mirror, so a
deployed instance leaves a trail for monitoring networking, subprocesses and
exceptions. Call :func:`setup_logging` once at each entry point (GUI / CLI /
headless serve); it is idempotent.

Log level comes from ``$RETROGUIDE_LOG`` (e.g. ``DEBUG``) and defaults to
``INFO``. Files live at ``<data_dir>/logs/retroguide.log`` (5 x 2 MB rotation).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from .config import DATA_DIR

_CONFIGURED = False
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "retroguide.log"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(component: str = "app") -> logging.Logger:
    """Configure root logging once and return the component logger."""
    global _CONFIGURED
    log = logging.getLogger("tvguide")
    if _CONFIGURED:
        return logging.getLogger(f"tvguide.{component}")

    level_name = os.environ.get("RETROGUIDE_LOG", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger("tvguide")
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(_FORMAT, _DATEFMT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    # A missing/unwritable data dir must never take the app down.
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fileh = RotatingFileHandler(
            LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError as exc:  # pragma: no cover - depends on FS perms
        root.warning("file logging disabled (%s): %s", LOG_FILE, exc)

    _install_excepthooks(root)
    _CONFIGURED = True
    root.info("logging started (component=%s, level=%s, file=%s)",
              component, level_name, LOG_FILE)
    return logging.getLogger(f"tvguide.{component}")


def _install_excepthooks(log: logging.Logger) -> None:
    """Route uncaught exceptions (main thread + worker threads) to the log."""
    prev_hook = sys.excepthook

    def _hook(exc_type, exc, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            prev_hook(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        prev_hook(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        log.error("uncaught exception in thread %s",
                  args.thread.name if args.thread else "?",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _thread_hook
