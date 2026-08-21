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
    # Optional sub-genre filter (a title tag, e.g. 'standup' or 'science').
    # When set, only titles carrying this tag are eligible -- lets a channel
    # carve a niche the coarse bucket system doesn't express.
    tag: str | None = None


@dataclass(frozen=True)
class Channel:
    slug: str
    name: str
    tagline: str
    accent: str
    weekday: tuple[Segment, ...]
    weekend: tuple[Segment, ...]
    offset: int = 0           # rotation seed so channels don't mirror each other
    # Old-school "+1" timeshift: instead of programming its own lineup, this
    # channel replays another channel's schedule, shifted later by
    # `shift_hours`. So catching a film midway on the parent means the +1 is
    # still that many hours behind -- a second start time an hour on.
    mirror_of: str | None = None
    shift_hours: float = 0.0

    @property
    def is_mirror(self) -> bool:
        return self.mirror_of is not None


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
    Segment(15, "After-School Block", "series", ("kids_cartoon",)),
    Segment(18, "Action Hour",        "series", ("kids_cartoon",)),
    Segment(20, "Family Toons",       "series", ("kids_cartoon",)),
    Segment(22, "Adult Swim",         "series", ("adult_animation",)),
    Segment(1,  "Graveyard Toons",    "series", ("adult_animation",)),
)
_TOON_WEEKEND = (
    Segment(6,  "Early Bird Toons",          "series", ("preschool", "kids_cartoon")),
    Segment(8,  "Saturday Morning Cartoons", "series", ("kids_cartoon",)),
    Segment(12, "Toon Matinee",              "series", ("kids_cartoon",)),
    Segment(15, "Hero Hour",                 "series", ("kids_cartoon",)),
    Segment(18, "Family Block",              "series", ("kids_cartoon",)),
    Segment(21, "Adult Swim",                "series", ("adult_animation",)),
    Segment(1,  "Graveyard Toons",           "series", ("adult_animation",)),
)

# ---- Prime: flagship live-action entertainment ----------------------------
_PRIME_WEEKDAY = (
    Segment(6,  "Breakfast Reruns",  "series", ("sitcom",)),
    Segment(9,  "Daytime",           "series", ("drama", "reality", "sitcom")),
    Segment(13, "Afternoon Stories", "series", ("drama", "documentary")),
    Segment(16, "Teatime Comedy",    "series", ("sitcom",)),
    Segment(19, "Primetime Drama",   "series", ("drama", "action_adventure")),
    Segment(22, "Late Show",         "series", ("sitcom",)),
    Segment(0,  "Graveyard Slot",    "series", ("drama", "documentary")),
)
_PRIME_WEEKEND = (
    Segment(7,  "Weekend Brunch",    "series", ("sitcom", "reality")),
    Segment(10, "Documentary Strand","series", ("documentary", "drama")),
    Segment(13, "Matinee Drama",     "series", ("drama", "action_adventure")),
    Segment(17, "Comedy Block",      "series", ("sitcom",)),
    Segment(19, "Saturday Primetime","series", ("drama", "action_adventure")),
    Segment(22, "Late Show",         "series", ("sitcom",)),
    Segment(0,  "Graveyard Slot",    "series", ("drama", "documentary")),
)

# ---- Maxx: action, adventure & sci-fi -------------------------------------
_MAXX_WEEKDAY = (
    Segment(6,  "Morning Maneuvers",  "series", ("action_adventure",)),
    Segment(9,  "Adventure Hour",     "series", ("action_adventure", "kids_cartoon")),
    Segment(12, "Sci-Fi Afternoon",   "series", ("action_adventure", "drama")),
    Segment(16, "After-School Action","series", ("action_adventure", "kids_cartoon")),
    Segment(19, "Primetime Action",   "series", ("action_adventure", "drama")),
    Segment(22, "Late Night Cult",    "series", ("action_adventure", "drama")),
    Segment(1,  "Graveyard Shift",    "series", ("action_adventure",)),
)
_MAXX_WEEKEND = (
    Segment(7,  "Saturday Adventure", "series", ("action_adventure", "kids_cartoon")),
    Segment(11, "Sci-Fi Marathon",    "series", ("action_adventure", "drama")),
    Segment(16, "Hero Block",         "series", ("action_adventure",)),
    Segment(19, "Primetime Action",   "series", ("action_adventure", "drama")),
    Segment(22, "Late Night Cult",    "series", ("action_adventure", "drama")),
    Segment(1,  "Graveyard Shift",    "series", ("action_adventure",)),
)

# ---- Chuckle: live-action comedy (adult animation lives on Swim) ----------
_CHUCKLE_WEEKDAY = (
    Segment(6,  "Wake-Up Comedy",      "series", ("sitcom",)),
    Segment(9,  "Sitcom Stack",        "series", ("sitcom",)),
    Segment(12, "Lunch Laughs",        "series", ("sitcom",)),
    Segment(15, "After-School Funnies","series", ("sitcom",)),
    Segment(18, "Dinnertime Comedy",   "series", ("sitcom",)),
    Segment(20, "Primetime Comedy",    "series", ("sitcom",)),
    Segment(22, "After Dark",          "series", ("sitcom",)),
    Segment(1,  "Graveyard Giggles",   "series", ("sitcom",)),
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

# ---- Nite Owl: after-dark cult & drama (adult animation lives on Swim) ----
_NITE_WEEKDAY = (
    Segment(8,  "Daytime Filler",    "series", ("documentary", "reality", "drama")),
    Segment(13, "Afternoon Oddities","series", ("drama", "action_adventure")),
    Segment(18, "Early Cult",        "series", ("action_adventure", "drama")),
    Segment(21, "Cult Primetime",    "series", ("drama", "action_adventure")),
    Segment(23, "After Dark",        "series", ("drama", "action_adventure")),
    Segment(2,  "Graveyard",         "series", ("drama",)),
)
_NITE_WEEKEND = (
    Segment(8,  "Weekend Docs",      "series", ("documentary", "reality")),
    Segment(13, "Cult Matinee",      "series", ("action_adventure", "drama")),
    Segment(18, "Cult Block",        "series", ("action_adventure", "drama")),
    Segment(21, "Cult Primetime",    "series", ("drama", "action_adventure")),
    Segment(23, "After Dark",        "series", ("drama", "action_adventure")),
    Segment(2,  "Graveyard",         "series", ("drama",)),
)

# ═══════════════════════════════════════════════════════════════════════════
#  EXTRA CHANNELS -- more film + comedy/animation options, plus a set of
#  old-school "+1" timeshift channels that replay a key channel one hour later.
# ═══════════════════════════════════════════════════════════════════════════

# ---- GanjFlix Gold: a second film channel leaning on classics/greats -------
_GREATS_WEEKDAY = (
    Segment(6,  "Dawn Classic",       "film", ("film",)),
    Segment(10, "Morning Matinee",    "film", ("film",)),
    Segment(14, "Afternoon Gold",     "film", ("film",)),
    Segment(18, "Teatime Feature",    "film", ("film",)),
    Segment(21, "Primetime Classic",  "film", ("film",)),
    Segment(0,  "Midnight Cult",      "film", ("film",)),
    Segment(3,  "Graveyard Reel",     "film", ("film",)),
)
_GREATS_WEEKEND = _GREATS_WEEKDAY

# ---- GanjFlix Premiere: a third film channel, staggered start times --------
_PREMIERE_WEEKDAY = (
    Segment(6,  "Early Feature",         "film", ("film",)),
    Segment(9,  "Morning Movie",         "film", ("film",)),
    Segment(12, "Lunchtime Film",        "film", ("film",)),
    Segment(15, "Afternoon Blockbuster", "film", ("film",)),
    Segment(18, "Teatime Feature",       "film", ("film",)),
    Segment(20, "Premiere Screen",       "film", ("film",)),
    Segment(23, "Late Premiere",         "film", ("film",)),
    Segment(2,  "Graveyard Feature",     "film", ("film",)),
)
_PREMIERE_WEEKEND = _PREMIERE_WEEKDAY

# ---- Tokyo Toke: anime around the clock (anime ONLY -- no soft fallbacks) -
_NIHON_WEEKDAY = (
    Segment(6,  "Morning Anime",      "series", ("anime",)),
    Segment(9,  "Shonen Block",       "series", ("anime",)),
    Segment(12, "Midday Mecha",       "series", ("anime",)),
    Segment(15, "After-School Anime", "series", ("anime",)),
    Segment(18, "Primetime Anime",    "series", ("anime",)),
    Segment(21, "Seinen Hour",        "series", ("anime",)),
    Segment(23, "Late Night Anime",   "series", ("anime",)),
    Segment(2,  "Graveyard OVA",      "series", ("anime",)),
)
_NIHON_WEEKEND = (
    Segment(7,  "Saturday Anime",      "series", ("anime",)),
    Segment(11, "Movie-Length Marathon","series", ("anime",)),
    Segment(16, "Shonen Block",        "series", ("anime",)),
    Segment(19, "Primetime Anime",     "series", ("anime",)),
    Segment(21, "Seinen Hour",         "series", ("anime",)),
    Segment(23, "Late Night Anime",    "series", ("anime",)),
    Segment(2,  "Graveyard OVA",       "series", ("anime",)),
)

# ---- Ganj's Stash: comedy repeats & cult sitcoms (no adult-animation) ------
_BANTER_WEEKDAY = (
    Segment(6,  "Repeat Offenders",   "series", ("sitcom",)),
    Segment(9,  "Morning Reruns",     "series", ("sitcom",)),
    Segment(12, "Lunchtime Laughs",   "series", ("sitcom",)),
    Segment(15, "Afternoon Comedy",   "series", ("sitcom",)),
    Segment(18, "Comedy Drive",       "series", ("sitcom",)),
    Segment(20, "Primetime Comedy",   "series", ("sitcom",)),
    Segment(22, "Cult Comedy",        "series", ("sitcom",)),
    Segment(1,  "Graveyard Giggles",  "series", ("sitcom",)),
)
_BANTER_WEEKEND = _BANTER_WEEKDAY

# ---- GanjaSwim: all-day adult animation (no sitcom/kids leakage) -----------
_SWIM_WEEKDAY = (
    Segment(6,  "Hair of the Dog",     "series", ("adult_animation",)),
    Segment(9,  "Morning Reruns",      "series", ("adult_animation",)),
    Segment(12, "Midday Mayhem",       "series", ("adult_animation",)),
    Segment(15, "After-School Special","series", ("adult_animation",)),
    Segment(18, "Dinnertime Toons",    "series", ("adult_animation",)),
    Segment(20, "Primetime Animation", "series", ("adult_animation",)),
    Segment(22, "The Late Shift",      "series", ("adult_animation",)),
    Segment(1,  "Graveyard Cartoons",  "series", ("adult_animation",)),
)
_SWIM_WEEKEND = _SWIM_WEEKDAY

# ---- Ganja Live: stand-up comedy specials round the clock ------------------
# Specials are films (kind='movie'); the 'standup' tag carves them out.
_STANDUP_WEEKDAY = (
    Segment(6,  "Breakfast Club", "film", ("film",), tag="standup"),
    Segment(10, "Morning Mic",    "film", ("film",), tag="standup"),
    Segment(13, "Matinee Set",    "film", ("film",), tag="standup"),
    Segment(16, "Happy Hour",     "film", ("film",), tag="standup"),
    Segment(19, "Headline Set",   "film", ("film",), tag="standup"),
    Segment(22, "Late Set",       "film", ("film",), tag="standup"),
    Segment(1,  "Last Call",      "film", ("film",), tag="standup"),
)
_STANDUP_WEEKEND = _STANDUP_WEEKDAY

# ---- Higher Learning: science & space factual ------------------------------
# Science-tagged series still claim this channel as their exclusive home.
# Series dayparts accept any documentary home'd here (so short nature/space
# strands aren't the only thing looping); film slots prefer the science tag
# and fall back to general features when that pool is tiny.
_SCIENCE_WEEKDAY = (
    Segment(6,  "Morning Briefing", "series", ("documentary",)),
    Segment(9,  "The Universe",     "series", ("documentary",)),
    Segment(12, "Midday Feature",   "film",   ("film",), tag="science"),
    Segment(15, "Space Hour",       "series", ("documentary",)),
    Segment(18, "Nature Block",     "series", ("documentary",)),
    Segment(20, "Feature Doc",      "film",   ("film",), tag="science"),
    Segment(22, "Deep Space",       "series", ("documentary",)),
    Segment(1,  "Graveyard Lab",    "film",   ("film",), tag="science"),
)
_SCIENCE_WEEKEND = _SCIENCE_WEEKDAY

# A channel that mirrors another needs no lineup of its own.
_NONE: tuple[Segment, ...] = ()


CHANNELS: tuple[Channel, ...] = (
    # ── flagship channels ─────────────────────────────────────────────────
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
    # ── extra film + comedy/animation channels ────────────────────────────
    Channel("greats", "GanjFlix Gold", "The classics, rolled out.", "#d4af37",
            _GREATS_WEEKDAY, _GREATS_WEEKEND, offset=6),
    Channel("premiere", "GanjFlix Premiere", "The big one, on the hour.", "#5ad1ff",
            _PREMIERE_WEEKDAY, _PREMIERE_WEEKEND, offset=7),
    Channel("swim", "GanjaSwim", "Cartoons for grown-ups.", "#c471ff",
            _SWIM_WEEKDAY, _SWIM_WEEKEND, offset=8),
    Channel("nihon", "Tokyo Toke", "Anime 'til your eyes go red.", "#ff2e9a",
            _NIHON_WEEKDAY, _NIHON_WEEKEND, offset=9),
    Channel("banter", "Ganj's Stash", "The good stuff, on repeat.", "#d98a3a",
            _BANTER_WEEKDAY, _BANTER_WEEKEND, offset=10),
    Channel("standup", "Ganja Live", "Stand-up, no sitting down.", "#ff8c42",
            _STANDUP_WEEKDAY, _STANDUP_WEEKEND, offset=11),
    Channel("science", "Higher Learning", "Science, space & higher learning.", "#6c5ce7",
            _SCIENCE_WEEKDAY, _SCIENCE_WEEKEND, offset=12),
    # ── +1 timeshift channels: replay a key channel one hour later ─────────
    Channel("cinema1", "GanjFlix +1", "The main feature, an hour behind.", "#8ab4ff",
            _NONE, _NONE, mirror_of="cinema", shift_hours=1),
    Channel("greats1", "GanjFlix Gold +1", "The classics, an hour behind.", "#d4af37",
            _NONE, _NONE, mirror_of="greats", shift_hours=1),
    Channel("prime1", "Prime +1", "Primetime, an hour behind.", "#ff5e8a",
            _NONE, _NONE, mirror_of="prime", shift_hours=1),
    Channel("chuckle1", "Chuckle +1", "The laughs, an hour behind.", "#ffd166",
            _NONE, _NONE, mirror_of="chuckle", shift_hours=1),
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
