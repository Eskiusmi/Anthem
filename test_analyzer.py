"""AI 分析的回归测试:用结构已知的合成曲检查段落切分和推荐是否合理。

运行:python test_analyzer.py   (首次会生成 music/demo_*.wav,约 10 秒)
"""
import os
import time

import analyzer
import demo_song
from config import EVENT_META, MUSIC_DIR

files = ["demo_edm.wav", "demo_sad.wav", "demo_happy.wav"]
if not all(os.path.exists(os.path.join(MUSIC_DIR, f)) for f in files):
    os.makedirs(MUSIC_DIR, exist_ok=True)
    x, _ = demo_song.edm_track()
    demo_song.write_wav(os.path.join(MUSIC_DIR, "demo_edm.wav"), x)
    demo_song.write_wav(os.path.join(MUSIC_DIR, "demo_sad.wav"), demo_song.sad_track())
    demo_song.write_wav(os.path.join(MUSIC_DIR, "demo_happy.wav"), demo_song.happy_track())

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'✓' if ok else '✗'} {name}  {detail}")
    fails += 0 if ok else 1


t = time.time()
songs = {f: analyzer.extract(os.path.join(MUSIC_DIR, f)) for f in files}
print(f"分析 3 首用时 {time.time() - t:.1f}s\n")

edm = songs["demo_edm.wav"]
starts = [s["start"] for s in edm["sections"]]
labels = [s["label"] for s in edm["sections"]]
check("EDM 速度≈128 BPM", abs(edm["tempo"] - 128) < 3, edm["tempo"])
for truth in (15, 45, 60, 90, 120):
    near = min(abs(s - truth) for s in starts)
    check(f"找到 {truth}s 的段落边界", near < 0.3, f"误差 {near:.2f}s")
check("两段高潮都被识别", labels.count("高潮") == 2, labels)
check("悲伤曲识别为小调", "小调" in songs["demo_sad.wav"]["key"], songs["demo_sad.wav"]["key"])

dur = {k: m["duration"] for k, m in EVENT_META.items()}
one = analyzer.recommend({"demo_edm.wav": edm}, dur)
b = lambda ev: one[ev][0]   # noqa: E731
check("MVP 从高潮开头切入", any(abs(b("mvp")["start"] - x) < 0.3 for x in (60, 120)), b("mvp"))
for ev in ("bomb_10s", "round_10s"):
    end = b(ev)["start"] + b(ev)["duration"]
    check(f"{EVENT_META[ev]['name']} 结尾正好是高潮开始", any(abs(end - x) < 0.3 for x in (60, 120)), f"结尾 {end:.2f}s")
check("两个倒计时用了不同的铺垫段", abs(b("bomb_10s")["start"] - b("round_10s")["start"]) > 30)
check("阵亡在安静段", b("death")["section"] in ("间奏", "前奏", "尾奏"), b("death")["section"])
check("回合失败在安静段", b("round_lost")["section"] in ("间奏", "前奏", "尾奏"), b("round_lost")["section"])
check("行动开始有冲击感", "开头有明显冲击(能量骤升)" in b("round_live")["reasons"], b("round_live")["reasons"])
check("主菜单片段可无缝循环", any("可循环" in r for r in b("menu")["reasons"]), b("menu")["reasons"])

multi = analyzer.recommend(songs, dur)
check("多首:比赛失败选了悲伤曲", multi["match_lost"][0]["file"] == "demo_sad.wav", multi["match_lost"][0]["file"])
check("多首:炸弹已安放没选安静的悲伤曲", multi["bomb_planted"][0]["file"] != "demo_sad.wav")
check("每个事件都有 3 个候选", all(len(v) == 3 for v in multi.values()))

print("\n全部通过" if not fails else f"\n{fails} 项未通过")
raise SystemExit(1 if fails else 0)
