"""音乐盒“配方”的导出与导入。

配方 = 每个站点用哪首原曲、从第几秒开始、多长、音量和淡入淡出。不含任何音频。
- 导出:剪辑台导出的片段会被还原成“原曲 + 区间”,并附上原曲的响度指纹
- 导入:按 指纹(最准)/ 文件名 + 时长 把配方里的歌匹配到本地曲库,
        并用指纹的互相关算出两个版本之间的时间差(比如对方的版本前面多 2 秒空白),自动对齐
"""
import os
import re

import numpy as np

FP_RATE = 10                      # 指纹:每秒 10 个响度值
MATCH_CORR = 0.80                 # 指纹相关系数达到这个值才算同一首歌
NAME_MATCH = 0.55                 # 只按名字匹配时的相似度门槛
DUR_TOL = 4.0                     # 只按名字匹配时允许的时长差(秒)

JUNK = re.compile(r"[\(\[【(](official|mv|lyrics?|audio|video|hd|hq|\d{3}\s*kbps|高清|官方|完整版|无损)[^\)\]】)]*[\)\]】)]", re.I)


# ============================================================ 名字
def parse_name(filename):
    """'Artist - Title (Official Video).mp3' → (title, artist)"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = JUNK.sub("", stem).replace("_", " ").strip()
    stem = re.sub(r"\s+", " ", stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip() or stem, artist.strip()
    return stem, ""


def _norm(s):
    s = JUNK.sub("", s.lower())
    return re.sub(r"[\s\W_]+", "", s)


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def name_similarity(title, artist, filename):
    """Dice 系数(字符二元组),中英文都适用;取“歌名”和“歌手+歌名”里更高的那个。"""
    stem = _norm(os.path.splitext(os.path.basename(filename))[0])
    best = 0.0
    for q in {_norm(title), _norm(artist + title), _norm(title + artist)}:
        if not q or not stem:
            continue
        a, b = _bigrams(q), _bigrams(stem)
        dice = 2 * len(a & b) / (len(a) + len(b))
        if q in stem:                         # 文件名里完整包含歌名
            dice = max(dice, 0.75 if len(q) >= 4 else 0.5)
        best = max(best, dice)
    return best


# ============================================================ 指纹
def fingerprint(analysis):
    """分析结果里 10fps 的响度曲线就是指纹。"""
    return [round(float(v), 2) for v in analysis["loud"]]


def align(kit_fp, local_fp, rate=FP_RATE, max_lag_s=30.0, min_overlap_s=20.0):
    """找出 local 相对 kit 的时间差 offset(local 时间 = kit 时间 + offset)和相关系数。
    用响度曲线 + 它的变化率一起做互相关,峰更尖;再用抛物线插值细化到亚帧精度。"""
    a = np.asarray(kit_fp, dtype=float)
    b = np.asarray(local_fp, dtype=float)
    if len(a) < rate * min_overlap_s or len(b) < rate * min_overlap_s:
        return 0.0, 0.0
    da, db = np.diff(a, prepend=a[0]), np.diff(b, prepend=b[0])
    max_lag = int(max_lag_s * rate)
    need = int(min_overlap_s * rate)
    lags, scores = [], []
    for L in range(-max_lag, max_lag + 1):
        i0, i1 = max(0, -L), min(len(a), len(b) - L)
        if i1 - i0 < need:
            continue
        r1 = _pearson(a[i0:i1], b[i0 + L:i1 + L])
        r2 = _pearson(da[i0:i1], db[i0 + L:i1 + L])
        lags.append(L)
        scores.append(0.5 * r1 + 0.5 * r2)
    if not scores:
        return 0.0, 0.0
    k = int(np.argmax(scores))
    frac = 0.0
    if 0 < k < len(scores) - 1:                      # 抛物线插值
        y0, y1, y2 = scores[k - 1], scores[k], scores[k + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    # 只看响度本身的相关性来判断“是不是同一首歌”(变化率对噪声敏感,只用来定位)
    L = lags[k]
    i0, i1 = max(0, -L), min(len(a), len(b) - L)
    corr = _pearson(a[i0:i1], b[i0 + L:i1 + L])
    return (L + frac) / rate, corr


def _pearson(x, y):
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 1e-12 else 0.0


# ============================================================ 导出
def build_recipe(events, clips, library, analyze=None, durations=None, meta=None):
    """把当前配置变成可分享的配方。
    events:   config["events"]
    clips:    {片段文件名: {"file": 原曲, "start", "end", "fade_in", "fade_out", "gain_db"}}
    library:  曲库里所有文件名
    analyze:  analyze(文件名) → 分析结果(用于时长和指纹;可为 None)
    durations:{文件名: 秒}(没有分析时的时长来源)
    meta:     {原曲文件名: {"title", "artist"}} 用户手动改过的歌名
    返回 (songs, events, warnings, song_files)"""
    meta = meta or {}
    durations = durations or {}
    warnings, out_events, song_ids = [], {}, {}

    def song_id(f):
        if f not in song_ids:
            song_ids[f] = f"s{len(song_ids) + 1}"
        return song_ids[f]

    for key, ev in events.items():
        f = ev.get("file")
        if not f:
            continue
        if f not in library:
            warnings.append({"event": key, "msg": f"「{f}」不在曲库里,这个站点不会被分享"})
            continue
        e = {"start": float(ev.get("start", 0)), "duration": float(ev.get("duration", 0)),
             "volume": float(ev.get("volume", 0.8)), "loop": bool(ev.get("loop")),
             "fade_in": float(ev.get("fade_in", 0.2)), "fade_out": float(ev.get("fade_out", 1.0)),
             "priority": int(ev.get("priority", 50)), "enabled": bool(ev.get("enabled", True))}
        origin = f
        if f in clips:                                   # 剪辑台导出的片段 → 还原成原曲上的区间
            c = clips[f]
            origin = c["file"]
            if origin not in library:
                warnings.append({"event": key, "msg": f"片段「{f}」的原曲「{origin}」已不在曲库里,这个站点不会被分享"})
                continue
            clip_len = float(c["end"]) - float(c["start"])
            inner = min(e["start"], clip_len)
            e["start"] = float(c["start"]) + inner
            e["duration"] = min(e["duration"], clip_len - inner) if e["duration"] > 0 else clip_len - inner
            e["fade_in"] = max(e["fade_in"], float(c.get("fade_in", 0))) if inner < 0.05 else e["fade_in"]
            e["fade_out"] = max(e["fade_out"], float(c.get("fade_out", 0)))
            e["volume"] = float(np.clip(e["volume"] * 10 ** (float(c.get("gain_db", 0)) / 20), 0, 1))
        elif re.search(r" 片段 .*\.wav$", f):
            warnings.append({"event": key, "msg": f"「{f}」是旧版本导出的片段,不知道它来自哪首歌,这个站点不会被分享。"
                                                  "在剪辑台用原曲重新导出一次即可"})
            continue
        e["song"] = song_id(origin)
        e["start"] = round(e["start"], 3)
        e["duration"] = round(e["duration"], 3)
        out_events[key] = e

    songs = []
    for f, sid in song_ids.items():
        title, artist = parse_name(f)
        if f in meta:
            title = (meta[f].get("title") or title).strip()[:80]
            artist = (meta[f].get("artist") or "").strip()[:60]
        s = {"id": sid, "title": title or f, "artist": artist, "duration": 0.0, "fp": None, "fp_rate": FP_RATE, "file": f}
        d = None
        if analyze:
            try:
                d = analyze(f)
            except Exception as ex:                       # 分析失败就不带指纹
                warnings.append({"event": None, "msg": f"「{f}」无法生成指纹,别人导入时只能按歌名匹配({ex})"})
        if d:
            s["duration"] = round(float(d["duration"]), 2)
            s["fp"] = fingerprint(d)
        else:
            s["duration"] = round(float(durations.get(f) or 0), 2)
        songs.append(s)
    return songs, out_events, warnings


# ============================================================ 导入
def match_songs(kit_songs, library, analyze=None, durations=None):
    """为配方里的每首歌在本地曲库里找对应文件。
    library: 可用来匹配的本地文件名(原曲,不含剪辑片段)
    返回 {song_id: {"file", "offset", "method", "score", "candidates": [...]}}"""
    durations = durations or {}
    result = {}
    for s in kit_songs:
        cands = []
        for f in library:
            ns = name_similarity(s["title"], s.get("artist", ""), f)
            cands.append({"file": f, "name": round(ns, 3)})
        cands.sort(key=lambda c: -c["name"])
        best = None
        # 1) 指纹:对名字最像的若干首、以及时长接近的歌做互相关
        if analyze and s.get("fp"):
            pool = [c for c in cands if c["name"] >= 0.25][:6]
            if s.get("duration"):
                pool += [c for c in cands if c not in pool and durations.get(c["file"])
                         and abs(durations[c["file"]] - s["duration"]) <= 40][:6]
            if len(library) <= 12:
                pool += [c for c in cands if c not in pool]
            for c in pool:
                try:
                    d = analyze(c["file"])
                except Exception:
                    continue
                off, corr = align(s["fp"], fingerprint(d), s.get("fp_rate", FP_RATE))
                c["corr"], c["offset"] = round(corr, 3), round(off, 3)
                if corr >= MATCH_CORR and (best is None or corr > best["score"]):
                    best = {"file": c["file"], "offset": round(off, 3), "method": "指纹", "score": round(corr, 3)}
        # 2) 名字 + 时长
        if best is None and cands:
            c = cands[0]
            dur = durations.get(c["file"])
            dur_ok = not s.get("duration") or dur is None or abs(dur - s["duration"]) <= DUR_TOL
            if c["name"] >= NAME_MATCH and dur_ok:
                best = {"file": c["file"], "offset": 0.0, "method": "名称", "score": c["name"]}
        result[s["id"]] = {**(best or {"file": None, "offset": 0.0, "method": None, "score": 0.0}),
                           "candidates": cands[:5]}
    return result


def apply_kit(events, kit_data, mapping, replace=False):
    """把配方写进本地配置。mapping: {song_id: {"file", "offset"}};没映射的歌对应的站点保持原样。
    返回 (新的 events, 已导入的站点列表, 跳过的站点列表)"""
    new = {k: dict(v) for k, v in events.items()}
    applied, skipped = [], []
    if replace:
        for k in new:
            new[k]["file"] = ""
    for key, e in kit_data["events"].items():
        if key not in new:
            continue
        m = mapping.get(e["song"]) or {}
        if not m.get("file"):
            skipped.append(key)
            continue
        off = float(m.get("offset") or 0)
        new[key].update({
            "file": m["file"], "start": round(max(0.0, e["start"] + off), 3), "duration": e["duration"],
            "volume": e.get("volume", 0.8), "loop": e.get("loop", False), "fade_in": e.get("fade_in", 0.2),
            "fade_out": e.get("fade_out", 1.0), "priority": e.get("priority", new[key].get("priority", 50)),
            "enabled": e.get("enabled", True)})
        applied.append(key)
    return new, applied, skipped
