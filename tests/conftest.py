"""Shared fixtures: temp SQLite catalog with playable media stubs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tvguide.config import Config
from tvguide.db import init_db


@pytest.fixture
def tmp_db(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def media_root(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    return root


@pytest.fixture
def cfg(media_root: Path):
    c = Config()
    c.library.tv_roots = [str(media_root)]
    c.library.movie_roots = [str(media_root)]
    c.schedule.days = 2
    c.schedule.events = False
    return c
