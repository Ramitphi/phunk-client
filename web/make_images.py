"""Draw phunk.social's mini app images into web/public/img/.

    python web/make_images.py

Needs Playwright with a Chromium and Pillow (dev only; not a dependency).
Set CHROMIUM to a browser binary if Playwright can't find one.
The images use the web client's own colours and logo.
"""
import asyncio
import io
import os
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / "public" / "img"
BG, CARD, TEXT, MUTED, EDGE, CTA, HL = "#f5f1ea", "#fffdf9", "#141414", "#5e5a54", "#141414", "#7959ff", "#ccc0ff"
FONT = "'Inter Display', Inter, 'Helvetica Neue', Arial, 'DejaVu Sans', sans-serif"


def logo(size: int, border: int, shadow: int) -> str:
    return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;background:{CTA};color:#fff;'
            f'border:{border}px solid {EDGE};box-shadow:{shadow}px {shadow}px 0 {EDGE};display:grid;'
            f'place-items:center;font-size:{int(size * 0.72)}px;font-weight:700;transform:rotate(-12deg);line-height:1;box-sizing:border-box;'
            f'padding-bottom:{int(size * 0.1)}px">φ</div>')


def chip(text: str, bg: str, fg: str) -> str:
    return (f'<span style="background:{bg};color:{fg};border:2px solid {EDGE};border-radius:999px;'
            f'padding:8px 22px;font-weight:800;letter-spacing:.06em;font-size:26px">{text}</span>')


def card(w: int, h: int, title_px: int) -> str:
    chips = (chip("RECONNECT", "#fff4cc", "#4a3200") + chip("YOUR CIRCLE", "#ece8ff", "#5d42d6")
             + chip("BRIDGE", "#e1f1ff", "#0a64a8"))
    return f'''<div style="width:{w}px;height:{h}px;background:{BG};display:flex;flex-direction:column;
      justify-content:center;padding:0 90px;box-sizing:border-box;gap:34px;font-family:{FONT};color:{TEXT}">
      <div style="display:flex;align-items:center;gap:34px">{logo(150, 5, 8)}
        <span style="font-size:110px;font-weight:800;letter-spacing:-.02em">phunk</span></div>
      <div style="font-size:{title_px}px;font-weight:800;line-height:1.1;letter-spacing:-.01em">
        Find your <span style="background:{HL};border:3px solid {EDGE};border-radius:16px;padding:0 14px">people</span>
        on Farcaster</div>
      <div style="display:flex;gap:16px">{chips}</div>
      <div style="font-size:30px;color:{MUTED}">Who you talk to, who moved apps, who to meet next.</div>
    </div>'''


IMAGES = {
    "icon.png": (1024, 1024, f'<div style="width:1024px;height:1024px;background:{BG};display:grid;'
                              f'place-items:center;font-family:{FONT}">{logo(760, 22, 30)}</div>'),
    "splash.png": (200, 200, f'<div style="width:200px;height:200px;background:{BG};display:grid;'
                             f'place-items:center;font-family:{FONT}">{logo(140, 5, 6)}</div>'),
    "embed.png": (1200, 800, card(1200, 800, 76)),   # 3:2, the card in a cast
    "og.png": (1200, 630, card(1200, 630, 64)),      # 1.91:1, link previews and the app store
}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        for name, (w, h, html) in IMAGES.items():
            page = await browser.new_page(viewport={"width": w, "height": h})
            await page.set_content(f'<html><body style="margin:0">{html}</body></html>')
            png = await page.screenshot(clip={"x": 0, "y": 0, "width": w, "height": h})
            # Mini app icons must have no alpha channel.
            Image.open(io.BytesIO(png)).convert("RGB").save(OUT / name, optimize=True)
            print(OUT / name, w, "x", h)
        await browser.close()


asyncio.run(main())
