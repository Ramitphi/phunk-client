"""phunk.social: the web client's read API as a Vercel Python function.

The page (index.html, app.js, app.css) is static; vercel.json sends every
/api/* request here. It answers the same read endpoints as `phunk ui`,
from one shared `UI` in public mode: no account, no key, nothing to post
with. Follow, reply and like open Farcaster in the page instead.

    GET /api/me
    GET /api/people?who=dwr
    GET /api/feed?who=dwr&hours=24
    GET /api/suggestions?who=dwr

Settings (environment variables, optional):
    PHUNK_NODE   the node to read from (default: the public node)
    PHUNK_RATE   requests per second to that node (default 20)

Successful reads carry Cache-Control: s-maxage, so Vercel's CDN answers
repeat lookups without touching the node. See web/README.md to deploy.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from phunk.hub import DEFAULT_NODE, Hub  # noqa: E402
from phunk.ui import PUBLIC_HELP, UI, get_json  # noqa: E402

TIMEOUT = 280  # seconds; under the function's maxDuration in vercel.json
CACHE = {
    "/api/me": "public, max-age=300, s-maxage=3600",
    "/api/people": "public, max-age=60, s-maxage=600, stale-while-revalidate=3600",
    "/api/feed": "public, max-age=60, s-maxage=300, stale-while-revalidate=1800",
    "/api/suggestions": "public, max-age=60, s-maxage=600, stale-while-revalidate=3600",
}

# One event loop on a background thread for the life of the instance, so the
# Hub's connection pool and the UI's caches outlive a single request.
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_ui: UI | None = None


def _start() -> tuple[asyncio.AbstractEventLoop, UI]:
    global _loop, _ui
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="phunk", daemon=True).start()

            async def make() -> UI:
                hub = Hub(os.environ.get("PHUNK_NODE") or DEFAULT_NODE,
                          rate=float(os.environ.get("PHUNK_RATE") or 20))
                await hub.__aenter__()
                return UI(hub, public=True)

            _ui = asyncio.run_coroutine_threadsafe(make(), loop).result()
            _loop = loop
    return _loop, _ui


def respond(target: str) -> tuple[int, dict, str]:
    """GET `target` ("/api/people?who=dwr") -> (status, JSON body, Cache-Control)."""
    parts = urlsplit(target)
    path = parts.path.rstrip("/")
    query = dict(parse_qsl(parts.query))
    loop, ui = _start()
    future = asyncio.run_coroutine_threadsafe(get_json(ui, path, query), loop)
    try:
        status, body = future.result(timeout=TIMEOUT)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return 504, {"error": "the node took too long; try again in a minute"}, "no-store"
    cache = CACHE.get(path) if status == 200 and query.get("refresh") != "1" else None
    return status, body, cache or "no-store"


class handler(BaseHTTPRequestHandler):  # Vercel's Python runtime looks for this name
    def _send(self, status: int, body: dict, cache: str = "no-store") -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        self._send(*respond(self.path))

    def do_POST(self):  # noqa: N802
        self._send(405, {"error": PUBLIC_HELP, "read_only": True})

    def log_message(self, format, *args):  # keep usernames out of the logs
        pass
