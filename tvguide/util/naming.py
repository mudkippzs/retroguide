"""Turn messy scene-release filenames into structured title/episode info."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from guessit import guessit

from .media_ok import is_partial_path

# Buckets used by the scheduler to slot content into dayparts.
BUCKETS = [
    "preschool",        # very young kids
    "kids_cartoon",     # Saturday-morning / after-school toons
    "anime",
    "action_adventure",
    "adult_animation",  # late-night toons (Futurama, Family Guy...)
    "sitcom",
    "drama",
    "documentary",
    "reality",
    "film",             # movies
    "other",
]

# An *explicit* episode marker (SxxExx, 1x01, "Season 2 Episode 5").
EPISODE_MARKER = re.compile(
    r"(s\d{1,2}[\s._-]?e\d{1,3})"
    r"|(\b\d{1,2}x\d{2,3}\b)"
    r"|(season\s*\d+.{0,14}episode\s*\d+)"
    r"|(\bs\d{1,2}\b\s*(complete|episode))",
    re.I,
)

# Explicit season/episode markers as they appear in a *single* filename.
_SXXEXX = re.compile(r"[Ss](\d{1,2})[\s._-]?[Ee](\d{1,3})")
_NxNN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
# "Season 2 Episode 01" spelled out in the filename itself.
_SEASON_EPISODE_WORD = re.compile(
    r"\bseason\s*(\d{1,2})\b[\s._,&-]*\bepisode\s*(\d{1,3})\b", re.I)
# Episode word with no season ("... Episode 05 - Title"); season comes from
# the folder instead.
_EPISODE_WORD_ONLY = re.compile(r"\bepisode\s*(\d{1,3})\b", re.I)
# A folder that names exactly one season ("Season 2", "S02", "Series 3").
_FOLDER_SEASON = re.compile(
    r"^\s*(?:season|series|staffel|saison)\s*(\d{1,2})\s*$|"
    r"^[Ss](\d{1,2})\s*$", re.I)


def folder_season_hint(path: Path) -> int | None:
    """Season number from the nearest unambiguous single-season parent folder.

    Box-set folders listing several seasons ("Season 1, 2, 3 & 4") are
    ambiguous and skipped -- guessing from those collapses every season onto
    the first one.
    """
    for parent in path.parents:
        m = _FOLDER_SEASON.match(parent.name)
        if m:
            return int(m.group(1) or m.group(2))
        if parent.parent == parent:
            break
    return None

_ANIME_HINTS = re.compile(r"\b(anime|sub(bed)?|dub(bed)?|\bova\b|bd\b)\b", re.I)
_JUNK_TOKENS = re.compile(
    r"\b(1080p|720p|2160p|480p|x264|x265|h264|h265|hevc|web[\- ]?dl|webrip|bluray|"
    r"blu-ray|brrip|hdrip|dvdrip|amzn|nf|dsnp|aac|ddp?5\.?1|atmos|10bit|"
    r"complete|proper|repack|remux|hdr|hdtv|xvid|eac3|dts)\b",
    re.I,
)


@dataclass
class ParsedMedia:
    kind: str            # 'episode' | 'movie' | 'unknown'
    title: str
    year: int | None
    season: int | None
    episode: int | None
    episode_title: str | None
    raw_guess: dict


# "extras"/"featurette"/"behind the scenes" name *bonus-content folders*. They
# must not knock out a real season folder that merely mentions them (e.g.
# "... Season 4 S04 + Extras"), nor a show actually called "Extras". So for
# these we only exclude a path component with no season/episode/year signal.
_BONUS_FOLDER_PATTERNS = ("extras", "featurette", "behind the scenes")
_CONTENT_SIGNAL = re.compile(
    r"(s\d{1,2}[\s._-]?e\d{1,3})|(\b\d{1,2}x\d{2,3}\b)|(\bseasons?\s*\d+)"
    r"|(\bs\d{1,2}\b)|(\b(19|20)\d{2}\b)",
    re.I,
)


def is_excluded(path: Path, patterns: list[str]) -> bool:
    # Hidden / partial download files (.part, .crdownload, .!qB, …).
    if is_partial_path(path):
        return True
    s = str(path).lower()
    parts = [part.lower() for part in path.parts]
    for p in patterns:
        pl = p.lower()
        if pl not in s:
            continue
        if pl in _BONUS_FOLDER_PATTERNS:
            # Only a component *dedicated* to bonus content (no episode/year of
            # its own) counts -- so "Featurettes" is skipped but "S04 + Extras"
            # and the sitcom "Extras (2005)" are kept.
            if any(pl in part and not _CONTENT_SIGNAL.search(part) for part in parts):
                return True
            continue
        return True
    return False


def is_special_episode(season: int | None, episode: int | None) -> bool:
    """True for bonus/special numbering that should not enter regular rotation."""
    if season is None:
        return False
    if season == 0:
        return True
    if season >= 90:
        return True
    if episode is not None and episode == 0:
        return True
    return False


def looks_like_video(path: Path, exts: list[str]) -> bool:
    return path.suffix.lower() in exts


def clean_title(title: str) -> str:
    title = _JUNK_TOKENS.sub("", title)
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -[]")
    return title.strip()


def has_episode_marker(text: str) -> bool:
    return bool(EPISODE_MARKER.search(text))


# Trailing runs of standalone numbers indicate a season list, not a title
# ("Game of Thrones 1 2 3 4 5 6 7"). Require >=2 numbers so single-number
# titles ("Babylon 5", "3rd Rock") survive.
_TRAILING_SEASON_RUN = re.compile(r"\s+\d{1,2}(\s+\d{1,2}){1,}\s*$")
_TRAILING_SEASON_WORD = re.compile(
    r"\s+(seasons?|series|complete|collection)\s*\d*(\s*[-\u2013]\s*\d+)?\s*$", re.I)


def series_title_from_name(name: str) -> str:
    """Derive a clean series title from a folder or file name, recombining the
    title fragments guessit splits on dashes (e.g. Avatar - The Last Airbender)."""
    g = guessit(name, {"type": "episode"})
    title = g.get("title") or ""
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)
    alt = g.get("alternative_title")
    if alt:
        if isinstance(alt, list):
            alt = " ".join(str(t) for t in alt)
        # Only fold in the alternative title if it isn't an episode detail.
        if not has_episode_marker(str(alt)):
            title = f"{title} {alt}"
    title = clean_title(title)
    title = _TRAILING_SEASON_WORD.sub("", title)
    title = _TRAILING_SEASON_RUN.sub("", title)
    return title.strip(" -[]") or clean_title(name)


def parse_media(path: Path, expected_type: str | None = None) -> ParsedMedia:
    """Parse a single media file path. Uses the folder context for accuracy.

    ``expected_type`` ('movie'|'episode') biases guessit when we already know
    which drive the file lives on, fixing things like "12 Angry Men" being
    read as episode 12.
    """
    # guessit does better with the whole relative path on scene releases.
    if expected_type:
        guess = dict(guessit(str(path), {"type": expected_type}))
    else:
        guess = dict(guessit(str(path)))
    gtype = guess.get("type", "unknown")

    title = guess.get("title") or path.stem
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)
    title = clean_title(str(title))

    year = guess.get("year")
    season = guess.get("season")
    episode = guess.get("episode")
    ep_title = guess.get("episode_title")

    # guessit sometimes returns lists for multi-episode files; take the first.
    if isinstance(season, list):
        season = season[0] if season else None
    if isinstance(episode, list):
        episode = episode[0] if episode else None

    # The explicit SxxExx in the *filename* is authoritative -- a parent folder
    # like "Show Season 1-6 S01-S06" otherwise poisons guessit's season guess
    # (collapsing several seasons onto season 1). Trust the file's own marker.
    # Parenthesized asides can carry another show's marker -- "(The Simpsons
    # S26E06)" on a Futurama crossover file -- so strip them before matching.
    fn = re.sub(r"\([^)]*\)", " ", path.name)
    fm = _SXXEXX.search(fn) or _NxNN.search(fn)
    if fm:
        season = int(fm.group(1))
        episode = int(fm.group(2))
    else:
        # "Season 2 Episode 01" spelled out in the filename beats whatever
        # season guessit picked up from the surrounding folders.
        wm = _SEASON_EPISODE_WORD.search(fn)
        if wm:
            season = int(wm.group(1))
            episode = int(wm.group(2))
        elif _EPISODE_WORD_ONLY.search(fn) and episode is not None:
            # Filename names an episode but no season; guessit's season may
            # have leaked from a box-set folder, so prefer the nearest
            # single-season parent folder when there is one.
            hint = folder_season_hint(path)
            if hint is not None:
                season = hint
        elif expected_type != "movie" and episode is None:
            # Bare leading number ("01 Night of the Lizard.mkv"): the number
            # is the episode. Without an explicit episode hint we also
            # require a single-season parent folder so stray numbers on a TV
            # drive don't invent episodes. The separator after the digits
            # keeps codec prefixes like "360p" from matching.
            bm = re.match(r"^0*(\d{1,3})[\s._\-]+", path.stem)
            if bm:
                hint = folder_season_hint(path)
                if hint is not None or expected_type == "episode":
                    episode = int(bm.group(1))
                    season = hint if hint is not None else (season or 1)

    kind = "unknown"
    if gtype == "episode" or season is not None or episode is not None:
        kind = "episode"
        if season is None:
            season = 1
    elif gtype == "movie":
        kind = "movie"

    if isinstance(ep_title, list):
        ep_title = ep_title[0] if ep_title else None
    if ep_title:
        ep_title = clean_title(str(ep_title))

    return ParsedMedia(
        kind=kind,
        title=title or path.stem,
        year=int(year) if isinstance(year, int) else None,
        season=int(season) if isinstance(season, int) else None,
        episode=int(episode) if isinstance(episode, int) else None,
        episode_title=ep_title or None,
        raw_guess=guess,
    )


def sort_name(name: str) -> str:
    s = name.lower().strip()
    for article in ("the ", "a ", "an "):
        if s.startswith(article):
            s = s[len(article):]
            break
    return s


# --- Heuristic classification (fallback when no TMDB genres / LLM) ----------

_BUCKET_KEYWORDS: dict[str, list[str]] = {
    "adult_animation": [
        "family guy", "futurama", "american dad", "rick and morty", "south park",
        "archer", "bojack", "robot chicken", "aqua teen", "king of the hill",
        "the simpsons", "disenchantment", "harley quinn", "big mouth",
    ],
    "anime": [
        "naruto", "bleach", "one piece", "dragon ball", "pokemon", "pokémon",
        "kamisama", "attack on titan", "demon slayer", "jujutsu", "fullmetal",
        "death note", "cowboy bebop", "sailor moon", "digimon", "evangelion",
    ],
    "kids_cartoon": [
        "avatar", "ninja turtles", "tmnt", "spongebob", "scooby", "looney",
        "tom and jerry", "ben 10", "powerpuff", "dexter", "samurai jack",
        "gravity falls", "adventure time", "regular show", "he-man",
        "transformers", "g.i. joe", "duck tales", "ducktales", "dungeons and dragons",
        "avengers earth", "spider-man", "x-men",
    ],
    "preschool": [
        "peppa", "bluey", "paw patrol", "dora", "teletubbies", "sesame street",
        "thomas", "fireman sam", "postman pat",
    ],
}


def looks_like_anime(name: str, path: str | None = None,
                     genres: list[str] | None = None,
                     origin: str | None = None) -> bool:
    """True when title/path/genres/origin strongly indicate Japanese animation."""
    low = (name or "").lower()
    g = {x.lower() for x in (genres or [])}
    if "anime" in g:
        return True
    if origin and origin.lower() in ("jp", "japan", "ja", "jpn"):
        if "animation" in g or _ANIME_HINTS.search(low):
            return True
    if any(kw in low for kw in _BUCKET_KEYWORDS["anime"]):
        return True
    hay = f"{low} {path or ''}".lower()
    if _ANIME_HINTS.search(hay):
        # Require an animation signal so "subbed" live-action doesn't match.
        if "animation" in g or "anime" in hay or "ova" in hay:
            return True
    return False


def heuristic_bucket(name: str, kind: str, genres: list[str] | None = None,
                     path: str | None = None, origin: str | None = None) -> str:
    if kind == "movie":
        return "film"
    low = name.lower()
    for bucket, kws in _BUCKET_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return bucket
    if looks_like_anime(name, path, genres, origin):
        return "anime"
    if genres:
        g = {x.lower() for x in genres}
        # Anime first (TVmaze tags it explicitly as a genre).
        if "anime" in g:
            return "anime"
        if "animation" in g or "children" in g or "kids" in g:
            # Adult-animation shows are caught by the keyword pass above, so any
            # remaining animation here is children's programming.
            return "kids_cartoon"
        if "documentary" in g:
            return "documentary"
        if "reality" in g:
            return "reality"
        if "comedy" in g and not ({"drama", "crime", "thriller"} & g):
            return "sitcom"
        if {"action", "adventure", "sci-fi", "science fiction", "fantasy",
            "supernatural", "war"} & g:
            return "action_adventure"
        if {"drama", "crime", "thriller", "mystery", "romance", "medical",
            "legal", "family"} & g:
            return "drama"
    return "drama"
