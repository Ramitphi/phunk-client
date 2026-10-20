"""Async client for the Snapchain / Hypersnap HTTP API (`/v1/...`)."""
from __future__ import annotations

import asyncio
import datetime as dt
import random
import time
from typing import AsyncIterator, Callable

import aiohttp

from .keys import decode_key_request
from .models import Cast, Follow, Key, Reaction, User
from .timeutil import from_datetime

DEFAULT_NODE = "https://haatz.quilibrium.com"
MAX_PAGE = 1000
RETRY_STATUS = {429, 500, 502, 503, 504}


class NodeError(Exception):
    """The node answered with an error we can't retry past."""


class NotFound(NodeError):
    """The node has no such fid, name, cast or page."""


class RateLimiter:
    """Token bucket: at most `rate` requests per second, with bursts up to `rate`."""

    def __init__(self, rate: float):
        self.rate = float(rate)
        self.tokens = self.rate
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


def _since(since: dt.datetime | int | None) -> int | None:
    """A cutoff as Farcaster time. Ints are already Farcaster time."""
    if since is None or isinstance(since, int):
        return since
    return from_datetime(since)


class Hub:
    """Read Farcaster from a node.

        async with Hub() as hub:
            fid = await hub.fid_by_name("dwr")
            async for cast in hub.casts(fid, limit=5):
                print(cast.text)

    `rate` caps requests per second and `concurrency` caps requests in
    flight. Public nodes are shared, so keep these modest.
    """

    def __init__(
        self,
        url: str = DEFAULT_NODE,
        *,
        rate: float = 20.0,
        concurrency: int = 12,
        timeout: float = 60.0,
        retries: int = 5,
        session: aiohttp.ClientSession | None = None,
    ):
        self.url = url.rstrip("/")
        self.limiter = RateLimiter(rate)
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retries = retries
        self.session = session
        self._own_session = session is None
        self.requests = 0
        self.errors = 0
        self._key_apps: dict[int, dict[str, int | None]] = {}
        self._app_names: dict[int, str | None] = {}

    async def __aenter__(self) -> "Hub":
        if self.session is None:
            # trust_env picks up HTTPS_PROXY and SSL_CERT_FILE.
            self.session = aiohttp.ClientSession(timeout=self.timeout, trust_env=True)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._own_session and self.session is not None:
            await self.session.close()
            self.session = None

    # ------------------------------------------------------------------ raw

    async def get(self, path: str, params: dict | None = None) -> dict:
        """GET one endpoint and return its JSON. Retries timeouts, 429 and 5xx
        with exponential backoff. Raises NotFound for 404 (and for 400, which
        some nodes use for "no such fid")."""
        if self.session is None:
            raise RuntimeError("use `async with Hub(...) as hub:` or call `await hub.__aenter__()`")
        url = f"{self.url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.retries):
            await self.limiter.acquire()
            async with self.sem:
                self.requests += 1
                try:
                    async with self.session.get(url, params=params) as r:
                        if r.status in (400, 404):
                            text = await r.text()
                            raise NotFound(f"{path} {params}: {text[:200]}")
                        if r.status in RETRY_STATUS:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        if r.status >= 400:
                            text = await r.text()
                            raise NodeError(f"{path}: HTTP {r.status}: {text[:200]}")
                        return await r.json(content_type=None)
                except NodeError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.errors += 1
                    if attempt == self.retries - 1:
                        raise NodeError(f"{path}: {e!r}") from e
            await asyncio.sleep(min(30, 2**attempt) * (0.5 + random.random()))
        raise NodeError(f"{path}: out of retries")

    async def post(self, path: str, body: bytes) -> dict:
        """POST protobuf bytes to an endpoint and return its JSON.

        Retries timeouts, 429 and 5xx (resubmitting a message is harmless:
        it has the same hash). Any other error raises NodeError with the
        node's explanation."""
        if self.session is None:
            raise RuntimeError("use `async with Hub(...) as hub:` or call `await hub.__aenter__()`")
        url = f"{self.url}{path}"
        headers = {"Content-Type": "application/octet-stream"}
        for attempt in range(self.retries):
            await self.limiter.acquire()
            async with self.sem:
                self.requests += 1
                try:
                    async with self.session.post(url, data=body, headers=headers) as r:
                        if r.status in RETRY_STATUS:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        if r.status >= 400:
                            text = await r.text()
                            raise NodeError(f"{path}: HTTP {r.status}: {text[:500]}")
                        return await r.json(content_type=None)
                except NodeError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.errors += 1
                    if attempt == self.retries - 1:
                        raise NodeError(f"{path}: {e!r}") from e
            await asyncio.sleep(min(30, 2**attempt) * (0.5 + random.random()))
        raise NodeError(f"{path}: out of retries")

    async def submit(self, message: bytes) -> dict:
        """Submit a signed message (protobuf bytes). A read node forwards it
        to the validators. Returns the message as the node sees it."""
        return await self.post("/v1/submitMessage", message)

    async def validate(self, message: bytes) -> dict:
        """Ask the node whether a message is valid, without submitting it."""
        return await self.post("/v1/validateMessage", message)

    async def pages(
        self,
        path: str,
        params: dict,
        *,
        key: str = "messages",
        limit: int | None = None,
        max_pages: int | None = None,
        stop: Callable[[dict], bool] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield every item of a paged endpoint, without repeats.

        Two node quirks this handles:

        * Queries that span shards (castsByParent, linksByTargetFid,
          castsByMention) return one sorted page per shard glued together,
          so a page is not sorted as a whole.
        * Once one shard runs dry, the page token can loop back to the start.

        So items are deduped by hash, and paging stops when a page adds
        nothing new.

        `stop(item)` marks items past a cutoff (for example older than a date,
        with reverse=true). Those items are skipped, and paging ends after a
        page in which every new item is past the cutoff. Each shard's part of
        a page is sorted, so this is safe across shards too.
        """
        page_size = MAX_PAGE if limit is None else max(1, min(MAX_PAGE, limit))
        token = None
        n_pages = 0
        n_out = 0
        seen: set[str] = set()
        while True:
            p = dict(params, pageSize=page_size)
            if token:
                p["pageToken"] = token
            try:
                data = await self.get(path, p)
            except NotFound:
                return
            items = data.get(key) or []
            new = kept = 0
            for item in items:
                h = item.get("hash") or repr(item)
                if h in seen:
                    continue
                seen.add(h)
                new += 1
                if stop and stop(item):
                    continue
                kept += 1
                yield item
                n_out += 1
                if limit is not None and n_out >= limit:
                    return
            token = data.get("nextPageToken")
            n_pages += 1
            if not token or not items or new == 0 or (stop and kept == 0):
                return
            if max_pages and n_pages >= max_pages:
                return

    async def collect(self, path: str, params: dict, **kw) -> list[dict]:
        """`pages(...)` into a list."""
        return [m async for m in self.pages(path, params, **kw)]

    # --------------------------------------------------------------- node

    async def info(self) -> dict:
        """Shards, message counts and FID registrations."""
        return await self.get("/v1/info")

    # -------------------------------------------------------------- people

    async def fid_by_name(self, name: str) -> int:
        """Username (fname, or an ENS name registered on Farcaster) -> fid."""
        proof = await self.get("/v1/userNameProofByName", {"name": name.lstrip("@")})
        return int(proof["fid"])

    async def user(self, fid: int) -> User:
        """Profile: username, display name, pfp, bio, primary addresses..."""
        data = await self.get("/v1/userDataByFid", {"fid": fid})
        return User.from_messages(fid, data.get("messages") or [])

    async def user_by_name(self, name: str) -> User:
        return await self.user(await self.fid_by_name(name))

    async def fid_by_address(self, address: str) -> int:
        """Custody address -> the fid it owns, from the IdRegistry events the
        node has synced."""
        event = await self.get("/v1/onChainIdRegistryEventByAddress", {"address": address})
        return int(event["fid"])

    # -------------------------------------------------------------- graph

    async def following(self, fid: int, *, limit: int | None = None) -> AsyncIterator[Follow]:
        """Accounts `fid` follows."""
        async for m in self.pages("/v1/linksByFid", {"fid": fid, "link_type": "follow"}, limit=limit):
            if (m["data"].get("linkBody") or {}).get("targetFid"):
                yield Follow.from_message(m)

    async def followers(self, fid: int, *, limit: int | None = None) -> AsyncIterator[Follow]:
        """Accounts that follow `fid`. Spans shards, so the order is mixed."""
        async for m in self.pages("/v1/linksByTargetFid", {"target_fid": fid, "link_type": "follow"},
                                  limit=limit):
            yield Follow.from_message(m)

    # -------------------------------------------------------------- casts

    def _newer_than(self, since):
        cutoff = _since(since)
        return None if cutoff is None else (lambda m: m["data"]["timestamp"] < cutoff)

    async def casts(self, fid: int, *, since: dt.datetime | int | None = None,
                    limit: int | None = None) -> AsyncIterator[Cast]:
        """Casts by `fid`, newest first. `since` is a datetime or Farcaster time."""
        async for m in self.pages("/v1/castsByFid", {"fid": fid, "reverse": "true"},
                                  limit=limit, stop=self._newer_than(since)):
            yield Cast.from_message(m)

    async def feed(self, fid: int, *, since: dt.datetime | int | None = None, per_account: int = 10,
                   limit: int = 50) -> list[Cast]:
        """Recent casts from everyone `fid` follows, newest first.

        A node has no feed endpoint: a client builds the feed itself, by
        reading each followed account's latest casts and merging them.
        `since` defaults to 24 hours ago. Large follow lists take a while on
        a shared node; your own node is much faster.
        """
        if since is None:
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        follows = [f.target_fid async for f in self.following(fid)]

        async def latest(target: int) -> list[Cast]:
            try:
                return [c async for c in self.casts(target, since=since, limit=per_account)]
            except NodeError:
                return []

        batches = await asyncio.gather(*(latest(t) for t in follows))
        # A few old messages carry timestamps in the future; they would sit on
        # top forever. Nodes reject anything over 10 minutes ahead today.
        cutoff = from_datetime(dt.datetime.now(dt.timezone.utc)) + 600
        casts = sorted((c for b in batches for c in b if c.timestamp <= cutoff),
                       key=lambda c: c.timestamp, reverse=True)
        return casts[:limit]

    async def cast(self, fid: int, hash: str) -> Cast:
        """One cast by author fid and hash."""
        return Cast.from_message(await self.get("/v1/castById", {"fid": fid, "hash": hash}))

    async def replies(self, fid: int, hash: str, *, limit: int | None = None) -> AsyncIterator[Cast]:
        """Direct replies to a cast."""
        async for m in self.pages("/v1/castsByParent", {"fid": fid, "hash": hash}, limit=limit):
            yield Cast.from_message(m)

    async def channel(self, url: str, *, since: dt.datetime | int | None = None,
                      limit: int | None = None) -> AsyncIterator[Cast]:
        """Casts in a channel, newest first per shard.

        A channel is just a `parentUrl` on top-level casts, for example
        "https://warpcast.com/~/channel/politics" or a chain:// URL for older
        channels. Spans shards, so the order is only roughly newest first.
        """
        async for m in self.pages("/v1/castsByParent", {"url": url, "reverse": "true"},
                                  limit=limit, stop=self._newer_than(since)):
            yield Cast.from_message(m)

    async def mentions(self, fid: int, *, limit: int | None = None) -> AsyncIterator[Cast]:
        """Casts that mention `fid`."""
        async for m in self.pages("/v1/castsByMention", {"fid": fid, "reverse": "true"}, limit=limit):
            yield Cast.from_message(m)

    # ---------------------------------------------------------- reactions

    async def reactions(self, fid: int, kind: str = "like", *, since: dt.datetime | int | None = None,
                        limit: int | None = None) -> AsyncIterator[Reaction]:
        """Likes (`kind="like"`) or recasts (`kind="recast"`) by `fid`, newest first."""
        rtype = {"like": "Like", "recast": "Recast"}[kind.lower()]
        async for m in self.pages("/v1/reactionsByFid", {"fid": fid, "reaction_type": rtype, "reverse": "true"},
                                  limit=limit, stop=self._newer_than(since)):
            yield Reaction.from_message(m)

    # --------------------------------------------------------------- keys

    async def keys(self, fid: int) -> list[Key]:
        """Every key event for `fid`, oldest first: on-chain adds and removes,
        plus off-chain ("gasless") keys, which have no on-chain event."""
        out: list[Key] = []
        events = await self.collect("/v1/onChainEventsByFid", {"fid": fid, "event_type": "EVENT_TYPE_SIGNER"},
                                    key="events")
        for e in events:
            b = e.get("signerEventBody") or {}
            kind = (b.get("eventType") or "").replace("SIGNER_EVENT_TYPE_", "").lower()
            app = None
            if kind == "add" and b.get("metadata"):
                try:
                    app = decode_key_request(b["metadata"]).request_fid
                except ValueError:
                    app = None
            out.append(Key(fid=fid, key=b.get("key"), event=kind, app_fid=app,
                           timestamp=e.get("blockTimestamp") or 0, onchain=True, raw=e))
        try:
            signers = await self.collect("/v1/signersByFid", {"fid": fid}, key="signers")
        except NodeError:
            signers = []  # older nodes don't have this endpoint
        for s in signers:
            if s.get("source") == "SIGNER_SOURCE_OFFCHAIN":
                out.append(Key(fid=fid, key=s.get("key"), event="add", app_fid=s.get("requestFid"),
                               timestamp=s.get("addedAt") or 0, onchain=False, raw=s))
        out.sort(key=lambda k: k.timestamp)
        return out

    async def signers(self, fid: int) -> dict:
        """Raw /v1/signersByFid for one page: the account's active keys, plus
        `currentUserNonce`, which a new KEY_ADD must exceed."""
        return await self.get("/v1/signersByFid", {"fid": fid})

    async def key_apps(self, fid: int) -> dict[str, int | None]:
        """{key: app fid} for every key `fid` has added. Cached per fid."""
        if fid not in self._key_apps:
            self._key_apps[fid] = {k.key: k.app_fid for k in await self.keys(fid) if k.event == "add"}
        return self._key_apps[fid]

    async def app_of(self, message: Cast | Reaction | Follow | dict) -> int | None:
        """The fid of the app that signed a message, or None if unknown.

        Works on the dataclasses here or on a raw node message. If an account
        requested its own key, this returns the account's own fid.
        """
        if isinstance(message, dict):
            fid, signer = message["data"]["fid"], message.get("signer")
        else:
            fid, signer = message.fid, message.signer
        return (await self.key_apps(fid)).get(signer)

    async def app_name(self, app_fid: int) -> str | None:
        """An app's username (apps are accounts too). Cached."""
        if app_fid not in self._app_names:
            try:
                self._app_names[app_fid] = (await self.user(app_fid)).username
            except NotFound:
                self._app_names[app_fid] = None
        return self._app_names[app_fid]
