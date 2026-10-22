"""phunk.social: the web client's read API, the `api` Vercel service.

An ASGI app (`app`), the entrypoint vercel.json names. The page is the
`web` service; vercel.json sends every /api/* request here (except the
signer's path). It answers the same read endpoints as `phunk ui`,
from one shared `UI` in public mode: no account, no key, nothing to post
with. Follow, reply and like open Farcaster in the page instead.

    GET /api/me
    GET /api/people?who=dwr
    GET /api/feed?who=dwr&hours=24
    GET /api/suggestions?who=dwr

Settings (environment variables, optional):
    PHUNK_NODE   the node to read from (default: the public node)
    PHUNK_RATE   requests per second to that node (default 20)
    PHUNK_TIP_FID  the account "Buy me a coffee" tips (unset: no button)

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
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from phunk.hub import DEFAULT_NODE, Hub  # noqa: E402
from phunk.onchain import base_rpc_from_env  # noqa: E402
from phunk.ui import PUBLIC_HELP, UI, get_json, tip_fid_from_env  # noqa: E402

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
                return UI(hub, public=True, tip_fid=tip_fid_from_env(), vote_rpc=base_rpc_from_env())

            _ui = asyncio.run_coroutine_threadsafe(make(), loop).result()
            _loop = loop
    return _loop, _ui


def _cache(path: str, status: int, query: dict) -> str:
    cache = CACHE.get(path) if status == 200 and query.get("refresh") != "1" else None
    return cache or "no-store"


def _split(target: str) -> tuple[str, dict]:
    parts = urlsplit(target)
    return parts.path.rstrip("/"), dict(parse_qsl(parts.query))


def respond(target: str) -> tuple[int, dict, str]:
    """GET `target` ("/api/people?who=dwr") -> (status, JSON body, Cache-Control). Blocking."""
    path, query = _split(target)
    loop, ui = _start()
    future = asyncio.run_coroutine_threadsafe(get_json(ui, path, query), loop)
    try:
        status, body = future.result(timeout=TIMEOUT)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return 504, {"error": "the node took too long; try again in a minute"}, "no-store"
    return status, body, _cache(path, status, query)


async def respond_async(target: str) -> tuple[int, dict, str]:
    """`respond`, awaitable from any event loop (the UI's loop stays its own)."""
    path, query = _split(target)
    loop, ui = await asyncio.to_thread(_start)
    future = asyncio.run_coroutine_threadsafe(get_json(ui, path, query), loop)
    try:
        status, body = await asyncio.wait_for(asyncio.wrap_future(future), TIMEOUT)
    except asyncio.TimeoutError:
        future.cancel()
        return 504, {"error": "the node took too long; try again in a minute"}, "no-store"
    return status, body, _cache(path, status, query)


async def app(scope, receive, send):
    """ASGI entrypoint: GET reads, anything else 405."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    if scope["method"] in ("GET", "HEAD"):
        target = scope["path"] + ("?" + scope["query_string"].decode("latin-1") if scope.get("query_string") else "")
        status, body, cache = await respond_async(target)
    else:
        status, body, cache = 405, {"error": PUBLIC_HELP, "read_only": True}, "no-store"
    raw = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(raw)).encode()),
        (b"cache-control", cache.encode()),
        (b"x-content-type-options", b"nosniff"),
    ]})
    await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else raw})
