"""`phunk ui`: a small local web client.

    phunk ui                 # http://127.0.0.1:8008, as your saved app key's fid
    phunk ui --as dwr        # look at someone else's people feed, read-only

The main screen is a feed of people to connect with (see `phunk.people`),
not posts. Casts live in a second tab.

The page is plain HTML, CSS and JS from `phunk/static`. It talks to this
server's JSON API, which talks to the node through one `Hub`. With an app key
(`phunk key add`) it can also post, react and follow, through `Writer`.

Safety: it binds to 127.0.0.1, refuses requests whose Host is not local
(DNS rebinding), and refuses POSTs from other origins or without a JSON body.
The app key never leaves this process: no endpoint returns it and nothing
logs it. The page never asks for a seed phrase.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from .hub import DEFAULT_NODE, Hub, NodeError, NotFound
from .models import Cast, User
from .onchain import holders
from .path import paths
from .people import people_feed
from .recommend import Suggestion, suggest
from .timeutil import to_unix

log = logging.getLogger("phunk.ui")
STATIC = Path(__file__).parent / "static"
CHANNEL_PREFIX = "https://warpcast.com/~/channel/"
# Older channels use the URL of the NFT they started with. A few well-known ones, for display.
KNOWN_CHANNELS = {"chain://eip155:1/erc721:0x7dd4e31f1530ac682c8ea4d8016e95773e08d8b0": "/dev"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
READ_ONLY_HELP = ("Read-only: no app key. To follow and like from here, run `phunk key add` in a terminal "
                  "(use a test account), then restart `phunk ui`.")
PUBLIC_HELP = "Read-only. Follow and Open take you to Farcaster."
# First release: the web client follows people and likes casts; it doesn't
# cast yet. Casting, replies and recasts come next (`phunk ui --casting` to try
# them). The CLI already does all of it: `phunk post`, `phunk like`.
CASTING = False


def tip_fid_from_env() -> int | None:
    """PHUNK_TIP_FID: the account that the money gun (Tip tab) tips. Unset: no button."""
    value = os.environ.get("PHUNK_TIP_FID", "").strip()
    return int(value) if _digits(value) and int(value) > 0 else None


CASTING_SOON = "Casting from phunk is coming soon. For now: `phunk post` in a terminal."
FEED_TTL = 300  # seconds a feed window stays cached
SUGGEST_TTL = 600
# On the hosted site, Refresh re-reads the node only for results older than
# this, so a script sending refresh=1 in a loop gets the cached answer.
PUBLIC_MIN_REFRESH = 60
NAME_TTL = 3600  # username -> fid; names change hands rarely
PAGE = 30
# Cache sizes, so a long-running public server doesn't grow without end.
MAX_USERS = 5000
MAX_WINDOWS = 64
MAX_FID = 2**64 - 1  # fids are uint64 on the node
NODE_BUSY = "the node is busy; try again in a minute"
# The hosted site: only the windows the page offers (app.js WINDOWS), so each
# account has a few cache keys, and at most this many followed accounts read
# per feed (the most recently followed).
PUBLIC_HOURS = (6, 24, 72, 168)
PUBLIC_MAX_FOLLOWS = 1000
PATH_TTL = 600  # a pair's paths
PATH_SHOWN = 5  # paths sent to the page


class NoSuchAccount(NotFound):
    """No account by that name or fid. Its text is safe to show as is."""


class BadRequest(ValueError):
    """Bad input from the request. Its text is safe to show as is; other
    ValueErrors get a plain "bad request" on the hosted site."""


def _digits(value) -> bool:
    """Only ASCII 0-9. `str.isdigit()` also takes "²", which int() refuses."""
    return re.fullmatch(r"[0-9]+", str(value)) is not None


# ------------------------------------------------------------------ helpers


def channel_url(value: str | None) -> str | None:
    """"politics", "/politics" or a full URL -> a channel URL. Empty -> None.

    A bare name maps to the newer https://warpcast.com/~/channel/<name> form.
    Older channels (like /dev) use a chain:// URL, so pass the URL for those.
    """
    value = (value or "").strip()
    if not value:
        return None
    if ":" in value:
        return value
    name = value.lstrip("/").lower()
    for url, known in KNOWN_CHANNELS.items():
        if known == "/" + name:
            return url
    return CHANNEL_PREFIX + name


def channel_name(url: str | None) -> str | None:
    """A channel URL -> "/politics". Unknown chain:// channels keep their URL."""
    if not url:
        return None
    if url in KNOWN_CHANNELS:
        return KNOWN_CHANNELS[url]
    for prefix in (CHANNEL_PREFIX, "https://farcaster.xyz/~/channel/"):
        if url.startswith(prefix):
            return "/" + url[len(prefix):].strip("/")
    return url


def cast_ref(value: Any) -> tuple[int, str]:
    """"fid:0xhash" or {"fid":..,"hash":..} -> (fid, hash)."""
    if isinstance(value, dict):
        fid, h = value.get("fid"), value.get("hash")
    else:
        fid, _, h = str(value or "").partition(":")
    if not _digits(fid) or not isinstance(h, str) or not h.startswith("0x"):
        raise BadRequest("expected a cast as fid:0xhash")
    return int(fid), h


def with_mentions(cast: Cast, names: dict[int, str | None]) -> str:
    """Put "@name" back where the node cut mentions out of the text."""
    body = cast.raw.get("data", {}).get("castAddBody") or {}
    positions = body.get("mentionsPositions") or []
    if not cast.mentions or len(positions) != len(cast.mentions):
        return cast.text
    raw = cast.text.encode()
    for fid, pos in sorted(zip(cast.mentions, positions), key=lambda p: p[1], reverse=True):
        name = names.get(fid) or f"fid:{fid}"
        raw = raw[:pos] + f"@{name}".encode() + raw[pos:]
    return raw.decode(errors="replace")


def filter_by_app(items: list[dict], *, only: set[str] | None = None, hide: set[str] = frozenset()) -> list[dict]:
    """Keep items whose "app" is in `only` (if given) and not in `hide`. Case-insensitive."""
    only = {a.lower() for a in only} if only else None
    hide = {a.lower() for a in hide}
    return [i for i in items if (only is None or i["app"].lower() in only) and i["app"].lower() not in hide]


def app_counts(items: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for i in items:
        counts[i["app"]] = counts.get(i["app"], 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def reasons(s: Suggestion, names: dict[int, str | None]) -> list[str]:
    """Why someone is suggested, in words."""
    out = []
    who = [f"@{names[f]}" if names.get(f) else f"fid {f}" for f in s.followed_by]
    if len(who) == 1:
        out.append(f"Followed by {who[0]}, whom you follow")
    elif who:
        shown = ", ".join(who[:3]) + (f" and {len(who) - 3} more" if len(who) > 3 else "")
        out.append(f"Followed by {len(who)} people you follow: {shown}")
    if s.channels:
        out.append("Both active in " + ", ".join(channel_name(c) or c for c in s.channels))
    return out


def _int(q, name: str, default: int) -> int:
    """A whole number from the query string, or a BadRequest that says so."""
    raw = str(q.get(name, default)).strip()
    if not re.fullmatch(r"-?[0-9]{1,30}", raw):
        raise BadRequest(f"{name} must be a whole number")
    return int(raw)


def _fid(q) -> int:
    """`fid` from the query string: a positive whole number."""
    raw = str(q.get("fid", "")).strip()
    if not re.fullmatch(r"[0-9]{1,30}", raw) or not 0 < int(raw) <= MAX_FID:
        raise BadRequest("fid must be a positive number")
    return int(raw)


def _split(value: str | None) -> set[str]:
    return {v.strip() for v in (value or "").split(",") if v.strip()}


def _trim(cache: dict, cap: int) -> None:
    """Drop the oldest entries until `cache` holds at most `cap`."""
    while len(cache) > cap:
        cache.pop(next(iter(cache)))


# --------------------------------------------------------------------- state


class UI:
    """Caches and node calls behind the JSON API.

    `public=True` is the hosted, read-only mode (phunk.social): no account of
    its own, and the page sends follows, replies and likes to Farcaster.
    """

    def __init__(self, hub: Hub, *, fid: int | None = None, key=None, public: bool = False,
                 casting: bool = CASTING, tip_fid: int | None = None, vote_rpc: str | None = None,
                 connect: bool = False):
        self.hub = hub
        self.connect = connect  # the hosted site can relay follows and likes signed in the page
        self.vote_rpc = vote_rpc  # Base JSON-RPC for the "voted NS" badge; None: no badge
        self.tip_fid = tip_fid
        self.fid = fid
        self.public = public
        self.casting = casting
        self.writer = None
        if key is not None and fid is not None and not public:
            from .write import Writer

            self.writer = Writer(hub, fid, key)
        self._users: dict[int, asyncio.Future] = {}
        self._feeds: dict[tuple[int, int], tuple[float, asyncio.Future]] = {}
        self._suggest: dict[int, tuple[float, asyncio.Future]] = {}
        self._people: dict[int, tuple[float, asyncio.Future]] = {}
        self._fids: dict[str, tuple[float, asyncio.Future]] = {}
        self._paths: dict[tuple[int, int], tuple[float, asyncio.Future]] = {}
        # 3-step paths read a list per account either end follows: fine on
        # your own node, too much for the shared public one. 2 steps read
        # only the accounts both follow.
        self.path_hops = 2 if public and hub.url.rstrip("/") == DEFAULT_NODE else 3
        self.followed_here: set[int] = set()  # keep them out of suggestions
        self.min_refresh = PUBLIC_MIN_REFRESH if public else 0

    def _fresh(self, hit, ttl: int, refresh: bool) -> bool:
        """Is a cached (time, future) still good to serve?"""
        if not hit or (hit[1].done() and hit[1].exception()):
            return False
        return time.monotonic() - hit[0] < (self.min_refresh if refresh else ttl)

    # ------------------------------------------------------------ cached reads

    async def user(self, fid: int) -> User:
        if fid not in self._users:
            async def load():
                try:
                    return await self.hub.user(fid)
                except NodeError:
                    return User(fid=fid)
            self._users[fid] = fut = asyncio.ensure_future(load())
            _trim(self._users, MAX_USERS)
            return await fut
        return await self._users[fid]

    async def voters(self, fids) -> set[int]:
        """Which of `fids` hold the "I voted for Network School Farcaster" NFT
        in any verified wallet: a fresh balanceOf check each time, nothing
        cached. If Base can't be reached the stamp just doesn't show."""
        fids = list(dict.fromkeys(fids))
        if not self.vote_rpc or not fids:
            return set()

        async def addresses(fid):
            try:
                return await self.hub.eth_addresses(fid)
            except NodeError:
                return []
        found = await asyncio.gather(*(addresses(f) for f in fids))
        try:
            held = await holders(self.hub.session, [a for a_s in found for a in a_s], rpc=self.vote_rpc)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return set()
        return {f for f, a_s in zip(fids, found) if any(a in held for a in a_s)}

    async def names(self, fids) -> dict[int, str | None]:
        fids = list(dict.fromkeys(fids))
        users = await asyncio.gather(*(self.user(f) for f in fids))
        return {u.fid: u.username for u in users}

    async def app_label(self, cast: Cast) -> str:
        try:
            app = await self.hub.app_of(cast)
        except NodeError:
            app = None
        if app is None:
            return "unknown"
        if app == cast.fid:
            return "own key"
        try:
            return await self.hub.app_name(app) or f"fid {app}"
        except NodeError:
            return f"fid {app}"

    def user_json(self, u: User) -> dict:
        return {"fid": u.fid, "username": u.username, "display_name": u.display_name, "pfp": u.pfp,
                "bio": u.bio}

    async def cast_json(self, c: Cast) -> dict:
        author, app, names = await asyncio.gather(
            self.user(c.fid), self.app_label(c),
            self.names(list(c.mentions) + ([c.parent_fid] if c.parent_fid else [])))
        return {
            "fid": c.fid, "hash": c.hash, "ts": int(to_unix(c.timestamp)),
            "text": with_mentions(c, names), "embeds": c.embeds[:4],
            "channel": channel_name(c.parent_url) if not c.is_reply else None,
            "channel_url": c.parent_url if not c.is_reply else None,
            "reply_to": {"fid": c.parent_fid, "hash": c.parent_hash,
                         "username": names.get(c.parent_fid)} if c.is_reply else None,
            "author": self.user_json(author), "app": app,
        }

    async def feed_window(self, fid: int, hours: int, refresh: bool = False) -> tuple[list[dict], bool]:
        """Every cast in the window, newest first, with author and app, and
        whether the follows read were capped (hosted site only). Cached."""
        key = (fid, hours)
        hit = self._feeds.get(key)
        if self._fresh(hit, FEED_TTL, refresh):
            return await hit[1]

        async def load():
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
            follows, capped = None, False
            if self.public:  # bound the cost of one request: the most recent follows only
                follows = [f.target_fid async for f in
                           self.hub.following(fid, limit=PUBLIC_MAX_FOLLOWS + 1, newest_first=True)]
                follows = list(dict.fromkeys(follows))
                capped = len(follows) > PUBLIC_MAX_FOLLOWS
                follows = follows[:PUBLIC_MAX_FOLLOWS]
            casts = await self.hub.feed(fid, since=since, per_account=10 if hours <= 24 else 25, limit=500,
                                        follows=follows)
            return list(await asyncio.gather(*(self.cast_json(c) for c in casts))), capped

        fut = asyncio.ensure_future(load())
        self._feeds[key] = (time.monotonic(), fut)
        _trim(self._feeds, MAX_WINDOWS)
        return await fut

    async def suggestions(self, fid: int, refresh: bool = False) -> list[dict]:
        hit = self._suggest.get(fid)
        if not self._fresh(hit, SUGGEST_TTL, refresh):
            async def load():
                found = await suggest(self.hub, fid, limit=25)
                names = await self.names(f for s in found for f in s.followed_by[:3])
                users = await asyncio.gather(*(self.user(s.fid) for s in found))
                return [dict(self.user_json(u), score=s.score, reasons=reasons(s, names),
                             followed_by=len(s.followed_by),
                             channels=[channel_name(c) for c in s.channels])
                        for s, u in zip(found, users)]
            hit = (time.monotonic(), asyncio.ensure_future(load()))
            self._suggest[fid] = hit
            _trim(self._suggest, MAX_WINDOWS)
        return [s for s in await hit[1] if s["fid"] not in self.followed_here]

    async def people(self, fid: int, refresh: bool = False) -> dict:
        """The people feed for `fid`: cards plus a summary. Cached."""
        hit = self._people.get(fid)
        if not self._fresh(hit, SUGGEST_TTL, refresh):
            async def load():
                feed = await people_feed(self.hub, fid, limit=30, channel_name=lambda u: channel_name(u) or u)
                profiles = feed["profiles"]

                async def card_json(c):
                    u = await self.user(c.fid)
                    p = profiles.get(c.fid)
                    latest = [await self.cast_json(x) for x in (p.latest if p else [])]
                    return dict(self.user_json(u), kind=c.kind, why=c.why, talked=c.talked,
                                following=c.following, followed_by=len(c.followed_by),
                                app_now=p.app_now if p else None, app_before=p.app_before if p else None,
                                latest=latest)
                cards = await asyncio.gather(*(card_json(c) for c in feed["cards"]))
                return {"summary": feed["summary"], "cards": list(cards)}
            hit = (time.monotonic(), asyncio.ensure_future(load()))
            self._people[fid] = hit
            _trim(self._people, MAX_WINDOWS)
        out = await hit[1]
        voted = await self.voters(c["fid"] for c in out["cards"])  # fresh on every load
        cards = [dict(c, following=c["following"] or c["fid"] in self.followed_here, ns_vote=c["fid"] in voted)
                 for c in out["cards"]]
        return {"summary": out["summary"], "cards": cards}

    async def who(self, value: str | None) -> int:
        """A username or fid from the query, or the key's fid."""
        value = (value or "").strip().lstrip("@")
        if _digits(value):
            if not 0 < int(value) <= MAX_FID:
                raise NoSuchAccount(f"No Farcaster account with fid {int(value)}.")
            return int(value)
        if value:
            name = value.lower()

            async def lookup() -> int:
                try:
                    return await self.hub.fid_by_name(name)
                except NotFound as e:
                    raise NoSuchAccount(f"No Farcaster account called @{name}.") from e

            hit = self._fids.get(name)
            if not self._fresh(hit, NAME_TTL, False):
                hit = (time.monotonic(), asyncio.ensure_future(lookup()))
                self._fids[name] = hit
                _trim(self._fids, MAX_USERS)
            return await hit[1]
        if self.fid is None:
            raise LookupError("pick an account to browse as")
        return self.fid

    # ---------------------------------------------------- the read endpoints
    # Each takes the query string as a dict and returns JSON. Shared by
    # `phunk ui` (make_app) and the hosted site (api/index.py, via get_json).

    async def me_json(self, q) -> dict:
        hint = PUBLIC_HELP if self.public else (None if self.writer else READ_ONLY_HELP)
        # The hosted site keeps its node's address to itself.
        out = {"fid": self.fid, "can_write": self.writer is not None, "can_cast": self.casting,
               "node": None if self.public else self.hub.url,
               "public": self.public, "help": hint, "can_connect": self.connect}
        if self.fid is not None:
            out["user"] = self.user_json(await self.user(self.fid))
        if self.tip_fid is not None:
            try:
                out["tip"] = self.user_json(await self.user(self.tip_fid))
            except (NotFound, NodeError):
                out["tip"] = {"fid": self.tip_fid, "username": None}
        return out

    async def feed_json(self, q) -> dict:
        fid = await self.who(q.get("who"))
        hours = _int(q, "hours", 24)
        if self.public and hours not in PUBLIC_HOURS:
            raise BadRequest("hours must be 6, 24, 72 or 168")
        hours = max(1, min(168, hours))
        offset = max(0, _int(q, "offset", 0))
        limit = max(1, min(100, _int(q, "limit", PAGE)))
        items, capped = await self.feed_window(fid, hours, refresh=q.get("refresh") == "1")
        kept = filter_by_app(items, only=_split(q.get("only")) or None, hide=_split(q.get("hide")))
        out = {"fid": fid, "hours": hours, "offset": offset, "total": len(kept), "all": len(items),
               "apps": app_counts(items), "casts": kept[offset:offset + limit],
               "more": offset + limit < len(kept)}
        if self.public:
            out["capped"] = capped  # true: read only the PUBLIC_MAX_FOLLOWS most recent follows
        return out

    async def suggestions_json(self, q) -> dict:
        fid = await self.who(q.get("who"))
        return {"fid": fid, "people": await self.suggestions(fid, refresh=q.get("refresh") == "1")}

    async def people_json(self, q) -> dict:
        fid = await self.who(q.get("who"))
        return dict(await self.people(fid, refresh=q.get("refresh") == "1"), fid=fid)


    async def path_json(self, q) -> dict:
        """Who could introduce `from` to `to`: the shortest paths of mutual
        follows (up to `path_hops` steps), warmest first, with the people on them."""
        start, end = await asyncio.gather(self.who(q.get("from")), self.who(q.get("to")))
        if start == end:
            raise BadRequest("pick two different accounts")
        key = (start, end)
        hit = self._paths.get(key)
        if not self._fresh(hit, PATH_TTL, q.get("refresh") == "1"):
            max_follows = PUBLIC_MAX_FOLLOWS if self.public else 5000
            hit = (time.monotonic(), asyncio.ensure_future(
                paths(self.hub, start, end, max_hops=self.path_hops, limit=PATH_SHOWN, max_follows=max_follows)))
            self._paths[key] = hit
            _trim(self._paths, MAX_WINDOWS)
        found = await hit[1]
        fids = list(dict.fromkeys([start, end, *(f for p in found.paths for f in p.fids)]))
        users = await asyncio.gather(*(self.user(f) for f in fids))
        return {"from": start, "to": end, "hops": found.hops, "reach": self.path_hops, "total": found.total,
                "paths": [{"fids": list(p.fids), "talks": list(p.talks)} for p in found.paths],
                "users": {str(u.fid): {"fid": u.fid, "username": u.username, "display_name": u.display_name,
                                       "pfp": u.pfp} for u in users}}

    async def owner_json(self, q) -> dict:
        """Who owns an account (custody address) and its key nonce: what
        authorizing a gasless KEY_ADD needs."""
        fid = _fid(q)
        custody = await self.hub.custody_address(fid)
        signers = await self.hub.signers(fid)
        return {"fid": fid, "custody": custody, "nonce": int(signers.get("currentUserNonce") or 0)}


    async def key_json(self, q) -> dict:
        """Is this app key active on the account? From the node, the source of
        truth: the mini app asks it on launch to know phunk is still connected."""
        fid = _fid(q)
        key = str(q.get("key", "")).lower()
        if not re.fullmatch(r"0x[0-9a-f]{64}", key):
            raise BadRequest("key must be 0x and 64 hex characters")
        signers = await self.hub.collect("/v1/signersByFid", {"fid": fid}, key="signers")
        return {"fid": fid, "key": key, "active": any(str(s.get("key", "")).lower() == key for s in signers)}


GET_ROUTES = {"/api/me": UI.me_json, "/api/feed": UI.feed_json,
              "/api/suggestions": UI.suggestions_json, "/api/people": UI.people_json,
              "/api/path": UI.path_json, "/api/owner": UI.owner_json, "/api/key": UI.key_json}


async def answer(coro, *, public: bool = False) -> tuple[int, dict]:
    """Await an endpoint; turn the errors it can raise into (status, JSON).

    `public` (the hosted site): node errors get a plain message, and the
    details go to the server's log only."""
    try:
        return 200, await coro
    except LookupError as e:  # NotFound is not a LookupError; KeyError is
        return 400, {"error": str(e).strip("'")}
    except NoSuchAccount as e:
        return 404, {"error": str(e)}
    except NotFound as e:
        if public:
            log.warning("not found: %s", e)
            return 404, {"error": "not found"}
        return 404, {"error": f"not found: {e}"}
    except BadRequest as e:
        return 400, {"error": str(e)}
    except ValueError as e:  # from deeper down (bad JSON from the node, a decode): not for users
        if public:
            log.warning("bad request: %r", e)
            return 400, {"error": "bad request"}
        return 400, {"error": str(e)}
    except NodeError as e:
        if public:
            log.warning("node error: %s", e)
            return 502, {"error": NODE_BUSY}
        return 502, {"error": f"node error: {e}"}


async def get_json(ui: UI, path: str, query) -> tuple[int, dict]:
    """One GET of the read API: (status, JSON body)."""
    route = GET_ROUTES.get(path)
    if route is None:
        return 404, {"error": "no such endpoint"}
    return await answer(route(ui, query), public=ui.public)


# ---------------------------------------------------------------------- web


def _json_error(status: int, message: str, **extra) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


def _host_of(request: web.Request) -> str:
    host = request.host or ""
    if host.startswith("["):
        return host[1:].split("]")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def make_guard(check_host: bool):
    @web.middleware
    async def guard(request: web.Request, handler):
        if check_host and _host_of(request).lower() not in LOCAL_HOSTS:
            return _json_error(403, "this server only answers on localhost")
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("Origin")
            if origin is not None and origin != f"{request.scheme}://{request.host}":
                return _json_error(403, "cross-origin request refused")
            if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                return _json_error(403, "cross-site request refused")
            if request.content_type != "application/json":
                return _json_error(415, "send JSON")
        resp = await handler(request)
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' https: data:; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp
    return guard


def make_app(ui: UI, *, check_host: bool = True) -> web.Application:
    app = web.Application(middlewares=[make_guard(check_host)], client_max_size=64 * 1024)
    r = web.RouteTableDef()

    def need_writer():
        if ui.writer is None:
            raise web.HTTPForbidden(text=_dumps({"error": ui.public and PUBLIC_HELP or READ_ONLY_HELP,
                                                 "read_only": True}),
                                    content_type="application/json")
        return ui.writer

    def need_casting():
        if not ui.casting:
            raise web.HTTPForbidden(text=_dumps({"error": CASTING_SOON, "coming_soon": True}),
                                    content_type="application/json")

    async def body(request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text=_dumps({"error": "bad JSON"}), content_type="application/json")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text=_dumps({"error": "expected a JSON object"}),
                                     content_type="application/json")
        return data

    async def run(coro):
        status, data = await answer(coro, public=ui.public)
        return web.json_response(data, status=status)

    @r.get("/")
    async def index(request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def read(request):
        status, data = await get_json(ui, request.path, request.query)
        return web.json_response(data, status=status)

    for path in GET_ROUTES:
        r.get(path)(read)

    @r.post("/api/cast")
    async def cast(request):
        need_casting()
        w = need_writer()
        d = await body(request)

        async def go():
            text = str(d.get("text") or "").strip()
            if not text:
                raise BadRequest("write something first")
            reply_to = cast_ref(d["reply_to"]) if d.get("reply_to") else None
            channel = None if reply_to else channel_url(d.get("channel"))
            h = await w.cast(text, reply_to=reply_to, channel=channel)
            return {"hash": h}
        return await run(go())

    @r.post("/api/react")
    async def react(request):
        d = await body(request)
        if d.get("action") in ("recast", "unrecast"):
            need_casting()  # a recast puts the cast in your followers' feeds: comes with casting
        w = need_writer()

        async def go():
            action = d.get("action")
            if action not in ("like", "unlike", "recast", "unrecast"):
                raise BadRequest("action is like, unlike, recast or unrecast")
            fid, h = cast_ref(d.get("cast"))
            return {"hash": await getattr(w, action)(fid, h)}
        return await run(go())

    @r.post("/api/follow")
    async def follow(request):
        w = need_writer()
        d = await body(request)

        async def go():
            action = d.get("action", "follow")
            if action not in ("follow", "unfollow"):
                raise BadRequest("action is follow or unfollow")
            target = d.get("fid")
            if not _digits(target):
                raise BadRequest("fid must be a number")
            h = await getattr(w, action)(int(target))
            if action == "follow":
                ui.followed_here.add(int(target))
            else:
                ui.followed_here.discard(int(target))
            return {"hash": h}
        return await run(go())

    app.add_routes(r)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def _dumps(obj) -> str:
    return json.dumps(obj)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def serve(hub: Hub, *, host: str = "127.0.0.1", port: int = 8008, fid: int | None = None,
                key=None, open_browser: bool = False, public: bool = False,
                casting: bool = CASTING, tip_fid: int | None = None, vote_rpc: str | None = None) -> None:
    """Run the UI until interrupted. `public=True` previews the hosted, read-only site."""
    ui = UI(hub, fid=fid, key=key, public=public, casting=casting, tip_fid=tip_fid, vote_rpc=vote_rpc)
    app = make_app(ui, check_host=is_loopback(host))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    shown = f"[{host}]" if ":" in host else host
    url = f"http://{shown}:{port}/"
    mode = f"as fid {fid}, {'can post' if casting else 'can follow'}" if ui.writer else (f"as fid {fid}, read-only" if fid else "read-only")
    if public:
        mode = "public preview, read-only"
    print(f"phunk ui on {url} ({mode}, node {hub.url})")
    if not is_loopback(host):
        print("warning: not bound to localhost. Anyone who can reach this port can use it"
              + (", and post as you." if ui.writer else "."))
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
