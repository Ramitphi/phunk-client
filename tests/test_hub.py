"""Hub against a fake node on localhost, so these run offline."""
import asyncio
import base64

from aiohttp import web

from phunk import Hub, NotFound


def msg(h, fid=1, ts=100, signer="0xk1", **body):
    return {"hash": h, "signer": signer, "data": {"fid": fid, "timestamp": ts, **body}}


def word(n):
    return n.to_bytes(32, "big")


def metadata(app_fid):
    sig = b"\x00" * 65
    raw = word(0x20) + word(app_fid) + b"\x00" * 32 + word(0x80) + word(0) + word(65) + sig + b"\x00" * 31
    return base64.b64encode(raw).decode()


async def with_node(routes, test):
    """Start a fake node with `routes` ({path: handler}) and run `test(hub, calls)`."""
    calls = []
    app = web.Application()
    for path, handler in routes.items():
        async def wrapped(request, handler=handler):
            calls.append((request.path, dict(request.query)))
            return await handler(request)
        app.router.add_get(path, wrapped)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with Hub(f"http://127.0.0.1:{port}", rate=1000, retries=3) as hub:
            return await test(hub, calls)
    finally:
        await runner.cleanup()


def run(routes, test):
    return asyncio.run(with_node(routes, test))


def test_pages_dedupe_and_stop_when_token_loops():
    # Two shards glued together; after page 2 the token loops back to page 1.
    pages = {
        None: {"messages": [msg("a"), msg("b"), msg("x")], "nextPageToken": "p2"},
        "p2": {"messages": [msg("c"), msg("y")], "nextPageToken": "p3"},
        "p3": {"messages": [msg("a"), msg("x")], "nextPageToken": "p2"},
    }

    async def handler(request):
        return web.json_response(pages[request.query.get("pageToken")])

    async def test(hub, calls):
        got = await hub.collect("/v1/castsByFid", {"fid": 1})
        return [m["hash"] for m in got], len(calls)

    hashes, n_calls = run({"/v1/castsByFid": handler}, test)
    assert hashes == ["a", "b", "x", "c", "y"]
    assert n_calls == 3  # the looped page added nothing, so paging stopped


def test_since_cutoff_skips_old_items_and_stops():
    pages = {
        None: {"messages": [msg("a", ts=300), msg("b", ts=250), msg("c", ts=150)], "nextPageToken": "p2"},
        "p2": {"messages": [msg("d", ts=120), msg("e", ts=90)], "nextPageToken": "p3"},
        "p3": {"messages": [msg("f", ts=80)], "nextPageToken": "p4"},
    }

    async def handler(request):
        return web.json_response(pages[request.query.get("pageToken")])

    async def test(hub, calls):
        got = [c.hash async for c in hub.casts(1, since=200)]
        return got, calls

    got, calls = run({"/v1/castsByFid": handler}, test)
    assert got == ["a", "b"]
    assert len(calls) == 2  # page 2 was all older than the cutoff
    assert calls[0][1]["reverse"] == "true"


def test_limit():
    async def handler(request):
        return web.json_response({"messages": [msg(str(i)) for i in range(10)], "nextPageToken": "more"})

    async def test(hub, calls):
        got = [c async for c in hub.casts(1, limit=3)]
        return got, calls

    got, calls = run({"/v1/castsByFid": handler}, test)
    assert [c.hash for c in got] == ["0", "1", "2"]
    assert calls[0][1]["pageSize"] == "3"


def test_retries_then_succeeds():
    state = {"n": 0}

    async def handler(request):
        state["n"] += 1
        if state["n"] < 3:
            return web.Response(status=503)
        return web.json_response({"fid": 3})

    async def test(hub, calls):
        return await hub.fid_by_name("@dwr"), calls

    fid, calls = run({"/v1/userNameProofByName": handler}, test)
    assert fid == 3
    assert len(calls) == 3
    assert calls[0][1] == {"name": "dwr"}


def test_not_found():
    async def handler(request):
        return web.Response(status=404, text="no such name")

    async def test(hub, calls):
        try:
            await hub.fid_by_name("nobody-here")
        except NotFound:
            return "not found"

    assert run({"/v1/userNameProofByName": handler}, test) == "not found"


def test_user_profile():
    async def handler(request):
        def ud(t, v):
            return msg(t, fid=3, userDataBody={"type": t, "value": v})
        return web.json_response({"messages": [
            ud("USER_DATA_TYPE_USERNAME", "dwr"),
            ud("USER_DATA_TYPE_DISPLAY", "Dan"),
            ud("USER_DATA_PRIMARY_ADDRESS_ETHEREUM", "0xabc"),
        ]})

    async def test(hub, calls):
        return await hub.user(3)

    u = run({"/v1/userDataByFid": handler}, test)
    assert (u.fid, u.username, u.display_name, u.eth_address) == (3, "dwr", "Dan", "0xabc")


def test_which_app_signed():
    async def events(request):
        return web.json_response({"events": [
            {"blockTimestamp": 1_700_000_000, "signerEventBody": {
                "key": "0xk1", "eventType": "SIGNER_EVENT_TYPE_ADD", "metadata": metadata(9152)}},
            {"blockTimestamp": 1_700_000_100, "signerEventBody": {
                "key": "0xk2", "eventType": "SIGNER_EVENT_TYPE_ADD", "metadata": metadata(1)}},
            {"blockTimestamp": 1_700_000_200, "signerEventBody": {
                "key": "0xk1", "eventType": "SIGNER_EVENT_TYPE_REMOVE"}},
        ]})

    async def signers(request):
        return web.json_response({"signers": [
            {"source": "SIGNER_SOURCE_ONCHAIN", "key": "0xk1"},
            {"source": "SIGNER_SOURCE_OFFCHAIN", "key": "0xk3", "requestFid": 777, "addedAt": 1_700_000_300},
        ]})

    async def casts(request):
        return web.json_response({"messages": [
            msg("a", signer="0xk1", castAddBody={"text": "hi"}),
            msg("b", signer="0xk2", castAddBody={"text": "yo"}),
            msg("c", signer="0xk3", castAddBody={"text": "gm"}),
            msg("d", signer="0xzz", castAddBody={"text": "?"}),
        ]})

    async def test(hub, calls):
        keys = await hub.keys(1)
        apps = [await hub.app_of(c) async for c in hub.casts(1)]
        n_key_calls = sum(1 for p, _ in calls if p != "/v1/castsByFid")
        return keys, apps, n_key_calls

    keys, apps, n_key_calls = run({"/v1/onChainEventsByFid": events, "/v1/signersByFid": signers,
                                   "/v1/castsByFid": casts}, test)
    assert [(k.key, k.event, k.app_fid, k.onchain) for k in keys] == [
        ("0xk1", "add", 9152, True),
        ("0xk2", "add", 1, True),
        ("0xk1", "remove", None, True),
        ("0xk3", "add", 777, False),
    ]
    assert keys[1].self_requested
    assert apps == [9152, 1, 777, None]
    assert n_key_calls == 4  # keys() once, then key_apps() once; cached after that
