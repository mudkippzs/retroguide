"""ROAMER stream-server API: health, catalog, queue, watch positions."""
from __future__ import annotations

import json
import urllib.request

import pytest

from tvguide.config import Config
from tvguide.db import connect as db_connect, init_db
from tvguide.stream import server as srv
from helpers import add_movie, add_series


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live StreamServer wired to a temp DB and temp VOD cache."""
    db = tmp_path / "test.db"
    conn = db_connect(db)
    init_db(conn)
    conn.close()
    monkeypatch.setattr(srv, "connect", lambda: db_connect(db))
    cfg = Config()
    cfg.stream.vod_dir = str(tmp_path / "vodcache")
    cfg.stream.port = 0
    server = srv.StreamServer(cfg, port=0)
    # StreamServer stores port before bind; grab the real one after start.
    server._httpd = None
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server.port = port
    server.start()
    yield f"http://127.0.0.1:{port}", db
    server.stop()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_health(api):
    base, _ = api
    j = _get(base, "/api/health")
    assert j["ok"] and j["active_streams"] == 0 and "server_now" in j


def test_catalog(api, media_root):
    base, db = api
    conn = db_connect(db)
    add_series(conn, media_root, "Show A", "sitcom", 2)
    add_movie(conn, media_root, "Film A")
    conn.close()
    j = _get(base, "/api/catalog")
    assert [s["name"] for s in j["series"]] == ["Show A"]
    assert j["movies"][0]["name"] == "Film A"
    tid = j["series"][0]["id"]
    d = _get(base, f"/api/catalog/series/{tid}")
    assert d["name"] == "Show A" and len(d["seasons"][0]["episodes"]) == 2


def test_queue_flow(api, media_root):
    base, db = api
    conn = db_connect(db)
    tid = add_series(conn, media_root, "Show A", "sitcom", 3)
    mid = add_movie(conn, media_root, "Film A")
    conn.close()

    assert _post(base, "/api/queue/add", {"kind": "series", "title_id": tid})["ok"]
    assert _post(base, "/api/queue/add", {"kind": "film", "title_id": mid})["ok"]
    q = _get(base, "/api/queue")
    assert len(q["items"]) == 2
    # Series + film expand to 3 episodes + 1 film, keys in ROAMER form.
    keys = [e["key"] for e in q["expanded"]]
    assert len(keys) == 4 and keys[-1] == f"film:{mid}"
    assert all(k.startswith(("ep:", "film:")) for k in keys)

    nxt = _post(base, "/api/queue/next",
                {"played": [0, 1], "random": False, "repeat": False})
    assert nxt["index"] == 2 and nxt["item"]["key"] == keys[2]
    done = _post(base, "/api/queue/next",
                 {"played": [0, 1, 2, 3], "random": False, "repeat": False})
    assert done["index"] is None and done["item"] is None
    loop = _post(base, "/api/queue/next",
                 {"played": [0, 1, 2, 3], "random": False, "repeat": True})
    assert loop["index"] == 0

    assert _post(base, "/api/queue/save", {"name": "airport"})["ok"]
    assert _get(base, "/api/queue/saved")["saved"][0]["name"] == "airport"
    assert _post(base, "/api/queue/clear", {})["ok"]
    assert _get(base, "/api/queue")["items"] == []
    assert _post(base, "/api/queue/load", {"name": "airport"})["ok"]
    assert len(_get(base, "/api/queue")["items"]) == 2
    assert _post(base, "/api/queue/shuffle", {})["ok"]
    assert _post(base, "/api/queue/delete", {"name": "airport"})["ok"]
    assert _get(base, "/api/queue/saved")["saved"] == []


def test_watch_positions(api):
    base, _ = api
    j = _get(base, "/api/watch/ep:5")
    assert j["position"] is None
    assert _post(base, "/api/watch",
                 {"key": "ep:5", "position": 120.0, "duration": 1300.0})["ok"]
    j = _get(base, "/api/watch/ep:5")
    assert j["position"] == 120.0 and j["duration"] == 1300.0
    # Keys are validated, never pathed.
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "/api/watch", {"key": "../x", "position": 1, "duration": 2})
    assert exc.value.code == 400


def test_vod_play_unknown_and_gone(api):
    base, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "/api/vod/play", {"episode_id": 424242})
    assert exc.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/v/ep:424242/index.m3u8")
    assert exc.value.code == 404
