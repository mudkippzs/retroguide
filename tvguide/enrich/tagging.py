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

# Stand-up comedy specials. Matched by explicit phrasing or well-known
# comedians so ordinary sitcoms/comedies don't get swept in.
_STANDUP_KW = ("stand-up", "stand up comedy", "standup", "comedy special",
               "live at the apollo", "live in concert", "one night stand")
_STANDUP_NAMES = (
    "chappelle", "joe rogan", "ricky gervais", "kevin hart", "bill burr",
    "john mulaney", "chris rock", "jimmy carr", "louis c.k", "louis ck",
    "daniel sloss", "bo burnham", "trevor noah", "hannah gadsby", "tom segura",
    "bill hicks", "george carlin", "eddie izzard", "frankie boyle",
    "stewart lee", "peter kay", "sarah silverman", "aziz ansari", "gabriel iglesias",
)
# Science & space factual. Gated on a documentary context so sci-fi *drama*
# (Apollo 13, Interstellar, Guardians of the Galaxy) is never mistagged.
_SCIENCE_KW = (
    "cosmos", "universe", "solar system", "black hole", "galaxy", "nasa",
    "apollo", "hubble", "telescope", "quantum", "physics", "astronaut",
    "spacex", "outer space", "space station", "mars", "moon landing",
    "big bang", "evolution", "dinosaur", "particle", "relativity",
    "planet earth", "blue planet", "how the universe", "wonders of",
    "science of", "astronomy", "astrophysics", "voyager probe",
)

# Titles that are unambiguously factual by name alone -- famous documentarians
# and landmark science/nature strands. TMDB frequently files these under
# "drama"/no genre, so we treat the name itself as the documentary context
# (both to grant the science tag and to correct the bucket). Kept deliberately
# specific so a sci-fi *drama* never trips it.
_DOC_STRONG = (
    "ken burns", "attenborough", "brian cox", "carl sagan", "neil degrasse",
    "cosmos", "the planets", "planet earth", "blue planet", "frozen planet",
    "our planet", "life on earth", "the universe", "how the universe works",
    "wonders of the solar system", "wonders of life", "wonders of the universe",
    "walking with dinosaurs", "a brief history of time", "the farthest",
    "bbc horizon", "particle fever",
)


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
    if (any(n in text for n in _STANDUP_NAMES)
            or any(k in text for k in _STANDUP_KW)
            or "stand-up" in g or "stand up" in g):
        tags.add("standup")
    # Strong doc signals are matched against the *title only* -- a documentarian
    # or strand name lives in the title, whereas an overview full of "universe"
    # / "the farthest reaches of space" is just sci-fi drama flavour (e.g.
    # Futurama) and must not be mistaken for a documentary.
    name_low = (name or "").lower()
    doc_strong = any(k in name_low for k in _DOC_STRONG)
    doc_context = (doc_strong or "documentary" in g
                   or "documentary" in name_low or "docuseries" in name_low)
    if doc_context and any(k in text for k in _SCIENCE_KW):
        tags.add("science")
    return sorted(tags)


def is_doc_series(name: str, overview: str | None, genres: list[str]) -> bool:
    """True when a *series* is clearly factual and belongs in the documentary
    bucket. Used to correct LLM/heuristic mis-bucketing (docs often land in
    'drama') so the science/factual strands can actually find them.

    Deliberately conservative: only the genre and the *title* are trusted, never
    the overview, so a sci-fi drama is never dragged into the documentary pool.
    """
    name_low = (name or "").lower()
    g = {x.lower() for x in (genres or [])}
    if "documentary" in g:
        return True
    if "documentary" in name_low or "docuseries" in name_low or "docu-series" in name_low:
        return True
    return any(k in name_low for k in _DOC_STRONG)


def tag_title(name: str, overview: str | None, genres: list[str]) -> tuple[str | None, list[str]]:
    return detect_franchise(name), detect_tags(name, overview, genres)


def backfill(conn: sqlite3.Connection,
             progress: Callable[[str, int, int], None] | None = None) -> int:
    """Compute franchise/tags for every title from data already on hand."""
    rows = conn.execute(
        "SELECT id, name, overview, genres, kind, bucket FROM titles").fetchall()
    total = len(rows)
    for i, r in enumerate(rows):
        try:
            genres = json.loads(r["genres"]) if r["genres"] else []
        except (json.JSONDecodeError, TypeError):
            genres = []
        franchise, tags = tag_title(r["name"], r["overview"], genres)
        # Correct clearly-factual series that were mis-bucketed (usually as
        # 'drama'); the science/factual strands only pull the documentary
        # bucket, so this is what actually lets them air there.
        bucket = r["bucket"]
        if (r["kind"] == "series" and bucket != "documentary"
                and is_doc_series(r["name"], r["overview"], genres)):
            bucket = "documentary"
        conn.execute(
            "UPDATE titles SET franchise=?, tags=?, bucket=? WHERE id=?",
            (franchise, json.dumps(tags), bucket, r["id"]),
        )
        if progress and i % 200 == 0:
            conn.commit()
            progress("Tagging catalog", i + 1, total)
    conn.commit()
    if progress:
        progress("Tagging complete", total, total)
    return total
