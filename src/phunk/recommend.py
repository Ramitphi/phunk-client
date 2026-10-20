"""People to follow, from node data only.

Two signals, both read straight from the node:

* **Friends of friends.** Accounts followed by several of the people you
  follow. We read the newest follows of a sample of the people you follow
  (the ones you followed most recently), not everyone, to keep request
  counts low on a shared node.
* **Shared channels.** People who posted recently in the channels you post in.

The score is a plain count: one point for each person you follow who follows
them, plus `CHANNEL_POINTS` for each channel you share. People you already
follow, and you, are never suggested.
"""
from __future__ import annotations

import asyncio
import collections
from dataclasses import dataclass, field

from .hub import Hub, NodeError
from .models import Follow

CHANNEL_POINTS = 2


@dataclass
class Suggestion:
    fid: int
    score: int
    followed_by: list[int] = field(default_factory=list)  # people you follow who follow them
    channels: list[str] = field(default_factory=list)  # channel URLs you both post in


def score(
    me: int,
    following: set[int],
    their_follows: dict[int, set[int]],
    channel_authors: dict[str, dict[int, int]],
    *,
    min_followed_by: int = 2,
    exclude: set[int] | frozenset = frozenset(),
    limit: int | None = None,
) -> list[Suggestion]:
    """Rank candidates. No network.

    `their_follows` maps each sampled account you follow to the fids it
    follows. `channel_authors` maps a channel URL to {fid: recent posts}.
    A friend-of-friend needs `min_followed_by` people you follow behind them,
    unless they also share a channel with you.
    """
    skip = set(following) | {me} | set(exclude)
    by: dict[int, list[int]] = collections.defaultdict(list)
    for friend, targets in their_follows.items():
        for t in targets:
            if t not in skip:
                by[t].append(friend)
    chans: dict[int, list[str]] = collections.defaultdict(list)
    posts: collections.Counter[int] = collections.Counter()
    for url, authors in channel_authors.items():
        for fid, n in authors.items():
            if fid not in skip:
                chans[fid].append(url)
                posts[fid] += n

    out = []
    for fid in set(by) | set(chans):
        fb, ch = sorted(by.get(fid, [])), sorted(chans.get(fid, []))
        if len(fb) < min_followed_by and not ch:
            continue
        out.append(Suggestion(fid, len(fb) + CHANNEL_POINTS * len(ch), fb, ch))
    # Ties: more people you follow first, then more channel posts, then the older account.
    out.sort(key=lambda s: (-s.score, -len(s.followed_by), -posts[s.fid], s.fid))
    return out if limit is None else out[:limit]


async def their_follows(hub: Hub, fid: int, limit: int) -> set[int]:
    """The newest `limit` accounts `fid` follows (one request for limit <= 1000)."""
    out = set()
    try:
        async for m in hub.pages("/v1/linksByFid", {"fid": fid, "link_type": "follow", "reverse": "true"},
                                 limit=limit):
            f = Follow.from_message(m)
            if f.target_fid:
                out.add(f.target_fid)
    except NodeError:
        pass
    return out


async def my_channels(hub: Hub, fid: int, *, casts: int = 100, top: int = 3) -> list[str]:
    """The channels `fid` posted in most, among their latest casts."""
    counts: collections.Counter[str] = collections.Counter()
    async for c in hub.casts(fid, limit=casts):
        if c.parent_url and not c.is_reply:
            counts[c.parent_url] += 1
    return [url for url, _ in counts.most_common(top)]


async def channel_authors(hub: Hub, url: str, limit: int) -> dict[int, int]:
    """{fid: posts} among a channel's latest `limit` casts."""
    counts: collections.Counter[int] = collections.Counter()
    try:
        async for c in hub.channel(url, limit=limit):
            counts[c.fid] += 1
    except NodeError:
        pass
    return dict(counts)


async def suggest(
    hub: Hub,
    fid: int,
    *,
    sample: int = 40,
    per_account: int = 300,
    channels: int = 3,
    per_channel: int = 100,
    min_followed_by: int = 2,
    exclude: set[int] | frozenset = frozenset(),
    limit: int | None = 20,
) -> list[Suggestion]:
    """People `fid` might follow, best first.

    Requests: your follow list, then one page each for `sample` of the people
    you follow, one for your casts and one per channel. About 50 in all.
    """
    follows = [f async for f in hub.following(fid)]
    following = {f.target_fid for f in follows}
    newest = sorted(follows, key=lambda f: f.timestamp, reverse=True)
    sampled = list(dict.fromkeys(f.target_fid for f in newest if f.target_fid != fid))[:sample]

    async def follows_of(t: int) -> tuple[int, set[int]]:
        return t, await their_follows(hub, t, per_account)

    async def authors_of(url: str) -> tuple[str, dict[int, int]]:
        return url, await channel_authors(hub, url, per_channel)

    try:
        urls = await my_channels(hub, fid, top=channels) if channels else []
    except NodeError:
        urls = []
    graph, chans = await asyncio.gather(
        asyncio.gather(*(follows_of(t) for t in sampled)),
        asyncio.gather(*(authors_of(u) for u in urls)),
    )
    return score(fid, following, dict(graph), dict(chans), min_followed_by=min_followed_by,
                 exclude=exclude, limit=limit)
