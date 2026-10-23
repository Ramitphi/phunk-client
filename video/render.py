"""Render the launch video: video/_build/phunk-launch.mp4 (1080 x 1080, 60 fps).

    python video/capture.py            # once: the client's page, fonts and pictures
    python video/render.py             # the video, with the soundtrack from sound.py
    python video/render.py --stills 2.5,9.5,16.4   # a few frames as PNG, to check
    python video/render.py --sound     # only the soundtrack

Each frame is launch.html at time t, drawn by Chromium at twice the size and
scaled down, so edges and small text stay sharp. Workers draw slices of the
video side by side; ffmpeg joins them and adds the sound.

Needs Playwright with a Chromium, ffmpeg, and numpy for the sound (dev only).
Set CHROMIUM to a browser binary if Playwright can't find one.
"""
import argparse
import asyncio
import base64
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
BUILD = HERE / "_build"
SIZE = 1080


def serve() -> int:
    """Serve video/ on a free local port (the page loads _build/app.html in a frame)."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(HERE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


async def open_page(p, port: int, scale: float):
    browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None,
                                      args=["--force-color-profile=srgb", "--hide-scrollbars"])
    page = await browser.new_page(viewport={"width": SIZE, "height": SIZE}, device_scale_factor=scale,
                                  color_scheme="light")
    await page.goto(f"http://127.0.0.1:{port}/launch.html?render")
    await page.wait_for_function("window.READY === true", timeout=120_000)
    cdp = await page.context.new_cdp_session(page)
    return browser, page, cdp


async def shot(page, cdp, t: float) -> bytes:
    await page.evaluate(f"seek({t:.6f})")
    r = await cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
    return base64.b64decode(r["data"])


async def stills(times, scale: float) -> None:
    port = serve()
    out = BUILD / "stills"
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser, page, cdp = await open_page(p, port, scale)
        for t in times:
            await page.evaluate("seek(0)")  # every frame must stand on its own
            path = out / f"t{t:06.2f}.png"
            path.write_bytes(await shot(page, cdp, t))
            print(path)
        await browser.close()


async def cues(port: int) -> dict:
    async with async_playwright() as p:
        browser, page, _ = await open_page(p, port, 1)
        c = await page.evaluate("window.CUES")
        await browser.close()
    return c


async def worker(p, port, scale, fps, frames, part: Path, done: list, i: int) -> None:
    browser, page, cdp = await open_page(p, port, scale)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
         "-vf", f"scale={SIZE}:{SIZE}:flags=lanczos+accurate_rnd+full_chroma_int",
         "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", str(part)],
        stdin=subprocess.PIPE)
    for n in frames:
        enc.stdin.write(await shot(page, cdp, n / fps))
        done[i] += 1
    enc.stdin.close()
    if enc.wait():
        raise SystemExit(f"ffmpeg failed on {part}")
    await browser.close()


async def soundtrack(port: int) -> dict:
    """Save the page's sound cues and make the soundtrack from them."""
    BUILD.mkdir(exist_ok=True)
    c = await cues(port)
    (BUILD / "cues.json").write_text(json.dumps(c, indent=1))
    subprocess.run([sys.executable, str(HERE / "sound.py")], check=True)
    return c


async def video(args) -> None:
    port = serve()
    wav = BUILD / "soundtrack.wav"
    if args.silent:
        c = await cues(port)
    else:
        c = await soundtrack(port)

    total = round(c["duration"] * args.fps)
    first, last = round(args.start * args.fps), min(total, round((args.end or c["duration"]) * args.fps))
    frames = list(range(first, last))
    n = max(1, args.workers)
    chunk = -(-len(frames) // n)
    parts = [BUILD / f"part{i}.mkv" for i in range(n)]
    done = [0] * n
    async with async_playwright() as p:
        jobs = [worker(p, port, args.scale, args.fps, frames[i * chunk:(i + 1) * chunk], parts[i], done, i)
                for i in range(n)]

        async def progress():
            while sum(done) < len(frames):
                print(f"\r{sum(done)}/{len(frames)} frames", end="", flush=True)
                await asyncio.sleep(2)
        task = asyncio.create_task(progress())
        await asyncio.gather(*jobs)
        task.cancel()
    print(f"\r{len(frames)}/{len(frames)} frames")

    listing = BUILD / "parts.txt"
    listing.write_text("".join(f"file '{q.name}'\n" for q in parts if q.exists()))
    out = Path(args.out) if args.out else BUILD / "phunk-launch.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    sound = wav.exists() and not args.silent
    if sound:
        cmd += ["-ss", f"{first / args.fps:.3f}", "-t", f"{len(frames) / args.fps:.3f}", "-i", str(wav)]
    cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf), "-tune", "animation",
            "-profile:v", "high", "-level", "4.2", "-g", str(args.fps * 2), "-bf", "2",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-movflags", "+faststart"]
    if sound:
        cmd += ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-shortest"]
    cmd += [str(out)]
    subprocess.run(cmd, check=True)
    for q in parts + [listing]:
        q.unlink(missing_ok=True)
    print(out)
    await poster(port, c["duration"] - 1 / args.fps, out.with_name(out.stem + "-poster.png"))


async def poster(port: int, t: float, out: Path) -> None:
    """The last frame as a PNG, for players that take a thumbnail."""
    async with async_playwright() as p:
        browser, page, cdp = await open_page(p, port, 2)
        png = await shot(page, cdp, t)
        await browser.close()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "png_pipe", "-i", "-", "-vf",
                    f"scale={SIZE}:{SIZE}:flags=lanczos+accurate_rnd+full_chroma_int", str(out)], input=png, check=True)
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stills", help="comma separated times in seconds: write PNGs instead of the video")
    ap.add_argument("--scale", type=float, help="drawing scale before scaling down (default 2; 1 for stills)")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--crf", type=int, default=14, help="x264 quality, lower is better (default 14)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--start", type=float, default=0, help="first second to render")
    ap.add_argument("--end", type=float, default=None, help="last second to render")
    ap.add_argument("--silent", action="store_true", help="no soundtrack")
    ap.add_argument("--sound", action="store_true", help="make only the soundtrack")
    ap.add_argument("--out", help="output path (default video/_build/phunk-launch.mp4)")
    args = ap.parse_args()
    if args.sound:
        asyncio.run(soundtrack(serve()))
    elif args.stills:
        asyncio.run(stills([float(x) for x in args.stills.split(",")], args.scale or 1))
    else:
        args.scale = args.scale or 2
        asyncio.run(video(args))


if __name__ == "__main__":
    main()
