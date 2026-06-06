"""Local LLM (Ollama) helpers: classification + retro-guide blurb writing."""
from __future__ import annotations

import json
import re

import ollama

from ..util.naming import BUCKETS

_BLURB_SYSTEM = (
    "You are the listings editor for a retro television guide, writing in the "
    "punchy, evocative style of a 1990s TV Guide magazine. Write vivid, "
    "SPOILER-FREE teasers. Never reveal twists, deaths, or endings. "
    "Be concise and atmospheric. Output ONLY the blurb text, no labels."
)

_CLASSIFY_SYSTEM = (
    "You classify TV shows into exactly one scheduling bucket. "
    "Respond with ONLY the bucket id, nothing else."
)


class LLM:
    def __init__(self, host: str, model: str, timeout: int = 120):
        self.model = model
        self.timeout = timeout
        self.client = ollama.Client(host=host, timeout=timeout)
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                self.client.list()
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def _chat(self, system: str, user: str, num_predict: int = 160) -> str | None:
        try:
            resp = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                options={"temperature": 0.7, "num_predict": num_predict},
            )
            return (resp.get("message", {}) or {}).get("content", "").strip()
        except Exception:
            return None

    # -- classification -----------------------------------------------------
    def classify(self, name: str, overview: str | None, genres: list[str]) -> str | None:
        prompt = (
            f"Buckets: {', '.join(BUCKETS)}.\n"
            "Definitions: preschool=very young kids; kids_cartoon=children's "
            "animation; anime=Japanese animation; adult_animation=animated "
            "comedy for adults (e.g. Family Guy, Futurama); action_adventure="
            "live-action action/sci-fi/fantasy; sitcom=live-action comedy; "
            "drama=serious live-action; documentary; reality; film; other.\n\n"
            f"Show: {name}\n"
            f"Genres: {', '.join(genres) or 'unknown'}\n"
            f"Overview: {(overview or 'unknown')[:400]}\n\n"
            "Bucket id:"
        )
        out = self._chat(_CLASSIFY_SYSTEM, prompt, num_predict=8)
        if not out:
            return None
        out = re.split(r"[^a-z_]", out.strip().lower())[0]
        return out if out in BUCKETS else None

    # -- blurbs -------------------------------------------------------------
    def movie_blurb(self, title: str, year, cast: list[str], overview: str | None) -> str | None:
        prompt = (
            f"Film: {title}" + (f" ({year})" if year else "") + "\n"
            f"Starring: {', '.join(cast) or 'unknown'}\n"
            f"Plot: {(overview or 'unknown')[:500]}\n\n"
            "Write a 2-sentence TV-guide blurb. Lead with the star names, then a "
            "tantalising, spoiler-free plot teaser."
        )
        return self._chat(_BLURB_SYSTEM, prompt, num_predict=120)

    def episode_blurb(self, show: str, ep_name: str | None, overview: str | None) -> str | None:
        prompt = (
            f"Series: {show}\n"
            f"Episode: {ep_name or 'unknown'}\n"
            f"Synopsis: {(overview or 'unknown')[:500]}\n\n"
            "Write a 2-3 sentence spoiler-free teaser for tonight's episode."
        )
        return self._chat(_BLURB_SYSTEM, prompt, num_predict=130)
