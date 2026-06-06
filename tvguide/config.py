"""Configuration loading and app data paths."""
from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "RetroGuide"
DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))
DB_PATH = DATA_DIR / "retroguide.db"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.toml"
EXAMPLE_CONFIG = PROJECT_ROOT / "config.example.toml"


@dataclass
class LibraryConfig:
    tv_roots: list[str] = field(default_factory=lambda: ["/mnt/tv"])
    movie_roots: list[str] = field(default_factory=lambda: ["/mnt/movies"])
    video_extensions: list[str] = field(
        default_factory=lambda: [
            ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm",
            ".ts", ".m2ts", ".wmv", ".mpg", ".mpeg",
        ]
    )
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "sample", "trailer", "$RECYCLE.BIN", ".parts",
            "featurette", "behind the scenes", "extras",
        ]
    )
    min_file_mb: int = 50


@dataclass
class OllamaConfig:
    host: str = "http://127.0.0.1:11434"
    model: str = "mistral-small3.2:latest"
    embed_model: str = "nomic-embed-text:latest"
    timeout: int = 120


@dataclass
class TmdbConfig:
    api_key: str = ""
    language: str = "en-US"


@dataclass
class ScheduleConfig:
    start: str = "today"
    days: int = 7
    day_start_hour: int = 6
    timezone: str = "local"
    # Seasonal / spontaneous special-event programming (Christmas movie
    # marathons, a May-4th Star Wars bonanza, etc.). Takes over a fitting
    # channel for the day; off-season it stays out of the way.
    events: bool = True


@dataclass
class UiConfig:
    theme: str = "dark-arc"
    default_view: str = "magazine"
    # Retro look. era: "70s" | "80s" | "90s" | "00s". crt = scanline/CRT
    # overlay. bw desaturates the *picture only* (simulating a B&W set).
    era: str = "90s"
    crt: bool = True
    bw: bool = False


@dataclass
class StreamConfig:
    port: int = 8722
    # Transcode to broadly-compatible H.264/AAC for the browser. Turn off to
    # remux (copy) when your files are already H.264 -- much lighter on CPU.
    transcode: bool = True
    bind: str = "0.0.0.0"
    # Cap on simultaneous ffmpeg transcodes. Over this, new tune-ins get a
    # short 503 so a misbehaving client can't storm the host into a lockup.
    max_streams: int = 4
    # Serve HLS to Apple devices (iOS/macOS Safari can't play the progressive
    # MP4 stream). Other browsers keep using the lower-latency MP4 path.
    hls: bool = True


@dataclass
class Config:
    library: LibraryConfig = field(default_factory=LibraryConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    tmdb: TmdbConfig = field(default_factory=TmdbConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    # Per-channel overrides keyed by channel slug. Empty = use the built-in
    # default name / no logo. Logos are absolute paths to an image file.
    channel_names: dict[str, str] = field(default_factory=dict)
    channel_logos: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_CONFIG
        data: dict = {}
        if path.exists():
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        return cls(
            library=LibraryConfig(**data.get("library", {})),
            ollama=OllamaConfig(**data.get("ollama", {})),
            tmdb=TmdbConfig(**data.get("tmdb", {})),
            schedule=ScheduleConfig(**data.get("schedule", {})),
            ui=UiConfig(**data.get("ui", {})),
            stream=StreamConfig(**data.get("stream", {})),
            channel_names=dict(data.get("channels", {})),
            channel_logos=dict(data.get("channel_logos", {})),
        )

    def save(self, path: Path | None = None) -> Path:
        """Write the config back to TOML (stdlib has no writer, so emit it)."""
        path = path or DEFAULT_CONFIG
        sections = {
            "library": asdict(self.library),
            "ollama": asdict(self.ollama),
            "tmdb": asdict(self.tmdb),
            "schedule": asdict(self.schedule),
            "ui": asdict(self.ui),
            "stream": asdict(self.stream),
            "channels": dict(self.channel_names),
            "channel_logos": dict(self.channel_logos),
        }
        lines: list[str] = []
        for name, values in sections.items():
            lines.append(f"[{name}]")
            for key, val in values.items():
                lines.append(f"{key} = {_toml_value(val)}")
            lines.append("")
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return path


def _toml_value(val: object) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return "[" + ", ".join(_toml_value(v) for v in val) + "]"
    return '"' + str(val).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
