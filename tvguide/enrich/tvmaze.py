"""TVmaze client - a completely free, no-key, no-billing TV metadata source.

Great for our needs: per-show genres + a `type` field (Animation/Scripted/
Reality...) and full per-episode names and summaries. Movies are not covered
by TVmaze; those fall back to the local LLM (or TMDB if a key is configured).
"""
from __future__ import annotations

import re
import time

import requests

from .tmdb import TitleMeta

BASE = "https://api.tvmaze.com"
_TAGS = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return _TAGS.sub("", text).replace("&amp;", "&").strip() or None


class TVMazeClient:
    enabled = True  # never needs a key

    def __init__(self):
        self.session = requests.Session()
        self._last = 0.0

    def _get(self, path: str, **params):
        # TVmaze asks for <=20 req/s; keep a gentle gap.
        dt = time.time() - self._last
        if dt < 0.06:
            time.sleep(0.06 - dt)
        try:
            r = self.session.get(f"{BASE}{path}", params=params, timeout=15)
            self._last = time.time()
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            return None
        return None

    def tv(self, name: str, year: int | None) -> TitleMeta | None:
        results = self._get("/search/shows", q=name)
        if not results:
            return None
        show = self._pick(results, year)
        if not show:
            return None
        genres = list(show.get("genres") or [])
        # Fold the show "type" in as a pseudo-genre so the classifier can use it.
        stype = show.get("type")
        if stype in ("Animation", "Reality", "Documentary"):
            genres = genres + [stype]
        image = show.get("image") or {}
        return TitleMeta(
            tmdb_id=show.get("id"),            # external provider id (TVmaze)
            overview=_strip_html(show.get("summary")),
            genres=genres,
            cast=[],
            rating=(show.get("rating") or {}).get("average"),
            poster_url=image.get("medium") or image.get("original"),
            runtime=show.get("averageRuntime") or show.get("runtime"),
            name=show.get("name"),
            year=_premiered_year(show.get("premiered")),
        )

    def _pick(self, results: list[dict], year: int | None) -> dict | None:
        """Pick the best show match, disambiguating by year when we know it."""
        shows = [r.get("show", r) for r in results]
        if year:
            dated = [(abs((_premiered_year(s.get("premiered")) or 9999) - year), s)
                     for s in shows]
            dated.sort(key=lambda t: t[0])
            if dated and dated[0][0] <= 2:
                return dated[0][1]
        return shows[0] if shows else None

    def tv_season_all(self, show_id: int) -> dict[tuple[int, int], dict]:
        """Return {(season, episode): {name, overview}} for the whole show."""
        eps = self._get(f"/shows/{show_id}/episodes")
        out: dict[tuple[int, int], dict] = {}
        if not eps:
            return out
        for e in eps:
            s, n = e.get("season"), e.get("number")
            if s is None or n is None:
                continue
            out[(s, n)] = {"name": e.get("name"),
                           "overview": _strip_html(e.get("summary"))}
        return out


def _premiered_year(date_str: str | None) -> int | None:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None
