"""Special-event programming: seasonal and spontaneous channel takeovers.

The idea is the opposite of "always on" -- most days nothing happens, but on a
holiday (or, rarely, a random weekend) a fitting channel is taken over for the
broadcast day with a themed run of films: a Christmas marathon, a Halloween
fright night, a May-the-4th Star Wars bonanza, a Marvel origins weekend.

Events only ever take over **The Movie Channel** (``cinema``) -- the natural
home for a film bonanza -- and degrade gracefully: if the library has nothing
matching, the event simply doesn't fire and normal programming runs.
"""
from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from ..enrich.tagging import MCU_ORIGINS

# The film pool query mirrors the scheduler's so rows carry ``file_dur`` and
# everything _place_film expects.
_FILM_SQL = (
    "SELECT t.*, m.duration_sec AS file_dur FROM titles t "
    "JOIN media_files m ON m.id = t.movie_file_id "
    "WHERE t.kind='movie' AND t.movie_file_id IS NOT NULL "
)

EVENT_CHANNEL = "cinema"


@dataclass
class Takeover:
    """An ordered, looping queue of films for one channel for one day."""
    label: str
    films: list = field(default_factory=list)
    _idx: int = 0

    def next_film(self):
        if not self.films:
            return None
        film = self.films[self._idx % len(self.films)]
        self._idx += 1
        return film


def _titlecase(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split())


def _films_by_tag(conn: sqlite3.Connection, tag: str) -> list[sqlite3.Row]:
    return conn.execute(
        _FILM_SQL + 'AND t.tags LIKE ? ORDER BY t.rating DESC, t.year',
        (f'%"{tag}"%',),
    ).fetchall()


def _films_by_franchise(conn: sqlite3.Connection, franchise: str) -> list[sqlite3.Row]:
    return conn.execute(
        _FILM_SQL + "AND t.franchise=? ORDER BY t.year",
        (franchise,),
    ).fetchall()


def _mcu_origins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The first film of each Phase-1 hero, in canon order -- not the sequels."""
    films = _films_by_franchise(conn, "mcu")
    chosen: list[sqlite3.Row] = []
    seen: set[int] = set()
    for token in MCU_ORIGINS:
        pat = re.compile(r"\b%s\b" % re.escape(token))
        matches = [f for f in films if pat.search((f["name"] or "").lower())]
        if not matches:
            continue
        matches.sort(key=lambda f: (f["year"] or 9999))
        pick = matches[0]
        if pick["id"] not in seen:
            seen.add(pick["id"])
            chosen.append(pick)
    return chosen


def _spontaneous(conn: sqlite3.Connection, d: date) -> Takeover | None:
    """A low-probability weekend marathon, stable within a calendar week."""
    yr, wk, _ = d.isocalendar()
    rng = random.Random(yr * 53 + wk)
    if rng.random() >= 0.22:        # ~1-in-5 weekends gets a stunt
        return None

    options: list[tuple[str, list]] = []
    mcu = _mcu_origins(conn)
    if len(mcu) >= 3:
        options.append(("Marvel Phase 1 Origins Weekend", mcu))
    rows = conn.execute(
        "SELECT franchise, COUNT(*) c FROM titles WHERE kind='movie' "
        "AND movie_file_id IS NOT NULL AND franchise IS NOT NULL "
        "GROUP BY franchise HAVING c >= 3"
    ).fetchall()
    for r in rows:
        options.append((f"{_titlecase(r['franchise'])} Marathon",
                        _films_by_franchise(conn, r["franchise"])))
    if not options:
        return None
    label, films = rng.choice(options)
    return Takeover(label, films)


def active_takeovers(conn: sqlite3.Connection, d: date) -> dict[str, Takeover]:
    """Channel-slug -> Takeover for the given broadcast day (empty if none)."""
    out: dict[str, Takeover] = {}
    m, day = d.month, d.day
    takeover: Takeover | None = None

    if m == 12 and 18 <= day <= 26:
        films = _films_by_tag(conn, "christmas")
        if films:
            takeover = Takeover("Christmas Cinema", films)
    elif m == 10 and 25 <= day <= 31:
        films = _films_by_tag(conn, "horror")
        if films:
            takeover = Takeover("Halloween Fright Night", films)
    elif (m, day) == (2, 14):
        films = _films_by_tag(conn, "romance")
        if films:
            takeover = Takeover("Valentine's Double Feature", films)
    elif (m, day) == (5, 4):
        films = _films_by_franchise(conn, "star wars")
        if films:
            takeover = Takeover("Star Wars Bonanza", films)
    elif d.weekday() >= 5:          # Saturday or Sunday
        takeover = _spontaneous(conn, d)

    if takeover is not None:
        out[EVENT_CHANNEL] = takeover
    return out
