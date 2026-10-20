"""A small command line: read from a node, and write with an app key.

    phunk whois dwr
    phunk casts dwr -n 5
    phunk apps dwr
    phunk --node http://localhost:3381 info

    phunk key add                # seed phrase in (hidden), app key out
    phunk post "hello from my own node"
    phunk feed --hide warpcast   # who you follow, minus one app
    phunk follow dwr
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import getpass
import json
import os
import sys
from pathlib import Path

from .hub import DEFAULT_NODE, Hub


async def _fid(hub: Hub, who: str) -> int:
    return int(who) if who.isdigit() else await hub.fid_by_name(who)


KEY_FILE = Path(os.environ.get("PHUNK_KEY_FILE", Path.home() / ".config" / "phunk" / "app-key.json"))

SEED_WARNING = """\
Your seed phrase controls your whole account. Read this first:
  * Use a TEST account, not your main one.
  * Only paste a seed phrase into software from developers you trust.
    You can read this code: src/phunk/write.py.
  * phunk uses it once, in memory, to sign one authorization for a new app
    key. It is never saved, logged or sent anywhere.
  * Apps that can, should ask your wallet to sign instead (delegated signer
    authorization), so the phrase never leaves it.
"""


def _load_key():
    """(fid, AppKey) from PHUNK_FID / PHUNK_APP_KEY, or the key file."""
    from .write import AppKey

    if os.environ.get("PHUNK_APP_KEY") and os.environ.get("PHUNK_FID"):
        return int(os.environ["PHUNK_FID"]), AppKey.from_hex(os.environ["PHUNK_APP_KEY"])
    if not KEY_FILE.exists():
        sys.exit(f"no app key yet: run `phunk key add` (or set PHUNK_FID and PHUNK_APP_KEY)")
    d = json.loads(KEY_FILE.read_text())
    return int(d["fid"]), AppKey.from_hex(d["app_key"])


def _save_key(fid: int, key) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"fid": fid, "app_key": key.private_hex, "public_key": key.public_hex}, f, indent=2)


def _cast_ref(value: str) -> tuple[int, str]:
    """"fid:0xhash" -> (fid, hash)."""
    fid, _, h = value.partition(":")
    if not (fid.isdigit() and h):
        raise argparse.ArgumentTypeError("expected fid:0xhash, e.g. 3:0xabc...")
    return int(fid), h


async def _app_label(hub: Hub, cast) -> str:
    app = await hub.app_of(cast)
    if app is None:
        return "unknown"
    if app == cast.fid:
        return "own key"
    return await hub.app_name(app) or f"fid {app}"


async def run_key(hub: Hub, args) -> None:
    from .write import DEFAULT_SCOPES, Custody, add_app_key

    if args.key_cmd == "show":
        fid, key = _load_key()
        print(f"fid         {fid}\npublic key  {key.public_hex}\nkey file    {KEY_FILE}")
        return
    print(SEED_WARNING)
    phrase = getpass.getpass("Seed phrase (hidden, not saved): ")
    custody = Custody.from_mnemonic(phrase)
    del phrase
    fid = await hub.fid_by_address(custody.address)
    print(f"custody address {custody.address} owns fid {fid}")
    if input(f"Add a new app key to fid {fid}? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")
    fid, key = await add_app_key(hub, custody, fid=fid, scopes=DEFAULT_SCOPES, ttl=args.ttl_days * 86400)
    del custody
    if args.print:
        print(f"app key (keep it secret): {key.private_hex}")
    else:
        _save_key(fid, key)
        print(f"saved to {KEY_FILE} (only you can read it)")
    print(f"added app key {key.public_hex} to fid {fid}. It stays alive while you use it "
          f"(sliding {args.ttl_days} days).")


async def run_write(hub: Hub, args) -> None:
    from .write import Writer

    fid, key = _load_key()
    me = Writer(hub, fid, key)
    if args.cmd == "post":
        h = await me.cast(args.text, reply_to=args.reply_to, channel=args.channel, embeds=args.embed or [])
    elif args.cmd in ("like", "unlike", "recast", "unrecast"):
        h = await getattr(me, args.cmd)(*args.cast)
    elif args.cmd in ("follow", "unfollow"):
        h = await getattr(me, args.cmd)(await _fid(hub, args.who))
    elif args.cmd == "delete":
        h = await me.delete_cast(args.hash)
    print(f"submitted {h}")


async def run_feed(hub: Hub, args) -> None:
    fid = await _fid(hub, args.who) if args.who else _load_key()[0]
    only = {a.lower() for a in args.only.split(",")} if args.only else None
    hide = {a.lower() for a in args.hide.split(",")} if args.hide else set()
    import datetime as dt

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
    casts = await hub.feed(fid, since=since, limit=args.n * 4 if (only or hide) else args.n)
    shown = 0
    for c in casts:
        app = await _app_label(hub, c)
        if (only and app.lower() not in only) or app.lower() in hide:
            continue
        who = (await hub.user(c.fid)).username or f"fid {c.fid}"
        text = c.text.replace("\n", " ")
        print(f"{c.time:%m-%d %H:%M}  @{who:15} [{app}]  {text[:90]}")
        print(f"{'':12} {c.fid}:{c.hash}")
        shown += 1
        if shown >= args.n:
            break


async def run(args) -> None:
    async with Hub(args.node) as hub:
        if args.cmd == "key":
            await run_key(hub, args)
        elif args.cmd in WRITE_CMDS:
            await run_write(hub, args)
        elif args.cmd == "feed":
            await run_feed(hub, args)
        elif args.cmd == "info":
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


WRITE_CMDS = {"post", "like", "unlike", "recast", "unrecast", "follow", "unfollow", "delete"}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="phunk", description="Read and write Farcaster through a Snapchain node.")
    p.add_argument("--node", default=DEFAULT_NODE, help=f"node URL (default {DEFAULT_NODE})")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="node stats")
    for name, helptext in (("whois", "profile"), ("casts", "latest casts"), ("apps", "which apps signed their casts")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("who", help="username or fid")
        s.add_argument("-n", type=int, default=10 if name == "casts" else 200, help="how many casts")

    k = sub.add_parser("key", help="app keys (needs phunk-client[write])")
    ksub = k.add_subparsers(dest="key_cmd", required=True)
    ka = ksub.add_parser("add", help="add a gasless app key, from your seed phrase")
    ka.add_argument("--ttl-days", type=int, default=30, help="sliding expiry, 1-90 days (default 30)")
    ka.add_argument("--print", action="store_true", help="print the key instead of saving it")
    ksub.add_parser("show", help="which fid and key are saved")

    s = sub.add_parser("post", help="post a cast")
    s.add_argument("text")
    s.add_argument("--channel", help="channel URL, e.g. https://farcaster.xyz/~/channel/dev")
    s.add_argument("--reply-to", type=_cast_ref, metavar="FID:HASH")
    s.add_argument("--embed", action="append", metavar="URL", help="up to 2")
    for name in ("like", "unlike", "recast", "unrecast"):
        sub.add_parser(name, help=f"{name} a cast").add_argument("cast", type=_cast_ref, metavar="FID:HASH")
    for name in ("follow", "unfollow"):
        sub.add_parser(name, help=f"{name} someone").add_argument("who", help="username or fid")
    sub.add_parser("delete", help="delete one of your casts").add_argument("hash")

    f = sub.add_parser("feed", help="recent casts from who you (or WHO) follow")
    f.add_argument("who", nargs="?", help="username or fid (default: your saved key's fid)")
    f.add_argument("-n", type=int, default=20)
    f.add_argument("--hours", type=int, default=24)
    f.add_argument("--only", help="only these apps, comma-separated (e.g. warpcast,fireflyapp)")
    f.add_argument("--hide", help="hide these apps, comma-separated")
    try:
        asyncio.run(run(p.parse_args(argv)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
