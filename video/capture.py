"""Snapshot the running web client for the launch video, into video/_build/.

    PHUNK_TIP_FID=3 phunk ui --public     # in another terminal; or set PHUNK_UI
    python video/capture.py

Loads the page as dwr (a public account) with follow and like on, fills the
People, Casts and Tip screens, then saves the page as static HTML with its
pictures and fonts next to it. launch.html shows that page inside the phone,
so the phone's text is real text and stays sharp at any size. Nothing is sent.

Needs Playwright with a Chromium (dev only; not a dependency).
Set CHROMIUM to a browser binary if Playwright can't find one.
"""
import asyncio
import hashlib
import os
import re
import shutil
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
OUT = HERE / "_build"
UI = os.environ.get("PHUNK_UI", "http://127.0.0.1:8008").rstrip("/")
STATIC = HERE.parent / "src" / "phunk" / "static"
WHO = "dwr"
W, H = 390, 844
AVATAR = 96  # px: a person card's 48 px avatar at the video's 2x, drawn 1:1
HOURS, CASTS = 72, 60  # casts from the last three days, two pages

# The fonts the video and the phone use: Bricolage Grotesque (the app's
# display face), Inter (body, and the φ of the logo) and JetBrains Mono.
FONTS = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wdth,wght@12..96,75..100,200..800"
         "&family=Inter:opsz,wght@14..32,400..800&family=JetBrains+Mono:wght@400..800&display=block")
SUBSETS = ("latin", "latin-ext", "greek")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def fetch_fonts() -> None:
    """Download the fonts once and write fonts/fonts.css pointing at them."""
    out = OUT / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "fonts.css").exists():
        return
    req = urllib.request.Request(FONTS, headers={"User-Agent": UA})
    css = urllib.request.urlopen(req).read().decode()
    faces = []
    for subset, block in re.findall(r"/\* ([\w-]+) \*/\s*(@font-face \{.*?\})", css, re.S):
        if subset not in SUBSETS:
            continue
        url = re.search(r"url\((https://[^)]+)\)", block).group(1)
        name = hashlib.sha1(url.encode()).hexdigest()[:12] + ".woff2"
        urllib.request.urlretrieve(url, out / name)
        faces.append(block.replace(url, name))
    (out / "fonts.css").write_text("\n".join(faces) + "\n")


ME = {"fid": 3, "can_write": True, "can_cast": False, "public": False, "help": "",
      "user": {"fid": 3, "username": WHO, "display_name": "Dan Romero", "pfp": None, "bio": None}}


async def wallet(route):
    """Serve app.js as if a Farcaster client with a wallet were the host."""
    resp = await route.fetch()
    js = (await resp.text()).replace("if (!miniapp) return false;", "return true;")
    await route.fulfill(response=resp, body=js)


async def main() -> None:
    fetch_fonts()
    pics = OUT / "pics"
    pics.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        page = await browser.new_page(viewport={"width": W, "height": H}, color_scheme="light")
        me = dict(ME, tip=(await (await page.request.get(f"{UI}/api/me")).json()).get("tip"))
        await page.route("**/static/app.js", wallet)
        await page.route("**/api/me", lambda r: r.fulfill(json=me))
        # Two pages of casts, so the video can pick short ones from three apps.
        await page.route("**/api/feed?*", lambda r: r.continue_(url=r.request.url.replace("limit=30", f"limit={CASTS}")))
        await page.goto(f"{UI}/?as={WHO}")
        await page.wait_for_selector(".person", timeout=300_000)
        await page.wait_for_load_state("networkidle")
        await page.click('.tabs button[data-tab="casts"]')
        await page.wait_for_selector(".cast", timeout=300_000)
        await page.click(f'#hours label:has(input[value="{HOURS}"])')
        await page.locator("#apps input").nth(2).wait_for(state="attached", timeout=300_000)
        await page.wait_for_load_state("networkidle")
        await page.click('.tabs button[data-tab="tip"]')
        await page.wait_for_timeout(800)
        await page.click('.tabs button[data-tab="people"]')
        await page.wait_for_timeout(800)

        # Pictures: save each once, point the page at the copy.
        srcs = await page.evaluate("[...new Set([...document.images].map(i => i.currentSrc || i.src))]")
        local = {}
        for src in srcs:
            if not src.startswith("http") or src.startswith(UI):
                continue
            name = hashlib.sha1(src.encode()).hexdigest()[:16]
            try:
                resp = await page.request.get(src, timeout=30_000)
                kind = resp.headers.get("content-type", "image/jpeg").split(";")[0].split("/")[-1]
                path = pics / f"{name}.{kind.replace('svg+xml', 'svg')}"
                path.write_bytes(await resp.body())
                local[src] = f"pics/{path.name}"
            except Exception as e:  # a missing picture leaves the initials circle
                print("skip", src, e)
        avatars = await page.evaluate("[...new Set([...document.querySelectorAll('img.avatar')].map(i => i.currentSrc || i.src))]")
        html = await page.evaluate("""(local) => {
          for (const img of document.images) {
            const src = img.currentSrc || img.src;
            if (local[src]) { img.src = local[src]; img.removeAttribute('srcset'); img.loading = 'eager'; }
          }
          for (const i of document.querySelectorAll('input')) i.toggleAttribute('checked', i.checked);
          document.querySelectorAll('script, .toast, .fx-layer').forEach(e => e.remove());
          return '<!doctype html>\\n' + document.documentElement.outerHTML;
        }""", local)
        await browser.close()

    html = html.replace('href="/static/app.css"', 'href="app.css"')
    html = html.replace("</head>", '<link rel="stylesheet" href="fonts/fonts.css">'
                        "<style>body{font-family:Inter,system-ui,sans-serif}"
                        "*,*::before,*::after{animation:none!important;transition:none!important}</style></head>")
    (OUT / "app.html").write_text(html)
    shrink([OUT / local[a] for a in avatars if a in local])
    shutil.copy(STATIC / "app.css", OUT / "app.css")
    print(OUT / "app.html", len(local), "pictures")


def shrink(paths) -> None:
    """Avatars at exactly the size the video draws them, so they stay sharp and steady."""
    for path in paths:
        try:
            im = Image.open(path)
            im.seek(0)
            im = im.convert("RGBA")
        except Exception:
            continue
        side = min(im.size)
        box = ((im.width - side) // 2, (im.height - side) // 2, (im.width + side) // 2, (im.height + side) // 2)
        out = path.with_suffix(".png")
        im.crop(box).resize((AVATAR, AVATAR), Image.LANCZOS).save(out)
        if out != path:
            path.unlink()
            html = (OUT / "app.html").read_text().replace(f"pics/{path.name}", f"pics/{out.name}")
            (OUT / "app.html").write_text(html)


if __name__ == "__main__":
    asyncio.run(main())
