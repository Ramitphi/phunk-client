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

Checking the signature uses `pynacl` when it is installed (the `[write]`
extra), and a small pure-Python Ed25519 check (RFC 8032) otherwise, so the
webhook works with `aiohttp` alone. Verifying needs no secret.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
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
DROP_REASONS = {"invalid_token"}   # failedTokens reasons that mean the token is gone for good

log = logging.getLogger(__name__)


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
    try:
        public = bytes.fromhex(key[2:])
    except ValueError:
        raise BadEvent("bad signature") from None
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        ok = ed25519_verify(public, signed, signature)
    else:
        try:
            VerifyKey(public).verify(signed, signature)
            ok = True
        except (BadSignatureError, ValueError):
            ok = False
    if not ok:
        raise BadEvent("bad signature")


# Ed25519 verification, after the reference code in RFC 8032 section 6. Only
# checks a signature against a public key: no secret is involved, so plain
# (not constant-time) integer maths is fine.

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    x = (a[1] - a[0]) * (b[1] - b[0]) % _P
    y = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c = 2 * a[3] * b[3] * _D % _P
    z = 2 * a[2] * b[2] % _P
    e, f, g, h = y - x, z - c, z + c, y + x
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(n: int, pt):
    out = (0, 1, 1, 0)
    while n:
        if n & 1:
            out = _add(out, pt)
        pt, n = _add(pt, pt), n >> 1
    return out


def _x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def _point(b: bytes):
    y = int.from_bytes(b, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_x(_GY, 0), _GY, 1, _x(_GY, 0) * _GY % _P)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Is `signature` `public`'s Ed25519 signature over `message`?"""
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _point(public), _point(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _Q
    left, right = _mul(s, _G), _add(r, _mul(h, a))
    return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and \
        (left[1] * right[2] - right[1] * left[2]) % _P == 0


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

    Tokens go in batches of 100 per client URL. Returns the fids by outcome
    ("sent", "invalid", "rate_limited", "failed") and "reasons", a dict of
    fid -> why it failed. A failed fid's reason is the client's
    `failedTokens` reason ("domain_mismatch", "target_url_mismatch",
    "no_webhook_url", "invalid_token", "unknown"), or "http_<status>",
    "no_reply" or "not_in_reply" when the client didn't say.

    Tokens in `invalidTokens`, and failed tokens with reason
    "invalid_token", are dropped from the store. Other failed tokens are
    kept, so they are tried again next time."""
    title, body, id = title[:TITLE_MAX], body[:BODY_MAX], id[:128]
    by_url: dict[str, dict[str, int]] = {}
    for fid, s in subs.items():
        by_url.setdefault(s["url"], {})[s["token"]] = fid
    out: dict = {"sent": [], "invalid": [], "rate_limited": [], "failed": [], "reasons": {}}
    drop: list[int] = []

    def fail(fid: int, reason: str):
        out["failed"].append(fid)
        out["reasons"][fid] = reason
        log.warning("notification to fid %s failed: %s", fid, reason)

    for url, tokens in by_url.items():
        items = list(tokens.items())
        for i in range(0, len(items), BATCH):
            batch = dict(items[i:i + BATCH])
            payload = {"notificationId": id, "title": title, "body": body,
                       "targetUrl": target_url, "tokens": list(batch)}
            result, why = None, "no_reply"
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 200:
                        result = (await r.json(content_type=None) or {}).get("result") or {}
                    else:
                        why = f"http_{r.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                result = None
            if not isinstance(result, dict):
                for fid in batch.values():
                    fail(fid, why)
                continue
            seen: set[str] = set()
            for name, field in (("sent", "successfulTokens"), ("invalid", "invalidTokens"),
                                ("rate_limited", "rateLimitedTokens")):
                for t in result.get(field) or []:
                    if t in batch and t not in seen:
                        seen.add(t)
                        out[name].append(batch[t])
            for f in result.get("failedTokens") or []:
                t = f.get("token") if isinstance(f, dict) else None
                if t not in batch or t in seen:
                    continue
                seen.add(t)
                reason = str(f.get("reason") or "unknown")
                fail(batch[t], reason)
                if reason in DROP_REASONS:
                    drop.append(batch[t])
            for t, fid in batch.items():   # the client said nothing about these
                if t not in seen:
                    fail(fid, "not_in_reply")
    for fid in out["invalid"] + drop:
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
    one in time (the budget, in seconds) are first in line next run. Returns
    the fids by outcome, plus "reasons" (fid -> why it failed; see `send`)."""
    now = time.time() if now is None else now
    start = time.monotonic()
    subs, sent = await store.all(), await store.sent_at()
    due = sorted((f for f in subs if now - sent.get(f, 0) >= WEEK - 24 * 3600), key=lambda f: sent.get(f, 0))
    week = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%G-W%V")
    gate = asyncio.Semaphore(concurrency)
    totals: dict[str, list[int]] = {"sent": [], "invalid": [], "rate_limited": [], "failed": [], "skipped": []}
    reasons: dict[int, str] = {}

    async def one(fid: int):
        async with gate:
            if time.monotonic() - start > budget:
                return
            try:
                note = digest(await people(fid))
            except Exception:  # one account's feed failing shouldn't stop the rest
                totals["failed"].append(fid)
                reasons[fid] = "feed_error"
                log.warning("people feed for fid %s failed", fid, exc_info=True)
                return
            if note is None:
                totals["skipped"].append(fid)
                await store.mark_sent(fid, now)
                return
            res = await send(session, store, {fid: subs[fid]}, id=f"digest-{week}-{fid}",
                             title=note[0], body=note[1], target_url=target_url)
            reasons.update(res.pop("reasons", {}))
            for k, v in res.items():
                totals[k] += v
            if res["sent"]:
                await store.mark_sent(fid, now)

    await asyncio.gather(*(one(f) for f in due))
    return {**{k: sorted(v) for k, v in totals.items()}, "reasons": dict(sorted(reasons.items()))}
