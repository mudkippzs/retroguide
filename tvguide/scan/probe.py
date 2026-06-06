"""ffprobe wrapper to extract duration / codec / resolution."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class ProbeResult:
    duration_sec: float | None
    video_codec: str | None
    width: int | None
    height: int | None
    container: str | None
    ok: bool
    error: str | None = None


def probe(path: str, timeout: int = 60) -> ProbeResult:
    """Run ffprobe on a file. Tolerant of network hiccups / odd files."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(None, None, None, None, None, False, "timeout")
    except FileNotFoundError:
        return ProbeResult(None, None, None, None, None, False, "ffprobe-missing")

    if out.returncode != 0 or not out.stdout.strip():
        return ProbeResult(None, None, None, None, None, False, out.stderr[:200] or "probe-failed")

    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        return ProbeResult(None, None, None, None, None, False, f"json:{exc}")

    duration = None
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    container = fmt.get("format_name")

    vcodec = width = height = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            vcodec = stream.get("codec_name")
            width = stream.get("width")
            height = stream.get("height")
            if duration is None and stream.get("duration"):
                try:
                    duration = float(stream["duration"])
                except (TypeError, ValueError):
                    pass
            break

    return ProbeResult(duration, vcodec, width, height, container, True)
