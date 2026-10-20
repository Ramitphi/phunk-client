"""Profile, latest casts and a few follows for one account.

    python examples/quickstart.py dwr
"""
import asyncio
import sys

from phunk import Hub


async def main(name: str):
    async with Hub() as hub:
        user = await hub.user_by_name(name)
        print(f"@{user.username} (fid {user.fid}): {user.display_name}")
        print(f"  {user.bio}\n")

        print("Latest casts:")
        async for cast in hub.casts(user.fid, limit=5):
            kind = "reply" if cast.is_reply else "cast "
            print(f"  {cast.time:%Y-%m-%d %H:%M} {kind} {cast.text[:70]!r}")

        print("\nFollows (first 10):")
        follows = [f async for f in hub.following(user.fid, limit=10)]
        profiles = await asyncio.gather(*(hub.user(f.target_fid) for f in follows))
        print("  " + ", ".join(f"@{p.username}" for p in profiles if p.username))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "dwr"))
