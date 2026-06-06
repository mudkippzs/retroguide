"""Franchise and holiday/mood tagging used by the special-event scheduler.

Deterministic and offline -- derived from the title, overview and genres we
already hold, so it can backfill an existing catalog without re-enriching or
hitting the network. The LLM is intentionally not involved: franchise/holiday
detection wants to be stable and cheap, not creative.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable

# Keyword -> franchise key. Checked against the title (lowercased). Order the
# entries specific-first; the first hit wins.
FRANCHISES: dict[str, list[str]] = {
    "star wars": [
        "star wars", "the mandalorian", "andor", "ahsoka", "obi-wan",
        "rogue one", "clone wars", "rebels", "boba fett", "the acolyte",
    ],
    "mcu": [
        "iron man", "incredible hulk", "captain america", "thor", "avengers",
        "guardians of the galaxy", "ant-man", "black panther", "doctor strange",
        "captain marvel", "black widow", "eternals", "shang-chi",
        "spider-man: homecoming", "spider-man: far from home", "spider-man: no way home",
    ],
    "harry potter": ["harry potter", "fantastic beasts"],
    "lord of the rings": ["lord of the rings", "the hobbit", "rings of power"],
    "james bond": ["james bond", "007", "no time to die", "skyfall",
                   "casino royale", "goldeneye", "spectre", "quantum of solace"],
    "jurassic park": ["jurassic park", "jurassic world"],
    "the matrix": ["the matrix"],
    "back to the future": ["back to the future"],
    "indiana jones": ["indiana jones", "raiders of the lost ark"],
    "alien": ["alien", "aliens", "prometheus"],
    "terminator": ["terminator"],
    "rocky": ["rocky", "creed"],
    "die hard": ["die hard"],
    "toy story": ["toy story"],
    "shrek": ["shrek"],
    "pirates of the caribbean": ["pirates of the caribbean"],
}

# The five Phase 1 origin films, in canon order, for the MCU origins weekend.
MCU_ORIGINS = ["iron man", "the incredible hulk", "thor",
               "captain america", "the avengers"]

_CHRISTMAS = ("christmas", "santa", "xmas", "yuletide", "st. nick", "saint nick",
              "north pole", "reindeer", "nativity", "scrooge", "elf", "noel")
_HORROR = ("halloween", "haunted", "ghost", "witch", "vampire", "zombie",
           "slasher", "demon", "possessed", "nightmare", "evil dead")
_ROMANCE_KW = ("valentine", "love story", "rom-com", "romantic")


# Word-boundary matchers so "andor" doesn't match "Pandorum" and "thor"
# doesn't match "Thornton".
_FRANCHISE_RE = {
    key: re.compile(r"\b(?:%s)\b" % "|".join(re.escape(k) for k in kws))
    for key, kws in FRANCHISES.items()
}


def detect_franchise(name: str) -> str | None:
    low = (name or "").lower()
    for key, pat in _FRANCHISE_RE.items():
        if pat.search(low):
            return key
    return None


def detect_tags(name: str, overview: str | None, genres: list[str]) -> list[str]:
    text = f"{name or ''} {overview or ''}".lower()
    g = {x.lower() for x in (genres or [])}
    tags: set[str] = set()
    if any(k in text for k in _CHRISTMAS):
        tags.add("christmas")
    if "horror" in g or "thriller" in g or any(k in text for k in _HORROR):
        tags.add("horror")
    if "romance" in g or any(k in text for k in _ROMANCE_KW):
        tags.add("romance")
    if "family" in g or "animation" in g:
        tags.add("family")
    if "war" in g:
        tags.add("war")
    return sorted(tags)


def tag_title(name: str, overview: str | None, genres: list[str]) -> tuple[str | None, list[str]]:
    return detect_franchise(name), detect_tags(name, overview, genres)


def backfill(conn: sqlite3.Connection,
             progress: Callable[[str, int, int], None] | None = None) -> int:
    """Compute franchise/tags for every title from data already on hand."""
    rows = conn.execute("SELECT id, name, overview, genres FROM titles").fetchall()
    total = len(rows)
    for i, r in enumerate(rows):
        try:
            genres = json.loads(r["genres"]) if r["genres"] else []
        except (json.JSONDecodeError, TypeError):
            genres = []
        franchise, tags = tag_title(r["name"], r["overview"], genres)
        conn.execute(
            "UPDATE titles SET franchise=?, tags=? WHERE id=?",
            (franchise, json.dumps(tags), r["id"]),
        )
        if progress and i % 200 == 0:
            conn.commit()
            progress("Tagging catalog", i + 1, total)
    conn.commit()
    if progress:
        progress("Tagging complete", total, total)
    return total
