"""A small command line: read from a node, and write with an app key.

    phunk whois dwr
    phunk casts dwr -n 5
    phunk apps dwr
    phunk --node http://localhost:3381 info
    PHUNK_NODE=https://node.example PHUNK_NODE_TOKEN=... phunk info

    phunk key add                # seed phrase in (hidden), app key out
    phunk key list               # the account's keys and which app asked for each
    phunk key revoke             # the saved key removes itself; no seed phrase
    phunk post "hello from my own node"
    phunk feed --hide warpcast   # who you follow, minus one app
    phunk follow dwr
    phunk ui                     # local web client on http://127.0.0.1:8008
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


def find_key():
    """(fid, AppKey) from PHUNK_FID / PHUNK_APP_KEY, or the key file. None if
    there is no key (or the [write] extra is not installed)."""
    if not (os.environ.get("PHUNK_APP_KEY") and os.environ.get("PHUNK_FID")) and not KEY_FILE.exists():
        return None
    try:
        from .write import AppKey
    except ImportError:
        return None
    if os.environ.get("PHUNK_APP_KEY") and os.environ.get("PHUNK_FID"):
        return int(os.environ["PHUNK_FID"]), AppKey.from_hex(os.environ["PHUNK_APP_KEY"])
    d = json.loads(KEY_FILE.read_text())
    return int(d["fid"]), AppKey.from_hex(d["app_key"])


def _load_key():
    """Like find_key, but exits with a hint when there is no key."""
    found = find_key()
    if found is None:
        from .write import AppKey  # noqa: F401  (a clear error if [write] is missing)

        sys.exit("no app key yet: run `phunk key add` (or set PHUNK_FID and PHUNK_APP_KEY)")
    return found


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


def _confirm(question: str) -> None:
    if input(f"{question} [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")


async def _custody(hub: Hub):
    """Read the seed phrase (hidden) and return (Custody, fid)."""
    from .write import Custody

    print(SEED_WARNING)
    phrase = getpass.getpass("Seed phrase (hidden, not saved): ")
    custody = Custody.from_mnemonic(phrase)
    del phrase
    fid = await hub.fid_by_address(custody.address)
    print(f"custody address {custody.address} owns fid {fid}")
    return custody, fid


async def run_key_list(hub: Hub, args) -> None:
    found = find_key()
    fid = await _fid(hub, args.who) if args.who else _load_key()[0]
    mine = found[1].public_hex if found and found[0] == fid else None
    live: dict[str, object] = {}
    for k in await hub.keys(fid):
        if k.event == "add":
            live[k.key] = k
        else:
            live.pop(k.key, None)
    for k in live.values():
        app = "own key" if k.self_requested else (await hub.app_name(k.app_fid) if k.app_fid else None)
        app = app or (f"fid {k.app_fid}" if k.app_fid else "unknown app")
        kind = "on-chain" if k.onchain else "gasless"
        here = "  <- saved here" if k.key == mine else ""
        print(f"{k.time:%Y-%m-%d}  {kind:8}  {k.key}  {app}{here}")


async def run_key_revoke(hub: Hub, args) -> None:
    from .write import revoke_key, revoke_key_with_custody

    if args.key:  # any key on the account: needs the custody key
        custody, fid = await _custody(hub)
        _confirm(f"Revoke key {args.key} of fid {fid}? Apps using it stop working.")
        await revoke_key_with_custody(hub, custody, args.key, fid=fid)
        del custody
        print(f"revoked {args.key}. Casts it signed stay up.")
        return
    fid, key = _load_key()
    _confirm(f"Revoke this machine's app key {key.public_hex} (fid {fid})?")
    await revoke_key(hub, fid, key)
    print(f"revoked {key.public_hex}. Casts it signed stay up.")
    if os.environ.get("PHUNK_APP_KEY"):
        print("unset PHUNK_APP_KEY and PHUNK_FID: that key no longer works.")
    elif KEY_FILE.exists():
        KEY_FILE.unlink()
        print(f"deleted {KEY_FILE}")


async def run_key(hub: Hub, args) -> None:
    from .write import DEFAULT_SCOPES, add_app_key

    if args.key_cmd == "show":
        fid, key = _load_key()
        print(f"fid         {fid}\npublic key  {key.public_hex}\nkey file    {KEY_FILE}")
        return
    if args.key_cmd == "list":
        return await run_key_list(hub, args)
    if args.key_cmd == "revoke":
        return await run_key_revoke(hub, args)
    custody, fid = await _custody(hub)
    _confirm(f"Add a new app key to fid {fid}?")
    signer = args.app_signer or os.environ.get("PHUNK_APP_SIGNER")
    if signer:
        print(f"asking the app's signer to approve the new key: {signer}")
    fid, key = await add_app_key(hub, custody, fid=fid, scopes=DEFAULT_SCOPES, ttl=args.ttl_days * 86400,
                                 app_signer=signer)
    del custody
    if args.print:
        print(f"app key (keep it secret): {key.private_hex}")
    else:
        _save_key(fid, key)
        print(f"saved to {KEY_FILE} (only you can read it)")
    print(f"added app key {key.public_hex} to fid {fid}. It stays alive while you use it "
          f"(sliding {args.ttl_days} days).")
    print("Other clients will show your casts as sent from "
          + ("the app behind that signer." if signer else "your own account (its own key)."))


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
    elif args.cmd == "profile":
        fields = {k: getattr(args, k) for k in ("username", "display", "bio", "pfp", "url")
                  if getattr(args, k) is not None}
        if not fields:
            sys.exit("nothing to change: pass --username, --display, --bio, --pfp or --url")
        h = ", ".join(await me.set_profile(**fields))
    if args.cmd == "post":
        # FID:HASH is what like/recast take; the URL shows whether it landed.
        print(f"posted {fid}:{h}")
        print(f"check:  {hub.url}/v1/castById?fid={fid}&hash={h}")
    else:
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


async def run_ui(hub: Hub, args) -> None:
    from .onchain import base_rpc_from_env
    from .ui import serve, tip_fid_from_env

    tip, rpc = tip_fid_from_env(), base_rpc_from_env()
    if args.public:  # what phunk.social serves: no account, no key
        return await serve(hub, host=args.host, port=args.port, open_browser=args.open, public=True,
                           tip_fid=tip, vote_rpc=rpc)
    found = None if args.read_only else find_key()
    fid, key = found if found else (None, None)
    if args.who:
        browse = await _fid(hub, args.who)
        if fid is not None and browse != fid:
            key = None  # someone else's feed: read-only
        fid = browse
    elif fid is None and os.environ.get("PHUNK_FID", "").isdigit():
        fid = int(os.environ["PHUNK_FID"])
    await serve(hub, host=args.host, port=args.port, fid=fid, key=key, open_browser=args.open,
                casting=args.casting, tip_fid=tip, vote_rpc=rpc)


async def run(args) -> None:
    async with Hub(args.node, rate=args.rate, concurrency=args.concurrency,
                   token=os.environ.get("PHUNK_NODE_TOKEN") or None) as hub:
        if args.cmd == "ui":
            await run_ui(hub, args)
        elif args.cmd == "key":
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


WRITE_CMDS = {"post", "like", "unlike", "recast", "unrecast", "follow", "unfollow", "delete", "profile"}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="phunk", description="Read and write Farcaster through a Snapchain node.")
    node = os.environ.get("PHUNK_NODE") or DEFAULT_NODE
    p.add_argument("--node", default=node, help=f"node URL (default $PHUNK_NODE or {DEFAULT_NODE}); "
                                                 "a token for it goes in $PHUNK_NODE_TOKEN")
    p.add_argument("--rate", type=float, default=float(os.environ.get("PHUNK_RATE") or 20),
                   help="requests per second (default $PHUNK_RATE or 20; raise it on your own node)")
    p.add_argument("--concurrency", type=int, default=int(os.environ.get("PHUNK_CONCURRENCY") or 12),
                   help="requests in flight (default $PHUNK_CONCURRENCY or 12)")
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
    ka.add_argument("--app-signer", metavar="URL",
                    help="an app's signing service, so casts show 'Sent from <app>' "
                         "(default: $PHUNK_APP_SIGNER; without one the account is its own app)")
    ksub.add_parser("show", help="which fid and key are saved")
    kl = ksub.add_parser("list", help="an account's active keys and the app behind each")
    kl.add_argument("who", nargs="?", help="username or fid (default: your saved key's fid)")
    kr = ksub.add_parser("revoke", help="revoke a gasless app key (casts it signed stay)",
                         description="Without --key, the saved key signs its own removal: no seed "
                                     "phrase. With --key, revoke any gasless key on the account; "
                                     "that needs the seed phrase.")
    kr.add_argument("--key", metavar="0x...", help="public key to revoke (see `phunk key list`)")

    s = sub.add_parser("post", help="post a cast")
    s.add_argument("text")
    s.add_argument("--channel", help="channel URL, e.g. https://warpcast.com/~/channel/politics")
    s.add_argument("--reply-to", type=_cast_ref, metavar="FID:HASH")
    s.add_argument("--embed", action="append", metavar="URL", help="up to 2")
    for name in ("like", "unlike", "recast", "unrecast"):
        sub.add_parser(name, help=f"{name} a cast").add_argument("cast", type=_cast_ref, metavar="FID:HASH")
    for name in ("follow", "unfollow"):
        sub.add_parser(name, help=f"{name} someone").add_argument("who", help="username or fid")
    sub.add_parser("delete", help="delete one of your casts").add_argument("hash")
    pr = sub.add_parser("profile", help="update your profile")
    pr.add_argument("--username", help="a name your fid already owns (fname or ENS)")
    pr.add_argument("--display", help="display name")
    pr.add_argument("--bio")
    pr.add_argument("--pfp", help="picture URL")
    pr.add_argument("--url", help="website")

    f = sub.add_parser("feed", help="recent casts from who you (or WHO) follow")
    f.add_argument("who", nargs="?", help="username or fid (default: your saved key's fid)")
    f.add_argument("-n", type=int, default=20)
    f.add_argument("--hours", type=int, default=24)
    f.add_argument("--only", help="only these apps, comma-separated (e.g. warpcast,fireflyapp)")
    f.add_argument("--hide", help="hide these apps, comma-separated")

    u = sub.add_parser("ui", help="local web client: people to connect with, then casts",
                       description="Serve a small web client on this machine. With an app key "
                                   "(`phunk key add`) it can follow people; casts are read-only "
                                   "for now (casting is coming soon). Without a key it is "
                                   "read-only. It never asks for a seed phrase.")
    u.add_argument("--as", dest="who", metavar="WHO",
                   help="open on this username or fid's people (read-only unless it is your key's fid)")
    u.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1, this machine only)")
    u.add_argument("--port", type=int, default=8008, help="port (default 8008)")
    u.add_argument("--open", action="store_true", help="open it in your browser")
    u.add_argument("--read-only", action="store_true", help="ignore any saved app key")
    u.add_argument("--casting", action="store_true",
                   help="preview casting, replies and likes in the page (coming soon; off by default)")
    u.add_argument("--public", action="store_true",
                   help="preview the hosted site: read-only, follows and replies open Farcaster")
    try:
        asyncio.run(run(p.parse_args(argv)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
