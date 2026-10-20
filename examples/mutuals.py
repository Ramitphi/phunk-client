"""Mutual follows between two people, and who they both follow.

    python examples/mutuals.py dwr v
"""
import asyncio
import sys

from phunk import Hub


async def follows(hub: Hub, fid: int) -> set[int]:
    return {f.target_fid async for f in hub.following(fid)}


async def main(a: str, b: str):
    async with Hub() as hub:
        fa, fb = await asyncio.gather(hub.fid_by_name(a), hub.fid_by_name(b))
        sa, sb = await asyncio.gather(follows(hub, fa), follows(hub, fb))

        print(f"@{a} follows {len(sa)}, @{b} follows {len(sb)}")
        print(f"@{a} -> @{b}: {'yes' if fb in sa else 'no'}")
        print(f"@{b} -> @{a}: {'yes' if fa in sb else 'no'}")

        both = sa & sb
        print(f"\nThey both follow {len(both)} accounts. A few:")
        users = await asyncio.gather(*(hub.user(f) for f in sorted(both)[:15]))
        print("  " + ", ".join(f"@{u.username}" for u in users if u.username))


if __name__ == "__main__":
    args = sys.argv[1:] or ["dwr", "v"]
    asyncio.run(main(*args[:2]))
