"""Shared subprocess helpers for the stream server (MP4 + HLS paths)."""
from __future__ import annotations

import logging
import os
import signal
import subprocess

log = logging.getLogger("tvguide.stream")


def kill_proc(proc: subprocess.Popen) -> None:
    """Kill an ffmpeg and its whole process group, then reap it.

    ffmpeg is spawned with ``start_new_session=True`` so it leads its own
    process group; killing the group guarantees no orphaned children.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg pid=%s did not exit after SIGKILL", proc.pid)
    except Exception:  # noqa: BLE001
        pass
