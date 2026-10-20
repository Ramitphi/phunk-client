"""A tiny command line, mostly to check a node works.

    phunk whois dwr
    phunk casts dwr -n 5
    phunk apps dwr
    phunk --node http://localhost:3381 info
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys

from .hub import DEFAULT_NODE, Hub


async def _fid(hub: Hub, who: str) -> int:
    return int(who) if who.isdigit() else await hub.fid_by_name(who)


async def run(args) -> None:
    async with Hub(args.node) as hub:
        if args.cmd == "info":
            info = await hub.info()
            print(json.dumps({k: info.get(k) for k in ("dbStats", "numShards")}, indent=2))
        elif args.cmd == "whois":
            u = await hub.user(await _fid(hub, args.who))
            for k in ("fid", "username", "display_name", "bio", "url", "eth_address"):
                if getattr(u, k):
                    print(f"{k:13} {getattr(u, k)}")
        elif args.cmd == "casts":
            async for c in hub.casts(await _fid(hub, args.who), limit=args.n):
                text = c.text.replace("\n", " ")
                print(f"{c.time:%Y-%m-%d %H:%M}  {text[:100]}")
        elif args.cmd == "apps":
            fid = await _fid(hub, args.who)
            counts = collections.Counter()
            async for c in hub.casts(fid, limit=args.n):
                counts[await hub.app_of(c)] += 1
            for app, n in counts.most_common():
                if app is None:
                    name = "unknown key"
                elif app == fid:
                    name = "own key"
                else:
                    name = await hub.app_name(app) or f"fid {app}"
                print(f"{n:5}  {name}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="phunk", description="Read Farcaster from a Snapchain node.")
    p.add_argument("--node", default=DEFAULT_NODE, help=f"node URL (default {DEFAULT_NODE})")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="node stats")
    for name, helptext in (("whois", "profile"), ("casts", "latest casts"), ("apps", "which apps signed their casts")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("who", help="username or fid")
        s.add_argument("-n", type=int, default=10 if name == "casts" else 200, help="how many casts")
    try:
        asyncio.run(run(p.parse_args(argv)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
