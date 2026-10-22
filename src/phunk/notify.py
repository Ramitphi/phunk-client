"""Mini app notifications: who added the app, and a weekly note about their people.

When someone adds the mini app (or turns its notifications on or off), their
Farcaster client POSTs a signed event to the manifest's `webhookUrl`:

    {"header": b64url(JSON {"fid", "type": "app_key", "key"}),
     "payload": b64url(JSON {"event", "notificationDetails": {"url", "token"}}),
     "signature": b64url(ed25519 over "<header>.<payload>")}

`handle_event` checks the signature and that the key is one of the account's
active keys on the node, then keeps or drops the account's token in a
`Store`. `send` posts a notification to the client's URL for each token, and
drops the tokens the client says are no longer valid. `digest` turns a
people feed into one short note, sent weekly by `send_digests`.

Checking the signature needs `pynacl` (the `[write]` extra, or the API's
requirements.txt); it is imported only when an event arrives.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp

ADDED = {"miniapp_added", "frame_added"}            # frame_* are the older names
REMOVED = {"miniapp_removed", "frame_removed"}
ENABLED = {"notifications_enabled"}
DISABLED = {"notifications_disabled"}
BATCH = 100      # tokens per request, the clients' limit
TITLE_MAX, BODY_MAX = 32, 128
WEEK = 7 * 24 * 3600


class BadEvent(ValueError):
    """The webhook body is not a valid, signed event from the account."""


@dataclass
class Event:
    fid: int
    key: str               # the app key that signed it, 0x hex
    event: str             # "miniapp_added", "notifications_disabled", ...
    url: str | None = None   # where to send this account's notifications
    token: str | None = None


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_event(body: dict) -> tuple[Event, bytes, bytes]:
    """The webhook body -> (Event, signed bytes, signature). Checks shape only."""
    try:
        header, payload, signature = body["header"], body["payload"], body["signature"]
        h, p, sig = json.loads(_b64(header)), json.loads(_b64(payload)), _b64(signature)
        fid, key, name = int(h["fid"]), str(h["key"]).lower(), str(p["event"])
    except (KeyError, TypeError, ValueError) as e:
        raise BadEvent(f"not a signed event: {e}") from None
    if h.get("type") != "app_key" or not key.startswith("0x") or len(key) != 66:
        raise BadEvent("not signed with an app key")
    details = p.get("notificationDetails") or {}
    url, token = details.get("url"), details.get("token")
    if (url or token) and not (isinstance(url, str) and url.startswith("https://") and isinstance(token, str)):
        raise BadEvent("bad notificationDetails")
    return Event(fid, key, name, url or None, token or None), f"{header}.{payload}".encode(), sig


def check_signature(key: str, signed: bytes, signature: bytes) -> None:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
    try:
        VerifyKey(bytes.fromhex(key[2:])).verify(signed, signature)
    except (BadSignatureError, ValueError):
        raise BadEvent("bad signature") from None


async def is_active_key(hub, fid: int, key: str) -> bool:
    """Is `key` one of `fid`'s active keys on the node right now?"""
    signers = await hub.collect("/v1/signersByFid", {"fid": fid}, key="signers")
    return any(str(s.get("key", "")).lower() == key for s in signers)


# ------------------------------------------------------------------ storage

class Store:
    """Each account's notification URL and token, and when it last got a digest."""

    async def put(self, fid: int, url: str, token: str) -> None: ...
    async def drop(self, fid: int) -> None: ...
    async def all(self) -> dict[int, dict]: ...      # {fid: {"url", "token"}}
    async def sent_at(self) -> dict[int, float]: ...  # {fid: unix time of last digest}
    async def mark_sent(self, fid: int, when: float) -> None: ...


class MemoryStore(Store):
    """For tests and `phunk ui`: forgets everything on restart."""

    def __init__(self):
        self.subs: dict[int, dict] = {}
        self.sent: dict[int, float] = {}

    async def put(self, fid, url, token):
        self.subs[fid] = {"url": url, "token": token}

    async def drop(self, fid):
        self.subs.pop(fid, None)

    async def all(self):
        return dict(self.subs)

    async def sent_at(self):
        return dict(self.sent)

    async def mark_sent(self, fid, when):
        self.sent[fid] = when


class RedisStore(Store):
    """Upstash Redis over its REST API (what Vercel's Redis integration sets up).

    Two hashes: `phunk:notify` (fid -> {"url", "token"}) and
    `phunk:notify:sent` (fid -> unix time of the last digest)."""

    SUBS, SENT = "phunk:notify", "phunk:notify:sent"

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str):
        self.session, self.url, self.token = session, url.rstrip("/"), token

    async def _cmd(self, *args):
        async with self.session.post(self.url, json=[str(a) for a in args],
                                     headers={"Authorization": f"Bearer {self.token}"}) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or "error" in data:
                raise RuntimeError(f"redis: {data.get('error') or r.status}")
            return data.get("result")

    @staticmethod
    def _pairs(flat) -> dict[int, str]:
        flat = flat or []
        return {int(flat[i]): flat[i + 1] for i in range(0, len(flat) - 1, 2)}

    async def put(self, fid, url, token):
        await self._cmd("HSET", self.SUBS, fid, json.dumps({"url": url, "token": token}))

    async def drop(self, fid):
        await self._cmd("HDEL", self.SUBS, fid)

    async def all(self):
        return {fid: json.loads(v) for fid, v in self._pairs(await self._cmd("HGETALL", self.SUBS)).items()}

    async def sent_at(self):
        return {fid: float(v) for fid, v in self._pairs(await self._cmd("HGETALL", self.SENT)).items()}

    async def mark_sent(self, fid, when):
        await self._cmd("HSET", self.SENT, fid, int(when))


def store_from_env(session: aiohttp.ClientSession, env: dict) -> Store | None:
    """A RedisStore from KV_REST_API_URL/TOKEN (or UPSTASH_REDIS_REST_URL/TOKEN); None if unset."""
    url = env.get("KV_REST_API_URL") or env.get("UPSTASH_REDIS_REST_URL")
    token = env.get("KV_REST_API_TOKEN") or env.get("UPSTASH_REDIS_REST_TOKEN")
    return RedisStore(session, url, token) if url and token else None


# ------------------------------------------------------------------ events

async def handle_event(hub, store: Store, body: dict, *, verify: bool = True) -> Event:
    """Check one webhook body and update the store. Raises BadEvent."""
    event, signed, sig = parse_event(body)
    if verify:
        check_signature(event.key, signed, sig)
        if not await is_active_key(hub, event.fid, event.key):
            raise BadEvent("the key is not one of the account's active keys")
    if event.event in ADDED | ENABLED:
        if event.url and event.token:
            await store.put(event.fid, event.url, event.token)
    elif event.event in REMOVED | DISABLED:
        await store.drop(event.fid)
    else:
        raise BadEvent(f"unknown event {event.event!r}")
    return event


# ------------------------------------------------------------------ sending

async def send(session: aiohttp.ClientSession, store: Store, subs: dict[int, dict], *,
               id: str, title: str, body: str, target_url: str) -> dict:
    """Notify every account in `subs` ({fid: {"url", "token"}}).

    Tokens go in batches of 100 per client URL. Tokens the client calls
    invalid are dropped from the store. Returns the fids by outcome."""
    title, body, id = title[:TITLE_MAX], body[:BODY_MAX], id[:128]
    by_url: dict[str, dict[str, int]] = {}
    for fid, s in subs.items():
        by_url.setdefault(s["url"], {})[s["token"]] = fid
    out: dict[str, list[int]] = {"sent": [], "invalid": [], "rate_limited": [], "failed": []}
    for url, tokens in by_url.items():
        items = list(tokens.items())
        for i in range(0, len(items), BATCH):
            batch = dict(items[i:i + BATCH])
            payload = {"notificationId": id, "title": title, "body": body,
                       "targetUrl": target_url, "tokens": list(batch)}
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    result = None
                    if r.status == 200:
                        result = (await r.json(content_type=None) or {}).get("result") or {}
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                result = None
            if result is None:
                out["failed"] += batch.values()
                continue
            for name, field in (("sent", "successfulTokens"), ("invalid", "invalidTokens"),
                                ("rate_limited", "rateLimitedTokens")):
                out[name] += [batch[t] for t in result.get(field) or [] if t in batch]
    for fid in out["invalid"]:
        await store.drop(fid)
    return out


async def welcome(session: aiohttp.ClientSession, store: Store, event: Event, *, target_url: str) -> dict:
    """A first note right after someone adds the app with notifications on."""
    if not (event.event in ADDED and event.url and event.token):
        return {}
    return await send(session, store, {event.fid: {"url": event.url, "token": event.token}},
                      id=f"welcome-{event.fid}", title="phunk is added",
                      body="Once a week, a short note about your people: who to reconnect with and who moved apps.",
                      target_url=target_url)


def _names(cards: list[dict], n: int) -> str:
    names = [f"@{c['username']}" for c in cards if c.get("username")][:n]
    more = len(cards) - len(names)
    if not names:
        return ""
    if more > 0:
        return ", ".join(names) + f" and {more} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def digest(feed: dict) -> tuple[str, str] | None:
    """A people feed (GET /api/people) -> (title, body) for the weekly note, or None if there's nothing to say."""
    cards = feed.get("cards") or []
    reconnect = [c for c in cards if c.get("kind") == "reconnect"]
    moved = int((feed.get("summary") or {}).get("moved") or 0)
    if reconnect:
        body = f"You talk with {_names(reconnect, 2)} but don't follow them yet."
    elif moved:
        body = f"{moved} {'person' if moved == 1 else 'people'} you talk to moved to another app. See who."
    elif cards:
        body = f"{len(cards)} people to meet this week, like {_names(cards, 2)}."
    else:
        return None
    return "Your people this week", body[:BODY_MAX]


async def send_digests(session: aiohttp.ClientSession, store: Store,
                       people: Callable[[int], Awaitable[dict]], *, target_url: str,
                       now: float | None = None, budget: float = 240, concurrency: int = 4) -> dict:
    """Send this week's note to everyone subscribed who hasn't had one in 6 days.

    `people(fid)` returns that account's people feed. Accounts that didn't get
    one in time (the budget, in seconds) are first in line next run."""
    now = time.time() if now is None else now
    start = time.monotonic()
    subs, sent = await store.all(), await store.sent_at()
    due = sorted((f for f in subs if now - sent.get(f, 0) >= WEEK - 24 * 3600), key=lambda f: sent.get(f, 0))
    week = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%G-W%V")
    gate = asyncio.Semaphore(concurrency)
    totals: dict[str, list[int]] = {"sent": [], "invalid": [], "rate_limited": [], "failed": [], "skipped": []}

    async def one(fid: int):
        async with gate:
            if time.monotonic() - start > budget:
                return
            try:
                note = digest(await people(fid))
            except Exception:  # one account's feed failing shouldn't stop the rest
                totals["failed"].append(fid)
                return
            if note is None:
                totals["skipped"].append(fid)
                await store.mark_sent(fid, now)
                return
            res = await send(session, store, {fid: subs[fid]}, id=f"digest-{week}-{fid}",
                             title=note[0], body=note[1], target_url=target_url)
            for k, v in res.items():
                totals[k] += v
            if res["sent"]:
                await store.mark_sent(fid, now)

    await asyncio.gather(*(one(f) for f in due))
    return {k: sorted(v) for k, v in totals.items()}
