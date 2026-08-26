"""Minimal TMDB v3 client for enrichment. Fails soft when offline/no key."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"


@dataclass
class TitleMeta:
    tmdb_id: int | None = None
    overview: str | None = None
    genres: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    rating: float | None = None
    poster_url: str | None = None
    runtime: int | None = None       # minutes (movie)
    name: str | None = None
    year: int | None = None
    origin: str | None = None        # ISO country / language hint (e.g. 'JP')


class TMDBClient:
    def __init__(self, api_key: str, language: str = "en-US"):
        self.api_key = api_key
        self.language = language
        self.session = requests.Session()
        self._last = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params) -> dict | None:
        if not self.api_key:
            return None
        params["api_key"] = self.api_key
        params.setdefault("language", self.language)
        # Gentle rate limit.
        dt = time.time() - self._last
        if dt < 0.05:
            time.sleep(0.05 - dt)
        try:
            r = self.session.get(f"{BASE}{path}", params=params, timeout=15)
            self._last = time.time()
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            return None
        return None

    @staticmethod
    def _best(results: list[dict], name: str, name_key: str) -> dict:
        """Pick the best search hit, not just the first.

        TMDB's first result can be a same-named remake/revival (e.g. the
        2026 "Scrubs" outranks the 2001 original on recency). Prefer the
        closest name match; break ties by popularity so the well-known
        original wins over a same-named newcomer. Uses plain ratio, not
        token_set_ratio: a subset title ("Dungeons and Dragons") must not
        score 100 against a longer unrelated hit ("Aunty Donna's Dungeons
        and Dragons").
        """
        from rapidfuzz import fuzz

        def score(r: dict) -> tuple:
            sim = fuzz.ratio(
                (name or "").lower(), (r.get(name_key) or "").lower())
            return (sim, r.get("popularity") or 0.0, r.get("vote_count") or 0)
        return max(results, key=score)

    # -- movies -------------------------------------------------------------
    def movie(self, name: str, year: int | None) -> TitleMeta | None:
        params = {"query": name}
        if year:
            params["year"] = year
        res = self._get("/search/movie", **params)
        if not res or not res.get("results"):
            return None
        m = self._best(res["results"][:8], name, "title")
        meta = TitleMeta(
            tmdb_id=m.get("id"),
            overview=m.get("overview") or None,
            rating=m.get("vote_average"),
            poster_url=IMG + m["poster_path"] if m.get("poster_path") else None,
            name=m.get("title"),
            year=_year(m.get("release_date")),
        )
        details = self._get(f"/movie/{meta.tmdb_id}", append_to_response="credits")
        if details:
            meta.genres = [g["name"] for g in details.get("genres", [])]
            meta.runtime = details.get("runtime") or None
            cast = details.get("credits", {}).get("cast", [])
            meta.cast = [c["name"] for c in cast[:4]]
        return meta

    # -- series -------------------------------------------------------------
    def tv(self, name: str, year: int | None) -> TitleMeta | None:
        params = {"query": name}
        if year:
            params["first_air_date_year"] = year
        res = self._get("/search/tv", **params)
        if not res or not res.get("results"):
            return None
        s = self._best(res["results"][:8], name, "name")
        meta = TitleMeta(
            tmdb_id=s.get("id"),
            overview=s.get("overview") or None,
            rating=s.get("vote_average"),
            poster_url=IMG + s["poster_path"] if s.get("poster_path") else None,
            name=s.get("name"),
            year=_year(s.get("first_air_date")),
            origin=(s.get("origin_country") or [None])[0],
        )
        details = self._get(f"/tv/{meta.tmdb_id}", append_to_response="credits")
        if details:
            meta.genres = [g["name"] for g in details.get("genres", [])]
            if not meta.origin:
                countries = details.get("origin_country") or []
                meta.origin = countries[0] if countries else None
            if not meta.origin:
                meta.origin = (details.get("original_language") or "").upper() or None
            cast = details.get("credits", {}).get("cast", [])
            meta.cast = [c["name"] for c in cast[:4]]
        return meta

    def tv_season(self, tmdb_id: int, season: int) -> dict[int, dict]:
        """Return {episode_number: {name, overview}} for a season."""
        res = self._get(f"/tv/{tmdb_id}/season/{season}")
        out: dict[int, dict] = {}
        if not res:
            return out
        for ep in res.get("episodes", []):
            out[ep["episode_number"]] = {
                "name": ep.get("name"),
                "overview": ep.get("overview") or None,
            }
        return out


def _year(date_str: str | None) -> int | None:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None
