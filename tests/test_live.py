"""Against the real default node. Run with PHUNK_LIVE=1 pytest."""
import asyncio
import os

import pytest

from phunk import Hub, NotFound

pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.environ.get("PHUNK_LIVE"), reason="set PHUNK_LIVE=1")]


def live(fn):
    async def go():
        async with Hub(rate=5) as hub:
            return await fn(hub)
    return asyncio.run(go())


def test_name_and_profile():
    async def t(hub):
        fid = await hub.fid_by_name("dwr")
        return fid, await hub.user(fid)
    fid, u = live(t)
    assert fid == 3
    assert u.username == "dwr"


def test_casts_and_apps():
    async def t(hub):
        casts = [c async for c in hub.casts(3, limit=3)]
        apps = [await hub.app_of(c) for c in casts]
        return casts, apps
    casts, apps = live(t)
    assert len(casts) == 3
    assert casts[0].timestamp >= casts[1].timestamp >= casts[2].timestamp
    assert any(a is not None for a in apps)


def test_following():
    async def t(hub):
        return [f async for f in hub.following(3, limit=50)]
    follows = live(t)
    assert len(follows) == 50
    assert all(f.fid == 3 and f.target_fid for f in follows)


def test_unknown_name():
    async def t(hub):
        with pytest.raises(NotFound):
            await hub.fid_by_name("this-name-should-not-exist-9q8w7e")
    live(t)
