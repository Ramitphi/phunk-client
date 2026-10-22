"""phunk.social, offline: public mode and the Vercel function (api/index.py)
against the fake node from test_ui."""
import asyncio
import importlib.util
import json
import subprocess
import threading
from pathlib import Path

import pytest
from aiohttp import web

from phunk.ui import PUBLIC_HELP
from test_ui import node, post, run

ROOT = Path(__file__).resolve().parent.parent


def test_public_mode_has_no_account_and_refuses_writes():
    async def test(client, base, calls, submitted):
        async with client.get(f"{base}/api/me") as r:
            me = await r.json()
        status, body = await post(client, f"{base}/api/follow", {"fid": 50})
        async with client.get(f"{base}/api/people") as r:
            no_who = r.status, await r.json()
        async with client.get(f"{base}/api/people?who=me") as r:
            people = r.status, await r.json()
        return me, status, body, no_who, people, submitted

    me, status, body, no_who, people, submitted = run(test, fid=None, public=True)
    assert me["public"] is True and me["can_write"] is False and me["fid"] is None
    assert me["help"] == PUBLIC_HELP
    assert status == 403 and body["read_only"] and body["error"] == PUBLIC_HELP
    assert no_who == (400, {"error": "pick an account to browse as"})
    assert people[0] == 200 and people[1]["fid"] == 9 and people[1]["cards"]
    assert submitted == []


@pytest.fixture
def fake_node():
    """The fake node on its own loop and thread, as a URL."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def start():
        app = web.Application()
        app.router.add_get("/{tail:.*}", node)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]

    runner, port = asyncio.run_coroutine_threadsafe(start(), loop).result()
    yield f"http://127.0.0.1:{port}"
    asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result()
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture
def site(fake_node, monkeypatch):
    """api/index.py, loaded fresh and pointed at the fake node."""
    monkeypatch.setenv("PHUNK_NODE", fake_node)
    monkeypatch.setenv("PHUNK_RATE", "1000")
    spec = importlib.util.spec_from_file_location("phunk_site", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    if mod._loop is not None:
        asyncio.run_coroutine_threadsafe(mod._ui.hub.close(), mod._loop).result()
        mod._loop.call_soon_threadsafe(mod._loop.stop)


def test_site_answers_reads_with_cdn_cache_headers(site):
    status, body, cache = site.respond("/api/people?who=me")
    assert status == 200 and body["fid"] == 9 and body["cards"]
    assert "s-maxage" in cache

    status, body, cache = site.respond("/api/feed?who=alice&hours=24")
    assert status == 200 and body["fid"] == 1 and "s-maxage" in cache

    status, body, cache = site.respond("/api/me")
    assert status == 200 and body["public"] is True and body["fid"] is None

    # A refresh, an error or an unknown path is never cached.
    assert site.respond("/api/people?who=me&refresh=1")[2] == "no-store"
    assert site.respond("/api/people")[:3] == (400, {"error": "pick an account to browse as"}, "no-store")
    assert site.respond("/api/people?who=nobody-here")[0] == 404
    assert site.respond("/api/nope") == (404, {"error": "no such endpoint"}, "no-store")


def asgi(app, method, path, body=b""):
    """One request through an ASGI app -> (status, headers dict, JSON body or None)."""
    path, _, query = path.partition("?")
    scope = {"type": "http", "method": method, "path": path, "query_string": query.encode(), "headers": []}
    sent, todo = [], [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return todo.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = sent[0]
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    raw = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], headers, json.loads(raw) if raw else None


def test_site_asgi_app(site):
    status, headers, body = asgi(site.app, "GET", "/api/people?who=me")
    assert status == 200 and body["fid"] == 9 and "s-maxage" in headers["cache-control"]
    assert headers["content-type"] == "application/json"
    status, headers, body = asgi(site.app, "POST", "/api/cast", b"{}")
    assert status == 405 and body["read_only"] is True and headers["cache-control"] == "no-store"
    assert asgi(site.app, "GET", "/api/nope")[0] == 404


def test_vercel_services_route_to_the_right_service():
    config = json.loads((ROOT / "vercel.json").read_text())
    services = config["services"]
    # The signer is its own Vercel project (Root Directory signer/), so its key isn't shared.
    assert set(services) == {"web", "api"}
    assert 'entrypoint = "api.sign_key_request:app"' in (ROOT / "signer" / "pyproject.toml").read_text()
    assert (ROOT / services["api"]["root"] / "api" / "index.py").is_file()
    assert (ROOT / services["web"]["root"] / "build.sh").is_file()
    assert not {"functions", "buildCommand", "outputDirectory", "framework"} & set(config)
    # /api/* to the API, the catch-all to the page, last.
    order = [(r["source"], r["destination"]["service"]) for r in config["rewrites"]]
    assert order == [("/api/(.*)", "api"), ("/(.*)", "web")]


def _png_info(path):
    """(width, height, colour type) from a PNG's IHDR. Colour type 2 is RGB, no alpha."""
    raw = path.read_bytes()[:26]
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"), raw[25]


def test_static_build_page_and_mini_app(monkeypatch):
    env = {"FARCASTER_HEADER": "eyJmaWQiOjF9", "FARCASTER_PAYLOAD": "eyJkb21haW4iOiJwaHVuay5zb2NpYWwifQ",
           "FARCASTER_SIGNATURE": "MHgxMjM0"}
    import os
    subprocess.run(["sh", str(ROOT / "web" / "build.sh")], check=True, capture_output=True,
                   env={**os.environ, **env})
    out = ROOT / "web" / "_site"
    config = json.loads((ROOT / "vercel.json").read_text())
    web = config["services"]["web"]
    assert (web["root"], web["outputDirectory"]) == ("web", "_site")
    assert {p.name for p in (out / "static").iterdir()} == {"app.css", "app.js"}

    # The page, plus the mini app embed and link previews in <head>.
    html = (out / "index.html").read_text()
    assert html.replace("\n", "").count("</head>") == 1
    embeds = {}
    for name in ("fc:miniapp", "fc:frame"):
        start = html.index(f'<meta name="{name}" content=\'') + len(f'<meta name="{name}" content=\'')
        embeds[name] = json.loads(html[start:html.index("'>", start)])
    embed = embeds["fc:miniapp"]
    assert embed["version"] == "1" and len(embed["button"]["title"]) <= 32
    assert embed["button"]["action"]["type"] == "launch_miniapp"
    assert embeds["fc:frame"]["button"]["action"]["type"] == "launch_frame"
    assert embed["imageUrl"] == "https://miniapp.phunk.social/img/embed.png"
    assert embed["button"]["action"]["url"] == "https://miniapp.phunk.social/"  # the mini app's own domain

    # Analytics on the hosted page only, allowed by its CSP; never in `phunk ui`.
    assert '<script defer src="https://assets.onedollarstats.com/stonks.js"></script>' in html
    assert "onedollarstats" not in (ROOT / "src/phunk/static/index.html").read_text()
    csp = config["services"]["web"]["headers"][0]["headers"][0]["value"]
    assert "https://assets.onedollarstats.com" in csp and "https://collector.onedollarstats.com" in csp

    # The manifest: filled from the environment, every image there and the right size.
    manifest = json.loads((out / ".well-known" / "farcaster.json").read_text())
    assert manifest["accountAssociation"] == {"header": env["FARCASTER_HEADER"],
                                              "payload": env["FARCASTER_PAYLOAD"],
                                              "signature": env["FARCASTER_SIGNATURE"]}
    app = manifest["miniapp"]
    assert app["version"] == "1" and len(app["name"]) <= 32 and app["homeUrl"] == "https://miniapp.phunk.social/"
    assert len(app["subtitle"]) <= 30 and len(app["description"]) <= 170 and len(app["tagline"]) <= 30
    assert len(app["ogDescription"]) <= 100 and app["canonicalDomain"] == "miniapp.phunk.social"
    assert all(t == t.lower() and " " not in t and len(t) <= 20 for t in app["tags"]) and len(app["tags"]) <= 5
    sizes = {"icon.png": (1024, 1024), "splash.png": (200, 200), "embed.png": (1200, 800), "og.png": (1200, 630)}
    for name, size in sizes.items():
        assert _png_info(out / "img" / name) == (*size, 2), name  # RGB: icons may not have alpha
    for url in [app["iconUrl"], app["splashImageUrl"], app["heroImageUrl"], app["imageUrl"]]:
        assert (out / url.removeprefix("https://miniapp.phunk.social/")).is_file(), url
