"""音频播放:把每个事件的片段预先解码切好,播放时交叉淡入淡出。"""
import os
import threading
import time
from array import array

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# SDL 默认会接管 SIGINT/SIGTERM,导致 Ctrl+C 无法退出程序;关掉它
os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")
import pygame  # noqa: E402  (pygame-ce 同样以 pygame 名称导入)


class Player:
    def __init__(self, music_dir, log=print):
        self.music_dir = music_dir
        self.log = log
        self.lock = threading.RLock()
        self.cache = {}        # key -> (Sound, seconds)
        self.current = None    # 当前播放信息
        self.master = 0.7
        self.tap = None        # 队友语音(voicebus.VoiceBus):本地播放的同时把同一段音乐混给队友
        self.ok = False
        self.error = None
        try:
            pygame.mixer.pre_init(44100, -16, 2, 1024)
            pygame.mixer.init()
            pygame.mixer.set_num_channels(8)
            self.freq, self.fmt, self.chans = pygame.mixer.get_init()
            self.frame = self.chans * abs(self.fmt) // 8
            self.ok = True
        except Exception as e:  # 没有声卡等情况
            self.error = str(e)
            self.log(f"[音频] 初始化失败,将以静音模式运行:{e}")

    # ------------------------------------------------------------ 片段构建
    def _path(self, name):
        return os.path.join(self.music_dir, os.path.basename(name))

    def _key(self, clip):
        path = self._path(clip["file"])
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        return (clip["file"], mtime, round(clip["start"], 3), round(clip["duration"], 3),
                round(clip["fade_out"], 3), bool(clip["loop"]))

    def _envelope(self, data, fin, fout):
        """在 PCM 数据两端加线性淡入/淡出,避免切片处的爆音。"""
        a = array("h")
        a.frombytes(data)
        ch = self.chans
        n = len(a) // ch
        fin = min(fin, n // 2)
        fout = min(fout, n - fin)
        for i in range(fin):
            g = i / fin
            base = i * ch
            for c in range(ch):
                a[base + c] = int(a[base + c] * g)
        for i in range(fout):
            g = i / fout
            base = (n - 1 - i) * ch
            for c in range(ch):
                a[base + c] = int(a[base + c] * g)
        return a.tobytes()

    def _build(self, clip):
        path = self._path(clip["file"])
        if not os.path.isfile(path):
            raise FileNotFoundError(f"找不到音乐文件:{clip['file']}")
        full = pygame.mixer.Sound(path)
        raw = full.get_raw()
        del full
        total = len(raw) // self.frame
        s = int(clip["start"] * self.freq)
        if s >= total:
            raise ValueError(f"起始时间 {clip['start']}s 超过曲目长度 {total / self.freq:.1f}s")
        e = total if clip["duration"] <= 0 else min(total, s + int(clip["duration"] * self.freq))
        seg = raw[s * self.frame:e * self.frame]
        del raw
        if self.fmt == -16:
            anti_click = int(0.01 * self.freq)
            fout = anti_click if clip["loop"] else max(anti_click, int(clip["fade_out"] * self.freq))
            seg = self._envelope(seg, anti_click, fout)
        return pygame.mixer.Sound(buffer=seg), (e - s) / self.freq

    def _get(self, clip):
        key = self._key(clip)
        with self.lock:
            hit = self.cache.get(key)
        if hit:
            return hit
        built = self._build(clip)
        with self.lock:
            self.cache[key] = built
        return built

    def preload(self, clips):
        """后台预解码所有已配置的片段,并清理不再使用的缓存。"""
        if not self.ok:
            return

        def work():
            keys = set()
            for name, clip in clips.items():
                if not clip.get("enabled") or not clip.get("file"):
                    continue
                try:
                    self._get(clip)
                    keys.add(self._key(clip))
                except Exception as e:
                    self.log(f"[音频] 预加载「{name}」失败:{e}")
            with self.lock:
                for k in list(self.cache):
                    if k not in keys:
                        del self.cache[k]
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 播放控制
    def play(self, event, clip, priority=0, mirror=True):
        """mirror=False:只在本地播(面板里的试听不会送给队友)。"""
        if not self.ok:
            return False
        try:
            snd, secs = self._get(clip)
        except Exception as e:
            self.log(f"[音频] 「{event}」无法播放:{e}")
            return False
        with self.lock:
            self._fade_current()
            ch = pygame.mixer.find_channel(True)
            ch.set_volume(max(0.0, min(1.0, clip["volume"] * self.master)))
            ch.play(snd, loops=-1 if clip["loop"] else 0, fade_ms=int(clip["fade_in"] * 1000))
            self.current = {"event": event, "priority": priority, "channel": ch, "sound": snd,
                            "clip": clip, "started": time.monotonic(), "length": secs}
            if mirror and self.tap is not None:
                self.tap.music_start(event, clip, snd, self.freq, self.chans)
        return True

    def resync_tap(self):
        """刚打开队友语音时,本地正在放的那首也接上(从当前位置开始送)。"""
        if self.tap is None:
            return False
        with self.lock:
            cur = self.current
            if not cur or not self.is_busy():
                return False
            elapsed = time.monotonic() - cur["started"]
            clip = dict(cur["clip"], fade_in=0.3)
            return self.tap.music_start(cur["event"], clip, cur["sound"], self.freq, self.chans, offset=elapsed)

    def send_test(self, event, clip):
        """只送给队友、本地不播:用来测试队友听到的效果。"""
        if not self.ok or self.tap is None:
            return False
        snd, _ = self._get(clip)
        return self.tap.music_start(event, clip, snd, self.freq, self.chans, test=True)

    def _fade_current(self, seconds=None):
        if self.tap is not None:
            self.tap.music_fade(seconds)
        cur = self.current
        if cur and self.is_busy():
            ms = int((cur["clip"]["fade_out"] if seconds is None else seconds) * 1000)
            if ms > 0:
                cur["channel"].fadeout(ms)
            else:
                cur["channel"].stop()
        self.current = None

    def stop(self, fade=None):
        if not self.ok:
            return
        with self.lock:
            self._fade_current(fade)

    def is_busy(self):
        cur = self.current
        if not cur:
            return False
        ch = cur["channel"]
        return ch.get_busy() and ch.get_sound() is cur["sound"]

    def set_master(self, v):
        self.master = max(0.0, min(1.0, float(v)))
        with self.lock:
            cur = self.current
            if cur and self.is_busy():
                cur["channel"].set_volume(max(0.0, min(1.0, cur["clip"]["volume"] * self.master)))

    def now_playing(self):
        with self.lock:
            cur = self.current
            if not cur or not self.is_busy():
                return None
            return {"event": cur["event"], "file": cur["clip"]["file"],
                    "elapsed": round(time.monotonic() - cur["started"], 1),
                    "length": round(cur["length"], 1), "loop": cur["clip"]["loop"]}
