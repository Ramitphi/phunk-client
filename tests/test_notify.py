"""Mini app notifications, offline: signed webhook events, sending, the weekly note."""
import base64
import json
import time

import pytest
from aiohttp import web

from conftest import run_loop
from phunk import notify

nacl = pytest.importorskip("nacl.signing", reason="needs the [write] extra")


def b64(data) -> str:
    raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def event_body(key, fid=9, event="miniapp_added", url="https://client.example/notify", token="t1",
               key_hex=None, sign_with=None):
    header = b64({"fid": fid, "type": "app_key", "key": key_hex or ("0x" + bytes(key.verify_key).hex())})
    payload = {"event": event}
    if url and token:
        payload["notificationDetails"] = {"url": url, "token": token}
    payload = b64(payload)
    signed = f"{header}.{payload}".encode()
    sig = (sign_with or key).sign(signed).signature
    return {"header": header, "payload": payload, "signature": b64(sig)}


class FakeHub:
    """Only what notify needs: the account's active keys."""

    def __init__(self, keys):
        self.keys = keys

    async def collect(self, path, params, key=None):
        assert path == "/v1/signersByFid"
        return [{"key": k} for k in self.keys.get(int(params["fid"]), [])]


def test_added_keeps_the_token_and_removed_drops_it():
    key = nacl.SigningKey.generate()
    hex_key = "0x" + bytes(key.verify_key).hex()
    hub, store = FakeHub({9: [hex_key]}), notify.MemoryStore()

    async def test():
        await notify.handle_event(hub, store, event_body(key))
        added = await store.all()
        await notify.handle_event(hub, store, event_body(key, event="notifications_disabled", url=None, token=None))
        return added, await store.all()

    added, after = run_loop(test())
    assert added == {9: {"url": "https://client.example/notify", "token": "t1"}}
    assert after == {}


def test_an_event_is_refused_unless_a_live_key_signed_it():
    key, other = nacl.SigningKey.generate(), nacl.SigningKey.generate()
    hex_key = "0x" + bytes(key.verify_key).hex()
    store = notify.MemoryStore()

    async def test():
        out = []
        for hub, body in [
            (FakeHub({9: [hex_key]}), event_body(key, sign_with=other)),        # someone else signed it
            (FakeHub({9: []}), event_body(key)),                                # the key was revoked
            (FakeHub({9: [hex_key]}), event_body(key, event="who_knows")),      # not an event we know
            (FakeHub({9: [hex_key]}), {"header": "x", "payload": "y"}),         # not an event at all
        ]:
            with pytest.raises(notify.BadEvent):
                await notify.handle_event(hub, store, body)
            out.append(await store.all())
        return out

    assert run_loop(test()) == [{}, {}, {}, {}]


def test_sending_batches_tokens_and_drops_the_invalid_ones(fake_client):
    session, seen, reply = fake_client
    store = notify.MemoryStore()
    subs = {i: {"url": "https://client.example/notify", "token": f"t{i}"} for i in range(1, 4)}
    reply({"result": {"successfulTokens": ["t1"], "invalidTokens": ["t2"], "rateLimitedTokens": ["t3"]}})

    async def test():
        for fid, s in subs.items():
            await store.put(fid, s["url"], s["token"])
        out = await notify.send(session, store, subs, id="n1", title="Hi", body="There",
                                target_url="https://miniapp.phunk.social/")
        return out, await store.all()

    out, left = run_loop(test())
    assert (out["sent"], out["invalid"], out["rate_limited"]) == ([1], [2], [3])
    assert sorted(left) == [1, 3]  # only the invalid token is forgotten
    assert seen[0]["tokens"] == ["t1", "t2", "t3"] and seen[0]["notificationId"] == "n1"


def test_a_long_title_or_body_is_cut_to_what_clients_accept(fake_client):
    session, seen, reply = fake_client
    store = notify.MemoryStore()
    reply({"result": {"successfulTokens": ["t1"]}})

    async def test():
        await notify.send(session, store, {1: {"url": "https://c.example/n", "token": "t1"}},
                          id="x" * 200, title="T" * 90, body="B" * 300, target_url="https://miniapp.phunk.social/")

    run_loop(test())
    assert len(seen[0]["title"]) == 32 and len(seen[0]["body"]) == 128 and len(seen[0]["notificationId"]) == 128


@pytest.mark.parametrize("feed, body", [
    ({"summary": {"moved": 0}, "cards": [{"kind": "reconnect", "username": "dwr"},
                                         {"kind": "reconnect", "username": "v"}]},
     "You talk with @dwr and @v but don't follow them yet."),
    ({"summary": {"moved": 2}, "cards": [{"kind": "circle", "username": "dwr"}]},
     "2 people you talk to moved to another app. See who."),
    ({"summary": {"moved": 0}, "cards": [{"kind": "bridge", "username": "dwr"},
                                         {"kind": "bridge", "username": "v"},
                                         {"kind": "bridge", "username": "ted"}]},
     "3 people to meet this week, like @dwr, @v and 1 more."),
])
def test_the_weekly_note_says_what_is_in_the_feed(feed, body):
    title, text = notify.digest(feed)
    assert title == "Your people this week" and text == body


def test_no_note_when_there_is_nothing_to_say():
    assert notify.digest({"summary": {"moved": 0}, "cards": []}) is None


def test_digests_go_out_weekly_and_skip_accounts_that_just_had_one(fake_client):
    session, seen, reply = fake_client
    store = notify.MemoryStore()
    now = time.time()
    reply({"result": {"successfulTokens": ["t1", "t2"]}})

    async def people(fid):
        return {"summary": {"moved": 0}, "cards": [{"kind": "reconnect", "username": f"u{fid}"}]}

    async def test():
        for fid in (1, 2):
            await store.put(fid, "https://c.example/n", f"t{fid}")
        await store.mark_sent(2, now - 3600)  # fid 2 got one an hour ago
        first = await notify.send_digests(session, store, people, target_url="https://m.example/", now=now)
        again = await notify.send_digests(session, store, people, target_url="https://m.example/", now=now)
        later = await notify.send_digests(session, store, people, target_url="https://m.example/",
                                          now=now + notify.WEEK)
        return first, again, later

    first, again, later = run_loop(test())
    assert first["sent"] == [1] and again["sent"] == []      # only the one that was due, and not twice
    assert later["sent"] == [1, 2]                           # a week on, both are due
    assert seen[0]["targetUrl"] == "https://m.example/"


def test_one_account_failing_does_not_stop_the_rest(fake_client):
    session, seen, reply = fake_client
    store = notify.MemoryStore()
    reply({"result": {"successfulTokens": ["t2"]}})

    async def people(fid):
        if fid == 1:
            raise RuntimeError("the node is having a day")
        return {"summary": {"moved": 0}, "cards": [{"kind": "reconnect", "username": "v"}]}

    async def test():
        for fid in (1, 2):
            await store.put(fid, "https://c.example/n", f"t{fid}")
        return await notify.send_digests(session, store, people, target_url="https://m.example/")

    out = run_loop(test())
    assert out["failed"] == [1] and out["sent"] == [2]


def test_welcome_note_only_on_add_with_notifications_on(fake_client):
    session, seen, reply = fake_client
    store = notify.MemoryStore()
    reply({"result": {"successfulTokens": ["t1"]}})

    async def test():
        on = notify.Event(9, "0x00", "miniapp_added", "https://c.example/n", "t1")
        off = notify.Event(9, "0x00", "miniapp_added")  # added, notifications off
        await notify.welcome(session, store, on, target_url="https://m.example/")
        await notify.welcome(session, store, off, target_url="https://m.example/")

    run_loop(test())
    assert len(seen) == 1 and seen[0]["title"] == "phunk is added"


def test_redis_store_round_trip(fake_redis):
    session, store = fake_redis

    async def test():
        await store.put(9, "https://c.example/n", "t9")
        await store.mark_sent(9, 1700000000)
        subs, sent = await store.all(), await store.sent_at()
        await store.drop(9)
        return subs, sent, await store.all()

    subs, sent, after = run_loop(test())
    assert subs == {9: {"url": "https://c.example/n", "token": "t9"}}
    assert sent == {9: 1700000000.0} and after == {}
