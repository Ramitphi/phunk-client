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
from phunk.write import AppKey, Custody, Writer, add_app_key, build_key_add, proto_data  # noqa: E402

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
