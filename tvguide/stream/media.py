"""Shared media-probing helpers for the stream server (MP4 + HLS paths)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading

from ..util.audio_norm import AUDIO_NORM

log = logging.getLogger("tvguide.stream")

# Scale filter shared by every transcode path (cap height at 720, keep AR, even
# dims for H.264). The "\\," escapes the comma inside min() for the filtergraph.
SCALE = "scale=-2:min(720\\,ih)"
_VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-profile:v", "main", "-pix_fmt", "yuv420p",
         "-g", "48", "-keyint_min", "48", "-sc_threshold", "0"]
_AENC = ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]

_TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "eia_608"}
_BITMAP_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle",
                "pgssub", "dvbsub", "xsub", "vobsub"}

_audio_map_cache: dict[str, str] = {}
_audio_map_lock = threading.Lock()
_sub_cache: dict[str, tuple[str | None, int]] = {}
_sub_lock = threading.Lock()


def english_audio_map(path: str) -> str:
    """Return the ffmpeg map spec (``0:a:N``) for an English audio stream when
    one is tagged, else the first audio stream. Cached per path so we don't run
    an ffprobe on every tune-in. Used by both the MP4 and HLS stream paths so
    English audio is the default everywhere (matching the desktop app's
    ``alang=eng``)."""
    with _audio_map_lock:
        cached = _audio_map_cache.get(path)
    if cached is not None:
        return cached

    result = "0:a:0"
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index:stream_tags=language",
             "-of", "json", path],
            capture_output=True, text=True, timeout=15)
        streams = json.loads(out.stdout or "{}").get("streams", [])
        for i, s in enumerate(streams):
            lang = ((s.get("tags") or {}).get("language") or "").lower()
            if lang in ("eng", "en", "english"):
                result = f"0:a:{i}"
                break
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe audio map failed for %s: %s", path, exc)
    with _audio_map_lock:
        _audio_map_cache[path] = result
    return result


def english_subtitle(path: str) -> tuple[str | None, int]:
    """Return ``(kind, si)`` for the English subtitle to burn in, where ``kind``
    is ``'text'`` (rendered via libass) or ``'bitmap'`` (overlaid), and ``si`` is
    the index *among subtitle streams* (0-based). Prefers an English-tagged
    track, falling back to the first usable subtitle. Returns ``(None, -1)`` if
    there's no usable subtitle. Cached per path."""
    with _sub_lock:
        if path in _sub_cache:
            return _sub_cache[path]

    result: tuple[str | None, int] = (None, -1)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=codec_name:stream_tags=language",
             "-of", "json", path],
            capture_output=True, text=True, timeout=15)
        streams = json.loads(out.stdout or "{}").get("streams", [])
        first: tuple[str | None, int] = (None, -1)
        for si, s in enumerate(streams):
            codec = (s.get("codec_name") or "").lower()
            kind = ("text" if codec in _TEXT_SUBS
                    else "bitmap" if codec in _BITMAP_SUBS else None)
            if kind is None:
                continue
            if first[0] is None:
                first = (kind, si)
            lang = ((s.get("tags") or {}).get("language") or "").lower()
            if lang in ("eng", "en", "english"):
                result = (kind, si)
                break
        else:
            result = first
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe subtitle probe failed for %s: %s", path, exc)
    with _sub_lock:
        _sub_cache[path] = result
    return result


def _sub_symlink(path: str, tmpdir: str) -> str:
    """Symlink the media file into ``tmpdir`` under a special-char-free name so
    the libass ``subtitles=`` filter never has to escape ``: , [ ]`` etc. in the
    real path. Falls back to the raw path if the symlink can't be made."""
    link = os.path.join(tmpdir, "subsrc" + os.path.splitext(path)[1])
    try:
        if not os.path.exists(link):
            os.symlink(path, link)
        return link
    except OSError:
        return path


def _af_args(normalize_audio: bool) -> list[str]:
    return ["-af", AUDIO_NORM] if normalize_audio else []


def encode_chain(path: str, transcode: bool, subs: bool,
                 tmpdir: str | None,
                 *, normalize_audio: bool = True) -> tuple[list[str], list[str]]:
    """Build the shared ffmpeg argument groups used by both the MP4 and HLS
    paths: ``(pre_input, post_input)`` where ``pre_input`` goes *before* ``-i``
    (e.g. ``-copyts``) and ``post_input`` covers maps + filters + codecs (after
    ``-i``, before the output muxer).

    With ``subs`` off this reproduces the original behaviour exactly. With
    ``subs`` on it burns the English subtitle into the picture (forcing a
    transcode); ``-copyts`` keeps subs in sync under input seeking.

    ``normalize_audio`` runs a dynamic loudness filter so rip volumes don't
    jump between programs/channels.
    """
    amap = english_audio_map(path)
    kind, si = english_subtitle(path) if (subs and tmpdir) else (None, -1)
    af = _af_args(normalize_audio)

    if kind == "text":
        link = _sub_symlink(path, tmpdir)
        return (["-copyts"],
                ["-map", "0:v:0", "-map", amap + "?", "-sn",
                 "-vf", f"{SCALE},subtitles={link}:si={si}",
                 *_VENC, *af, *_AENC])
    if kind == "bitmap":
        # Audio must join the filter_complex when we normalize (can't mix
        # -filter_complex video with a separate -af on some ffmpeg builds
        # when maps point at labeled pads).
        if normalize_audio:
            fc = (f"[0:v:0]{SCALE}[vb];[vb][0:s:{si}]overlay[v];"
                  f"[{amap}]{AUDIO_NORM}[a]")
            return (["-copyts"],
                    ["-filter_complex", fc,
                     "-map", "[v]", "-map", "[a]", "-sn", *_VENC, *_AENC])
        return (["-copyts"],
                ["-filter_complex",
                 f"[0:v:0]{SCALE}[vb];[vb][0:s:{si}]overlay[v]",
                 "-map", "[v]", "-map", amap + "?", "-sn", *_VENC, *_AENC])

    # No subtitles: original behaviour (+ optional loudness norm).
    post = ["-map", "0:v:0", "-map", amap + "?", "-sn"]
    if transcode:
        post += ["-vf", SCALE, *_VENC, *af, *_AENC]
    else:
        post += ["-c:v", "copy", *af, *_AENC]
    return ([], post)
