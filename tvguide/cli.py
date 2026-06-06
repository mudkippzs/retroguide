"""Command-line entry point for headless catalog/schedule operations."""
from __future__ import annotations

import argparse
import sys
import time

from .config import Config, DB_PATH
from .db import connect, init_db


def _progress(msg: str, cur: int, total: int) -> None:
    if total:
        pct = int(cur / total * 100)
        sys.stdout.write(f"\r  [{pct:3d}%] {cur}/{total}  {msg:<60}")
    else:
        sys.stdout.write(f"\r  {msg:<70}")
    sys.stdout.flush()
    if total and cur >= total:
        sys.stdout.write("\n")


def cmd_scan(args, cfg, conn):
    from .scan.indexer import Indexer
    t0 = time.time()
    stats = Indexer(conn, cfg).scan(_progress)
    print(f"\nScan done in {time.time()-t0:.1f}s: {stats}")


def cmd_probe(args, cfg, conn):
    from .scan.indexer import Indexer
    t0 = time.time()
    n = Indexer(conn, cfg).probe_pending(_progress, limit=args.limit)
    print(f"\nProbed {n} files in {time.time()-t0:.1f}s")


def cmd_reindex(args, cfg, conn):
    from .scan.indexer import Indexer
    t0 = time.time()
    stats = Indexer(conn, cfg).reindex_from_db(_progress)
    print(f"\nReindex done in {time.time()-t0:.1f}s: {stats}")


def cmd_enrich(args, cfg, conn):
    from .enrich.enricher import Enricher
    t0 = time.time()
    n = Enricher(conn, cfg).run(_progress, limit=args.limit, kind=args.kind)
    print(f"\nEnriched {n} titles in {time.time()-t0:.1f}s")


def cmd_schedule(args, cfg, conn):
    from .schedule.scheduler import Scheduler
    t0 = time.time()
    n = Scheduler(conn, cfg).build(_progress)
    print(f"\nScheduled {n} programs in {time.time()-t0:.1f}s")


def cmd_tag(args, cfg, conn):
    from .enrich.tagging import backfill
    t0 = time.time()
    n = backfill(conn, _progress)
    print(f"\nTagged {n} titles (franchise + holiday/mood) in {time.time()-t0:.1f}s")


def cmd_blurbs(args, cfg, conn):
    from .enrich.enricher import Enricher
    t0 = time.time()
    n = Enricher(conn, cfg).write_program_blurbs(_progress)
    print(f"\nWrote blurbs for {n} programs in {time.time()-t0:.1f}s")


def cmd_serve(args, cfg, conn):
    import time as _time

    from .stream import StreamServer
    port = args.port or cfg.stream.port
    server = StreamServer(cfg, port=port)
    url = server.start()
    print(f"RetroGuide streaming on {url}")
    print("Open that address from any device on your LAN. Ctrl-C to stop.")
    try:
        while True:
            _time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping...")
        server.stop()


def cmd_stats(args, cfg, conn):
    def one(q):
        return conn.execute(q).fetchone()[0]
    n_files = one("SELECT COUNT(*) FROM media_files")
    n_probed = one("SELECT COUNT(*) FROM media_files WHERE probe_state='done'")
    n_series = one("SELECT COUNT(*) FROM titles WHERE kind='series'")
    n_movies = one("SELECT COUNT(*) FROM titles WHERE kind='movie'")
    n_eps = one("SELECT COUNT(*) FROM episodes")
    n_enriched = one("SELECT COUNT(*) FROM titles WHERE enriched_at IS NOT NULL")
    n_programs = one("SELECT COUNT(*) FROM programs")
    print(f"Database: {DB_PATH}")
    print(f"  media files    : {n_files}")
    print(f"  probed         : {n_probed}")
    print(f"  series         : {n_series}")
    print(f"  movies         : {n_movies}")
    print(f"  episodes       : {n_eps}")
    print(f"  enriched titles: {n_enriched}")
    print(f"  programs       : {n_programs}")
    print("  buckets:")
    for row in conn.execute(
        "SELECT bucket, COUNT(*) c FROM titles GROUP BY bucket ORDER BY c DESC"
    ):
        print(f"     {row['bucket'] or '(none)':<18} {row['c']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="retroguide", description="RetroGuide CLI")
    p.add_argument("--config", default=None, help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="walk library roots and build the catalog")

    sp = sub.add_parser("probe", help="ffprobe pending files (durations/codecs)")
    sp.add_argument("--limit", type=int, default=None)

    sub.add_parser("reindex", help="rebuild titles/episodes from cached files (fast)")

    se = sub.add_parser("enrich", help="fetch metadata + write retro blurbs")
    se.add_argument("--limit", type=int, default=None)
    se.add_argument("--kind", choices=["series", "movie", "all"], default="all")

    sub.add_parser("tag", help="tag franchises + holidays for special events")
    sub.add_parser("schedule", help="build the 7-day programming grid")
    sub.add_parser("blurbs", help="write retro-voice blurbs for scheduled programs")
    sub.add_parser("stats", help="show catalog statistics")

    sv = sub.add_parser("serve", help="stream the live channels to your LAN browser")
    sv.add_argument("--port", type=int, default=None)

    args = p.parse_args(argv)
    from .logsetup import setup_logging
    setup_logging(args.cmd or "cli")
    cfg = Config.load(args.config)
    conn = connect()
    init_db(conn)

    dispatch = {
        "scan": cmd_scan, "probe": cmd_probe, "reindex": cmd_reindex,
        "enrich": cmd_enrich, "tag": cmd_tag, "schedule": cmd_schedule,
        "blurbs": cmd_blurbs, "stats": cmd_stats, "serve": cmd_serve,
    }
    dispatch[args.cmd](args, cfg, conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
