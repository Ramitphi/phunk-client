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
    POST /api/webhook         the mini app's signed add / remove events
    POST /api/connect         {"key": "0x..."}: start approving a key made in the page
    GET  /api/connect?token=  where that approval stands
    POST /api/submit          {"message": "0x..."}: a follow or like signed in the page
    GET /api/cron/digest      the weekly note, from Vercel Cron

Settings (environment variables, optional):
    PHUNK_NODE         the node to read from (default: the public node)
    PHUNK_NODE_TOKEN   the token for a node behind scripts/gateway.sh
    PHUNK_RATE         requests per second to that node, per instance (default 20)
    PHUNK_CONCURRENCY  requests in flight to that node, per instance (default 12)
    PHUNK_TIP_FID      the account the money gun (Tip tab) tips (unset: no button)

Follow and like in the mini app (optional; unset, they open Farcaster):
    PHUNK_SIGNER_URL   the app's signing service, e.g.
                       https://sign.phunk.social/api/sign-key-request (signer/)

Mini app notifications (all optional; without a store the webhook still
answers, it just keeps nobody):
    KV_REST_API_URL    Upstash Redis, from Vercel's Redis integration. It
    KV_REST_API_TOKEN  holds each account's notification token.
    MINIAPP_DOMAIN     where notifications open (default miniapp.phunk.social)
    CRON_SECRET        Vercel sets this; the weekly digest checks for it

On your own node, raise PHUNK_RATE and PHUNK_CONCURRENCY (for example 150
and 48): a feed is one request per account followed, so this is what makes
it fast. The gateway's per-token limit caps the total across instances.

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

from phunk import connect, notify  # noqa: E402
from phunk.hub import DEFAULT_NODE, Hub, NodeError  # noqa: E402
from phunk.onchain import base_rpc_from_env  # noqa: E402
from phunk.ui import PUBLIC_HELP, UI, get_json, tip_fid_from_env  # noqa: E402

TIMEOUT = 280  # seconds; under the function's maxDuration in vercel.json
MINIAPP = f"https://{os.environ.get('MINIAPP_DOMAIN') or 'miniapp.phunk.social'}/"
# Paused: in-app follow and like (approving a phunk key) are off for now, so
# /api/connect and /api/submit answer 503 even with PHUNK_SIGNER_URL set.
# To turn them back on, restore this line:
# SIGNER_URL = os.environ.get("PHUNK_SIGNER_URL") or None
SIGNER_URL = None
FARCASTER_API = os.environ.get("PHUNK_FARCASTER_API") or connect.FARCASTER_API
# Per instance, per IP: start a few approvals, poll their status, send follows and likes.
LIMITS = {"start": connect.RateLimit(10, 600), "status": connect.RateLimit(300, 600),
          "submit": connect.RateLimit(60, 60)}
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
_store: notify.Store | None = None


def _start() -> tuple[asyncio.AbstractEventLoop, UI]:
    global _loop, _ui, _store
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="phunk", daemon=True).start()

            async def make() -> UI:
                hub = Hub(os.environ.get("PHUNK_NODE") or DEFAULT_NODE,
                          rate=float(os.environ.get("PHUNK_RATE") or 20),
                          concurrency=int(os.environ.get("PHUNK_CONCURRENCY") or 12),
                          token=os.environ.get("PHUNK_NODE_TOKEN") or None)
                await hub.__aenter__()
                return UI(hub, public=True, tip_fid=tip_fid_from_env(), vote_rpc=base_rpc_from_env(),
                          connect=SIGNER_URL is not None)

            _ui = asyncio.run_coroutine_threadsafe(make(), loop).result()
            _store = notify.store_from_env(_ui.hub.session, os.environ)
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


async def _webhook(body: bytes) -> tuple[int, dict]:
    """POST /api/webhook: one signed event from a Farcaster client."""
    loop, ui = await asyncio.to_thread(_start)

    async def run():
        if _store is None:
            return 503, {"error": "notifications are not set up on this deployment"}
        try:
            payload = json.loads(body)
        except ValueError:
            return 400, {"error": "not JSON"}
        try:
            event = await notify.handle_event(ui.hub, _store, payload)
        except notify.BadEvent as e:
            return 400, {"error": str(e)}
        await notify.welcome(ui.hub.session, _store, event, target_url=MINIAPP)
        return 200, {"ok": True, "event": event.event}

    future = asyncio.run_coroutine_threadsafe(run(), loop)
    return await asyncio.wait_for(asyncio.wrap_future(future), TIMEOUT)


async def _digest(headers: dict) -> tuple[int, dict]:
    """GET /api/cron/digest: the weekly note. Only Vercel Cron may call it."""
    secret = os.environ.get("CRON_SECRET")
    if secret and headers.get("authorization") != f"Bearer {secret}":
        return 401, {"error": "not allowed"}
    loop, ui = await asyncio.to_thread(_start)

    async def run():
        if _store is None:
            return 503, {"error": "notifications are not set up on this deployment"}
        async def people(fid: int) -> dict:
            return await ui.people(fid)
        out = await notify.send_digests(ui.hub.session, _store, people, target_url=MINIAPP)
        return 200, {k: len(v) for k, v in out.items()}

    future = asyncio.run_coroutine_threadsafe(run(), loop)
    return await asyncio.wait_for(asyncio.wrap_future(future), TIMEOUT)


async def _connect(kind: str, ip: str, body: bytes = b"", query: dict | None = None) -> tuple[int, dict]:
    """POST /api/connect, GET /api/connect, POST /api/submit."""
    if SIGNER_URL is None:
        return 503, {"error": "follow and like are not set up on this deployment"}
    if not LIMITS[kind].allow(ip):
        return 429, {"error": "slow down a little and try again in a minute"}
    loop, ui = await asyncio.to_thread(_start)

    async def run():
        try:
            if kind == "status":
                return 200, await connect.status(ui.hub.session, (query or {}).get("token", ""), api=FARCASTER_API)
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                return 400, {"error": "send JSON"}
            if not isinstance(data, dict):
                return 400, {"error": "send a JSON object"}
            if kind == "start":
                return 200, await connect.start(ui.hub.session, SIGNER_URL, data.get("key"), api=FARCASTER_API)
            raw = str(data.get("message") or "").removeprefix("0x")
            try:
                message = bytes.fromhex(raw)
            except ValueError:
                return 400, {"error": "message must be hex"}
            connect.check(message)
            try:
                sent = await ui.hub.submit(message)
            except NodeError as e:
                return 400, {"error": f"the node said no: {e}"}
            return 200, {"hash": sent.get("hash")}
        except connect.ConnectError as e:
            return e.status, {"error": str(e)}

    future = asyncio.run_coroutine_threadsafe(run(), loop)
    return await asyncio.wait_for(asyncio.wrap_future(future), TIMEOUT)


def _client_ip(scope) -> str:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
    forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client") or ("", 0)
    return str(client[0])


async def _body(receive) -> bytes:
    """The whole request body, up to 64 KB."""
    out = b""
    while True:
        message = await receive()
        out += message.get("body") or b""
        if len(out) > 65536 or not message.get("more_body"):
            return out[:65536]


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
    path = scope["path"].rstrip("/")
    if path in ("/api/connect", "/api/submit") and scope["method"] in ("GET", "POST"):
        kind = "submit" if path == "/api/submit" else ("start" if scope["method"] == "POST" else "status")
        if kind == "submit" and scope["method"] != "POST":
            status, body = 405, {"error": "POST a signed message"}
        else:
            query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1")))
            status, body = await _connect(kind, _client_ip(scope), await _body(receive) if scope["method"] == "POST" else b"", query)
        cache = "no-store"
    elif scope["method"] == "POST" and path == "/api/webhook":
        status, body = await _webhook(await _body(receive))
        cache = "no-store"
    elif scope["method"] in ("GET", "HEAD") and path == "/api/cron/digest":
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        status, body = await _digest(headers)
        cache = "no-store"
    elif scope["method"] in ("GET", "HEAD"):
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
