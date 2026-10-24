"""The write path, offline.

Messages are checked byte for byte against the official TypeScript library
(tests/vectors/write.json, made by make_write_vectors.mjs), and the KEY_ADD
flow runs against a fake node.
"""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("nacl")
pytest.importorskip("blake3")
pytest.importorskip("eth_account")

from aiohttp import web  # noqa: E402

from phunk import Hub, NodeError, decode_key_request, proto  # noqa: E402
from phunk.write import (  # noqa: E402
    AppKey, Custody, Writer, add_app_key, build_key_add, build_key_remove_custody, build_key_remove_self,
    proto_data, revoke_key, revoke_key_with_custody,
)

V = json.loads((Path(__file__).parent / "vectors" / "write.json").read_text())
FID, TS = 1234, 183000000
AB, CD, EF = b"\xab" * 20, b"\xcd" * 20, b"\xef" * 20
KEY = AppKey(b"\x22" * 32)
CUSTODY = Custody.from_private_key("0x" + "11" * 32)

BODIES = {
    "cast_simple": (proto.CAST_ADD, proto.CastAddBody(text="hello from phunk")),
    "cast_full": (proto.CAST_ADD, proto.CastAddBody(
        text="hi  and see", mentions=[3], mentions_positions=[3],
        embeds=[proto.Embed(url="https://example.com"), proto.Embed(cast_id=proto.CastId(3, AB))],
        parent_cast_id=proto.CastId(2, CD))),
    "cast_channel": (proto.CAST_ADD, proto.CastAddBody(text="gm", parent_url="https://farcaster.xyz/~/channel/dev")),
    "cast_remove": (proto.CAST_REMOVE, proto.CastRemoveBody(EF)),
    "like": (proto.REACTION_ADD, proto.ReactionBody(proto.LIKE, proto.CastId(3, AB))),
    "unrecast": (proto.REACTION_REMOVE, proto.ReactionBody(proto.RECAST, proto.CastId(3, AB))),
    "follow": (proto.LINK_ADD, proto.LinkBody(3)),
    "unfollow": (proto.LINK_REMOVE, proto.LinkBody(3)),
}


def test_keys_match_reference():
    assert KEY.public_key.hex() == V["signer_key"]
    assert CUSTODY.address == V["custody_address"]
    assert "22" not in repr(KEY)  # repr shows the public key only


@pytest.mark.parametrize("name", sorted(BODIES))
def test_messages_match_reference(name):
    type_, body = BODIES[name]
    msg = KEY.sign(type_, FID, body.encode(), TS)
    assert msg.hex() == V[name]["message"]
    assert proto_data(msg).hex() == V[name]["data"]


def test_key_add_matches_reference():
    kb = V["key_add_body"]
    metadata = CUSTODY.sign_key_request(FID, KEY.public_key, kb["deadline"])
    assert metadata.hex() == kb["metadata"]
    req = decode_key_request(metadata)  # round-trips through the reader
    assert (req.request_fid, req.request_signer.lower(), req.deadline) == (FID, CUSTODY.address.lower(), kb["deadline"])
    sig = CUSTODY.sign_key_add(FID, KEY.public_key, kb["scopes"], kb["ttl"], kb["nonce"], kb["deadline"])
    assert sig.hex() == kb["custody_signature"]
    msg = build_key_add(CUSTODY, FID, KEY, nonce=kb["nonce"], deadline=kb["deadline"],
                        scopes=kb["scopes"], ttl=kb["ttl"], timestamp=TS)
    assert msg.hex() == V["key_add"]["message"]


def test_key_remove_matches_reference():
    deadline = V["key_add_body"]["deadline"]
    sig = CUSTODY.sign_key_remove(FID, KEY.public_key, 6, deadline)
    assert sig.hex() == V["key_remove_custody_body"]["signature"]
    msg = build_key_remove_custody(CUSTODY, FID, KEY.public_key, nonce=6, deadline=deadline,
                                   envelope_key=KEY, timestamp=TS)
    assert msg.hex() == V["key_remove_custody"]["message"]
    msg = build_key_remove_self(KEY, FID, nonce=3, deadline=deadline, timestamp=TS)
    assert msg.hex() == V["key_remove_self"]["message"]


def test_key_add_rejects_bad_scopes_and_ttl():
    with pytest.raises(ValueError):
        build_key_add(CUSTODY, FID, KEY, nonce=1, deadline=1, scopes=[proto.KEY_ADD])
    with pytest.raises(ValueError):
        build_key_add(CUSTODY, FID, KEY, nonce=1, deadline=1, ttl=91 * 24 * 3600)


def test_custody_from_mnemonic_uses_ethereum_path():
    # The standard BIP-39 test phrase (not a real account).
    phrase = "test test test test test test test test test test test junk"
    assert Custody.from_mnemonic(phrase).address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert Custody.from_mnemonic("  " + phrase.replace(" ", "\n") + " ").address.startswith("0xf39F")


# ------------------------------------------------------------ fake node


async def with_node(test, *, nonce=4, fid=1234, reject=None):
    submitted = []
    app = web.Application()

    async def submit(request):
        body = await request.read()
        submitted.append(body)
        if reject:
            return web.json_response({"errCode": "bad_request.validation_failure", "details": reject}, status=400)
        return web.json_response({"hash": "0x" + body[0:0].hex() + "aa" * 20})

    async def signers(request):
        return web.json_response({"signers": [], "currentUserNonce": nonce})

    async def by_address(request):
        return web.json_response({"fid": fid, "idRegisterEventBody": {"to": request.query["address"]}})

    async def proof(request):
        names = {"dwr": 3, "v": 2}
        name = request.query["name"]
        if name not in names:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"fid": names[name], "name": name})

    app.router.add_post("/v1/submitMessage", submit)
    app.router.add_get("/v1/signersByFid", signers)
    app.router.add_get("/v1/onChainIdRegistryEventByAddress", by_address)
    app.router.add_get("/v1/userNameProofByName", proof)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with Hub(f"http://127.0.0.1:{port}", rate=1000, retries=2) as hub:
            return await test(hub, submitted)
    finally:
        await runner.cleanup()


def decode_fields(buf):
    """Tiny protobuf reader for the tests: {field: [values]}."""
    out, i = {}, 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        num, wire = tag >> 3, tag & 7
        if wire == 0:
            v, i = _varint(buf, i)
        else:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        out.setdefault(num, []).append(v)
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


def test_add_app_key_finds_fid_and_uses_next_nonce():
    async def test(hub, submitted):
        fid, key = await add_app_key(hub, CUSTODY)
        assert fid == 1234
        (msg,) = submitted
        data = decode_fields(proto_data(msg))
        assert data[1] == [proto.KEY_ADD] and data[2] == [1234]
        body = decode_fields(data[19][0])
        assert body[1] == [key.public_key]
        assert body[5] == [5]  # currentUserNonce 4 -> 5
        assert decode_fields(msg)[6] == [key.public_key]  # envelope signed by the new key

    asyncio.run(with_node(test, nonce=4))


def test_submit_error_carries_node_reason():
    async def test(hub, submitted):
        with pytest.raises(NodeError, match="invalid signature"):
            await add_app_key(hub, CUSTODY, fid=99)

    asyncio.run(with_node(test, reject="invalid signature"))


def test_writer_cast_resolves_mentions_and_channel():
    async def test(hub, submitted):
        me = Writer(hub, 1234, KEY)
        await me.cast("hi @dwr and @nobody-here", channel="https://farcaster.xyz/~/channel/dev")
        await me.cast("reply", reply_to=(3, "0x" + "ab" * 20), channel="ignored-when-replying")
        await me.follow(3)
        first, reply, follow = [decode_fields(proto_data(m)) for m in submitted]
        body = decode_fields(first[5][0])
        assert body[4] == [b"hi  and @nobody-here"]
        assert body[2] == [bytes([3])]  # packed mentions: [3]
        assert body[5] == [bytes([3])]  # byte offset where "@dwr" was
        assert body[7] == [b"https://farcaster.xyz/~/channel/dev"]
        rbody = decode_fields(reply[5][0])
        assert 7 not in rbody and decode_fields(rbody[3][0]) == {1: [3], 2: [AB]}
        assert follow[1] == [proto.LINK_ADD]

    asyncio.run(with_node(test))


def test_cast_too_long_is_refused():
    async def test(hub, submitted):
        with pytest.raises(ValueError):
            await Writer(hub, 1234, KEY).cast("x" * 1025, resolve_mentions=False)
        assert submitted == []

    asyncio.run(with_node(test))


# ------------------------------------------------------------ app signer

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sign_key_request", Path(__file__).parent.parent / "signer" / "api" / "sign_key_request.py")
signer_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(signer_api)

from phunk.write import check_key_request, now  # noqa: E402

APP_FID = 777
APP = Custody.from_private_key("0x" + "33" * 32)


def test_signer_approval_checks_out_and_matches_phunk_encoding():
    out = signer_api.approve(KEY.public_hex, APP_FID, APP._account)
    md = bytes.fromhex(out["metadata"][2:])
    req = check_key_request(md, KEY.public_key, app_fid=APP_FID, app_custody=APP.address)
    assert req.request_fid == APP_FID and out["request_signer"] == APP.address
    assert md == APP.sign_key_request(APP_FID, KEY.public_key, req.deadline)  # same as signing locally


# From @farcaster/core 0.20.0: ViemLocalEip712Signer(0x33..33).signKeyRequest(
#   {requestFid: 777n, key: 0x22 * 32, deadline: 1900003600n})
ONCHAIN_SIG = ("73dc01cd7dc1d3dce0fec04881acea973ad889e8ec3bea51ba618c4b6fc7c57c"
               "3459164c0853f4e2a57783d4b1e9690d1d6724be0e24c2070833e1b35716f02b1c")


def test_signer_onchain_approval_matches_the_official_library():
    """The Farcaster app adds keys on Optimism: validator domain, Unix deadline."""
    out = signer_api.approve("0x" + "22" * 32, APP_FID, APP._account, now=1900000000, onchain=True)
    req = decode_key_request(bytes.fromhex(out["metadata"][2:]))
    assert APP.address == "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"
    assert (req.request_fid, req.request_signer, req.deadline) == (APP_FID, APP.address.lower(), 1900003600)
    assert req.signature.hex() == ONCHAIN_SIG
    # The default stays the gasless KEY_ADD form, in Farcaster time.
    assert signer_api.approve("0x" + "22" * 32, APP_FID, APP._account, now=1900000000)["deadline"] == 290544400


def test_signer_asgi_app(monkeypatch):
    from test_web import asgi

    monkeypatch.setenv("PHUNK_APP_FID", str(APP_FID))
    monkeypatch.setenv("PHUNK_APP_CUSTODY_KEY", "0x" + "33" * 32)
    status, headers, body = asgi(signer_api.app, "GET", "/api/sign-key-request")
    assert status == 200 and body == {"request_fid": APP_FID, "request_signer": APP.address}
    assert headers["access-control-allow-origin"] == "*"
    status, _, body = asgi(signer_api.app, "POST", "/api/sign-key-request", json.dumps({"key": KEY.public_hex}).encode())
    md = bytes.fromhex(body["metadata"][2:])
    assert status == 200 and check_key_request(md, KEY.public_key, app_fid=APP_FID, app_custody=APP.address)
    onchain = json.dumps({"key": KEY.public_hex, "onchain": True}).encode()
    assert asgi(signer_api.app, "POST", "/api/sign-key-request", onchain)[0] == 403  # paused
    assert asgi(signer_api.app, "POST", "/api/sign_key_request", b"nope")[0] == 400
    assert asgi(signer_api.app, "POST", "/api/sign-key-request", b"x" * 2000)[0] == 413
    assert asgi(signer_api.app, "GET", "/elsewhere")[0] == 404
    monkeypatch.delenv("PHUNK_APP_CUSTODY_KEY")
    assert asgi(signer_api.app, "GET", "/api/sign-key-request")[0] == 500


def test_check_key_request_rejects_wrong_app_signer_key_and_expiry():
    md = bytes.fromhex(signer_api.approve(KEY.public_hex, APP_FID, APP._account)["metadata"][2:])
    with pytest.raises(ValueError, match="app fid"):
        check_key_request(md, KEY.public_key, app_fid=APP_FID + 1)
    with pytest.raises(ValueError, match="custody address"):
        check_key_request(md, KEY.public_key, app_custody=CUSTODY.address)
    with pytest.raises(ValueError, match="does not match"):
        check_key_request(md, AppKey.generate().public_key)  # approval was for another key
    with pytest.raises(ValueError, match="expired"):
        check_key_request(md, KEY.public_key, at=now() + 7200)
    with pytest.raises(ValueError):
        signer_api.approve("0x1234", APP_FID, APP._account)


def test_add_app_key_with_app_signer_registers_under_the_app():
    async def go(app_custody):
        submitted, approvals = [], []
        app = web.Application()

        async def sign(request):
            body = await request.json()
            approvals.append(body)
            return web.json_response(signer_api.approve(body["key"], APP_FID, APP._account))

        async def events(request):
            fid = int(request.query["fid"])
            return web.json_response({"events": [{"blockNumber": 1, "logIndex": 0, "fid": fid,
                                                  "idRegisterEventBody": {"to": app_custody}}]})

        async def signers(request):
            return web.json_response({"signers": [], "currentUserNonce": 2})

        async def submit(request):
            submitted.append(await request.read())
            return web.json_response({"hash": "0x" + "aa" * 20})

        app.router.add_post("/sign", sign)
        app.router.add_get("/v1/onChainEventsByFid", events)
        app.router.add_get("/v1/signersByFid", signers)
        app.router.add_post("/v1/submitMessage", submit)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            async with Hub(base, rate=1000, retries=1) as hub:
                try:
                    fid, key = await add_app_key(hub, CUSTODY, fid=1234, app_signer=f"{base}/sign")
                except ValueError as e:
                    return None, str(e), submitted, approvals
                return key, None, submitted, approvals
        finally:
            await runner.cleanup()

    key, err, submitted, approvals = asyncio.run(go(APP.address.lower()))
    assert err is None and approvals == [{"key": key.public_hex}]  # only the public key is sent
    body = decode_fields(decode_fields(proto_data(submitted[0]))[19][0])
    assert decode_key_request(body[6][0]).request_fid == APP_FID  # registered under the app
    assert body[5] == [3]

    # A signer whose key isn't the app's custody key on-chain is refused before submitting.
    key, err, submitted, approvals = asyncio.run(go(CUSTODY.address.lower()))
    assert err and "custody address" in err and submitted == []


# ------------------------------------------------------------ revoking keys


async def with_key_node(test, *, gasless=(), app_nonces=None, user_nonce=4, stored=None):
    """A fake node for KEY_REMOVE. `gasless` is [(key hex, request fid)].
    `app_nonces` None mimics a node that doesn't report them; `stored` makes
    the first submit fail like a node whose stored nonce is higher."""
    submitted, queries = [], []
    app = web.Application()

    async def signers(request):
        queries.append(dict(request.query))
        out = {"signers": [{"source": "SIGNER_SOURCE_OFFCHAIN", "key": k, "fid": FID, "requestFid": rf,
                            "addedAt": 1760000000} for k, rf in gasless],
               "currentUserNonce": user_nonce}
        asked = [int(v) for k, v in request.query.items() if k.startswith("requesterFids[")]
        if app_nonces is not None and asked:
            out["requesterFidNonces"] = {str(f): app_nonces.get(f, 0) for f in asked}
        return web.json_response(out)

    async def events(request):
        return web.json_response({"events": []})

    async def by_address(request):
        return web.json_response({"fid": FID, "idRegisterEventBody": {"to": request.query["address"]}})

    async def submit(request):
        body = await request.read()
        submitted.append(body)
        if stored is not None and len(submitted) == 1:
            return web.json_response({"errCode": "bad_request.conflict", "details":
                                      f"gasless-key nonce 1 is not greater than stored nonce {stored}"}, status=400)
        return web.json_response({"hash": "0x" + "aa" * 20})

    app.router.add_get("/v1/signersByFid", signers)
    app.router.add_get("/v1/onChainEventsByFid", events)
    app.router.add_get("/v1/onChainIdRegistryEventByAddress", by_address)
    app.router.add_post("/v1/submitMessage", submit)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with Hub(f"http://127.0.0.1:{port}", rate=1000, retries=2) as hub:
            await test(hub)
    finally:
        await runner.cleanup()
    return submitted, queries


def _key_remove(msg):
    data = decode_fields(proto_data(msg))
    assert data[1] == [proto.KEY_REMOVE] and data[2] == [FID]
    return decode_fields(data[20][0]), decode_fields(msg)[6][0]  # body, envelope signer


def test_revoke_key_signs_its_own_removal_with_the_app_nonce():
    async def test(hub):
        await revoke_key(hub, FID, KEY)

    submitted, queries = asyncio.run(with_key_node(test, gasless=[(KEY.public_hex, 777)], app_nonces={777: 2}))
    assert any(q.get("requesterFids[0]") == "777" for q in queries)
    (msg,) = submitted
    body, signer = _key_remove(msg)
    assert body[1] == [KEY.public_key] and signer == KEY.public_key
    assert body[3] == [proto.REMOVE_BY_SELF] and body[5] == [3]  # app nonce 2 -> 3
    assert 2 not in body  # no inner signature


def test_revoke_key_retries_once_with_the_stored_nonce():
    async def test(hub):
        await revoke_key(hub, FID, KEY)

    submitted, _ = asyncio.run(with_key_node(test, gasless=[(KEY.public_hex, FID)], stored=4))
    assert [_key_remove(m)[0][5] for m in submitted] == [[1], [5]]


def test_revoke_key_refuses_a_key_that_is_not_active():
    async def test(hub):
        with pytest.raises(ValueError, match="not an active gasless key"):
            await revoke_key(hub, FID, KEY)

    submitted, _ = asyncio.run(with_key_node(test, gasless=[("0x" + "99" * 32, FID)]))
    assert submitted == []


def test_revoke_key_with_custody_uses_the_user_nonce():
    other = AppKey(b"\x44" * 32).public_hex

    async def test(hub):
        assert await revoke_key_with_custody(hub, CUSTODY, other) == FID

    submitted, _ = asyncio.run(with_key_node(test, gasless=[(other, FID)], user_nonce=4))
    (msg,) = submitted
    body, signer = _key_remove(msg)
    assert body[1] == [bytes.fromhex(other[2:])] and signer != body[1][0]
    assert body[3] == [proto.REMOVE_BY_CUSTODY] and body[5] == [5]
    assert body[2] == [CUSTODY.sign_key_remove(FID, body[1][0], 5, body[4][0])]


def test_cli_post_prints_fid_hash_and_where_to_check(monkeypatch, capsys):
    # `phunk post` prints FID:HASH (what `phunk like` takes) and a castById URL.
    import argparse

    from phunk.__main__ import run_write

    monkeypatch.setenv("PHUNK_FID", "1234")
    monkeypatch.setenv("PHUNK_APP_KEY", KEY.private_hex)
    args = argparse.Namespace(cmd="post", text="hello", reply_to=None, channel=None, embed=None)

    async def test(hub, submitted):
        await run_write(hub, args)
        out = capsys.readouterr().out.splitlines()
        assert len(submitted) == 1
        assert out[0].startswith("posted 1234:0x")
        h = out[0].split(":", 1)[1]
        assert out[1].endswith(f"/v1/castById?fid=1234&hash={h}")
        assert out[1].startswith("check:  http://127.0.0.1:")

    asyncio.run(with_node(test))
