"""`phunk ui` and recommendations, offline: a fake node plus the UI server."""
import asyncio
import base64
import datetime as dt

import aiohttp
import pytest
from aiohttp import web

from phunk import Hub, proto
from phunk.recommend import Suggestion, score, suggest
from phunk.timeutil import from_datetime
from phunk.ui import UI, channel_name, channel_url, filter_by_app, make_app, reasons, with_mentions
from phunk.models import Cast

NOW = from_datetime(dt.datetime.now(dt.timezone.utc))
DEV = "chain://eip155:1/erc721:0x7dd4e31f1530ac682c8ea4d8016e95773e08d8b0"
ART = "https://warpcast.com/~/channel/art"


def msg(h, fid, ts=None, signer="0xk1", **body):
    return {"hash": h, "signer": signer, "data": {"fid": fid, "timestamp": NOW - 60 if ts is None else ts, **body}}


def link(fid, target, ts=100):
    return msg(f"l{fid}-{target}", fid, ts=ts, linkBody={"type": "follow", "targetFid": target})


def cast(h, fid, signer="0xk1", parent_url=None, text=None, ts=None, **extra):
    return msg(h, fid, ts=ts, signer=signer, castAddBody={"text": text or h, "parentUrl": parent_url, **extra})


def metadata(app_fid):
    word = lambda n: n.to_bytes(32, "big")  # noqa: E731
    raw = word(0x20) + word(app_fid) + b"\x00" * 32 + word(0x80) + word(0) + word(65) + b"\x00" * 96
    return base64.b64encode(raw).decode()


NAMES = {1: "alice", 2: "bob", 3: "carol", 9: "me", 50: "erin", 51: "frank", 60: "gina", 61: "hal",
         9152: "warpcast"}
# Who follows whom. fid 9 is "me"; the timestamp says when the follow happened.
FOLLOWS = {9: [link(9, 1, ts=300), link(9, 2, ts=200), link(9, 3, ts=100)],
           1: [link(1, 50), link(1, 51), link(1, 9), link(1, 2)],
           2: [link(2, 50), link(2, 52)],
           3: [link(3, 50), link(3, 51)]}
CASTS = {
    1: [cast("c1", 1, signer="0xk1", text="hi  there", mentions=[2], mentionsPositions=[3]),
        cast("c1-old", 1, ts=NOW - 3 * 86400)],
    2: [cast("c2", 2, signer="0xown", parent_url=DEV)],
    3: [cast("c3", 3, signer="0xmystery")],
    9: [cast("m1", 9, parent_url=DEV), cast("m2", 9, parent_url=DEV), cast("m3", 9, parent_url=ART),
        cast("m4", 9, parent_url=DEV, parentCastId={"fid": 1, "hash": "0x01"})],
}
CHANNELS = {DEV: [cast("d1", 60), cast("d2", 60), cast("d3", 61), cast("d4", 9), cast("d5", 1)],
            ART: [cast("a1", 61)]}
KEYS = {1: [("0xk1", 9152)], 2: [("0xown", 2)], 3: []}
# "me" liked two casts by erin (50), whom they don't follow: a reconnect card.
REACTIONS = {9: [msg(f"r{i}", 9, reactionBody={"type": "REACTION_TYPE_LIKE",
                                               "targetCastId": {"fid": 50, "hash": f"0x5{i}"}}) for i in range(2)]}


async def node(request):
    p, q = request.path, request.query
    if p == "/v1/linksByFid":
        return web.json_response({"messages": FOLLOWS.get(int(q["fid"]), [])})
    if p == "/v1/castsByFid":
        return web.json_response({"messages": CASTS.get(int(q["fid"]), [])})
    if p == "/v1/castsByParent":
        return web.json_response({"messages": CHANNELS.get(q["url"], [])})
    if p == "/v1/userDataByFid":
        fid = int(q["fid"])
        if fid not in NAMES:
            return web.json_response({"messages": []})
        return web.json_response({"messages": [msg("u", fid, userDataBody={
            "type": "USER_DATA_TYPE_USERNAME", "value": NAMES[fid]})]})
    if p == "/v1/userNameProofByName":
        fid = {v: k for k, v in NAMES.items()}.get(q["name"])
        return web.json_response({"fid": fid}) if fid else web.Response(status=404)
    if p == "/v1/onChainEventsByFid":
        return web.json_response({"events": [
            {"blockTimestamp": 1, "signerEventBody": {"key": k, "eventType": "SIGNER_EVENT_TYPE_ADD",
                                                      "metadata": metadata(app)}}
            for k, app in KEYS.get(int(q["fid"]), [])]})
    if p == "/v1/reactionsByFid":
        if q.get("reaction_type") != "Like":
            return web.json_response({"messages": []})
        return web.json_response({"messages": REACTIONS.get(int(q["fid"]), [])})
    if p == "/v1/signersByFid":
        return web.json_response({"signers": []})
    if p == "/v1/verificationsByFid":
        return web.json_response({"messages": [
            msg(f"v{i}", int(q["fid"]), verificationAddAddressBody={"address": a, "protocol": "PROTOCOL_ETHEREUM"})
            for i, a in enumerate(WALLETS.get(int(q["fid"]), []))]})
    return web.Response(status=404)


# erin (50) holds the "I voted for Network School Farcaster" NFT in her second wallet.
WALLETS = {50: ["0x" + "11" * 20, "0x" + "5e" * 20], 61: ["0x" + "22" * 20]}
VOTED = {"0x" + "5e" * 20}


async def base_rpc(request):
    from test_hub import multicall_reply
    call = await request.json()
    result = multicall_reply(call["params"][0]["data"], lambda a: 1 if a in VOTED else 0)
    return web.json_response({"jsonrpc": "2.0", "id": call["id"], "result": result})


async def with_ui(test, *, fid=9, key=None, public=False, casting=False, tip_fid=None, votes=False):
    """Start the fake node and the UI against it; run `test(client, base, calls, submitted)`."""
    calls, submitted = [], []
    nodeapp = web.Application()

    async def get(request):
        calls.append((request.path, dict(request.query)))
        return await node(request)

    async def submit(request):
        submitted.append(await request.read())
        return web.json_response({"hash": "0x" + "aa" * 20})

    nodeapp.router.add_get("/{tail:.*}", get)
    nodeapp.router.add_post("/v1/submitMessage", submit)
    nodeapp.router.add_post("/rpc", base_rpc)
    runners = []

    async def start(app):
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        return site._server.sockets[0].getsockname()[1]

    try:
        node_port = await start(nodeapp)
        async with Hub(f"http://127.0.0.1:{node_port}", rate=1000, retries=2) as hub:
            rpc = f"http://127.0.0.1:{node_port}/rpc" if votes else None
            ui = UI(hub, fid=fid, key=key, public=public, casting=casting, tip_fid=tip_fid, vote_rpc=rpc)
            port = await start(make_app(ui))
            async with aiohttp.ClientSession() as client:
                return await test(client, f"http://127.0.0.1:{port}", calls, submitted)
    finally:
        for r in runners:
            await r.cleanup()


def run(test, **kw):
    return asyncio.run(with_ui(test, **kw))


# ----------------------------------------------------------- recommendations


def test_score_friends_of_friends_needs_two_and_skips_known():
    got = score(9, {1, 2, 3}, {1: {50, 51, 9, 2}, 2: {50, 52}, 3: {50, 51}}, {})
    assert [(s.fid, s.score, s.followed_by) for s in got] == [(50, 3, [1, 2, 3]), (51, 2, [1, 3])]


def test_score_channels_add_points_and_reasons():
    got = score(9, {1}, {1: {61}}, {DEV: {60: 2, 61: 1, 9: 5, 1: 1}, ART: {61: 1}}, exclude={70})
    assert [(s.fid, s.score) for s in got] == [(61, 1 + 2 + 2), (60, 2)]
    names = {1: "alice"}
    assert reasons(got[0], names) == ["Followed by @alice, whom you follow", "Both active in /dev, /art"]
    many = Suggestion(5, 4, followed_by=[1, 2, 3, 4])
    assert reasons(many, {1: "a", 2: "b", 3: "c"}) == ["Followed by 4 people you follow: @a, @b, @c and 1 more"]


def test_score_exclude_and_limit():
    graph = {1: {50, 51, 52}, 2: {50, 51, 52}}
    assert [s.fid for s in score(9, {1, 2}, graph, {}, exclude={51}, limit=1)] == [50]


def test_suggest_reads_sample_and_channels_from_the_node():
    async def go():
        calls = []
        app = web.Application()

        async def get(request):
            calls.append((request.path, dict(request.query)))
            return await node(request)

        app.router.add_get("/{tail:.*}", get)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with Hub(f"http://127.0.0.1:{port}", rate=1000) as hub:
                full = await suggest(hub, 9)
                calls.clear()
                small = await suggest(hub, 9, sample=2, channels=0)
                return full, small, calls
        finally:
            await runner.cleanup()

    full, small, calls = asyncio.run(go())
    got = {s.fid: (s.score, s.followed_by, s.channels) for s in full}
    assert got == {50: (3, [1, 2, 3], []), 51: (2, [1, 3], []), 60: (2, [], [DEV]),
                   61: (4, [], [DEV, ART])}
    assert [s.fid for s in full][:2] == [61, 50]
    # sample=2 reads only the two most recent follows (1 and 2), newest follows first.
    sampled = [q["fid"] for p, q in calls if p == "/v1/linksByFid" and q["fid"] != "9"]
    assert sorted(sampled) == ["1", "2"]
    assert all(q.get("reverse") == "true" for p, q in calls if p == "/v1/linksByFid" and q["fid"] != "9")
    assert {s.fid for s in small} == {50}
    assert not any(p == "/v1/castsByParent" for p, _ in calls)


# ------------------------------------------------------------------- the feed


def test_filter_by_app():
    items = [{"app": "warpcast"}, {"app": "own key"}, {"app": "unknown"}, {"app": "Warpcast"}]
    assert filter_by_app(items, hide={"WARPCAST"}) == [{"app": "own key"}, {"app": "unknown"}]
    assert filter_by_app(items, only={"own key", "unknown"}) == [{"app": "own key"}, {"app": "unknown"}]
    assert filter_by_app(items) == items


def test_feed_endpoint_labels_apps_and_filters():
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/feed") as r:
            full = await r.json()
        async with client.get(f"{base}/api/feed", params={"hide": "warpcast,unknown"}) as r:
            own = await r.json()
        async with client.get(f"{base}/api/feed", params={"only": "unknown"}) as r:
            unknown = await r.json()
        async with client.get(f"{base}/api/feed", params={"limit": "1", "offset": "1"}) as r:
            page2 = await r.json()
        n_casts_calls = sum(1 for p, _ in calls if p == "/v1/castsByFid")
        return full, own, unknown, page2, n_casts_calls

    full, own, unknown, page2, n = run(test)
    by_hash = {c["hash"]: c for c in full["casts"]}
    assert set(by_hash) == {"c1", "c2", "c3"}  # c1-old is outside 24 hours
    assert by_hash["c1"]["app"] == "warpcast"
    assert by_hash["c2"]["app"] == "own key"
    assert by_hash["c3"]["app"] == "unknown"
    assert by_hash["c1"]["text"] == "hi @bob there"  # mention put back
    assert by_hash["c1"]["author"]["username"] == "alice"
    assert by_hash["c2"]["channel"] == "/dev"
    assert {a["name"] for a in full["apps"]} == {"warpcast", "own key", "unknown"}
    assert [c["hash"] for c in own["casts"]] == ["c2"] and own["all"] == 3
    assert [c["hash"] for c in unknown["casts"]] == ["c3"]
    assert len(page2["casts"]) == 1 and page2["more"] is True
    assert n == 3  # the window is cached: one castsByFid per followed account


def test_feed_as_someone_else_and_no_account():
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/feed", params={"who": "bob"}) as r:
            bob = await r.json()
        async with client.get(f"{base}/api/feed") as r:
            status, err = r.status, await r.json()
        return bob, status, err

    bob, status, err = run(test, fid=None)
    assert bob["fid"] == 2
    assert status == 400 and "browse" in err["error"]


def test_with_mentions_uses_byte_offsets():
    c = Cast.from_message(cast("x", 1, text="héllo  and ", mentions=[2, 3], mentionsPositions=[7, 12]))
    assert with_mentions(c, {2: "bob", 3: None}) == "héllo @bob and @fid:3"


def test_channel_url():
    assert channel_url("art") == ART
    assert channel_url("/Art") == ART
    assert channel_url("dev") == DEV  # an older channel, by its chain:// URL
    assert channel_url(" ") is None
    assert channel_url("chain://eip155:1/erc721:0xabc") == "chain://eip155:1/erc721:0xabc"
    assert channel_name(ART) == "/art" and channel_name(DEV) == "/dev"
    assert channel_name("https://farcaster.xyz/~/channel/x") == "/x"


# ------------------------------------------------------------------ writing


async def post(client, url, body, **headers):
    async with client.post(url, json=body, headers=headers) as r:
        return r.status, await r.json()


def test_read_only_write_endpoints_explain_key_add():
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/me") as r:
            me = await r.json()
        out = [await post(client, f"{base}/api/cast", {"text": "hi"}),
               await post(client, f"{base}/api/react", {"action": "like", "cast": "1:0xabc"}),
               await post(client, f"{base}/api/follow", {"fid": 50})]
        return me, out, submitted

    me, out, submitted = run(test, casting=True)
    assert me["can_write"] is False and "phunk key add" in me["help"]
    for status, body in out:
        assert status == 403 and body["read_only"] and "phunk key add" in body["error"]
    assert submitted == []


@pytest.fixture
def key():
    pytest.importorskip("nacl")
    pytest.importorskip("blake3")
    pytest.importorskip("eth_account")
    from phunk.write import AppKey

    return AppKey(b"\x22" * 32)


def _fields(buf):
    out, i = {}, 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        if tag & 7 == 0:
            v, i = _varint(buf, i)
        else:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        out.setdefault(tag >> 3, []).append(v)
    return out


def _varint(buf, i):
    n = s = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << s
        s += 7
        if not b & 0x80:
            return n, i

def test_first_release_follows_and_likes_but_casting_is_coming_soon(key):
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/me") as r:
            me = await r.json()
        out = [await post(client, f"{base}/api/cast", {"text": "hi"}),
               await post(client, f"{base}/api/react", {"action": "recast", "cast": "1:0x" + "ab" * 20})]
        ok = [await post(client, f"{base}/api/follow", {"fid": 50}),
              await post(client, f"{base}/api/react", {"action": "like", "cast": "1:0x" + "ab" * 20}),
              await post(client, f"{base}/api/react", {"action": "unlike", "cast": "1:0x" + "ab" * 20})]
        return me, out, ok, submitted

    me, out, ok, submitted = run(test, key=key)
    assert me["can_write"] is True and me["can_cast"] is False
    for status, body in out:
        assert status == 403 and body["coming_soon"] and "coming soon" in body["error"]
    assert [s for s, _ in ok] == [200, 200, 200]
    assert len(submitted) == 3  # the follow, the like and the unlike; no cast, no recast



def test_keyed_writes_submit_signed_messages(key):
    from phunk.write import proto_data

    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/me") as r:
            me_text = await r.text()
        async with client.get(f"{base}/api/suggestions") as r:
            before = [p["fid"] for p in (await r.json())["people"]]
        results = [
            await post(client, f"{base}/api/cast", {"text": "gm", "channel": "dev"}),
            await post(client, f"{base}/api/cast", {"text": "yes", "reply_to": {"fid": 1, "hash": "0x" + "ab" * 20}}),
            await post(client, f"{base}/api/react", {"action": "like", "cast": "1:0x" + "ab" * 20}),
            await post(client, f"{base}/api/react", {"action": "unrecast", "cast": "1:0x" + "ab" * 20}),
            await post(client, f"{base}/api/follow", {"fid": 50}),
            await post(client, f"{base}/api/follow", {"fid": 2, "action": "unfollow"}),
        ]
        async with client.get(f"{base}/api/suggestions") as r:
            after = [p["fid"] for p in (await r.json())["people"]]
        bad = [await post(client, f"{base}/api/react", {"action": "boo", "cast": "1:0xab"}),
               await post(client, f"{base}/api/cast", {"text": "  "}),
               await post(client, f"{base}/api/follow", {"fid": "dwr"})]
        return me_text, before, results, after, bad, submitted

    me_text, before, results, after, bad, submitted = run(test, key=key, casting=True)
    assert '"can_write": true' in me_text
    assert key.private_hex[2:] not in me_text  # the key never leaves the server
    assert all(status == 200 and body["hash"].startswith("0x") for status, body in results)
    types = [_fields(proto_data(m))[1][0] for m in submitted]
    assert types == [proto.CAST_ADD, proto.CAST_ADD, proto.REACTION_ADD, proto.REACTION_REMOVE,
                     proto.LINK_ADD, proto.LINK_REMOVE]
    assert all(_fields(proto_data(m))[2] == [9] for m in submitted)  # as fid 9
    cast_body = _fields(_fields(proto_data(submitted[0]))[5][0])
    assert cast_body[7] == [DEV.encode()]
    assert 50 in before and 50 not in after  # followed here: no longer suggested
    assert [s for s, _ in bad] == [400, 400, 400]
    assert len(submitted) == 6


def test_cross_origin_and_non_json_posts_are_refused(key):
    async def test(client, base, calls, submitted):
        out = [await post(client, f"{base}/api/follow", {"fid": 50}, Origin="http://evil.example"),
               await post(client, f"{base}/api/follow", {"fid": 50}, **{"Sec-Fetch-Site": "cross-site"})]
        async with client.post(f"{base}/api/follow", data="fid=50",
                               headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
            out.append((r.status, await r.json()))
        async with client.get(f"{base}/api/me", headers={"Host": "evil.example"}) as r:
            out.append((r.status, await r.json()))
        same = await post(client, f"{base}/api/follow", {"fid": 50}, Origin=base)
        return out, same, submitted

    out, same, submitted = run(test, key=key)
    assert [s for s, _ in out] == [403, 403, 415, 403]
    assert same[0] == 200
    assert len(submitted) == 1  # only the same-origin one


def test_index_and_static_are_served():
    async def test(client, base, calls, submitted):
        async with client.get(base + "/") as r:
            html, csp = await r.text(), r.headers.get("Content-Security-Policy", "")
        async with client.get(base + "/static/app.js") as r:
            js = r.status
        return html, csp, js

    html, csp, js = run(test)
    assert "<title>phunk</title>" in html and "seed" not in html.lower()
    assert "frame-ancestors 'none'" in csp
    assert js == 200


def test_people_endpoint_cards_lookup_and_follow(key):
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/people") as r:
            mine = await r.json()
        async with client.get(f"{base}/api/people", params={"who": "bob"}) as r:
            bobs = await r.json()
        await post(client, f"{base}/api/follow", {"fid": 50})
        async with client.get(f"{base}/api/people") as r:
            after = await r.json()
        return mine, bobs, after

    mine, bobs, after = run(test, key=key)
    assert mine["fid"] == 9 and mine["summary"]["following"] == 3 and mine["summary"]["talked_to"] == 2  # erin (likes) and alice (a reply)
    cards = {c["fid"]: c for c in mine["cards"]}
    assert mine["cards"][0]["fid"] == 50 and mine["cards"][0]["kind"] == "reconnect"
    assert "You don't follow them yet." in cards[50]["why"] and cards[50]["following"] is False
    assert all(c["kind"] in ("reconnect", "circle", "bridge") and c["why"] for c in mine["cards"])
    assert 9 not in cards and not ({1, 2, 3} & set(cards))  # never me or people I follow
    assert all({"username", "latest", "app_now"} <= set(c) for c in mine["cards"])
    assert bobs["fid"] == 2  # anyone can be looked up
    assert {c["fid"]: c for c in after["cards"]}[50]["following"] is True


def test_people_cards_stamp_who_voted_for_network_school():
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/people") as r:
            return await r.json(), calls

    out, calls = run(test, votes=True)
    cards = {c["fid"]: c for c in out["cards"]}
    assert cards[50]["ns_vote"] is True  # any verified wallet counts, not just the primary one
    assert not any(c["ns_vote"] for f, c in cards.items() if f != 50)
    assert any(p == "/v1/verificationsByFid" for p, _ in calls)

    out, calls = run(test)  # no Base RPC configured: no badge, no wallet lookups
    assert not any(c["ns_vote"] for c in out["cards"])
    assert not any(p == "/v1/verificationsByFid" for p, _ in calls)


def test_tip_account_is_named_in_me_only_when_set(monkeypatch):
    from phunk.ui import tip_fid_from_env

    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/me") as r:
            return await r.json()

    assert "tip" not in run(test, public=True)
    me = run(test, public=True, tip_fid=3)
    assert me["tip"]["fid"] == 3 and me["tip"]["username"] == "carol"
    monkeypatch.setenv("PHUNK_TIP_FID", " 2391 ")
    assert tip_fid_from_env() == 2391
    for bad in ("", "0", "dwr", "-5"):
        monkeypatch.setenv("PHUNK_TIP_FID", bad)
        assert tip_fid_from_env() is None
