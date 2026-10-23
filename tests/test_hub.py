"""Hub against a fake node on localhost, so these run offline."""
import asyncio
import base64

from aiohttp import web

from phunk import Hub, NodeError, NotFound


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


def test_errors_never_carry_the_token_or_the_node_url():
    """aiohttp's error repr holds the request headers and URL; NodeError's text must not."""
    async def busy(request):
        return web.Response(status=429)

    async def main():
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", busy)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        out = []
        try:
            async with Hub(url, rate=1000, retries=2, token="tok-very-secret-123") as hub:
                for call in (hub.get("/v1/info"), hub.post("/v1/validateMessage", b"x")):
                    try:
                        await call
                    except NodeError as e:
                        out.append(str(e) + repr(e))
        finally:
            await runner.cleanup()
        return out

    out = asyncio.run(main())
    assert len(out) == 2
    for text in out:
        assert "HTTP 429" in text
        assert "tok-very-secret-123" not in text and "127.0.0.1" not in text and "Bearer" not in text


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


def test_app_lookups_shared_by_concurrent_casts():
    # A feed asks "which app?" for every cast at once; one author's keys and
    # one app's name should each be read once, not once per cast.
    async def events(request):
        await asyncio.sleep(0.05)  # slow enough that every caller arrives before the answer
        return web.json_response({"events": [
            {"blockTimestamp": 1_700_000_000, "signerEventBody": {
                "key": "0xk1", "eventType": "SIGNER_EVENT_TYPE_ADD", "metadata": metadata(9152)}}]})

    async def signers(request):
        return web.json_response({"signers": []})

    async def user_data(request):
        await asyncio.sleep(0.05)
        return web.json_response({"messages": [
            msg("u", fid=9152, userDataBody={"type": "USER_DATA_TYPE_USERNAME", "value": "someapp"})]})

    async def test(hub, calls):
        casts = [{"data": {"fid": 1}, "signer": "0xk1"}] * 20
        apps = await asyncio.gather(*(hub.app_of(c) for c in casts))
        names = await asyncio.gather(*(hub.app_name(a) for a in apps))
        return apps, names, [p for p, _ in calls]

    apps, names, paths = run({"/v1/onChainEventsByFid": events, "/v1/signersByFid": signers,
                              "/v1/userDataByFid": user_data}, test)
    assert apps == [9152] * 20
    assert names == ["someapp"] * 20
    assert sorted(paths) == ["/v1/onChainEventsByFid", "/v1/signersByFid", "/v1/userDataByFid"]


def test_failed_app_lookup_is_retried():
    tries = []

    async def events(request):
        tries.append(1)
        if len(tries) <= 3:  # the Hub's 3 tries all fail: the first lookup raises
            return web.json_response({"error": "down"}, status=500)
        return web.json_response({"events": [
            {"blockTimestamp": 1_700_000_000, "signerEventBody": {
                "key": "0xk1", "eventType": "SIGNER_EVENT_TYPE_ADD", "metadata": metadata(9152)}}]})

    async def signers(request):
        return web.json_response({"signers": []})

    async def test(hub, calls):
        cast = {"data": {"fid": 1}, "signer": "0xk1"}
        try:
            await hub.app_of(cast)
        except NodeError:
            pass
        return await hub.app_of(cast)

    assert run({"/v1/onChainEventsByFid": events, "/v1/signersByFid": signers}, test) == 9152


def test_feed_merges_followed_accounts_newest_first_and_skips_future():
    from phunk.timeutil import from_datetime
    import datetime as dt

    now = from_datetime(dt.datetime.now(dt.timezone.utc))

    def cast(h, fid, ts):
        return msg(h, fid=fid, ts=ts, castAddBody={"text": h})

    links = {"messages": [msg("l1", fid=9, linkBody={"targetFid": 1}), msg("l2", fid=9, linkBody={"targetFid": 2})]}
    casts = {
        "1": [cast("future", 1, now + 3600), cast("a-new", 1, now - 10), cast("a-old", 1, now - 5000)],
        "2": [cast("b-mid", 2, now - 100), cast("b-ancient", 2, now - 10 * 86400)],
    }

    async def links_handler(request):
        return web.json_response(links)

    async def casts_handler(request):
        return web.json_response({"messages": casts[request.query["fid"]]})

    async def test(hub, calls):
        return [c.hash for c in await hub.feed(9, limit=10)]

    got = run({"/v1/linksByFid": links_handler, "/v1/castsByFid": casts_handler}, test)
    assert got == ["a-new", "b-mid", "a-old"]


def test_custody_address_is_the_latest_register_or_transfer():
    events = {"events": [
        {"blockNumber": 5, "logIndex": 1, "idRegisterEventBody": {"to": "0xAAA", "eventType": "Register"}},
        {"blockNumber": 9, "logIndex": 0, "idRegisterEventBody": {"to": "0xBBB", "eventType": "Transfer"}},
    ]}

    async def handler(request):
        return web.json_response(events)

    async def test(hub, calls):
        return await hub.custody_address(3)

    assert run({"/v1/onChainEventsByFid": handler}, test) == "0xbbb"


def test_signers_asks_for_app_nonces_in_the_nodes_array_syntax():
    # The node parses query arrays as key[i]=v; a repeated key is an error.
    async def signers(request):
        return web.json_response({"signers": [], "currentUserNonce": 2, "requesterFidNonces": {"7": 1, "8": 0}})

    async def test(hub, calls):
        return await hub.signers(3, [7, 8]), calls

    r, calls = run({"/v1/signersByFid": signers}, test)
    assert r["requesterFidNonces"] == {"7": 1, "8": 0}
    assert calls == [("/v1/signersByFid", {"fid": "3", "requesterFids[0]": "7", "requesterFids[1]": "8"})]


def test_eth_addresses_keeps_ethereum_only_lowercase_and_unique():
    def v(h, address, protocol=None):
        body = {"address": address}
        if protocol:
            body["protocol"] = protocol
        return msg(h, fid=3, verificationAddAddressBody=body)

    async def handler(request):
        return web.json_response({"messages": [
            v("a", "0xABCDEF0000000000000000000000000000000001", "PROTOCOL_ETHEREUM"),
            v("b", "ExAqci8uUVKtqHqFW58fmwgMMY9PATfRGGyv6837j9Lx", "PROTOCOL_SOLANA"),
            v("c", "0x0000000000000000000000000000000000000002"),  # default protocol left out
            v("d", "0xabcdef0000000000000000000000000000000001", "PROTOCOL_ETHEREUM"),
        ]})

    got = run({"/v1/verificationsByFid": handler}, lambda hub, calls: hub.eth_addresses(3))
    assert got == ["0xabcdef0000000000000000000000000000000001", "0x0000000000000000000000000000000000000002"]


def multicall_reply(data, balance):
    """A fake Multicall3.aggregate3 answer: `balance(address)` for each balanceOf in `data`."""
    import re

    from phunk.onchain import BALANCE_OF
    addrs = ["0x" + m for m in re.findall(BALANCE_OF[2:] + "0{24}([0-9a-f]{40})", data)]
    w = lambda n: f"{n:064x}"  # noqa: E731
    heads = "".join(w(32 * len(addrs) + i * 128) for i in range(len(addrs)))
    tails = "".join(w(1) + w(0x40) + w(32) + w(balance(a)) for a in addrs)
    return "0x" + w(0x20) + w(len(addrs)) + heads + tails


def test_holders_checks_wallets_with_multicall():
    from phunk.onchain import AGGREGATE3, MULTICALL3, NS_VOTE, holders

    seen = []

    async def rpc(request):
        call = await request.json()
        tx = call["params"][0]
        seen.append(tx)
        assert call["method"] == "eth_call" and tx["to"] == MULTICALL3 and tx["data"].startswith(AGGREGATE3)
        assert NS_VOTE[2:] in tx["data"]
        result = multicall_reply(tx["data"], lambda a: 2 if a == "0x" + "ab" * 20 else 0)
        return web.json_response({"jsonrpc": "2.0", "id": call["id"], "result": result})

    async def go():
        app = web.Application()
        app.router.add_post("/", rpc)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                addrs = ["0x" + f"{i:040x}" for i in range(250)] + ["0x" + "AB" * 20, "not an address"]
                return await holders(s, addrs, rpc=f"http://127.0.0.1:{port}/")
        finally:
            await runner.cleanup()

    assert asyncio.run(go()) == {"0x" + "ab" * 20}
    assert len(seen) == 2  # 251 valid addresses, 200 per call


def test_token_goes_in_a_header_and_a_refusal_says_so():
    seen = []

    async def handler(request):
        seen.append((request.headers.get("Authorization"), str(request.url)))
        if request.headers.get("Authorization") != "Bearer s3cret":
            return web.Response(status=401)
        return web.json_response({"fid": 3})

    async def body():
        app = web.Application()
        app.router.add_get("/v1/userNameProofByName", handler)
        app.router.add_post("/v1/validateMessage", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        out = []
        try:
            async with Hub(url, rate=1000, token="s3cret") as hub:
                out.append(await hub.fid_by_name("dwr"))
                out.append(await hub.validate(b"\x00"))
            for token in (None, "wrong"):
                async with Hub(url, rate=1000, token=token) as hub:
                    try:
                        await hub.fid_by_name("dwr")
                    except NotFound:
                        out.append("not found")  # a 401 must not look like a missing name
                    except NodeError as e:
                        out.append(str(e))
        finally:
            await runner.cleanup()
        return out

    out = asyncio.run(body())
    assert out[:2] == [3, {"fid": 3}]
    assert "wants a token" in out[2] and "refused this token" in out[3]
    assert all("s3cret" not in m and "wrong" not in m for m in out[2:])
    assert seen[0][0] == "Bearer s3cret" and seen[1][0] == "Bearer s3cret"
    assert all("s3cret" not in url for _, url in seen)  # never in the URL
    assert len(seen) == 4  # a refusal is not retried


def test_casts_skip_removes_and_the_casts_they_removed():
    # castsByFid returns CAST_REMOVE messages too (a deleted cast), with the
    # removed cast's hash in base64. Newest first, so a remove comes before
    # its add if the node still has both.
    gone = "0x" + "ab" * 20
    gone_b64 = base64.b64encode(bytes.fromhex(gone[2:])).decode()

    def add(h, ts):
        return msg(h, ts=ts, type="MESSAGE_TYPE_CAST_ADD", castAddBody={"text": h})

    def remove(h, ts, target):
        return msg(h, ts=ts, type="MESSAGE_TYPE_CAST_REMOVE", castRemoveBody={"targetHash": target})

    pages = {
        None: {"messages": [add("0x01", 500), remove("0xr1", 450, gone_b64), remove("0xr2", 400, "AAAA")],
               "nextPageToken": "p2"},
        "p2": {"messages": [remove("0xr3", 390, "BBBB"), remove("0xr4", 380, "CCCC")], "nextPageToken": "p3"},
        "p3": {"messages": [add(gone, 300), add("0x02", 200), add("0x03", 100)], "nextPageToken": None},
    }

    async def handler(request):
        return web.json_response(pages[request.query.get("pageToken")])

    async def test(hub, calls):
        every = [c.hash async for c in hub.casts(1)]
        limited = [c.hash async for c in hub.casts(1, limit=2)]
        recent = [c.hash async for c in hub.casts(1, since=150)]
        return every, limited, recent

    every, limited, recent = run({"/v1/castsByFid": handler}, test)
    # A page of only removes doesn't end paging, and they don't use up the limit.
    assert every == ["0x01", "0x02", "0x03"]
    assert limited == ["0x01", "0x02"]
    assert recent == ["0x01", "0x02"]


def test_mentions_and_replies_skip_removes():
    def remove(h):
        return msg(h, type="MESSAGE_TYPE_CAST_REMOVE", castRemoveBody={"targetHash": "AAAA"})

    async def handler(request):
        return web.json_response({"messages": [remove("0xr"), msg("0x01", castAddBody={"text": "hi"})]})

    async def test(hub, calls):
        return ([c.hash async for c in hub.mentions(1)], [c.hash async for c in hub.replies(1, "0x00")],
                [c.hash async for c in hub.channel("https://example.com/c")])

    assert run({"/v1/castsByMention": handler, "/v1/castsByParent": handler}, test) == (["0x01"],) * 3
