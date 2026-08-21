"""Shared playability checks for catalog, schedule, and playback."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Minimum usable runtime. Anything shorter is almost always a sample, stub,
# or truncated download -- never schedule it.
MIN_DURATION_SEC = 60.0

# Partial-download / in-progress suffixes that must never enter the catalog.
PARTIAL_SUFFIXES = (
    ".part", ".parts", ".crdownload", ".download", ".tmp", ".!qb",
    ".partial", ".temp", ".aria2", ".ytdl",
)


def is_partial_path(path: str | Path) -> bool:
    """True when the path looks like an unfinished download."""
    name = Path(path).name.lower()
    if name.startswith("."):
        return True
    for suf in PARTIAL_SUFFIXES:
        if name.endswith(suf):
            return True
    # e.g. show.mkv.part / show.part.mkv
    low = name
    if ".part." in low or low.endswith(".part"):
        return True
    return False


def file_exists(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path)


def probe_row_playable(row: sqlite3.Row | dict) -> bool:
    """True when a media_files row has enough probe evidence to air."""
    try:
        state = row["probe_state"]
        dur = row["duration_sec"]
        codec = row["video_codec"]
        path = row["path"]
    except (IndexError, KeyError, TypeError):
        return False
    if state != "done":
        return False
    if dur is None or float(dur) < MIN_DURATION_SEC:
        return False
    if not codec:
        return False
    return file_exists(path)


def program_playable(path: str | None, probe_state: str | None = None,
                     duration_sec: float | None = None,
                     video_codec: str | None = None,
                     *, require_codec: bool = False) -> bool:
    """Runtime check used by desktop + stream when tuning in.

    When ``probe_state`` is provided it must be ``done`` with a usable
    duration. Pass ``require_codec=True`` (or a non-empty codec) to also
    demand a video codec — used by the scheduler pool gate.
    """
    if not file_exists(path) or is_partial_path(path or ""):
        return False
    if probe_state is not None and probe_state != "done":
        return False
    if duration_sec is not None and float(duration_sec) < MIN_DURATION_SEC:
        return False
    if require_codec and not video_codec:
        return False
    if video_codec is not None and video_codec == "":
        return False
    return True


# SQL fragment: media_files alias `m` is playable (probe done + duration).
# Callers still need an existence check at schedule/tune-in time for stale paths.
PLAYABLE_SQL = (
    "m.probe_state='done' AND m.duration_sec IS NOT NULL "
    f"AND m.duration_sec > {MIN_DURATION_SEC} AND m.video_codec IS NOT NULL"
)
