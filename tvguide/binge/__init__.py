"""Binge-mode: user-built playlists of catalog content, played locally."""
from __future__ import annotations

from .playlist import (
    QueueItem,
    add,
    clear,
    delete_saved,
    ensure_queue,
    expand,
    item_stats,
    items,
    list_saved,
    load,
    move,
    next_index,
    remove,
    save_as,
    shuffle,
    theme_random,
)

__all__ = [
    "QueueItem",
    "add",
    "clear",
    "delete_saved",
    "ensure_queue",
    "expand",
    "item_stats",
    "items",
    "list_saved",
    "load",
    "move",
    "next_index",
    "remove",
    "save_as",
    "shuffle",
    "theme_random",
]
