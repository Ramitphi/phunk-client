"""The people feed: who to connect with, and why.

phunk's main screen is a feed of people, not posts. Each card is one person,
one plain reason, and their latest casts. There are three kinds:

* **reconnect**: people you talked to (replied to, mentioned, liked or
  recast) who now post from a different app, went quiet, or whom you never
  followed. Leaving an app shouldn't mean losing them.
* **circle**: friends of friends with many mutual follows. The fastest way
  to feel at home on a new client.
* **bridge**: people with only a few mutual follows who post from a
  different app or in channels you don't use. They keep the network from
  splitting into islands.

Everything comes from the node: your casts and reactions, follow lists,
and the key that signed each cast (which says which app posted it).
`build()` is pure and does the ranking; `people_feed()` reads the node.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
from dataclasses import dataclass, field

from .hub import Hub, NodeError
from .models import Cast
from .recommend import suggest
from .timeutil import from_datetime

TALK_DAYS = 90  # how far back "you talked" looks
MOVED_AFTER_DAYS = 14  # casts older than this show which app someone used before
PROFILE_CASTS = 100  # casts read per person: enough to reach back past a recent move
NOW_CASTS = 5  # the app someone uses now: the most common on their newest casts
QUIET_DAYS = 14
CIRCLE_MIN = 4  # mutual follows for a "circle" card; 2-3 can be a "bridge"
MIN_TALKS = 2


@dataclass
class Profile:
    """How someone posts lately."""

    fid: int
    latest: list[Cast] = field(default_factory=list)  # newest first
    app_now: str | None = None  # app that signed their newest cast
    app_before: str | None = None  # most common app on their older casts
    last_ts: int | None = None  # Farcaster time of their newest cast
    channels: list[str] = field(default_factory=list)  # where they post, most first

    @property
    def moved(self) -> bool:
        return bool(self.app_now and self.app_before and self.app_now != self.app_before)


@dataclass
class Card:
    fid: int
    kind: str  # "reconnect", "circle" or "bridge"
    why: list[str]
    score: float
    talked: int = 0
    followed_by: list[int] = field(default_factory=list)
    following: bool = False


CHANNEL_PREFIXES = ("https://warpcast.com/~/channel/", "https://farcaster.xyz/~/channel/", "chain://")


def is_channel(url: str | None) -> bool:
    """A cast's parentUrl can be any URL. Only these are channels."""
    return bool(url) and url.startswith(CHANNEL_PREFIXES)


def _place(url: str, channel_name) -> str:
    """"/dev", or "a channel" when the channel has no readable name."""
    name = channel_name(url)
    return name if name and name.startswith("/") else "a channel"


def _app(label: str | None, whose: str) -> str:
    """"own key" -> "their own key"; app names stay as they are."""
    return f"{whose} own key" if label == "own key" else (label or "an unknown app")


def _who(fids: list[int], names: dict[int, str | None], n: int = 3) -> str:
    shown = [f"@{names[f]}" if names.get(f) else f"fid {f}" for f in fids[:n]]
    more = f" and {len(fids) - n} more" if len(fids) > n else ""
    return ", ".join(shown) + more


def talked_to(me: int, casts: list[Cast], reactions: list) -> collections.Counter:
    """{fid: times} you replied to, mentioned, liked or recast them."""
    out: collections.Counter = collections.Counter()
    for c in casts:
        if c.parent_fid and c.parent_fid != me:
            out[c.parent_fid] += 1
        for m in set(c.mentions):
            if m != me:
                out[m] += 1
    for r in reactions:
        if r.target_fid and r.target_fid != me:
            out[r.target_fid] += 1
    return out


def build(
    me: int,
    *,
    following: set[int],
    talked: collections.Counter,
    suggestions: list,  # recommend.Suggestion
    profiles: dict[int, Profile],
    names: dict[int, str | None],
    my_app: str | None,
    my_channels: set[str],
    channel_name=lambda url: url,
    now: int | None = None,
    dismissed: set[int] = frozenset(),
    limit: int = 15,
    max_reconnect: int = 5,
) -> list[Card]:
    """Rank people into cards. No network.

    Reconnect cards come first, then circle and bridge cards alternate, so
    the feed mixes "settle in" with "reach out"."""
    now = from_datetime(dt.datetime.now(dt.timezone.utc)) if now is None else now
    skip = {me} | set(dismissed)

    reconnect = []
    for fid, n in talked.most_common():
        if fid in skip or n < MIN_TALKS:
            continue
        p = profiles.get(fid)
        follows = fid in following
        quiet = None
        if p and p.last_ts is not None:
            days = (now - p.last_ts) // 86400
            quiet = days if days >= QUIET_DAYS else None
        moved = bool(p and p.moved)
        if follows and not moved and quiet is None:
            continue  # nothing to reconnect
        why = [f"You talked {n} times in the last {TALK_DAYS} days."]
        if moved:
            why.append(f"Now posts from {_app(p.app_now, 'their')} (was {_app(p.app_before, 'their')}).")
        if quiet is not None:
            why.append(f"Quiet for {quiet} days.")
        if not follows:
            why.append("You don't follow them yet.")
        reconnect.append(Card(fid, "reconnect", why, n + 5 * moved + 3 * (not follows),
                              talked=n, following=follows))
    reconnect.sort(key=lambda c: (-c.score, c.fid))
    reconnect = reconnect[:max_reconnect]
    taken = {c.fid for c in reconnect}

    circle, bridge = [], []
    for s in suggestions:
        if s.fid in skip or s.fid in taken or s.fid in following:
            continue
        p = profiles.get(s.fid)
        fb = list(s.followed_by)
        shared = [_place(c, channel_name) for c in s.channels if is_channel(c)]
        shared = [x for x in dict.fromkeys(shared) if x != "a channel"]
        elsewhere_app = p and p.app_now and my_app and p.app_now != my_app
        their_top = p.channels[0] if p and p.channels else None
        elsewhere_channel = their_top and their_top not in my_channels
        why = []
        if fb:
            why.append(f"Followed by {len(fb)} {'person' if len(fb) == 1 else 'people'} you follow: "
                       f"{_who(fb, names)}.")
        if 2 <= len(fb) < CIRCLE_MIN and (elsewhere_app or elsewhere_channel):
            if elsewhere_app:
                why.append(f"Posts from {_app(p.app_now, 'their')}; you post from {_app(my_app, 'your')}.")
            if elsewhere_channel:
                where = _place(their_top, channel_name)
                why.append(f"Most active in {where}, which you don't post in." if where != "a channel"
                           else "Most active in a channel you don't post in.")
            bridge.append(Card(s.fid, "bridge", why, len(fb), followed_by=fb))
        else:
            if shared:
                why.append("Both active in " + ", ".join(shared) + ".")
            circle.append(Card(s.fid, "circle", why, s.score, followed_by=fb))

    mixed: list[Card] = list(reconnect)
    for pair in zip(circle, bridge):
        mixed.extend(pair)
    longer = circle if len(circle) > len(bridge) else bridge
    mixed.extend(longer[min(len(circle), len(bridge)):])
    return mixed[:limit]


# ------------------------------------------------------------------ node


async def app_label(hub: Hub, cast: Cast) -> str:
    """Name of the app that signed a cast: an app's username, "own key" or "unknown"."""
    try:
        app = await hub.app_of(cast)
    except NodeError:
        return "unknown"
    if app is None:
        return "unknown"
    if app == cast.fid:
        return "own key"
    try:
        return await hub.app_name(app) or f"fid {app}"
    except NodeError:
        return f"fid {app}"


def _most_common(labels: list[str]) -> str | None:
    """The most used app, ignoring "unknown" when anything else is there.
    Ties go to the newest (labels are newest first)."""
    known = [x for x in labels if x != "unknown"] or labels
    if not known:
        return None
    counts = collections.Counter(known)
    top = max(counts.values())
    return next(x for x in known if counts[x] == top)


def apps_now_and_before(casts: list[Cast], labels: list[str], now: int) -> tuple[str | None, str | None]:
    """(app now, app before) from someone's casts, newest first.

    Now is the most used app on the newest few casts. Before is the most
    used app on casts older than `MOVED_AFTER_DAYS`. Someone who posts a lot
    may have no casts that old in what we read; then the oldest third of
    their casts stands in for "before", so a move a few days ago still shows."""
    if not casts:
        return None, None
    app_now = _most_common(labels[:NOW_CASTS])
    rest = list(zip(casts, labels))[NOW_CASTS:]
    older = [x for c, x in rest if now - c.timestamp > MOVED_AFTER_DAYS * 86400]
    if len(older) < NOW_CASTS and len(rest) >= NOW_CASTS:
        older = [x for _, x in rest[-max(NOW_CASTS, len(casts) // 3):]]
    return app_now, _most_common(older)


async def profile(hub: Hub, fid: int, *, casts: int = PROFILE_CASTS, now: int | None = None) -> Profile:
    """Someone's recent posting: latest casts, current app, earlier app, channels.

    One request for casts, plus the key lookup that `app_of` caches per fid
    (so naming the app of every cast costs no more requests)."""
    now = from_datetime(dt.datetime.now(dt.timezone.utc)) if now is None else now
    try:
        recent = [c async for c in hub.casts(fid, limit=casts)]
    except NodeError:
        recent = []
    recent = [c for c in recent if c.timestamp <= now + 600]  # skip future-dated casts
    p = Profile(fid)
    if not recent:
        return p
    p.latest = recent[:2]
    p.last_ts = recent[0].timestamp
    labels = list(await asyncio.gather(*(app_label(hub, c) for c in recent)))
    p.app_now, p.app_before = apps_now_and_before(recent, labels, now)
    chans = collections.Counter(c.parent_url for c in recent if is_channel(c.parent_url) and not c.is_reply)
    p.channels = [u for u, _ in chans.most_common(3)]
    return p


async def people_feed(hub: Hub, fid: int, *, dismissed: set[int] = frozenset(), limit: int = 15,
                      check_talked: int = 15, check_circle: int = 12, check_bridge: int = 12,
                      channel_name=lambda u: u) -> dict:
    """Cards for `fid`, plus a one-line summary of their network.

    Roughly: your follows, your casts and reactions from the last 90 days,
    the friend-of-friend sample in `recommend.suggest`, then one profile
    (casts + keys) per person checked. About 150 requests on first load.

    Only people with a profile check can become bridge cards, so the
    candidates are split into two pools: many mutual follows (circle) and a
    few (bridge)."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=TALK_DAYS)
    now = from_datetime(dt.datetime.now(dt.timezone.utc))

    async def safe(agen):
        try:
            return [x async for x in agen]
        except NodeError:
            return []

    follows, my_casts, likes, recasts, suggestions = await asyncio.gather(
        safe(hub.following(fid)),
        safe(hub.casts(fid, since=since, limit=300)),
        safe(hub.reactions(fid, "like", since=since, limit=300)),
        safe(hub.reactions(fid, "recast", since=since, limit=300)),
        suggest(hub, fid, limit=None, exclude=set(dismissed)),
    )
    following = {f.target_fid for f in follows}
    talked = talked_to(fid, my_casts, likes + recasts)
    talked_fids = [f for f, n in talked.most_common() if n >= MIN_TALKS and f not in dismissed][:check_talked]
    # Two pools: many mutuals (circle) and a few (bridge candidates, which
    # need a profile to see whether they post from another app or channel).
    fresh = [s for s in suggestions if s.fid not in following]
    pool_circle = [s for s in fresh if len(s.followed_by) >= CIRCLE_MIN or not s.followed_by][:check_circle]
    pool_bridge = [s for s in fresh if 2 <= len(s.followed_by) < CIRCLE_MIN][:check_bridge]
    suggestions = pool_circle + pool_bridge
    sugg_fids = [s.fid for s in suggestions]

    me_profile, *checked = await asyncio.gather(
        profile(hub, fid, now=now), *(profile(hub, f, now=now) for f in dict.fromkeys(talked_fids + sugg_fids)))
    profiles = {p.fid: p for p in checked}
    my_app = me_profile.app_now
    my_channels = set(me_profile.channels) | {c.parent_url for c in my_casts
                                              if is_channel(c.parent_url) and not c.is_reply}

    name_fids = {f for s in suggestions for f in s.followed_by[:3]}
    users = await asyncio.gather(*(hub.user(f) for f in name_fids), return_exceptions=True)
    names = {u.fid: u.username for u in users if not isinstance(u, Exception)}

    cards = build(fid, following=following, talked=talked, suggestions=suggestions, profiles=profiles,
                  names=names, my_app=my_app, my_channels=my_channels, channel_name=channel_name,
                  now=now, dismissed=set(dismissed), limit=limit)

    talked_any = [f for f in talked if f != fid]
    checked_talked = [profiles[f] for f in talked_fids if f in profiles]
    summary = {
        "following": len(following),
        "talked_to": len(talked_any),
        "moved": sum(1 for p in checked_talked if p.moved),
        "not_following": sum(1 for f in talked_fids if f not in following),
        "my_app": my_app,
        "days": TALK_DAYS,
    }
    return {"cards": cards, "profiles": profiles, "summary": summary}
