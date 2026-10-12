"""合成结构已知的测试曲,用于验证分析结果(不依赖任何真实歌曲)。

python demo_song.py      → 在 music/ 下生成三首测试曲
"""
import os

import numpy as np

SR = 44100
rng = np.random.default_rng(7)


def _env(n, attack, decay):
    t = np.arange(n) / SR
    e = np.exp(-t / decay)
    a = int(attack * SR)
    if a > 0:
        e[:a] *= np.linspace(0, 1, a)
    return e


def kick(gain=1.0):
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    f = 45 + 75 * np.exp(-t / 0.04)
    return gain * np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.12)


def snare(gain=0.6):
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    return gain * (0.7 * rng.standard_normal(n) + 0.5 * np.sin(2 * np.pi * 185 * t)) * _env(n, 0.001, 0.06)


def hat(gain=0.25):
    n = int(0.05 * SR)
    x = np.diff(rng.standard_normal(n + 1))
    return gain * x * _env(n, 0.0005, 0.012)


def tone(freq, dur, harmonics=1, gain=0.3, attack=0.01, decay=None):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * freq * h * t) / h for h in range(1, harmonics + 1))
    e = _env(n, attack, decay) if decay else np.minimum(1, np.linspace(0, dur / max(attack, 1e-3), n))
    if not decay:
        r = int(min(0.05, dur / 4) * SR)
        e[-r:] *= np.linspace(1, 0, r)
    return gain * x * e


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def place(buf, x, at):
    i = int(at * SR)
    j = min(len(buf), i + len(x))
    if i < len(buf):
        buf[i:j] += x[: j - i]


def edm_track(bpm=128):
    """前奏8 主歌16 铺垫8 高潮16 间奏8 铺垫8 高潮16 尾奏8 小节(4/4)。"""
    beat = 60 / bpm
    bar = 4 * beat
    plan = [("intro", 8), ("verse", 16), ("build", 8), ("drop", 16),
            ("break", 8), ("build", 8), ("drop", 16), ("outro", 8)]
    total = sum(b for _, b in plan) * bar
    buf = np.zeros(int(total * SR) + SR)
    prog = [57, 53, 48, 55]                                      # Am F C G
    chord = {57: [57, 60, 64], 53: [53, 57, 60], 48: [48, 52, 55], 55: [55, 59, 62]}
    lead = [72, 74, 76, 79, 76, 74, 72, 67]                      # 大调五声明亮旋律
    t0 = 0.0
    truth = []
    for name, bars in plan:
        truth.append((name, round(t0, 3), round(t0 + bars * bar, 3)))
        for b in range(bars):
            tb = t0 + b * bar
            root = prog[b % 4]
            prog_frac = b / bars
            pad_gain = {"intro": 0.05, "verse": 0.06, "build": 0.06, "drop": 0.08,
                        "break": 0.07, "outro": 0.05 * (1 - prog_frac)}[name]
            for m in chord[root]:
                place(buf, tone(midi(m), bar, harmonics=3, gain=pad_gain, attack=0.3), tb)
            if name in ("verse", "drop"):
                g = 1.0 if name == "drop" else 0.55
                for k in range(4):
                    place(buf, kick(g), tb + k * beat)
                for k in range(8):
                    place(buf, hat(0.25 if name == "drop" else 0.15), tb + k * beat / 2)
                for k in range(8):
                    place(buf, tone(midi(root - 24), beat / 2, harmonics=6,
                                    gain=0.22 if name == "drop" else 0.12, attack=0.005), tb + k * beat / 2)
            if name == "drop":
                for k in (1, 3):
                    place(buf, snare(0.7), tb + k * beat)
                for k in range(8):
                    place(buf, tone(midi(lead[k]), beat / 2, harmonics=5, gain=0.12, attack=0.005),
                          tb + k * beat / 2)
            if name == "build":
                steps = 4 if prog_frac < 0.25 else 8 if prog_frac < 0.5 else 16 if prog_frac < 0.75 else 32
                for k in range(steps):
                    place(buf, snare(0.15 + 0.6 * prog_frac), tb + k * bar / steps)
                n = int(bar * SR)
                x = np.diff(rng.standard_normal(n + 1)) * np.linspace(prog_frac, prog_frac + 1 / bars, n) * 0.18
                place(buf, x, tb)
        t0 += bars * bar
    return buf[: int(total * SR)], truth


def sad_track(bpm=70, bars=24):
    """安静的小调钢琴感曲子,无鼓。"""
    beat = 60 / bpm
    bar = 4 * beat
    buf = np.zeros(int(bars * bar * SR) + SR)
    prog = [50, 46, 41, 45]                                      # Dm Bb F A
    chords = {50: [50, 53, 57], 46: [46, 50, 53], 41: [41, 45, 48], 45: [45, 49, 52]}
    mel = [69, 65, 62, 64, 65, 62, 60, 61]
    for b in range(bars):
        tb = b * bar
        for m in chords[prog[b % 4]]:
            place(buf, tone(midi(m), bar, harmonics=2, gain=0.05, decay=1.6), tb)
        for k in range(4):
            place(buf, tone(midi(mel[(b * 4 + k) % 8]), beat, harmonics=2, gain=0.06, decay=0.7), tb + k * beat)
    return buf[: int(bars * bar * SR)]


def happy_track(bpm=140, bars=40):
    """一直很嗨的大调快歌。"""
    beat = 60 / bpm
    bar = 4 * beat
    buf = np.zeros(int(bars * bar * SR) + SR)
    prog = [60, 67, 69, 65]
    chords = {60: [60, 64, 67], 67: [67, 71, 74], 69: [69, 72, 76], 65: [65, 69, 72]}
    mel = [76, 79, 81, 79, 76, 74, 72, 74]
    for b in range(bars):
        tb = b * bar
        root = prog[b % 4]
        for m in chords[root]:
            place(buf, tone(midi(m), bar, harmonics=4, gain=0.06, attack=0.05), tb)
        for k in range(4):
            place(buf, kick(0.9), tb + k * beat)
        for k in (1, 3):
            place(buf, snare(0.6), tb + k * beat)
        for k in range(8):
            place(buf, hat(0.25), tb + k * beat / 2)
            place(buf, tone(midi(mel[k]), beat / 2, harmonics=5, gain=0.11, attack=0.005), tb + k * beat / 2)
            place(buf, tone(midi(root - 24), beat / 2, harmonics=6, gain=0.18, attack=0.005), tb + k * beat / 2)
    return buf[: int(bars * bar * SR)]


def write_wav(path, x):
    import wave
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.9
    pcm = (np.stack([x, x], axis=1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
    os.makedirs(out, exist_ok=True)
    x, truth = edm_track()
    write_wav(os.path.join(out, "demo_edm.wav"), x)
    write_wav(os.path.join(out, "demo_sad.wav"), sad_track())
    write_wav(os.path.join(out, "demo_happy.wav"), happy_track())
    print("已生成 music/demo_edm.wav, demo_sad.wav, demo_happy.wav")
    for name, a, b in truth:
        print(f"  {name:6s} {a:7.2f} – {b:7.2f}")
