# Launch video

The 32-second launch video for phunk.social, made from code: 1080 x 1080,
60 fps, with its own soundtrack. The phone in it shows the real web client,
so its screens are the app itself, not pictures of it.

| File | What |
|---|---|
| `launch.html` | The video. Every frame is a function of time: `seek(t)` puts each element where it is at `t` |
| `capture.py` | Saves the running client as static HTML for the phone: People, Casts and Tip, as dwr, with the pictures and fonts |
| `render.py` | Draws each frame in Chromium at twice the size, scales it down, and encodes it with ffmpeg |
| `sound.py` | Makes the soundtrack: a 120 BPM loop in F minor and a sound for each thing on screen |
| `_build/` | Everything made from the above (ignored by git) |

## Make it

Dev tools only, none of them phunk dependencies: Playwright with a Chromium,
Pillow, numpy, scipy and ffmpeg.

```sh
pip install -e . playwright pillow numpy scipy
PHUNK_TIP_FID=3 phunk ui --public     # in another terminal
python video/capture.py               # once: _build/app.html, pictures, fonts
python video/render.py                # _build/phunk-launch.mp4 and -poster.png
```

`capture.py` reads the live node, so the people and casts are whatever they
are that day. `launch.html` picks people and casts by handle; if one is
missing, the phone shows the next one in the list.

Set `CHROMIUM` to a browser binary if Playwright can't find one.

## Check it

```sh
python video/render.py --stills 9.5,16.4,23.7     # PNGs in _build/stills/
python video/render.py --start 14 --end 18        # a few seconds of video
python video/render.py --sound                    # only the soundtrack
python -m http.server -d video 8123               # then open localhost:8123/launch.html
```

The page plays in real time with the soundtrack once it is built. `?t=12`
starts at 12 seconds; space pauses.

## How it fits together

- **Timing.** The beat is 0.5 s and a bar is 2 s. Scenes start on bar lines and
  things happen on beats. All times are in `T` at the top of the script.
- **Sound.** `launch.html` publishes `window.CUES` (when each pop, tap, whoosh
  and hit happens). `render.py` saves them to `_build/cues.json` and `sound.py`
  builds the track from them, so moving a time in `T` moves its sound too.
- **Sharpness.** Frames are drawn at 2160 x 2160 and scaled down with Lanczos.
  Avatars are saved at the size they are drawn (96 px), so nothing is resampled
  in the browser.
- **Same frame every time.** Each frame depends only on `t`, not on the frames
  before it, so workers can draw slices in parallel. Particles use a seeded
  random generator. Don't add CSS transitions, `will-change`, or state that
  carries over from one `seek` to the next.
- **Output.** H.264 High, CRF 14, BT.709, AAC 320 kbps, `+faststart`.
  Loudness is -14 LUFS, as social video players expect.
