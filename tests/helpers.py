"""Catalog builders shared by scheduler / enricher tests."""
from __future__ import annotations

import json
from pathlib import Path


def _add_file(conn, root: Path, name: str, *, duration=1320.0,
              probe_state="done", codec="h264") -> int:
    path = root / name
    path.write_bytes(b"0" * 2048)
    cur = conn.execute(
        "INSERT INTO media_files(path,root,size,mtime,duration_sec,"
        "video_codec,width,height,container,probe_state,scanned_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (str(path), "tv", path.stat().st_size, path.stat().st_mtime,
         duration, codec, 1280, 720, "matroska", probe_state, 0),
    )
    return int(cur.lastrowid)


def add_series(conn, root: Path, name: str, bucket: str, n_eps: int,
               *, season: int = 1, start_ep: int = 1,
               seasons: int | None = None, eps_per_season: int | None = None,
               playhead: int = 0, tags: list[str] | None = None,
               duration: float = 1320.0) -> int:
    """Insert a playable series.

    By default creates ``n_eps`` episodes in ``season`` starting at ``start_ep``.
    Pass ``seasons`` + ``eps_per_season`` for a multi-season catalog (overrides
    ``n_eps``). ``playhead`` is the abs_order cursor stored in playheads.
    """
    cur = conn.execute(
        "INSERT INTO titles(kind,name,year,sort_name,bucket,tags,enrich_state) "
        "VALUES('series',?,?,?,?,?, 'done')",
        (name, 2005, name.lower(), bucket, json.dumps(tags or [])),
    )
    tid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO playheads(title_id, next_order) VALUES(?, ?)",
        (tid, playhead),
    )
    order = 0
    if seasons is not None:
        per = eps_per_season or max(1, n_eps)
        for s in range(1, seasons + 1):
            for ep in range(1, per + 1):
                fid = _add_file(conn, root, f"{name}.S{s:02d}E{ep:02d}.mkv",
                                duration=duration)
                conn.execute(
                    "INSERT INTO episodes(title_id,file_id,season,episode,"
                    "abs_order,name,duration_sec) VALUES(?,?,?,?,?,?,?)",
                    (tid, fid, s, ep, order, f"S{s}E{ep}", duration),
                )
                order += 1
    else:
        for i in range(n_eps):
            ep = start_ep + i
            fid = _add_file(conn, root, f"{name}.S{season:02d}E{ep:02d}.mkv",
                            duration=duration)
            conn.execute(
                "INSERT INTO episodes(title_id,file_id,season,episode,abs_order,"
                "name,duration_sec) VALUES(?,?,?,?,?,?,?)",
                (tid, fid, season, ep, order, f"Ep {ep}", duration),
            )
            order += 1
    conn.commit()
    return tid


def add_movie(conn, root: Path, name: str, *, year=2008, duration=6000.0,
              probe_state="done", overview=None, cast=None, tags=None,
              rating=7.0) -> int:
    fid = _add_file(conn, root, f"{name}.{year}.mkv", duration=duration,
                    probe_state=probe_state)
    if probe_state != "done":
        conn.execute(
            "UPDATE media_files SET duration_sec=NULL, video_codec=NULL "
            "WHERE id=?", (fid,))
    cur = conn.execute(
        "INSERT INTO titles(kind,name,year,sort_name,bucket,movie_file_id,"
        "overview,cast_json,tags,rating,enrich_state,runtime_hint) "
        "VALUES('movie',?,?,?,'film',?,?,?,?,?, 'done',?)",
        (name, year, name.lower(), fid if probe_state == "done" else None,
         overview, json.dumps(cast or []), json.dumps(tags or []), rating,
         int(duration // 60)),
    )
    conn.commit()
    return int(cur.lastrowid)
