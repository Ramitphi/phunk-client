"""Make the launch video's soundtrack: video/_build/soundtrack.wav.

    python video/sound.py      # render.py runs it for you

A short house loop at 120 BPM in F minor (Fm7, Dbmaj7, Abmaj7, Eb), drawn
from scratch, with a sound for each thing that happens on screen: pops,
taps, chimes, cash, whooshes and hits. The times come from launch.html
(window.CUES, saved to _build/cues.json), so sound and picture stay together.
Loudness is set to -14 LUFS with peaks near -1 dBTP, as social video players expect.

Needs numpy, scipy and ffmpeg (dev only).
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import butter, fftconvolve, sosfilt

HERE = Path(__file__).resolve().parent
BUILD = HERE / "_build"
SR = 48000
BEAT = 0.5
BAR = 2.0

# Bar by bar: chord voicing (MIDI) and bass root.
CHORDS = [[53, 56, 60, 63], [53, 56, 60, 61], [56, 60, 63, 67], [55, 58, 62, 63]]
ROOTS = [41, 37, 44, 39]
PENTA = [77, 80, 82, 84, 87, 89]  # F minor pentatonic, high: for the pops

rand = np.random.default_rng(7)


def mtof(m):
    return 440.0 * 2 ** ((np.asarray(m, float) - 69) / 12)


def n_(sec):
    return int(round(sec * SR))


def tt(n):
    return np.arange(n) / SR


def noise(n):
    return rand.standard_normal(n)


def filt(x, kind, f, order=2):
    return sosfilt(butter(order, f, kind, fs=SR, output="sos"), x)


def sweep(x, f0, f1, kind="bandpass", q=1.2, block=256):
    """A filter whose centre moves from f0 to f1 (log), in small blocks."""
    out = np.zeros_like(x)
    nb = -(-len(x) // block)
    zi = None
    for i in range(nb):
        f = f0 * (f1 / f0) ** (i / max(1, nb - 1))
        if kind == "bandpass":
            band = [max(20, f / (1 + 1 / (2 * q))), min(SR / 2 - 100, f * (1 + 1 / (2 * q)))]
            sos = butter(1, band, "bandpass", fs=SR, output="sos")
        else:
            sos = butter(2, min(f, SR / 2 - 100), kind, fs=SR, output="sos")
        if zi is None:
            zi = np.zeros((sos.shape[0], 2))
        out[i * block:(i + 1) * block], zi = sosfilt(sos, x[i * block:(i + 1) * block], zi=zi)
    return out


def saw(f, n, phase=0.0):
    """A band-limited sawtooth (PolyBLEP)."""
    dt = np.broadcast_to(np.asarray(f, float) / SR, (n,))
    ph = (phase + np.cumsum(dt)) % 1.0
    y = 2 * ph - 1
    m = ph < dt
    x = ph[m] / dt[m]
    y[m] -= x + x - x * x - 1
    m = ph > 1 - dt
    x = (ph[m] - 1) / dt[m]
    y[m] -= x * x + x + x + 1
    return y


def sine(f, n, phase=0.0):
    return np.sin(phase + 2 * np.pi * np.cumsum(np.broadcast_to(np.asarray(f, float), (n,))) / SR)


def env(n, a=0.002, d=0.2, hold=0.0):
    t = tt(n)
    e = np.minimum(1, t / max(a, 1e-4)) * np.where(t < a + hold, 1, np.exp(-(t - a - hold) / d))
    return e


class Mix:
    def __init__(self, seconds):
        self.n = n_(seconds)
        self.bus = {k: np.zeros((2, self.n)) for k in ("drums", "music", "sfx", "verb")}

    def add(self, bus, x, at, gain=1.0, pan=0.0, verb=0.0):
        """Put x (mono, or 2 x n) at time at; pan -1..1 with equal power."""
        x = np.asarray(x, float)
        if x.ndim == 1:
            a = (pan + 1) * np.pi / 4
            x = np.vstack([x * np.cos(a), x * np.sin(a)]) * np.sqrt(2)
        i = n_(at)
        if i < 0:
            x, i = x[:, -i:], 0
        m = min(x.shape[1], self.n - i)
        if m <= 0:
            return
        self.bus[bus][:, i:i + m] += x[:, :m] * gain
        if verb:
            self.bus["verb"][:, i:i + m] += x[:, :m] * gain * verb


# ------------------------------------------------------------------ drums
def kick():
    n = n_(0.5)
    t = tt(n)
    f = 46 + 150 * np.exp(-t / 0.028)
    body = sine(f, n) * np.exp(-t / 0.2) * np.minimum(1, t / 0.001)
    click = filt(noise(n), "highpass", 3000) * np.exp(-t / 0.003) * 0.3
    return np.tanh(1.8 * (body + click)) / np.tanh(1.8)


def clap():
    n = n_(0.4)
    t = tt(n)
    e = sum(np.exp(-np.maximum(0, t - k * 0.009) / 0.005) * (t >= k * 0.009) for k in range(3))
    e = e + 0.9 * np.exp(-np.maximum(0, t - 0.027) / 0.12) * (t >= 0.027)
    return filt(noise(n), "bandpass", [1000, 3400]) * e * 0.9


def hat(decay):
    n = n_(decay * 6)
    t = tt(n)
    metal = sum(np.sign(sine(f, n)) for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)) / 6
    x = filt(0.6 * metal + 0.5 * noise(n), "highpass", 7000, 4)
    return x * np.exp(-t / decay)


# ------------------------------------------------------------------ music
def bass_note(midi, dur):
    n = n_(dur + 0.06)
    t = tt(n)
    f = mtof(midi)
    x = 0.55 * filt(saw(f, n), "lowpass", 420) + 0.75 * sine(f, n)
    e = np.minimum(1, t / 0.004) * np.clip((dur + 0.05 - t) / 0.05, 0, 1)
    return np.tanh(1.4 * x * e)


def supersaw(notes, n, spread=0.12, voices=4):
    left, right = np.zeros(n), np.zeros(n)
    for m in notes:
        for v in range(voices):
            d = spread * (v / (voices - 1) - 0.5) * 2
            x = saw(mtof(m + d), n, phase=rand.random())
            (left if v % 2 else right)[:] += x
    k = 1 / (len(notes) * voices) * 2.2
    return left * k, right * k


def stab(chord, length=0.42, bright=1.0):
    n = n_(length + 0.1)
    t = tt(n)
    left, right = supersaw(chord, n)
    out = []
    for ch in (left, right):
        hi = filt(ch, "lowpass", 5200 * bright) * np.exp(-t / 0.05)
        lo = filt(ch, "lowpass", 1300) * np.exp(-t / 0.2)
        out.append((0.7 * hi + lo) * np.minimum(1, t / 0.003) * np.clip((length + 0.1 - t) / 0.1, 0, 1))
    return np.vstack(out)


def pad(chord, dur, cutoff=1000, attack=0.35):
    n = n_(dur + 0.6)
    t = tt(n)
    left, right = supersaw(chord, n, spread=0.16)
    e = np.minimum(1, t / attack) * np.clip((dur + 0.6 - t) / 0.6, 0, 1)
    return np.vstack([filt(left, "lowpass", cutoff), filt(right, "lowpass", cutoff)]) * e


# ------------------------------------------------------------------ sfx
def pop(midi):
    n = n_(0.16)
    t = tt(n)
    f = mtof(midi) * (1 + 1.2 * np.exp(-t / 0.01))
    return sine(f, n) * np.exp(-t / 0.05) * np.minimum(1, t / 0.0008) + filt(noise(n), "highpass", 4000) * np.exp(-t / 0.002) * 0.15


def tick():
    n = n_(0.08)
    t = tt(n)
    return (sine(2350, n) + 0.5 * sine(3520, n)) * np.exp(-t / 0.014) * 0.6


def tap():
    n = n_(0.1)
    t = tt(n)
    return (filt(noise(n), "bandpass", [1800, 6500]) * np.exp(-t / 0.0022) * 0.9
            + sine(190 * (1 + 0.5 * np.exp(-t / 0.008)), n) * np.exp(-t / 0.022) * 0.6)


def bell(f, dur=0.6):
    n = n_(dur * 1.2)
    t = tt(n)
    parts = [(1, 1.0, 1.0), (2.0, 0.35, 0.55), (2.76, 0.22, 0.35), (5.4, 0.08, 0.12)]
    return sum(a * sine(f * r, n) * np.exp(-t / (dur * d)) for r, a, d in parts) * np.minimum(1, t / 0.001)


def whoosh(dur=0.42):
    n = n_(dur)
    x = np.linspace(0, 1, n)
    e = np.sin(np.pi * x) ** 1.6
    s = sweep(noise(n), 350, 3800, q=1.4) * e
    pan = np.linspace(-0.7, 0.7, n)
    a = (pan + 1) * np.pi / 4
    return np.vstack([s * np.cos(a), s * np.sin(a)]) * np.sqrt(2)


def impact():
    n = n_(2.2)
    t = tt(n)
    boom = sine(38 + 34 * np.exp(-t / 0.12), n) * np.exp(-t / 0.55)
    hit = filt(noise(n), "lowpass", 900) * np.exp(-t / 0.09) * 0.6
    crash = filt(noise(n), "highpass", 5200, 2) * np.exp(-t / 0.75) * 0.22 * np.minimum(1, t / 0.003)
    return np.tanh(1.3 * (boom + hit + crash))


def riser(dur):
    n = n_(dur)
    x = np.linspace(0, 1, n)
    s = sweep(noise(n), 500, 9000, q=2.0) * x ** 2.2 * 0.9
    tone = sine(220 * 4 ** x, n) * x ** 3 * 0.18
    return (s + tone) * np.clip((1 - x) * n / n_(0.006), 0, 1)


def thud():
    n = n_(0.25)
    t = tt(n)
    return sine(70 + 60 * np.exp(-t / 0.02), n) * np.exp(-t / 0.08) + filt(noise(n), "lowpass", 500) * np.exp(-t / 0.02) * 0.3


# ------------------------------------------------------------------ the track
def section(c, t):
    for a, b, k in c["music"]:
        if a <= t < b:
            return k
    return 0


def build(c) -> np.ndarray:
    dur = c["duration"]
    mix = Mix(dur + 0.01)
    beats = np.arange(0, dur, BEAT)
    K, CL = kick(), clap()
    HC, HO = hat(0.022), hat(0.075)

    # Drums, by how busy each section is
    kicks = []
    for b in beats:
        s = section(c, b)
        if s >= 1:
            kicks.append(b)
            mix.add("drums", K, b, 0.72)
            if s >= 2:
                mix.add("drums", HO, b + BEAT / 2, 0.16, pan=0.15)
            else:
                mix.add("drums", HC, b + BEAT / 2, 0.07, pan=0.15)
            if s >= 3:
                if round(b / BEAT) % 2 == 1:
                    mix.add("drums", CL, b, 0.36, pan=-0.05, verb=0.25)
                for k, v in ((0.125, 0.05), (0.375, 0.06)):
                    mix.add("drums", HC, b + k, v, pan=-0.25)
    for t0 in c["stab"]:
        kicks.append(t0)
        mix.add("drums", K, t0, 0.8)
    kicks.sort()

    # Bass on the off-beats, a stab on the ands of 2 and 4, a pad underneath
    for b in beats:
        s = section(c, b)
        bar = int(b // BAR) % 4
        if s >= 1:
            dark = s == 1
            note = bass_note(ROOTS[bar], 0.19)
            mix.add("music", filt(note, "lowpass", 260) if dark else note, b + BEAT / 2, 0.5)
        if s >= 2 and round((b % BAR) / BEAT) in (1, 3):
            mix.add("music", stab(CHORDS[bar], bright=1.0 if s >= 3 else 0.7), b + BEAT / 2, 0.24, verb=0.3)
    for b0 in np.arange(0, dur, BAR):
        s = section(c, b0)
        bar = int(b0 // BAR) % 4
        if s == -1:
            continue
        cut = {0: 900, 1: 650, 2: 1100, 3: 1300}[s]
        gain = {0: 0.2, 1: 0.13, 2: 0.1, 3: 0.09}[s]
        attack = 1.4 if b0 == 0 else 0.25
        if b0 + BAR >= dur:  # the last chord rings out to the end
            p = pad([41 + 12] + CHORDS[0], dur - b0 - 0.6, cutoff=1500, attack=0.3)
            mix.add("music", p, b0, 0.22, verb=0.5)
        else:
            mix.add("music", pad(CHORDS[bar], BAR, cut, attack), b0, gain, verb=0.25)
    for i, t0 in enumerate(c["stab"]):  # one chord per word in the node section
        mix.add("music", stab(CHORDS[i % 4], length=0.35), t0, 0.36, verb=0.35)

    # Duck the music under every kick
    t = tt(mix.n)
    duck = np.ones(mix.n)
    for k in kicks:
        i = n_(k)
        seg = t[i:i + n_(0.4)] - k
        duck[i:i + len(seg)] = np.minimum(duck[i:i + len(seg)], 1 - 0.55 * np.exp(-seg / 0.11))
    duck = filt(duck, "lowpass", 60, 1)
    mix.bus["music"] *= duck

    # What happens on screen
    for i, t0 in enumerate(c["pop"]):
        mix.add("sfx", pop(PENTA[i % len(PENTA)]), t0, 0.22, pan=(i % 5 - 2) * 0.18, verb=0.12)
    for t0 in c["tick"]:
        mix.add("sfx", tick(), t0, 0.25, pan=0.2)
    for t0 in c["tap"]:
        mix.add("sfx", tap(), t0, 0.5, pan=-0.2)
    for t0 in c["success"]:
        mix.add("sfx", bell(mtof(84)), t0 + 0.02, 0.12, pan=0.25, verb=0.35)
        mix.add("sfx", bell(mtof(89)), t0 + 0.09, 0.12, pan=0.3, verb=0.35)
    for t0 in c["sparkle"]:
        for j, m in enumerate([89, 92, 96, 99, 101]):
            mix.add("sfx", bell(mtof(m), 0.35), t0 + j * 0.035, 0.05, pan=(j - 2) * 0.3, verb=0.6)
    for t0 in c["cash"]:
        n = n_(0.2)
        cha = filt(noise(n), "bandpass", [2500, 7500]) * (np.exp(-tt(n) / 0.025) + 0.7 * np.exp(-np.maximum(0, tt(n) - 0.05) / 0.025) * (tt(n) > 0.05))
        mix.add("sfx", cha, t0, 0.22, pan=0.2)
        mix.add("sfx", bell(2637, 0.7) + bell(3136, 0.7), t0 + 0.09, 0.07, pan=0.3, verb=0.4)
        for j in range(7):  # coins
            mix.add("sfx", bell(3000 + 3000 * rand.random(), 0.12), t0 + 0.12 + rand.random() * 0.45, 0.03,
                    pan=rand.uniform(-0.8, 0.8), verb=0.3)
    for t0 in c["whoosh"]:
        mix.add("sfx", whoosh(), t0 - 0.2, 0.32, verb=0.2)
    for t0 in c["impact"]:
        mix.add("sfx", impact(), t0, 0.5, verb=0.35)
    for a, b in c["riser"]:
        mix.add("sfx", riser(b - a), a, 0.3, verb=0.2)
    for t0 in c["words"]:
        mix.add("sfx", thud(), t0, 0.35)

    # Reverb: a short room, then everything together
    nI = n_(1.6)
    tI = tt(nI)
    ir = np.vstack([filt(noise(nI), "lowpass", 6500) * np.exp(-tI / 0.32) for _ in range(2)])
    ir[:, :n_(0.012)] = 0
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True))
    verb = np.vstack([fftconvolve(mix.bus["verb"][i], ir[i])[:mix.n] for i in range(2)]) * 0.5
    drums = np.tanh(mix.bus["drums"] * 1.15) / 1.15  # a little glue on the drums
    out = drums + mix.bus["music"] * 1.25 + mix.bus["sfx"] * 0.9 + verb
    out = np.vstack([filt(ch, "highpass", 28) for ch in out])
    out = out * (0.6 / np.percentile(np.abs(out), 99.9))  # then only the loudest hits touch the curve
    out = np.tanh(out)
    out = out[:, :n_(dur)]
    fade = np.clip((dur - tt(out.shape[1])) / 0.35, 0, 1)
    return out * fade


def limit(x, ceiling_db=-1.8, look=0.004, release=0.09):
    """A look-ahead peak limiter, both channels together."""
    c = 10 ** (ceiling_db / 20)
    g = np.minimum(1.0, c / np.maximum(np.abs(x).max(axis=0), 1e-9))
    L = max(1, n_(look))
    g = minimum_filter1d(g, 2 * L + 1)
    # let go slowly
    a = np.exp(-1 / (release * SR))
    r = 1 - g
    held = np.empty_like(r)
    h = 0.0
    for i, v in enumerate(r):
        h = v if v > h else h * a
        held[i] = h
    g = uniform_filter1d(1 - held, L)
    return x * g, 1 - g.min()


def lufs(x) -> float:
    """Integrated loudness, as ffmpeg's ebur128 measures it."""
    tmp = BUILD / "measure.wav"
    wavfile.write(tmp, SR, x.T.astype(np.float32))
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(tmp), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, check=True)
    tmp.unlink()
    tail = r.stderr[r.stderr.rindex("Integrated loudness"):]
    return float(tail.split("I:")[1].split("LUFS")[0])


def master(x, target=-14.0):
    """Gain to the target loudness, peaks held under -1.8 dBFS (about -1 dBTP)."""
    x = x / np.abs(x).max() * 0.5
    gain = 10 ** ((target - lufs(x)) / 20)
    for _ in range(3):
        y, gr = limit(x * gain)
        miss = target - lufs(y)
        if abs(miss) < 0.1:
            break
        gain *= 10 ** (miss / 20)
    print(f"limiter: up to {20 * np.log10(1 / max(1e-9, 1 - gr)):.1f} dB on the loudest hits")
    return y


def main() -> None:
    path = BUILD / "cues.json"
    if not path.exists():
        sys.exit("No _build/cues.json: run render.py, which saves it from launch.html.")
    c = json.loads(path.read_text())
    out = master(build(c))
    pcm = np.clip(np.round(out.T * (2 ** 31 - 1)), -(2 ** 31), 2 ** 31 - 1).astype(np.int32)
    wavfile.write(BUILD / "soundtrack.wav", SR, pcm)
    print(BUILD / "soundtrack.wav")


if __name__ == "__main__":
    main()
