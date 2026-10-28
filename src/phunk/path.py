"""Paths to someone: who could introduce you, over mutual follows.

A step counts only when both accounts follow each other. Paths stop at 3
steps (you, A, B, them): a longer chain is too long for an intro.

What it reads: whom you follow, and whom each of them follows (that is how
it knows who follows you back). The same for the other person. Those lists
also say whether a friend of yours and a friend of theirs follow each other,
so 3 steps need nothing more: about one request per account the two of you
follow.

Accounts that follow thousands make most short paths, and almost never
introduce anyone, so go-betweens who follow more than `MAX_FOLLOWING` are
left out. Paths are ranked by how often each end talked with its
go-between lately (replies, mentions, likes, recasts).

`find()` and `rank()` are pure; `paths()` reads the node.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
from dataclasses import dataclass, field

from .hub import Hub, NodeError
from .people import TALK_DAYS, talked_to

MAX_HOPS = 3
MAX_FOLLOWS = 5000  # follows read for each end, newest first
MAX_FOLLOWING = 5000  # a go-between who follows more is left out


@dataclass
class Path:
    fids: tuple[int, ...]  # start first, end last
    talks: tuple[int, int] = (0, 0)  # start with the first go-between, end with the last
    busy: int = 0  # the most accounts any go-between follows

    @property
    def hops(self) -> int:
        return len(self.fids) - 1

    @property
    def via(self) -> tuple[int, ...]:
        return self.fids[1:-1]


@dataclass
class Found:
    start: int
    end: int
    hops: int | None  # the shortest path's steps; None when there is none within reach
    paths: list[Path] = field(default_factory=list)  # the shortest ones, warmest first
    total: int = 0  # shortest paths found, before the limit
    lists: int = 0  # follow lists read
    partial: bool = False  # an end follows more than MAX_FOLLOWS; only the newest were read
    skipped: int = 0  # go-betweens left out for following too many


def find(start: int, end: int, follows: dict[int, set[int]], *, max_hops: int = MAX_HOPS,
         skip: set[int] = frozenset()) -> list[tuple[int, ...]]:
    """The shortest paths from `start` to `end` where every step is a mutual
    follow, each a tuple of fids. No network.

    `follows` maps a fid to the fids it follows. It needs `start`, `end` and
    everyone they follow; that covers paths of up to 3 steps. Nobody in
    `skip` is a go-between. [] when there is no path within `max_hops`."""
    if start == end or max_hops < 1:
        return []

    def mutual(x: int, y: int) -> bool:
        return y in follows.get(x, ()) and x in follows.get(y, ())

    if mutual(start, end):
        return [(start, end)]
    banned = set(skip) | {start, end}
    mine = {x for x in follows.get(start, ()) if x not in banned and mutual(start, x)}
    theirs = {y for y in follows.get(end, ()) if y not in banned and mutual(end, y)}
    if max_hops >= 2 and mine & theirs:
        return [(start, x, end) for x in sorted(mine & theirs)]
    if max_hops >= 3:
        # No one is in both sets here, so each x and y below is a new face.
        return [(start, x, y, end) for x in sorted(mine)
                for y in sorted(follows.get(x, set()) & theirs) if mutual(x, y)]
    return []


def rank(found: list[tuple[int, ...]], *, talked_start: collections.Counter, talked_end: collections.Counter,
         following: dict[int, int]) -> list[Path]:
    """Warmest first: the most talks between each end and its go-between.
    On a tie, go-betweens who follow fewer accounts come first; their follow
    back means more. No network."""
    out = []
    for fids in found:
        via = fids[1:-1]
        talks = (talked_start[via[0]], talked_end[via[-1]]) if via else (0, 0)
        out.append(Path(fids, talks, max((following.get(f, 0) for f in via), default=0)))
    out.sort(key=lambda p: (-sum(p.talks), -min(p.talks), p.busy, p.fids))
    return out


async def _follows(hub: Hub, fid: int, limit: int) -> set[int]:
    try:
        return {f.target_fid async for f in hub.following(fid, limit=limit, newest_first=True)}
    except NodeError:
        return set()


async def _talked(hub: Hub, fid: int, since: dt.datetime) -> collections.Counter:
    try:
        casts, likes, recasts = await asyncio.gather(
            _all(hub.casts(fid, since=since, limit=300)),
            _all(hub.reactions(fid, "like", since=since, limit=300)),
            _all(hub.reactions(fid, "recast", since=since, limit=300)))
    except NodeError:
        return collections.Counter()
    return talked_to(fid, casts, likes + recasts)


async def _all(agen) -> list:
    return [x async for x in agen]


async def paths(hub: Hub, start: int, end: int, *, max_hops: int = MAX_HOPS, limit: int = 10,
                max_follows: int = MAX_FOLLOWS, max_following: int = MAX_FOLLOWING,
                avoid: set[int] = frozenset()) -> Found:
    """The shortest mutual-follow paths from `start` to `end`, warmest first.
    Nobody in `avoid` is a go-between.

    About one request per account the two of them follow (cut at
    `max_follows` each), plus a few for who talked with whom."""
    fa, fb = await asyncio.gather(_follows(hub, start, max_follows), _follows(hub, end, max_follows))
    follows = {start: fa, end: fb}
    out = Found(start, end, None, lists=2, partial=max(len(fa), len(fb)) >= max_follows)
    if max_hops >= 2 and not (end in fa and start in fb):
        others = sorted((fa | fb) - {start, end})
        # One more than the cap is enough to know someone follows too many.
        sets = await asyncio.gather(*(_follows(hub, f, max_following + 1) for f in others))
        follows.update(zip(others, sets))
        out.lists += len(others)
    skip = {f for f, s in follows.items() if f not in (start, end) and len(s) > max_following}
    out.skipped = len(skip)

    found = find(start, end, follows, max_hops=max_hops, skip=skip | set(avoid))
    if not found:
        return out
    talked_start = talked_end = collections.Counter()
    if len(found[0]) > 2:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=TALK_DAYS)
        talked_start, talked_end = await asyncio.gather(_talked(hub, start, since), _talked(hub, end, since))
    ranked = rank(found, talked_start=talked_start, talked_end=talked_end,
                  following={f: len(s) for f, s in follows.items()})
    out.hops, out.total, out.paths = len(found[0]) - 1, len(found), ranked[:limit]
    return out
