"""Take the mini app's store screenshots into web/public/img/.

    phunk ui --public            # in another terminal; or set PHUNK_UI to a URL
    python web/make_screenshots.py

Three portrait screenshots, 1284 x 2778 as Farcaster asks: People, Casts and
Tip, as a phone shows them inside a Farcaster client. They show dwr's
people (a public account). Tip shows the wallet view, which needs a
Farcaster client, so the page is told it has one; nothing is sent.

Needs Playwright with a Chromium and Pillow (dev only; not a dependency).
Set CHROMIUM to a browser binary if Playwright can't find one.
"""
import asyncio
import io
import os
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "public" / "img"
UI = os.environ.get("PHUNK_UI", "http://127.0.0.1:8008").rstrip("/")
WHO = "dwr"
ME = {"fid": None, "can_write": False, "can_cast": False, "public": True,
      "help": "Read-only.", "tip": {"fid": 3, "username": "dwr"}}
W, H, SCALE = 428, 926, 3  # 1284 x 2778


async def show_wallet(route):
    """Serve app.js as if a Farcaster client with a wallet were the host."""
    resp = await route.fetch()
    js = (await resp.text()).replace("if (!miniapp) return false;", "return true;")
    await route.fulfill(response=resp, body=js)


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE,
                                      color_scheme="light")
        await page.route("**/static/app.js", show_wallet)
        await page.route("**/api/me", lambda r: r.fulfill(json=ME))
        await page.goto(f"{UI}/?as={WHO}")
        await page.wait_for_selector(".person", timeout=180_000)
        await page.wait_for_load_state("networkidle")
        shots = {"screen-people.png": "people", "screen-casts.png": "casts", "screen-tip.png": "tip"}
        for name, tab in shots.items():
            await page.click(f'.tabs button[data-tab="{tab}"]')
            if tab == "casts":
                await page.wait_for_selector(".cast", timeout=300_000)
            await page.wait_for_load_state("networkidle")
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(400)
            png = await page.screenshot()
            Image.open(io.BytesIO(png)).convert("RGB").save(OUT / name, optimize=True)
            print(OUT / name, W * SCALE, "x", H * SCALE)
        await browser.close()


asyncio.run(main())
