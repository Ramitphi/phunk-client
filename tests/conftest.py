"""Shared test helpers: an event loop, a fake Farcaster client, a fake Redis.

Both fakes are real aiohttp servers on localhost, so the code under test
makes real HTTP requests and nothing is monkeypatched.
"""
import asyncio

import aiohttp
import pytest
from aiohttp import web


def run_loop(coro):
    """Run one coroutine to completion.

    Inside a fixture that started a server, this uses that fixture's loop, so
    the test body and the session it was handed share one loop."""
    loop = _loop[0]
    if loop is None:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


_loop: list = [None]  # the loop a server fixture is running on, if any


class _Server:
    """An aiohttp app on a port of its own, started and stopped per test."""

    def __init__(self, routes):
        self.routes, self.runner, self.base = routes, None, None

    async def start(self):
        app = web.Application()
        for method, path, handler in self.routes:
            app.router.add_route(method, path, handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self.base

    async def stop(self):
        await self.runner.cleanup()


def _with_server(routes, make):
    """Start `routes`, build the fixture value with `make(session, base)`, tear down after."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _loop[0] = loop
    server = _Server(routes)
    base = loop.run_until_complete(server.start())
    session = loop.run_until_complete(_session())
    try:
        yield make(session, base)
    finally:
        loop.run_until_complete(session.close())
        loop.run_until_complete(server.stop())
        loop.close()
        _loop[0] = None
        asyncio.set_event_loop(None)


async def _session():
    return aiohttp.ClientSession()


@pytest.fixture
def fake_client():
    """A stand-in Farcaster client: (session, requests it got, set its reply).

    The notification endpoint answers with whatever `reply(...)` was last
    given, so a test says which tokens the client accepted.
    """
    seen, answer = [], {"body": {"result": {"successfulTokens": []}}, "status": 200}

    async def notify(request):
        seen.append(await request.json())
        return web.json_response(answer["body"], status=answer["status"])

    def reply(body, status=200):
        answer["body"], answer["status"] = body, status

    yield from _with_server([("POST", "/notify", notify), ("POST", "/n", notify)],
                            lambda session, base: (_Rebased(session, base), seen, reply))


@pytest.fixture
def fake_redis():
    """A stand-in Upstash Redis over its REST API: (session, RedisStore)."""
    from phunk.notify import RedisStore

    hashes: dict[str, dict[str, str]] = {}

    async def command(request):
        if request.headers.get("Authorization") != "Bearer test-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        cmd, *args = await request.json()
        name = cmd.upper()
        if name == "HSET":
            hashes.setdefault(args[0], {})[args[1]] = args[2]
            return web.json_response({"result": 1})
        if name == "HDEL":
            hashes.get(args[0], {}).pop(args[1], None)
            return web.json_response({"result": 1})
        if name == "HGETALL":
            flat = [x for kv in hashes.get(args[0], {}).items() for x in kv]
            return web.json_response({"result": flat})
        return web.json_response({"error": f"unknown command {cmd}"})

    yield from _with_server([("POST", "/", command)],
                            lambda session, base: (session, RedisStore(session, base, "test-token")))


class _Rebased:
    """A session that sends every https://…example… request to the local fake."""

    def __init__(self, session, base):
        self._session, self._base = session, base

    def post(self, url, **kw):
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        return self._session.post(self._base + path, **kw)

    def __getattr__(self, name):
        return getattr(self._session, name)
