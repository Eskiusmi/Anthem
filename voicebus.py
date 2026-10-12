"""队友语音(Voice Mix,实验性功能):把你的麦克风和正在播放的音乐混在一起,送到虚拟声卡,游戏把它当麦克风用。

    真实麦克风 ──▶ 输入流 ──▶ 环形缓冲 ─┐
                                          ├─▶ 混音:麦克风增益 / 噪声门 + 音乐(说话时自动压低)+ 软限幅
    正在播放的音乐片段(和本地同步开始)──┘                │
                                                           ▼
                                   输出流 ──▶ 「CABLE Input」(VB-CABLE 虚拟声卡的播放端)
    Steam / CS2 的语音输入设备选「CABLE Output」(同一根虚拟线的录音端),队友听到的就是这份混音。

程序自己做不出“麦克风设备”(那需要 Windows 内核驱动),所以依赖免费的虚拟声卡 VB-CABLE。
本地耳机里的音乐仍由 player.py 播放,这里只是把同一段音乐额外混一份给队友。
音频设备用 sounddevice(PortAudio);没装或没有设备时,这个功能显示为不可用,不影响其他功能。
"""
import math
import sys
import threading
import time
import weakref
from collections import deque

import numpy as np

try:
    import sounddevice as sd
    SD_ERROR = None
except Exception as e:                       # 没装 sounddevice,或系统里没有 PortAudio
    sd = None
    SD_ERROR = str(e)

RATES = (48000, 44100)          # 优先 48 kHz(Windows 和 VB-CABLE 的常用格式)
BLOCK = 480                     # 每次处理 10 毫秒(48 kHz 时)
RING_TARGET = 2 * BLOCK         # 麦克风缓冲:正常保持约 20 毫秒
RING_MAX = 10 * BLOCK           # 超过约 100 毫秒说明两个设备时钟有偏差,丢掉多余的保持低延迟
OUT_LATENCY = 0.06              # 输出缓冲 60 毫秒:Python 回调偶尔被其他线程耽误几毫秒也不会断音;对语音聊天来说察觉不到
HOLD_S = 0.4                    # 说话检测:声音低于阈值后再保持 0.4 秒,避免句中停顿就把音乐抬回来
DUCK_ATTACK_S, DUCK_RELEASE_S = 0.03, 0.35
GATE_ATTACK_S, GATE_RELEASE_S = 0.005, 0.15
TEST_SECONDS = 8.0

CABLE_OUT_HINTS = ("cable input",)                                  # 我们往这里写:虚拟线的“播放”端
VIRTUAL_HINTS = ("cable", "vb-audio", "voicemeeter", "virtual audio")  # 这些不能当麦克风,否则会自己录自己


def db_to_gain(db):
    return 10.0 ** (db / 20.0)


def level_db(x):
    if len(x) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20.0 * math.log10(rms) if rms > 1e-6 else -120.0


def is_virtual(name):
    n = (name or "").lower()
    return any(h in n for h in VIRTUAL_HINTS)


def is_cable_out(name):
    n = (name or "").lower()
    return any(h in n for h in CABLE_OUT_HINTS)


def softclip(x, knee=0.8):
    """0.8 以下原样,超过的部分平滑压到 1.0 以内,不会削波爆音。"""
    a = np.abs(x)
    over = a > knee
    if over.any():
        x = x.copy()
        x[over] = np.sign(x[over]) * (knee + (1.0 - knee) * np.tanh((a[over] - knee) / (1.0 - knee)))
    return x


def _same_device(a, b):
    """同一个设备在不同音频接口(WASAPI / MME)里名字可能被截断成 31 个字。"""
    if a == b:
        return True
    short = min(len(a), len(b))
    return short >= 20 and (a.startswith(b) or b.startswith(a))


# ============================================================ 一段送给队友的音乐
class MusicVoice:
    def __init__(self, pcm, src_rate, out_rate, gain=1.0, loop=False, fade_in=0.0, fade_out=1.0, tag="",
                 max_seconds=None, offset=0.0):
        self.pcm = pcm                         # int16 单声道
        self.n = len(pcm)
        self.step = src_rate / out_rate        # 每个输出样本走过多少源样本(顺便完成重采样)
        self.out_rate = out_rate
        self.pos = offset * src_rate           # 从第几秒开始(中途打开队友语音时,接上本地正在放的位置)
        if loop and self.n:
            self.pos %= self.n
        self.gain = gain
        self.loop = loop
        self.fade_out_s = fade_out
        self.tag = tag
        self.env = 0.0 if fade_in > 0 else 1.0
        self.in_step = 1.0 / max(1.0, fade_in * out_rate)
        self.out_step = 0.0
        self.fading = False
        self.done = self.n < 2
        self.played = 0
        self.max_frames = int(max_seconds * out_rate) if max_seconds else None

    def fade(self, seconds=None):
        seconds = self.fade_out_s if seconds is None else seconds
        if seconds <= 0:
            self.done = True
            return
        if not self.fading:
            self.fading = True
            self.out_step = max(self.env, 1e-3) / (seconds * self.out_rate)

    def _envelope(self, frames):
        k = np.arange(1, frames + 1, dtype=np.float64)
        if self.fading:
            e = np.maximum(self.env - self.out_step * k, 0.0)
            self.env = float(e[-1])
            if self.env <= 0.0:
                self.done = True
            return e
        if self.env < 1.0:
            e = np.minimum(self.env + self.in_step * k, 1.0)
            self.env = float(e[-1])
            return e
        return None

    def render(self, frames):
        out = np.zeros(frames, dtype=np.float64)
        if self.done:
            return out
        idx = self.pos + self.step * np.arange(frames, dtype=np.float64)
        last = self.n - 1
        if self.loop:
            idx %= self.n
            i0 = idx.astype(np.int64)
            i1 = (i0 + 1) % self.n
            valid = frames
            self.pos = (self.pos + self.step * frames) % self.n
        else:
            valid = int(np.searchsorted(idx, last))         # 到结尾为止的样本数
            idx = idx[:valid]
            i0 = idx.astype(np.int64)
            i1 = np.minimum(i0 + 1, last)
            self.pos += self.step * frames
            if valid < frames:
                self.done = True
        if valid:
            frac = idx - i0
            out[:valid] = (self.pcm[i0] * (1.0 - frac) + self.pcm[i1] * frac) * (self.gain / 32768.0)
        env = self._envelope(frames)
        if env is not None:
            out *= env
        self.played += frames
        if self.max_frames is not None and self.played >= self.max_frames:
            self.fade(1.0)
        return out


# ============================================================ 混音总线
class VoiceBus:
    def __init__(self, get_cfg, log=print):
        self.get_cfg = get_cfg                 # 返回 config["voice"]
        self.log = log
        self.lock = threading.Lock()           # 保护 voices(音频回调线程和程序线程都会用)
        self.ctl = threading.RLock()           # 保护开关流程
        self.ring_lock = threading.Lock()
        self.voices = []
        self.pcm_cache = weakref.WeakKeyDictionary()   # pygame Sound → 单声道 PCM
        self.in_stream = self.out_stream = None
        self.ring = deque()
        self.ring_frames = 0
        self.status = "off"                    # off / on / error
        self.error = None
        self.warning = None
        self.mic_name = self.out_name = None
        self.sr = RATES[0]
        self.reset_meters()
        self.apply_config()

    @property
    def available(self):
        return sd is not None

    @property
    def duck_db(self):
        return 20.0 * math.log10(max(self.duck, 1e-6))

    def reset_meters(self):
        self.mic_db = self.out_db = self.music_db = -120.0
        self.speaking = False
        self.hold_until = 0
        self.duck = 1.0
        self.gate = 1.0
        self.samples = 0
        self.underruns = self.overruns = 0
        self.cb_errors = 0
        self.last_cb_error = None
        self.cb_count = self.cb_status = 0
        self.cb_last = 0.0
        self.cb_gap_max = 0.0
        self.mic_started = False

    def apply_config(self):
        """把配置换算成音频回调里直接用的数值(回调里不查字典)。"""
        c = self.get_cfg()
        self.p_mic_gain = db_to_gain(float(c.get("mic_gain_db", 0.0)))
        self.p_music = max(0.0, min(1.0, float(c.get("music_level", 0.35))))
        self.p_duck_on = bool(c.get("duck", True))
        self.p_duck = db_to_gain(float(c.get("duck_db", -12.0)))
        self.p_threshold = float(c.get("threshold_db", -45.0))
        self.p_gate = bool(c.get("gate", False))
        self.p_events = set(c.get("events") or [])

    # ------------------------------------------------------------ 设备
    @staticmethod
    def _preferred_api(apis):
        """Windows 上优先 WASAPI(延迟低、设备名完整);其他系统用默认音频接口。"""
        if sys.platform == "win32":
            for i, a in enumerate(apis):
                if "WASAPI" in a["name"]:
                    return i
        try:
            return int(sd.default.hostapi)
        except Exception:
            return 0

    def devices(self):
        if sd is None:
            return {"available": False, "error": f"缺少音频组件 sounddevice / PortAudio:{SD_ERROR}",
                    "inputs": [], "outputs": [], "cable": None, "default_input": None}
        try:
            devs, apis = sd.query_devices(), sd.query_hostapis()
        except Exception as e:
            return {"available": False, "error": f"读取音频设备失败:{e}", "inputs": [], "outputs": [],
                    "cable": None, "default_input": None}
        api = self._preferred_api(apis)
        ins, outs = [], []
        for d in devs:
            if d["hostapi"] != api:
                continue
            if d["max_input_channels"] > 0 and d["name"] not in ins:
                ins.append(d["name"])
            if d["max_output_channels"] > 0 and d["name"] not in outs:
                outs.append(d["name"])
        default_in = None
        try:
            idx = apis[api].get("default_input_device", -1)
            if idx is not None and idx >= 0:
                default_in = devs[idx]["name"]
        except Exception:
            pass
        return {"available": True, "error": None, "hostapi": apis[api]["name"],
                "inputs": [{"name": n, "virtual": is_virtual(n)} for n in ins],
                "outputs": [{"name": n, "cable": is_cable_out(n)} for n in outs],
                "cable": next((n for n in outs if is_cable_out(n)), None),
                "default_input": default_in}

    def _candidates(self, name, kind):
        """按名字找设备:先在首选音频接口里找,再到其他接口里找同名(或被截断的)设备。"""
        devs, apis = sd.query_devices(), sd.query_hostapis()
        api = self._preferred_api(apis)
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        found = [(d["hostapi"] != api, d["index"], d["hostapi"]) for d in devs
                 if d[key] > 0 and _same_device(d["name"], name)]
        found.sort()
        out = []
        for _, idx, hapi in found:
            extra = None
            if "WASAPI" in apis[hapi]["name"]:
                try:
                    extra = sd.WasapiSettings(auto_convert=True)    # 让 Windows 帮忙转换采样率
                except TypeError:
                    extra = None
            out.append((idx, extra))
        return out

    def _open_output(self, name):
        last = None
        for idx, extra in self._candidates(name, "output"):
            for sr in RATES:
                for ch in (2, 1):
                    try:
                        s = sd.OutputStream(device=idx, samplerate=sr, channels=ch, dtype="float32", blocksize=0,
                                            latency=OUT_LATENCY, extra_settings=extra, callback=self._out_cb)
                        return s, sr
                    except Exception as e:
                        last = e
        raise RuntimeError(f"打不开「{name}」:{last or '没有找到这个设备'}")

    # ------------------------------------------------------------ 开关
    def start(self):
        with self.ctl:
            self.stop()
            self.apply_config()
            self.error = self.warning = None
            if sd is None:
                return self._fail(f"缺少音频组件 sounddevice / PortAudio:{SD_ERROR}")
            info = self.devices()
            if not info["available"]:
                return self._fail(info["error"])
            cfg = self.get_cfg()
            warns = []
            out_name = cfg.get("out_device") or info["cable"]
            if not out_name:
                return self._fail("没有找到虚拟声卡 VB-CABLE(「CABLE Input」)。它需要你自己到 vb-audio.com/Cable 下载安装"
                                  "(免费,本程序不附带),装完重启电脑")
            if not is_cable_out(out_name) and is_virtual(out_name) is False:
                warns.append(f"「{out_name}」看起来不是虚拟声卡,队友可能听不到;一般应该选「CABLE Input」")
            mic_name = cfg.get("mic_device") or info["default_input"]
            if mic_name and is_virtual(mic_name):
                warns.append(f"麦克风选成了虚拟设备「{mic_name}」,会自己录到自己形成回声,已停用麦克风。"
                             "请在下面选你真实的麦克风")
                mic_name = None
            self.reset_meters()
            try:
                self.out_stream, self.sr = self._open_output(out_name)
            except Exception as e:
                return self._fail(str(e))
            if mic_name:
                try:
                    self.in_stream, _ = self._open_input(mic_name)
                except Exception as e:
                    warns.append(f"麦克风打不开,先只发送音乐:{e}")
                    self.in_stream = None
            try:
                if self.in_stream:
                    self.in_stream.start()
                self.out_stream.start()
            except Exception as e:
                self.stop()
                return self._fail(f"启动音频流失败:{e}")
            self.mic_name = mic_name if self.in_stream else None
            self.out_name = out_name
            self.warning = ";".join(warns) or None
            self.status = "on"
            self.log(f"[语音](实验性功能)已开启:麦克风「{self.mic_name or '无'}」+ 音乐 → 「{out_name}」({self.sr} Hz)")
            if self.warning:
                self.log("[语音] " + self.warning)
            return True

    def _open_input(self, name):
        last = None
        for idx, extra in self._candidates(name, "input"):
            try:
                s = sd.InputStream(device=idx, samplerate=self.sr, channels=1, dtype="float32", blocksize=0,
                                   latency="low", extra_settings=extra, callback=self._in_cb)
                return s, self.sr
            except Exception as e:
                last = e
        raise RuntimeError(f"打不开「{name}」:{last or '没有找到这个设备'}")

    def _fail(self, msg):
        self.status, self.error = "error", msg
        self.log("[语音] " + msg)
        return False

    def stop(self):
        """关掉音频流。状态回到 off(之前的报错也清掉)。"""
        with self.ctl:
            for s in (self.in_stream, self.out_stream):
                if s is not None:
                    try:
                        s.stop()
                        s.close()
                    except Exception:
                        pass
            self.in_stream = self.out_stream = None
            with self.lock:
                self.voices = []
            with self.ring_lock:
                self.ring.clear()
                self.ring_frames = 0
            if self.status == "on":
                self.log("[语音] 已关闭")
            self.status, self.error = "off", None
            self.mic_name = self.out_name = None

    # ------------------------------------------------------------ 音乐(由 player 调用)
    def _pcm(self, sound, channels):
        pcm = self.pcm_cache.get(sound)
        if pcm is None:
            a = np.frombuffer(sound.get_raw(), dtype=np.int16)
            if channels > 1:
                a = a[: len(a) // channels * channels].reshape(-1, channels)
                pcm = ((a[:, 0].astype(np.int32) + a[:, 1]) >> 1).astype(np.int16)    # 混成单声道
            else:
                pcm = a.copy()
            self.pcm_cache[sound] = pcm
        return pcm

    def music_start(self, event, clip, sound, src_rate, channels, test=False, offset=0.0):
        """本地开始播放一个站点的音乐时调用。只有开启了、且这个站点被选中时才送给队友。"""
        if self.status != "on" or (not test and event not in self.p_events):
            return False
        try:
            pcm = self._pcm(sound, channels)
        except Exception as e:
            self.log(f"[语音] 读取音乐数据失败:{e}")
            return False
        v = MusicVoice(pcm, src_rate, self.sr, gain=float(clip.get("volume", 0.8)),
                       loop=bool(clip.get("loop")), fade_in=float(clip.get("fade_in", 0.2)),
                       fade_out=float(clip.get("fade_out", 1.0)), tag="test" if test else event,
                       max_seconds=TEST_SECONDS if test else None, offset=offset)
        with self.lock:
            self.voices.append(v)
        return True

    def music_fade(self, seconds=None):
        """本地切歌 / 停止时调用:送给队友的那份也一起淡出。"""
        with self.lock:
            for v in self.voices:
                v.fade(seconds)

    # ------------------------------------------------------------ 音频回调
    def _in_cb(self, indata, frames, time_info, status):
        x = indata[:, 0].astype(np.float64)
        with self.ring_lock:
            self.ring.append(x)
            self.ring_frames += len(x)
            self.mic_started = True
            if self.ring_frames > RING_MAX:        # 两个设备时钟不一致,缓冲越积越多:丢掉旧的
                while self.ring_frames > RING_TARGET and self.ring:
                    self.ring_frames -= len(self.ring.popleft())
                self.overruns += 1

    def _take_mic(self, frames):
        out = np.zeros(frames, dtype=np.float64)
        got = 0
        with self.ring_lock:
            while got < frames and self.ring:
                chunk = self.ring[0]
                n = min(frames - got, len(chunk))
                out[got:got + n] = chunk[:n]
                if n == len(chunk):
                    self.ring.popleft()
                else:
                    self.ring[0] = chunk[n:]
                self.ring_frames -= n
                got += n
            if got < frames and self.mic_started and self.in_stream is not None:
                self.underruns += 1
        return out

    def _out_cb(self, outdata, frames, time_info, status):
        now = time.perf_counter()
        if self.cb_last:
            self.cb_gap_max = max(self.cb_gap_max, now - self.cb_last)
        self.cb_last = now
        self.cb_count += 1
        if status:
            self.cb_status += 1
        try:
            y = self.process(self._take_mic(frames), frames)
            outdata[:] = y.astype(np.float32)[:, None]
        except Exception as e:                  # 回调里绝不能抛异常,出问题就输出静音
            outdata.fill(0)
            self.cb_errors += 1
            self.last_cb_error = repr(e)

    def process(self, mic, frames):
        """混音核心(纯计算,单元测试直接调用):麦克风 + 音乐 → 送给队友的声音。"""
        sr = self.sr
        mic = mic * self.p_mic_gain
        db = level_db(mic)
        fall = 20.0 * frames / sr                         # 音量表每秒下落 20 dB
        self.mic_db = max(db, self.mic_db - fall)
        if db > self.p_threshold:
            self.hold_until = self.samples + int(HOLD_S * sr)
        self.speaking = self.samples < self.hold_until

        def ramp(cur, target, attack, release):
            tau = attack if target < cur else release
            new = target + (cur - target) * math.exp(-frames / (tau * sr))
            return new, np.linspace(cur, new, frames)

        if self.p_gate:
            self.gate, gate_r = ramp(self.gate, 1.0 if self.speaking else 0.0, GATE_RELEASE_S, GATE_ATTACK_S)
            mic = mic * gate_r
        duck_target = self.p_duck if (self.p_duck_on and self.speaking) else 1.0
        self.duck, duck_r = ramp(self.duck, duck_target, DUCK_ATTACK_S, DUCK_RELEASE_S)

        music = np.zeros(frames, dtype=np.float64)
        with self.lock:
            if self.voices:
                for v in self.voices:
                    music += v.render(frames)
                self.voices = [v for v in self.voices if not v.done]
        music *= self.p_music * duck_r
        self.music_db = max(level_db(music), self.music_db - fall)
        y = softclip(mic + music)
        self.out_db = max(level_db(y), self.out_db - fall)
        self.samples += frames
        return y

    # ------------------------------------------------------------ 给面板看的状态
    def meters(self):
        with self.ring_lock:
            buffered = self.ring_frames
        latency = None
        try:
            if self.out_stream is not None:
                latency = (self.out_stream.latency + (self.in_stream.latency if self.in_stream else 0)
                           + buffered / self.sr) * 1000
        except Exception:
            pass
        with self.lock:
            sending = [v.tag for v in self.voices if not v.done]
        return {"status": self.status, "error": self.error, "warning": self.warning,
                "mic": self.mic_name, "out": self.out_name, "rate": self.sr,
                "mic_db": round(self.mic_db, 1), "music_db": round(self.music_db, 1),
                "out_db": round(self.out_db, 1), "speaking": self.speaking,
                "duck_db": round(self.duck_db, 1),
                "threshold_db": self.p_threshold, "sending": sending,
                "latency_ms": None if latency is None else round(latency),
                "underruns": self.underruns, "overruns": self.overruns,
                "cb_errors": self.cb_errors, "last_cb_error": self.last_cb_error,
                "cb_count": self.cb_count, "cb_status": self.cb_status,
                "cb_gap_ms": round(self.cb_gap_max * 1000, 1), "time": time.time()}
