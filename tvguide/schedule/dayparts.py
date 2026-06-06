"""Themed channels and their day-part programming templates.

Each channel is a continuous strip of "segments". A segment declares which
content buckets are eligible during a wall-clock window, emulating the rhythm
of 80s/90s/2000s broadcast television.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start_hour: int           # 0-23, when this segment begins
    label: str
    kind: str                 # 'series' | 'film'
    buckets: tuple[str, ...]  # eligible content buckets, in priority order
    weekend_only: bool = False


@dataclass(frozen=True)
class Channel:
    slug: str
    name: str
    tagline: str
    accent: str
    weekday: tuple[Segment, ...]
    weekend: tuple[Segment, ...]
    offset: int = 0           # rotation seed so channels don't mirror each other


# Fallback episode/film durations (seconds) when a file hasn't been probed.
DEFAULT_DURATIONS = {
    "preschool": 12 * 60,
    "kids_cartoon": 22 * 60,
    "anime": 24 * 60,
    "adult_animation": 22 * 60,
    "sitcom": 22 * 60,
    "drama": 45 * 60,
    "action_adventure": 45 * 60,
    "documentary": 48 * 60,
    "reality": 42 * 60,
    "film": 108 * 60,
    "other": 30 * 60,
}


# ---- ToonWorld: all-day kids animation, retro cartoon-channel cadence ------
# NOTE: action *cartoons* live in the kids_cartoon bucket; the
# action_adventure bucket is live-action, so ToonWorld never uses it.
_TOON_WEEKDAY = (
    Segment(6,  "Sunrise Toons",      "series", ("preschool", "kids_cartoon")),
    Segment(9,  "Morning Cartoons",   "series", ("kids_cartoon",)),
    Segment(12, "Midday Toons",       "series", ("kids_cartoon",)),
    Segment(15, "After-School Block", "series", ("kids_cartoon", "anime")),
    Segment(18, "Action Hour",        "series", ("kids_cartoon", "anime")),
    Segment(20, "Family Toons",       "series", ("kids_cartoon", "adult_animation")),
    Segment(22, "Adult Swim",         "series", ("adult_animation", "anime")),
    Segment(1,  "Graveyard Toons",    "series", ("adult_animation", "anime")),
)
_TOON_WEEKEND = (
    Segment(6,  "Early Bird Toons",          "series", ("preschool", "kids_cartoon")),
    Segment(8,  "Saturday Morning Cartoons", "series", ("kids_cartoon",)),
    Segment(12, "Toon Matinee",              "series", ("kids_cartoon",)),
    Segment(15, "Hero Hour",                 "series", ("kids_cartoon", "anime")),
    Segment(18, "Family Block",              "series", ("kids_cartoon", "adult_animation")),
    Segment(21, "Adult Swim",                "series", ("adult_animation", "anime")),
    Segment(1,  "Graveyard Toons",           "series", ("adult_animation", "anime")),
)

# ---- Prime: flagship live-action entertainment ----------------------------
_PRIME_WEEKDAY = (
    Segment(6,  "Breakfast Reruns",  "series", ("sitcom",)),
    Segment(9,  "Daytime",           "series", ("drama", "reality", "sitcom")),
    Segment(13, "Afternoon Stories", "series", ("drama", "documentary")),
    Segment(16, "Teatime Comedy",    "series", ("sitcom",)),
    Segment(19, "Primetime Drama",   "series", ("drama", "action_adventure")),
    Segment(22, "Late Show",         "series", ("sitcom", "adult_animation")),
    Segment(0,  "Graveyard Slot",    "series", ("drama", "documentary")),
)
_PRIME_WEEKEND = (
    Segment(7,  "Weekend Brunch",    "series", ("sitcom", "reality")),
    Segment(10, "Documentary Strand","series", ("documentary", "drama")),
    Segment(13, "Matinee Drama",     "series", ("drama", "action_adventure")),
    Segment(17, "Comedy Block",      "series", ("sitcom",)),
    Segment(19, "Saturday Primetime","series", ("drama", "action_adventure")),
    Segment(22, "Late Show",         "series", ("sitcom", "adult_animation")),
    Segment(0,  "Graveyard Slot",    "series", ("drama", "documentary")),
)

# ---- Maxx: action, adventure & sci-fi -------------------------------------
_MAXX_WEEKDAY = (
    Segment(6,  "Morning Maneuvers",  "series", ("action_adventure",)),
    Segment(9,  "Adventure Hour",     "series", ("action_adventure", "kids_cartoon")),
    Segment(12, "Sci-Fi Afternoon",   "series", ("action_adventure", "drama")),
    Segment(16, "After-School Action","series", ("action_adventure", "kids_cartoon")),
    Segment(19, "Primetime Action",   "series", ("action_adventure", "drama")),
    Segment(22, "Late Night Cult",    "series", ("action_adventure", "anime", "adult_animation")),
    Segment(1,  "Graveyard Shift",    "series", ("action_adventure", "anime")),
)
_MAXX_WEEKEND = (
    Segment(7,  "Saturday Adventure", "series", ("action_adventure", "kids_cartoon")),
    Segment(11, "Sci-Fi Marathon",    "series", ("action_adventure", "drama")),
    Segment(16, "Hero Block",         "series", ("action_adventure",)),
    Segment(19, "Primetime Action",   "series", ("action_adventure", "drama")),
    Segment(22, "Late Night Cult",    "series", ("action_adventure", "anime", "adult_animation")),
    Segment(1,  "Graveyard Shift",    "series", ("action_adventure", "anime")),
)

# ---- Chuckle: round-the-clock comedy --------------------------------------
_CHUCKLE_WEEKDAY = (
    Segment(6,  "Wake-Up Comedy",     "series", ("sitcom",)),
    Segment(9,  "Sitcom Stack",       "series", ("sitcom",)),
    Segment(12, "Lunch Laughs",       "series", ("sitcom",)),
    Segment(15, "After-School Funnies","series", ("kids_cartoon", "sitcom")),
    Segment(18, "Dinnertime Comedy",  "series", ("sitcom",)),
    Segment(20, "Primetime Comedy",   "series", ("sitcom", "adult_animation")),
    Segment(22, "After Dark",         "series", ("adult_animation",)),
    Segment(1,  "Graveyard Giggles",  "series", ("adult_animation", "sitcom")),
)
_CHUCKLE_WEEKEND = _CHUCKLE_WEEKDAY

# ---- The Movie Channel: films round the clock -----------------------------
_CINEMA_WEEKDAY = (
    Segment(6,  "Morning Matinee",   "film", ("film",)),
    Segment(11, "Afternoon Feature", "film", ("film",)),
    Segment(16, "Early Show",        "film", ("film",)),
    Segment(20, "Primetime Premiere","film", ("film",)),
    Segment(23, "Late Night Double", "film", ("film",)),
    Segment(2,  "Graveyard Feature", "film", ("film",)),
)
_CINEMA_WEEKEND = _CINEMA_WEEKDAY

# ---- Nite Owl: after-dark cult, adult animation & anime -------------------
_NITE_WEEKDAY = (
    Segment(8,  "Daytime Filler",    "series", ("documentary", "reality", "drama")),
    Segment(13, "Afternoon Oddities","series", ("drama", "action_adventure")),
    Segment(18, "Early Cult",        "series", ("action_adventure", "drama")),
    Segment(21, "Adult Animation",   "series", ("adult_animation",)),
    Segment(23, "Anime After Dark",  "series", ("anime", "adult_animation")),
    Segment(2,  "Graveyard",         "series", ("adult_animation", "anime", "drama")),
)
_NITE_WEEKEND = (
    Segment(8,  "Weekend Docs",      "series", ("documentary", "reality")),
    Segment(13, "Cult Matinee",      "series", ("action_adventure", "drama")),
    Segment(18, "Anime Block",       "series", ("anime", "action_adventure")),
    Segment(21, "Adult Animation",   "series", ("adult_animation",)),
    Segment(23, "Anime After Dark",  "series", ("anime", "adult_animation")),
    Segment(2,  "Graveyard",         "series", ("adult_animation", "anime", "drama")),
)


CHANNELS: tuple[Channel, ...] = (
    Channel("toonworld", "ToonWorld", "All cartoons, all day.", "#36e0c8",
            _TOON_WEEKDAY, _TOON_WEEKEND, offset=0),
    Channel("prime", "Prime", "Primetime never sleeps.", "#ff5e8a",
            _PRIME_WEEKDAY, _PRIME_WEEKEND, offset=1),
    Channel("maxx", "Maxx", "Action to the maxx.", "#ff7a45",
            _MAXX_WEEKDAY, _MAXX_WEEKEND, offset=2),
    Channel("chuckle", "Chuckle", "The funny channel.", "#ffd166",
            _CHUCKLE_WEEKDAY, _CHUCKLE_WEEKEND, offset=3),
    Channel("cinema", "The Movie Channel", "Always a feature.", "#8ab4ff",
            _CINEMA_WEEKDAY, _CINEMA_WEEKEND, offset=4),
    Channel("niteowl", "Nite Owl", "After dark, anything goes.", "#9b6bff",
            _NITE_WEEKDAY, _NITE_WEEKEND, offset=5),
)


def segment_for(channel: Channel, hour: int, is_weekend: bool) -> Segment:
    """Return the active segment for a wall-clock hour."""
    segs = channel.weekend if is_weekend else channel.weekday
    # Segments are start-hour anchored; find the latest segment whose start
    # hour is <= the current hour, wrapping through midnight.
    best: Segment | None = None
    best_dist = 25
    for s in segs:
        dist = (hour - s.start_hour) % 24
        if dist < best_dist:
            best_dist = dist
            best = s
    assert best is not None
    return best
