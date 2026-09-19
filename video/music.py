"""A small original music bed, synthesized: pulsing arpeggio, sub, soft kick, keystroke ticks. python music.py <seconds> <out.wav> [cue seconds...]"""
import sys, wave
import numpy as np

SR = 44100
dur = float(sys.argv[1]); out = sys.argv[2]; cues = [float(x) for x in sys.argv[3:]]
bpm = 118.0; beat = 60 / bpm; n = int(dur * SR); t = np.arange(n) / SR
rng = np.random.default_rng(7)
mix = np.zeros(n)
prog = [[45, 52, 57, 60, 64], [41, 48, 53, 57, 60], [48, 55, 60, 64, 67], [43, 50, 55, 59, 62]]  # Am F C G
hz = lambda m: 440 * 2 ** ((m - 69) / 12)

def env(length, a=0.005, d=0.25):
    x = np.arange(length) / SR
    return np.minimum(x / a, 1) * np.exp(-x / d)

bar = beat * 4
energy = np.clip(t / (dur * 0.45), 0.25, 1.0)  # builds over the first half
for cue in cues:
    energy *= 1 - 0.55 * np.exp(-((t - cue) ** 2) / 0.6)  # breathe at scene changes
# pad
for b in range(int(dur / bar) + 1):
    ch = prog[b % 4]; s = int(b * bar * SR); e = min(n, int((b + 1) * bar * SR))
    if s >= n: break
    tt = t[s:e] - t[s]; seg = np.zeros(e - s)
    for m in ch[:4]:
        f = hz(m + 12); seg += np.sin(2 * np.pi * f * tt) * 0.5 + np.sin(2 * np.pi * f * 1.004 * tt) * 0.4 + np.sin(2 * np.pi * f * 2 * tt) * 0.08
    fade = np.minimum(1, np.minimum(tt / 0.4, (tt[-1] - tt) / 0.4))
    mix[s:e] += seg * fade * 0.035
# arpeggio, 16ths
step = beat / 4
for i in range(int(dur / step)):
    ch = prog[int(i * step / bar) % 4]; m = ch[[0, 2, 1, 3, 2, 4, 3, 1][i % 8]] + 12
    s = int(i * step * SR); L = min(int(0.32 * SR), n - s)
    if L <= 0: break
    tt = np.arange(L) / SR; f = hz(m)
    tone = np.sign(np.sin(2 * np.pi * f * tt)) * 0.25 + np.sin(2 * np.pi * f * tt) * 0.75
    lp = np.convolve(tone, np.ones(9) / 9, mode="same")
    mix[s:s + L] += lp * env(L, 0.003, 0.11) * 0.10 * (1.0 if i % 4 == 0 else 0.7)
# sub bass on the root, eighths
for i in range(int(dur / (beat / 2))):
    ch = prog[int(i * beat / 2 / bar) % 4]; s = int(i * beat / 2 * SR); L = min(int(0.26 * SR), n - s)
    if L <= 0: break
    tt = np.arange(L) / SR
    mix[s:s + L] += np.sin(2 * np.pi * hz(ch[0] - 12) * tt) * env(L, 0.004, 0.16) * 0.22
# kick on every beat from bar 4, ticks on off-16ths
for i in range(int(dur / beat)):
    s = int(i * beat * SR); L = min(int(0.3 * SR), n - s)
    if L <= 0: break
    tt = np.arange(L) / SR
    if i >= 16:
        mix[s:s + L] += np.sin(2 * np.pi * (48 + 90 * np.exp(-tt / 0.03)) * tt) * np.exp(-tt / 0.11) * 0.42
for i in range(int(dur / step)):
    if i % 2 == 1 and i * step > bar * 2:
        s = int(i * step * SR); L = min(int(0.03 * SR), n - s)
        if L > 0: mix[s:s + L] += rng.standard_normal(L) * np.exp(-np.arange(L) / (0.004 * SR)) * 0.07
# risers into cues
for cue in cues:
    s = int(max(0, cue - 1.6) * SR); e = min(n, int(cue * SR))
    if e > s:
        x = np.linspace(0, 1, e - s); mix[s:e] += rng.standard_normal(e - s) * x ** 3 * 0.05
    L = min(int(1.2 * SR), n - e)
    if L > 0:
        tt = np.arange(L) / SR; mix[e:e + L] += np.sin(2 * np.pi * 55 * tt) * np.exp(-tt / 0.35) * 0.35
mix *= energy
mix *= np.minimum(1, np.minimum(t / 0.8, (dur - t) / 2.5))
mix = np.tanh(mix * 1.6) * 0.8
stereo = np.stack([mix, np.roll(mix, 220)], axis=1)
with wave.open(out, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((stereo * 32767).astype(np.int16).tobytes())
print(out, round(dur, 2))
