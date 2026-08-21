"""Tiny service-health indicator: dots for the local model + metadata APIs
plus a catalog-integrity signal.

Each dot turns green when the service answered, red when it didn't, and grey
when it isn't configured (e.g. no TMDB key). Checks run off the GUI thread so a
slow/offline endpoint never freezes the window.
"""
from __future__ import annotations

import urllib.request

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import repo
from ..config import Config
from ..db import connect

_UP = "#3fd07a"
_DOWN = "#ff5e6a"
_OFF = "#44566a"
_WARN = "#ffd166"


def _reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


class _HealthCheck(QThread):
    result = Signal(dict)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        out: dict[str, object] = {}
        host = (self.cfg.ollama.host or "").rstrip("/")
        out["ollama"] = _reachable(f"{host}/api/tags") if host else False
        out["tvmaze"] = _reachable("https://api.tvmaze.com/")
        if (self.cfg.tmdb.api_key or "").strip():
            out["tmdb"] = _reachable(
                f"https://api.themoviedb.org/3/configuration?api_key={self.cfg.tmdb.api_key}")
        else:
            out["tmdb"] = None      # not configured
        # Catalog integrity: green when clean, warn when issues, red when bad.
        try:
            conn = connect()
            try:
                stats = repo.catalog_integrity(conn)
            finally:
                conn.close()
            bad = (stats["missing_paths"] + stats["probe_error"]
                   + stats["unplayable_programs"])
            if bad == 0 and stats["movies_no_overview"] == 0:
                out["catalog"] = "ok"
            elif bad == 0:
                out["catalog"] = "warn"
            else:
                out["catalog"] = "bad"
            out["catalog_stats"] = stats
        except Exception:
            out["catalog"] = "bad"
            out["catalog_stats"] = {}
        self.result.emit(out)


class HealthDots(QWidget):
    """A row of labelled status dots; call :meth:`check` to refresh."""

    _LABELS = (("ollama", "MODEL"), ("tvmaze", "TVMAZE"), ("tmdb", "TMDB"),
               ("catalog", "CATALOG"))

    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self._worker: _HealthCheck | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._dots: dict[str, QLabel] = {}
        for key, text in self._LABELS:
            cell = QLabel(f"\u25cf {text}")
            cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell.setStyleSheet(
                f"color:{_OFF}; font-size:10px; font-weight:700; letter-spacing:1px;")
            self._dots[key] = cell
            lay.addWidget(cell)

    def shutdown(self) -> None:
        """Block briefly for any in-flight check so we never tear down a live
        QThread (Qt aborts on that)."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3500)

    def check(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _HealthCheck(self.cfg)
        self._worker.result.connect(self._apply)
        self._worker.start()

    def _apply(self, states: dict) -> None:
        for key, text in self._LABELS:
            state = states.get(key)
            if key == "catalog":
                if state == "ok":
                    color, tip = _UP, "catalog healthy"
                elif state == "warn":
                    color, tip = _WARN, "metadata gaps"
                else:
                    color, tip = _DOWN, "missing/unplayable media"
                stats = states.get("catalog_stats") or {}
                if stats:
                    tip = (f"{tip}: missing={stats.get('missing_paths', 0)} "
                           f"probe_err={stats.get('probe_error', 0)} "
                           f"unplayable={stats.get('unplayable_programs', 0)} "
                           f"films_no_meta={stats.get('movies_no_overview', 0)}")
            elif state is None:
                color, tip = _OFF, "not configured"
            elif state:
                color, tip = _UP, "reachable"
            else:
                color, tip = _DOWN, "unreachable"
            dot = self._dots[key]
            dot.setStyleSheet(
                f"color:{color}; font-size:10px; font-weight:700; letter-spacing:1px;")
            dot.setToolTip(f"{text}: {tip}")
