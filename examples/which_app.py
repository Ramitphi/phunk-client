"""Which apps someone uses: their key history, and which app signed their
recent casts, likes and follows.

    python examples/which_app.py dwr
"""
import asyncio
import collections
import sys

from phunk import Hub


async def label(hub: Hub, fid: int, app: int | None) -> str:
    if app is None:
        return "unknown key"
    if app == fid:
        return "own key (app not named)"
    return await hub.app_name(app) or f"fid {app}"


async def main(name: str):
    async with Hub() as hub:
        fid = await hub.fid_by_name(name)

        print(f"Key history for @{name} (fid {fid}):")
        for k in await hub.keys(fid):
            where = "on-chain " if k.onchain else "off-chain"
            app = await label(hub, fid, k.app_fid) if k.event == "add" else ""
            print(f"  {k.time:%Y-%m-%d}  {where}  {k.event:6}  {k.key[:12]}…  {app}")

        counts = collections.Counter()
        async for c in hub.casts(fid, limit=100):
            counts["cast", await hub.app_of(c)] += 1
        async for r in hub.reactions(fid, "like", limit=100):
            counts["like", await hub.app_of(r)] += 1
        async for f in hub.following(fid, limit=100):
            counts["follow", await hub.app_of(f)] += 1

        print("\nWho signed their last 100 casts, likes and follows:")
        for (kind, app), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1])):
            print(f"  {kind:6} {n:4}  {await label(hub, fid, app)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "dwr"))
