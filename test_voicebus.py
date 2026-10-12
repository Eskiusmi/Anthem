"""队友语音混音的单元测试(不需要声卡):python test_voicebus.py"""
import numpy as np

import voicebus as vb
from config import EVENT_META

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'✓' if ok else '✗'} {name}  {detail if not ok else ''}")
    fails += 0 if ok else 1


def tone(freq, seconds, rate, db=-6.0):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * vb.db_to_gain(db) * 32767).astype(np.int16)


def peak_freq(x, rate):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * rate / len(x)


def run(voice, seconds, rate=48000, block=480):
    out = [voice.render(block) for _ in range(int(seconds * rate / block))]
    return np.concatenate(out)


class FakeSound:
    """模拟 pygame Sound:只需要 get_raw(),并且能当弱引用字典的键。"""
    def __init__(self, stereo_int16):
        self.raw = stereo_int16.tobytes()

    def get_raw(self):
        return self.raw


# ---- 片段播放:重采样、循环、淡入淡出
src = tone(441, 1.0, 44100)
v = vb.MusicVoice(src, 44100, 48000, gain=1.0)
y = run(v, 1.2)
check("44.1k → 48k 重采样后音高不变(441 Hz)", abs(peak_freq(y[:40000], 48000) - 441) < 2, peak_freq(y[:40000], 48000))
check("不循环的片段播完就结束,时长正确(1.0 秒)", v.done and abs(np.count_nonzero(np.abs(y) > 1e-4) / 48000 - 1.0) < 0.01,
      np.count_nonzero(np.abs(y) > 1e-4) / 48000)
v = vb.MusicVoice(src, 44100, 48000, loop=True)
y = run(v, 3.0)
check("循环片段一直播,不会结束", not v.done and np.abs(y[-4800:]).max() > 0.3)
v = vb.MusicVoice(src, 44100, 48000, fade_in=0.5)
y = run(v, 0.6)
env = np.abs(y).reshape(-1, 480).max(axis=1)
check("淡入:音量从 0 逐渐升高", env[0] < 0.05 and env[25] < env[45] and env[55] > 0.45, env[[0, 25, 45, 55]])
v = vb.MusicVoice(src, 44100, 48000, loop=True)
run(v, 0.2)
v.fade(0.5)
y = run(v, 0.7)
env = np.abs(y).reshape(-1, 480).max(axis=1)
check("淡出 0.5 秒后结束,中间是逐渐变小的", v.done and env[10] > env[30] > env[45] and env[-1] == 0, env[[10, 30, 45, 69]])

v = vb.MusicVoice(np.arange(44100, dtype=np.int16), 44100, 44100, offset=0.5)
check("中途接上:从指定位置开始送", abs(v.render(1)[0] * 32768 - 22050) < 2, v.render(1))
v = vb.MusicVoice(np.arange(44100, dtype=np.int16), 44100, 44100, loop=True, offset=2.25)
check("循环片段中途接上:位置取余", abs(v.pos - 11025) < 1, v.pos)

# ---- 软限幅
x = np.linspace(-3, 3, 1001)
s = vb.softclip(x)
check("软限幅:再大的声音也不超过 1.0", np.abs(s).max() < 1.0)
check("软限幅:0.8 以下原样通过", np.allclose(vb.softclip(np.array([0.5, -0.79])), [0.5, -0.79]))

# ---- 设备名
check("识别 VB-CABLE 的播放端", vb.is_cable_out("CABLE Input (VB-Audio Virtual Cable)"))
check("虚拟设备不能当麦克风", vb.is_virtual("CABLE Output (VB-Audio Virtual Cable)") and not vb.is_virtual("麦克风 (Realtek(R) Audio)"))
check("MME 截断的设备名也能对上", vb._same_device("CABLE Input (VB-Audio Virtual C", "CABLE Input (VB-Audio Virtual Cable)")
      and not vb._same_device("Speakers", "Speakers 2"))

# ---- 混音总线
cfg = {"mic_gain_db": 0.0, "music_level": 0.5, "duck": True, "duck_db": -12.0, "threshold_db": -40.0,
       "gate": False, "events": ["mvp", "round_won"]}
bus = vb.VoiceBus(lambda: cfg, log=lambda m: None)
bus.status, bus.sr = "on", 48000
music = np.stack([tone(220, 4.0, 44100, -6)] * 2, axis=1)
snd = FakeSound(music)
clip = {"volume": 1.0, "loop": True, "fade_in": 0.0, "fade_out": 0.5}
check("没选中的站点不送给队友", bus.music_start("death", clip, snd, 44100, 2) is False)
check("选中的站点送给队友", bus.music_start("mvp", clip, snd, 44100, 2) is True)


def blocks(bus, mic_fn, seconds, block=480):
    outs = []
    for i in range(int(seconds * 48000 / block)):
        outs.append(bus.process(mic_fn(i, block), block))
    return np.concatenate(outs)


silent = lambda i, n: np.zeros(n)  # noqa: E731
y = blocks(bus, silent, 0.5)
lvl_alone = vb.level_db(y[-4800:])
check("不说话时音乐按设定音量送出", -20 < lvl_alone < -6 and not bus.speaking, lvl_alone)
t = np.arange(480) / 48000
voice = lambda i, n: 0.2 * np.sin(2 * np.pi * 300 * (t + i * n / 48000))  # noqa: E731  约 -17 dB 的“说话声”
y = blocks(bus, voice, 0.5)
check("检测到说话", bus.speaking)
check("说话时音乐自动压低约 12 dB", abs(bus.duck_db - (-12.0)) < 0.5, bus.duck_db)
check("压低后的音乐电平", bus.music_db < lvl_alone - 9, (bus.music_db, lvl_alone))
f300 = np.abs(np.fft.rfft(y[-9600:]))
check("队友能同时听到说话声(300 Hz)和音乐(220 Hz)", f300[60] > 50 and f300[44] > 5, (f300[60], f300[44]))
blocks(bus, silent, 0.3)
check("说完话后的 0.4 秒内保持压低(句中停顿不来回跳)", bus.speaking and bus.duck_db < -10)
blocks(bus, silent, 1.5)
check("说完一会儿后音乐恢复", not bus.speaking and bus.duck_db > -1, bus.duck_db)
cfg["duck"] = False
bus.apply_config()
blocks(bus, voice, 0.3)
check("关闭闪避后说话不压低音乐", bus.duck_db > -0.5, bus.duck_db)
cfg["duck"] = True
bus.apply_config()

bus.music_fade(0.5)
y = blocks(bus, silent, 0.6)
check("本地停止 → 队友那份也淡出并移除", not bus.voices and vb.level_db(y[-4800:]) < -80, (len(bus.voices), vb.level_db(y[-4800:])))

# 噪声门:开着时,低于阈值的底噪被压掉
cfg["gate"] = True
bus.apply_config()
rng = np.random.default_rng(1)
noise = lambda i, n: rng.normal(0, 0.003, n)  # noqa: E731  约 -50 dB 的底噪
y = blocks(bus, noise, 1.0)
check("噪声门:没说话时底噪被压掉", vb.level_db(y[-4800:]) < -80, vb.level_db(y[-4800:]))
y = blocks(bus, voice, 0.2)
check("噪声门:说话时声音正常通过", vb.level_db(y[-4800:]) > -20, vb.level_db(y[-4800:]))
cfg["gate"] = False
bus.apply_config()

# 测试发送:最多 8 秒
bus.music_start("mvp", clip, snd, 44100, 2, test=True)
blocks(bus, silent, 7.5)
check("测试音乐在播", bool(bus.voices))
blocks(bus, silent, 2.0)
check("测试音乐 8 秒后自动淡出", not bus.voices)

# 环形缓冲:两个设备时钟不一致
for _ in range(30):
    bus._in_cb(np.full((480, 1), 0.1, dtype=np.float32), 480, None, None)
check("麦克风数据积压时丢掉旧的,延迟不会越来越大", bus.ring_frames <= vb.RING_MAX and bus.overruns > 0, bus.ring_frames)
bus.in_stream = object()
for _ in range(10):
    bus._take_mic(480)
check("麦克风数据不够时补静音并计数", bus.underruns > 0 and bus.ring_frames == 0)
bus.in_stream = None

# 回调出错只输出静音,不能让音频线程崩掉
out = np.ones((480, 2), dtype=np.float32)
bus.process = None
bus._out_cb(out, 480, None, None)
check("回调出错时输出静音", not out.any())

check("默认配置包含全部 13 个站点", len(EVENT_META) == 13)
print("\n全部通过" if not fails else f"\n{fails} 项未通过")
raise SystemExit(1 if fails else 0)
