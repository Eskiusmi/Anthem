"""AI 分析:找出歌曲的段落结构,并为每个游戏事件推荐最合适的片段。

方法(音乐信息检索 MIR, Music Information Retrieval):
1. 特征提取:响度、鼓点密度、明亮度、和声色彩(大小调)、音色(MFCC)
2. 节拍追踪:找到小节线,推荐片段都从小节线切入
3. 结构分析:自相似矩阵 + 新颖度曲线切分段落,并标注 前奏/主歌/铺垫/高潮/间奏/尾奏
4. 事件画像打分:每个事件有一个“理想片段”画像,对所有候选窗口打分,
   多首歌一起比较,并尽量让不同事件用不同的段落
"""
import hashlib
import json
import os

import numpy as np

VERSION = 9
SR = 22050
HOP = 512
FPS = 10                       # 打分用的特征分辨率:每秒 10 帧

# Krumhansl-Kessler 调性轮廓,用于估计大调/小调
KK_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# ============================================================ 载入与特征
def load_mono(path):
    import librosa
    try:
        y, _ = librosa.load(path, sr=SR, mono=True)
        return y
    except Exception:
        # 退路:用 pygame 解码(与播放器支持的格式一致)
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init(44100, -16, 2)
        freq, fmt, ch = pygame.mixer.get_init()
        if fmt != -16:
            raise
        a = np.frombuffer(pygame.mixer.Sound(path).get_raw(), dtype=np.int16).astype(np.float32) / 32768
        a = a.reshape(-1, ch).mean(axis=1)
        return librosa.resample(a, orig_sr=freq, target_sr=SR)


def _bin(x, frame_times, n_bins):
    """把高帧率特征平均到 FPS 分辨率。x: (d, n) 或 (n,)"""
    idx = np.minimum((frame_times * FPS).astype(int), n_bins - 1)
    cnt = np.maximum(np.bincount(idx, minlength=n_bins), 1)
    if x.ndim == 1:
        return np.bincount(idx, weights=x, minlength=n_bins) / cnt
    return np.stack([np.bincount(idx, weights=r, minlength=n_bins) / cnt for r in x])


def _norm01(x, lo_q=5, hi_q=98):
    lo, hi = np.percentile(x, lo_q), np.percentile(x, hi_q)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def _mode_diff(chroma_mean):
    """大调相关度 - 小调相关度,范围约 -1..1;同时返回最可能的调。"""
    c = chroma_mean - chroma_mean.mean()
    if np.allclose(c, 0):
        return 0.0, "未知"
    maj = [np.corrcoef(c, np.roll(KK_MAJ, k))[0, 1] for k in range(12)]
    mnr = [np.corrcoef(c, np.roll(KK_MIN, k))[0, 1] for k in range(12)]
    km, kn = int(np.argmax(maj)), int(np.argmax(mnr))
    key = f"{NOTES[km]} 大调" if maj[km] >= mnr[kn] else f"{NOTES[kn]} 小调"
    return float(maj[km] - mnr[kn]), key


def _foote(ssm, w):
    """棋盘核新颖度:段落切换处数值高。"""
    n = ssm.shape[0]
    if n < 2 * w + 2:
        return np.zeros(n)
    pos = np.arange(-w, w) + 0.5
    g = np.exp(-0.5 * (pos / (w / 2)) ** 2)
    k = np.outer(g, g) * np.sign(np.outer(pos, pos))
    p = np.pad(ssm, w, mode="constant")
    return np.array([np.sum(k * p[i:i + 2 * w, i:i + 2 * w]) for i in range(n)])


def extract(path):
    """对一首歌做完整分析,返回可缓存的 dict。"""
    import librosa
    y = load_mono(path)
    dur = len(y) / SR
    if dur < 8:
        raise ValueError("曲目太短(少于 8 秒)")

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    n_frames = S.shape[1]
    ftimes = librosa.frames_to_time(np.arange(n_frames), sr=SR, hop_length=HOP)
    T = int(np.ceil(dur * FPS))

    rms = librosa.feature.rms(S=S)[0]
    mel = librosa.feature.melspectrogram(S=S ** 2, sr=SR)
    log_mel = librosa.power_to_db(mel, ref=np.max)
    onset = librosa.onset.onset_strength(S=log_mel, sr=SR)
    cent = librosa.feature.spectral_centroid(S=S, sr=SR)[0]
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=SR)
    # 打击乐占比:在 128 维梅尔谱上做谐波/打击乐分离(比在完整频谱上快约 8 倍)
    _, mel_p = librosa.decompose.hpss(mel)
    mfcc = librosa.feature.mfcc(S=log_mel, n_mfcc=13)

    # ---- 节拍与小节
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    beat_t = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)
    has_beat = len(beat_t) >= 16 and tempo > 0
    if has_beat:
        # 找小节第一拍:底鼓能量 + 起音强度 + 和弦变化(和弦通常在小节第一拍换)
        low = mel[:6].sum(axis=0)
        low = low / (low.max() + 1e-9)
        onset_n = onset / (onset.max() + 1e-9)
        cb = librosa.util.sync(chroma, beats, aggregate=np.median)   # 每拍一列(首列是第一拍之前)
        cb = cb / (np.linalg.norm(cb, axis=0, keepdims=True) + 1e-9)
        change = np.zeros(len(beats))
        for i in range(1, len(beats)):
            change[i] = 1 - float(cb[:, i] @ cb[:, i + 1]) if i + 1 < cb.shape[1] else 0
        change = change / (change.max() + 1e-9)
        bw = max(1, int(0.05 * SR / HOP))               # 拍点附近 ±50ms 取最大
        lowb = np.array([low[max(0, f - bw):f + bw + 1].max() for f in beats])
        onb = np.array([onset_n[max(0, f - bw):f + bw + 1].max() for f in beats])
        phase = int(np.argmax([np.mean(lowb[p::4] + onb[p::4] + 2 * change[p::4]) for p in range(4)]))
        bars = beat_t[phase::4]
        beat_len = 60.0 / tempo
    else:                                               # 没有明显节拍:按 2 秒网格
        beat_len = 0.5
        beat_t = np.arange(0, dur, beat_len)
        bars = np.arange(0, dur, 2.0)
    bar_len = float(np.median(np.diff(bars))) if len(bars) > 1 else 2.0

    # ---- 降到 10 fps
    def smooth_db(p):
        # 0.4 秒窗口平滑(类似 LUFS 的瞬时响度),鼓点之间的空隙不会被当成“变安静”
        p = np.convolve(np.pad(p, (2, 1), mode="edge"), np.ones(4) / 4, mode="valid")[:T]
        return 10 * np.log10(p + 1e-12)

    def rel(db_):
        # 相对响度:以全曲最响处为 1,刻度至少 12 dB(整首都很响的歌不会被拉成“中等”)
        top = np.percentile(db_, 98)
        rng_ = max(top - np.percentile(db_, 5), 12.0)
        return np.clip((db_ - top + rng_) / rng_, 0, 1)

    db = smooth_db(_bin(rms ** 2, ftimes, T))
    loud_rel = rel(db)
    loud_abs = np.clip((db + 45) / 40, 0, 1)            # -45 dBFS → 0,-5 dBFS → 1
    # 低频能量(< 160 Hz:底鼓 + 贝斯)。高潮/Drop 的标志往往是低频回归,而不是整体变响
    n_low = max(2, int(np.sum(librosa.mel_frequencies(n_mels=mel.shape[0], fmax=SR / 2) < 160)))
    low10 = rel(smooth_db(_bin(mel[:n_low].sum(axis=0), ftimes, T)))
    loud = 0.7 * loud_rel + 0.3 * loud_abs
    perc_share = np.clip(_bin(mel_p.sum(axis=0), ftimes, T) / (_bin(mel.sum(axis=0), ftimes, T) + 1e-12), 0, 1)
    # 鼓点密度:起音强度先平滑再归一(起音是尖峰,直接平均会偏低)+ 打击乐能量占比
    on10 = np.convolve(np.pad(_bin(onset, ftimes, T), (2, 2), mode="edge"), np.ones(5) / 5, mode="valid")[:T]
    dens = 0.5 * _norm01(on10) + 0.5 * np.clip(perc_share * 2, 0, 1)
    bright = np.clip((np.log2(_bin(cent, ftimes, T) + 1) - np.log2(500)) / 3, 0, 1)   # 500Hz→0, 4kHz→1
    chroma10 = _bin(chroma, ftimes, T)
    mfcc10 = _bin(mfcc, ftimes, T)

    # ---- 结构:按拍子同步的音色+和声特征 → 自相似矩阵 → 新颖度 → 边界
    edges = np.concatenate([[0.0], beat_t[(beat_t > 0) & (beat_t < dur)], [dur]])
    seg_idx = np.minimum((edges * FPS).astype(int), T)
    feat = np.vstack([
        (mfcc10[1:] - mfcc10[1:].mean(1, keepdims=True)) / (mfcc10[1:].std(1, keepdims=True) + 1e-9),
        chroma10 * 2,
        loud[None, :] * 4,
    ])
    cols, lb = [], []
    for a, b in zip(seg_idx[:-1], seg_idx[1:]):
        b = max(b, a + 1)
        cols.append(feat[:, a:b].mean(axis=1))
        lb.append(loud[a:b].mean())
    F = np.array(cols).T
    Fn = F / (np.linalg.norm(F, axis=0, keepdims=True) + 1e-9)
    ssm = Fn.T @ Fn
    w = 8                                               # 半宽 8 拍 ≈ 2 小节
    nov_t = _foote(ssm, w)
    lb = np.array(lb)
    nov_l = np.array([abs(lb[i:i + w].mean() - lb[max(0, i - w):i].mean()) if i >= 1 else 0.0
                      for i in range(len(lb))])

    def n01(x):
        return (x - x.min()) / (x.max() - x.min() + 1e-9) if len(x) else x
    nov = 0.5 * n01(np.maximum(nov_t, 0)) + 0.5 * n01(nov_l)
    peaks = librosa.util.peak_pick(nov, pre_max=w, post_max=w, pre_avg=2 * w, post_avg=2 * w,
                                   delta=0.08, wait=2 * w) if len(nov) > 4 * w else np.array([], int)
    bounds = [0.0]
    for p in peaks:
        t = edges[p]
        t = float(bars[np.argmin(np.abs(bars - t))]) if len(bars) else t   # 对齐到小节线
        if t - bounds[-1] >= 2 * bar_len - 0.05 and dur - t >= 2 * bar_len - 0.05:
            bounds.append(t)
    bounds.append(dur)
    sections = _label_sections(bounds, loud_rel, dens, chroma10, mfcc10)

    is_chorus = np.zeros(T)
    for s in sections:
        if s["label"] == "高潮":
            is_chorus[int(s["start"] * FPS):int(s["end"] * FPS)] = 1

    _, key = _mode_diff(chroma10.mean(axis=1))
    r3 = lambda a: np.round(a, 3).tolist()   # noqa: E731
    return {
        "version": VERSION, "duration": round(dur, 3), "tempo": round(tempo, 1), "has_beat": has_beat,
        "key": key, "beat_len": round(beat_len, 4), "bar_len": round(bar_len, 4),
        "bars": r3(bars), "sections": sections,
        "loud": r3(loud), "loud_abs": r3(loud_abs), "low": r3(low10), "dens": r3(dens), "bright": r3(bright),
        "chroma": r3(chroma10), "mfcc": r3(mfcc10), "is_chorus": is_chorus.astype(int).tolist(),
    }


def _label_sections(bounds, loud_rel, dens, chroma10, mfcc10):
    secs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        i, j = int(a * FPS), max(int(b * FPS), int(a * FPS) + 1)
        x = loud_rel[i:j]
        slope = float(np.polyfit(np.arange(len(x)) / FPS, x, 1)[0]) * (b - a) if len(x) > 3 else 0.0
        secs.append({"start": round(a, 3), "end": round(b, 3), "loud": float(x.mean()),
                     "dens": float(dens[i:j].mean()), "rise": slope,
                     "desc": np.concatenate([chroma10[:, i:j].mean(1), mfcc10[1:, i:j].mean(1) / 50])})
    n = len(secs)
    L = np.array([s["loud"] for s in secs])
    top = max(0.72, float(np.quantile(L, 0.7))) if n > 2 else 0.85
    med = float(np.median(L))
    lowq = float(np.quantile(L, 0.35))
    for k, s in enumerate(secs):
        if s["loud"] >= top or (n <= 2 and s["loud"] >= 0.7):
            s["label"] = "高潮"
    for k, s in enumerate(secs):
        if "label" in s:
            continue
        nxt = secs[k + 1] if k + 1 < n else None
        if k == 0 and s["loud"] < med - 0.08:
            s["label"] = "前奏"
        elif k == n - 1 and s["loud"] < med - 0.08:
            s["label"] = "尾奏"
        elif nxt and nxt.get("label") == "高潮" and (s["rise"] > 0.12 or nxt["loud"] - s["loud"] > 0.2):
            s["label"] = "铺垫"
        elif s["loud"] <= lowq and s["loud"] < med - 0.05:
            s["label"] = "间奏"
        else:
            s["label"] = "主歌"
    out = []
    for s in secs:
        out.append({"start": s["start"], "end": s["end"], "label": s["label"],
                    "loud": round(s["loud"], 3)})
    return out


# ============================================================ 缓存
def cache_key(path):
    st = os.stat(path)
    raw = f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}|v{VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def analyze_cached(path, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    cp = os.path.join(cache_dir, cache_key(path) + ".json")
    if os.path.exists(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("version") == VERSION:
                return d, True
        except (OSError, ValueError):
            pass
    d = extract(path)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d, False


# ============================================================ 事件画像
# 每个分量取值 0..1;权重之和为 1。dur=默认片段长度(配置里时长为 0 时使用)
PROFILES = {
    "mvp":          {"dur": 12, "w": {"loud": .30, "chorus": .25, "impact": .15, "steady": .10, "valence": .10, "start": .10}},
    "match_won":    {"dur": 20, "w": {"loud": .30, "chorus": .25, "impact": .10, "steady": .10, "valence": .15, "late": .10}},
    "round_won":    {"dur": 8,  "w": {"loud": .30, "valence": .20, "impact": .20, "dens": .15, "start": .15}},
    "round_live":   {"dur": 10, "w": {"impact": .30, "dens": .25, "loud": .25, "start": .20}},
    "freezetime":   {"dur": 15, "w": {"mid": .30, "groove": .20, "rise_mild": .20, "nonchorus": .15, "start": .15}},
    "bomb_planted": {"dur": 30, "w": {"chain": .20, "tense": .25, "dens": .20, "dark": .15, "rise": .10, "start": .10}},
    "bomb_10s":     {"dur": 10, "w": {"into_chorus": .30, "rise": .25, "jump_after": .15, "dens_rise": .15, "end": .15}},
    "round_10s":    {"dur": 10, "w": {"into_chorus": .30, "rise": .25, "jump_after": .15, "dens_rise": .15, "end": .15}},
    "death":        {"dur": 8,  "w": {"calm": .35, "sparse": .25, "smooth": .20, "start": .20}},
    "round_lost":   {"dur": 8,  "w": {"lowmid": .30, "dark": .25, "fall": .20, "dull": .10, "start": .15}},
    "match_lost":   {"dur": 20, "w": {"lowmid": .30, "dark": .25, "fall": .15, "late": .15, "start": .15}},
    "menu":         {"loop": True, "w": {"loop": .40, "mid": .25, "steady": .15, "start": .20}},
    "warmup":       {"loop": True, "w": {"loop": .35, "mid": .25, "groove": .20, "start": .20}},
}
# 分配顺序:越靠前越先挑段落(重要的事件优先拿到最好的段)
ORDER = ["mvp", "bomb_10s", "round_10s", "round_won", "round_live", "bomb_planted", "freezetime",
         "death", "round_lost", "match_won", "match_lost", "menu", "warmup"]

PHRASE = {
    "loud":       lambda v: f"能量高({v * 100:.0f}%)" if v > .6 else None,
    "chorus":     lambda v: "处在副歌/高潮段" if v > .6 else None,
    "impact":     lambda v: "开头有明显冲击(能量骤升)" if v > .45 else None,
    "steady":     lambda v: "全程能量稳定" if v > .7 else None,
    "valence":    lambda v: "听感明亮、偏大调" if v > .6 else None,
    "start":      lambda v: "从段落开头切入" if v > .9 else None,
    "end":        lambda v: "结尾正好落在段落交界" if v > .9 else None,
    "late":       lambda v: "位于歌曲后段(收尾感)" if v > .6 else None,
    "dens":       lambda v: "节奏密集" if v > .6 else None,
    "mid":        lambda v: "中等能量,不抢戏" if v > .8 else None,
    "groove":     lambda v: "律动稳定" if v > .6 else None,
    "rise_mild":  lambda v: "缓慢推进" if v > .8 else None,
    "nonchorus":  lambda v: "把副歌留给更重要的事件" if v > .9 else None,
    "tense":      lambda v: "紧张感强" if v > .7 else None,
    "dark":       lambda v: "情绪偏暗(偏小调/音色暗)" if v > .55 else None,
    "rise":       lambda v: "能量持续上升" if v > .5 else None,
    "jump_after": lambda v: "结尾后能量/低频爆发" if v > .4 else None,
    "into_chorus": lambda v: "结尾直接进入高潮,适合倒计时" if v > .6 else None,
    "dens_rise":  lambda v: "鼓点越来越密" if v > .4 else None,
    "calm":       lambda v: "安静、留白多" if v > .7 else None,
    "sparse":     lambda v: "鼓点稀疏" if v > .6 else None,
    "smooth":     lambda v: "没有突兀的冲击" if v > .8 else None,
    "lowmid":     lambda v: "能量偏低" if v > .7 else None,
    "fall":       lambda v: "能量回落" if v > .4 else None,
    "dull":       lambda v: "音色偏暗" if v > .6 else None,
    "chain":      lambda v: "与「炸弹剩余10秒」首尾相接,爆炸时正好进入高潮" if v > .5 else None,
    "loop":       lambda v: f"首尾衔接自然,可循环({v * 100:.0f}%)" if v > .6 else None,
}


def _band(x, lo, hi, fall=0.25):
    if lo <= x <= hi:
        return 1.0
    d = lo - x if x < lo else x - hi
    return max(0.0, 1 - d / fall)


class Song:
    def __init__(self, name, d):
        self.name = name
        self.d = d
        self.dur = d["duration"]
        self.loud = np.array(d["loud"])
        self.loud_abs = np.array(d["loud_abs"])
        self.low = np.array(d["low"])
        self.dens = np.array(d["dens"])
        self.bright = np.array(d["bright"])
        self.chroma = np.array(d["chroma"])
        self.mfcc = np.array(d["mfcc"])
        self.is_chorus = np.array(d["is_chorus"], dtype=float)
        self.bars = np.array(d["bars"])
        self.bounds = np.array([s["start"] for s in d["sections"]][1:])
        self.T = len(self.loud)
        self.tol = max(0.6 * d["beat_len"], 0.35)

    def section_at(self, t):
        for s in self.d["sections"]:
            if s["start"] - 1e-6 <= t < s["end"]:
                return s
        return self.d["sections"][-1]

    def at_bound(self, t):
        return len(self.bounds) > 0 and float(np.min(np.abs(self.bounds - t))) <= self.tol

    def comps(self, start, dur):
        a, b = int(round(start * FPS)), int(round((start + dur) * FPS))
        if a < 0 or b > self.T or b - a < 2 * FPS:
            return None
        L = self.loud[a:b]
        n2, n3 = 2 * FPS, 3 * FPS
        has_post = b < self.T - 5

        # 冲击感 = 整体响度跳升 与 低频(底鼓/贝斯)跳升 中较大者
        # 曲目开头没有“之前”,按中等电平 0.5 计算,避免把歌的第一秒误判为冲击
        def ctx(arr):
            head, tail = arr[a:a + n2].mean(), arr[b - n2:b].mean()
            pre = arr[max(0, a - n3):a].mean() if a > 0 else 0.5
            post = arr[b:b + n3].mean() if has_post else tail
            return head - pre, post - tail
        imp_l, aft_l = ctx(self.loud)
        imp_b, aft_b = ctx(self.low)
        impact = float(np.clip(max(imp_l, imp_b) * 2.5, 0, 1))
        after = float(np.clip(max(aft_l, aft_b) * 2.5, 0, 1)) if has_post else 0.0
        into_chorus = float(self.is_chorus[b:b + n3].mean()) if has_post else 0.0
        if into_chorus > 0 and self.is_chorus[max(0, b - n2):b].mean() > 0.5:
            into_chorus = 0.0          # 已经在高潮里了,不算“进入”高潮
        x = np.arange(b - a) / FPS
        rise = float(np.polyfit(x, L, 1)[0]) * dur
        D = self.dens[a:b]
        drise = float(np.polyfit(x, D, 1)[0]) * dur
        B = float(self.bright[a:b].mean())
        md, _ = _mode_diff(self.chroma[:, a:b].mean(axis=1))
        valence = float(np.clip(0.5 + 2.0 * md, 0, 1)) * 0.6 + B * 0.4
        Lm, Dm = float(L.mean()), float(D.mean())
        c = {
            "loud": Lm, "chorus": float(self.is_chorus[a:b].mean()),
            "impact": impact, "jump_after": after, "into_chorus": into_chorus,
            "steady": float(np.clip(1 - L.std() * 4, 0, 1)),
            "valence": valence, "dark": 1 - valence,
            "start": 1.0 if (start < 0.05 or self.at_bound(start)) else 0.3,
            "end": 1.0 if self.at_bound(start + dur) else 0.2,
            "late": float(np.clip((start + dur / 2) / self.dur, 0, 1)),
            "dens": Dm, "sparse": 1 - Dm,
            "mid": _band(Lm, .35, .65), "lowmid": _band(Lm, .2, .55), "calm": _band(Lm, .12, .45),
            "tense": _band(Lm, .5, .85) * float(np.clip(Dm / 0.5, 0, 1)),   # 紧张感需要节奏推动
            "groove": _band(Dm, .35, .8) * (1.0 if self.d["has_beat"] else 0.5),
            "rise": float(np.clip(rise * 2.5, 0, 1)), "rise_mild": _band(rise, 0.0, 0.3, 0.2),
            "fall": float(np.clip(-rise * 2.5, 0, 1)),
            "dens_rise": float(np.clip(drise * 2.5, 0, 1)),
            "nonchorus": 1 - float(self.is_chorus[a:b].mean()),
            "smooth": 1 - impact,
            "dull": 1 - B,
        }
        bar = int(round(self.d["bar_len"] * FPS))
        if b + bar <= self.T and bar >= 2:
            f1 = np.concatenate([self.chroma[:, a:a + bar].mean(1), self.mfcc[1:, a:a + bar].mean(1) / 50])
            f2 = np.concatenate([self.chroma[:, b:b + bar].mean(1), self.mfcc[1:, b:b + bar].mean(1) / 50])
            # 循环点:片段结束后的那一小节应该和片段开头那一小节听起来一样
            cos = float(f1 @ f2 / (np.linalg.norm(f1) * np.linalg.norm(f2) + 1e-9))
            ldiff = abs(self.loud[a:a + bar].mean() - self.loud[b - bar:b].mean())
            c["loop"] = float(np.clip((cos - 0.8) / 0.2, 0, 1)) * float(np.clip(1 - ldiff * 2, 0, 1))
        else:
            c["loop"] = 0.0
        silence = float(np.mean(self.loud_abs[a:b] < 0.05))
        c["_silence"] = silence
        return c


def _score(comps, weights):
    s = sum(weights[k] * comps[k] for k in weights)
    return s * (1 - comps["_silence"])


def _reasons(song, start, comps, weights):
    sec = song.section_at(start + 0.01)
    out = [f"「{sec['label']}」段"]
    ranked = sorted(weights, key=lambda k: weights[k] * comps[k], reverse=True)
    for k in ranked:
        p = PHRASE[k](comps[k])
        if p and p not in out:
            out.append(p)
        if len(out) >= 4:
            break
    return out


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def recommend(songs, durations, top_k=3):
    """songs: {文件名: 分析结果};durations: {事件: 配置时长(秒, 0=按画像默认)}"""
    S = [Song(n, d) for n, d in songs.items()]
    used = {s.name: [] for s in S}
    best = {}                       # 每个事件的第一推荐 (song, start, length)
    result = {}
    for ev in ORDER:
        prof = PROFILES[ev]
        w = prof["w"]
        cands = []
        for song in S:
            if prof.get("loop"):
                bar = song.d["bar_len"]
                lens = [8 * bar, 16 * bar, 32 * bar] if song.d["has_beat"] else [15.0, 30.0, 60.0]
                lens = [L for L in lens if 12 <= L <= song.dur - 2] or [max(8.0, song.dur * 0.6)]
            else:
                L = float(durations.get(ev) or 0) or prof["dur"]
                lens = [min(L, song.dur)]
            for L in lens:
                starts = set(np.round(song.bars, 2).tolist()) | {0.0}
                if "end" in w:          # 倒计时类:让片段结尾对齐段落边界
                    starts |= {round(t - L, 2) for t in song.bounds if t - L >= 0}
                b10 = best.get("bomb_10s") if "chain" in w else None
                if b10 and b10[0] is song and b10[1] - L >= 0:
                    starts.add(round(b10[1] - L, 2))      # 结尾接上“炸弹剩余10秒”的开头
                for st in sorted(starts):
                    c = song.comps(st, L)
                    if c is None:
                        continue
                    c["chain"] = 1.0 if (b10 and b10[0] is song
                                         and abs(st + L - b10[1]) <= 0.15) else 0.0
                    sc = _score(c, w)
                    ov = sum(_overlap(st, st + L, u0, u1) for u0, u1 in used[song.name]) / L
                    adj = sc - (0.1 if prof.get("loop") else 0.3) * min(ov, 1.0)
                    cands.append((adj, sc, song, st, L, c))
        cands.sort(key=lambda x: x[0], reverse=True)
        picks = []
        for adj, sc, song, st, L, c in cands:
            if any(p[2] is song and _overlap(st, st + L, p[3], p[3] + p[4]) > 0.5 * L for p in picks):
                continue
            picks.append((adj, sc, song, st, L, c))
            if len(picks) >= top_k:
                break
        if picks:
            _, _, song, st, L, _ = picks[0]
            used[song.name].append((st, st + L))
            best[ev] = (song, st, L)
        result[ev] = [{
            "file": song.name, "start": round(st, 2), "duration": round(L, 2),
            "loop": bool(prof.get("loop")), "score": round(sc * 100),
            "section": song.section_at(st + 0.01)["label"],
            "reasons": _reasons(song, st, c, w),
        } for adj, sc, song, st, L, c in picks]
    return result


def song_summary(name, d, points=600):
    """给面板画图用:降采样的能量曲线 + 段落。"""
    loud = np.array(d["loud"])
    step = max(1, len(loud) // points)
    env = loud[: len(loud) // step * step].reshape(-1, step).mean(axis=1) if step > 1 else loud
    return {"file": name, "duration": d["duration"], "tempo": d["tempo"], "key": d["key"],
            "has_beat": d["has_beat"], "sections": d["sections"],
            "envelope": np.round(env, 3).tolist(), "env_dt": step / FPS}
