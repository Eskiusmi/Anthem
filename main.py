"""CS2 自定义音乐盒

用法:python main.py(或双击打包好的 CS2MusicBox.exe),然后在浏览器打开 http://127.0.0.1:31777
"""
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque

import cs2_setup
from config import (APP_DIR, AUDIO_EXTS, EVENT_META, FROZEN, MUSIC_DIR, VERSION, load_config,
                    sanitize_clip, sanitize_guide, sanitize_voice, save_config)
from community_api import CommunityMixin
from engine import OFFLINE, Engine
from player import Player
from server import serve
from voicebus import VoiceBus


class App(CommunityMixin):
    def __init__(self):
        os.makedirs(MUSIC_DIR, exist_ok=True)
        self.logs = deque(maxlen=80)
        self.events = deque(maxlen=30)
        self.lock = threading.RLock()
        self.config = load_config()
        self.engine = Engine(lambda: self.config)
        self.player = Player(MUSIC_DIR, self.log)
        self.player.set_master(self.config["master_volume"])
        self.cfg_status = {}
        self.player.preload(self.config["events"])
        self.voice = VoiceBus(lambda: self.config["voice"], self.log)
        self.player.tap = self.voice
        self.proc = {"available": None, "running": None, "pid": None, "since": None, "needs_restart": False}
        self.demo = {"running": False, "i": 0, "n": 0, "step": "", "stop": False}
        self.analysis = {"status": "idle", "progress": "", "done": 0, "total": 0,
                         "error": None, "songs": [], "reco": None, "failed": []}

    # ------------------------------------------------------------ 日志
    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.logs.append(line)

    # ------------------------------------------------------------ 游戏事件
    def on_gsi(self, data, real=True):
        if real:
            if self.demo["running"]:                # 真的游戏数据来了:停掉模拟
                self.demo["stop"] = True
            if self.proc.get("needs_restart"):      # 收到数据就说明配置已生效
                self.proc["needs_restart"] = False
            if not self.config["guide"]["cs2_ok"]:  # 新手教程:“连接 CS2”这一步完成
                self.config["guide"]["cs2_ok"] = True
                save_config(self.config)
        with self.lock:
            evs = self.engine.process(data, time.monotonic())
            self.dispatch(evs)

    def tick_loop(self):
        while True:
            time.sleep(0.1)
            with self.lock:
                evs = self.engine.tick(time.monotonic())
                if OFFLINE in evs:
                    self.log("[游戏] 超过时限没有收到数据,视为游戏已关闭,停止音乐")
                    self.player.stop(1.0)
                    evs = [e for e in evs if e != OFFLINE]
                self.dispatch(evs)

    def dispatch(self, evs):
        """一批事件里选优先级最高、且配置了音乐的那个来播放。"""
        if not evs:
            return
        clips = self.config["events"]
        for e in evs:
            self.events.append({"t": time.strftime("%H:%M:%S"), "event": e,
                                "name": EVENT_META[e]["name"]})
            self.log(f"[事件] {EVENT_META[e]['name']} ({e})")

        ordered = sorted(evs, key=lambda e: clips[e]["priority"], reverse=True)
        playable = [e for e in ordered if clips[e]["enabled"] and clips[e]["file"]]
        resets = [e for e in ordered if EVENT_META[e]["reset"]]

        if not playable:
            if resets:  # 例如回到主菜单但没配菜单音乐 → 停掉上一首
                self.player.stop()
            return

        ev = playable[0]
        clip = clips[ev]
        cur = self.player.current if self.player.is_busy() else None
        if (cur is None or EVENT_META[ev]["reset"]
                or clip["priority"] >= cur["priority"]):
            if self.player.play(ev, clip, clip["priority"]):
                self.log(f"[播放] {EVENT_META[ev]['name']} → {clip['file']}")
        else:
            self.log(f"[跳过] {EVENT_META[ev]['name']} 优先级低于正在播放的"
                     f"「{EVENT_META[cur['event']]['name']}」")

    # ------------------------------------------------------------ 面板 API
    def library(self):
        out = []
        for name in sorted(os.listdir(MUSIC_DIR), key=str.lower):
            fp = os.path.join(MUSIC_DIR, name)
            if os.path.isfile(fp) and os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                out.append({"file": name, "size": os.path.getsize(fp)})
        return out

    def state(self):
        with self.lock:
            snap = self.engine.snapshot(time.monotonic())
        return {
            "game": snap,
            "playing": self.player.now_playing(),
            "audio_ok": self.player.ok,
            "audio_error": self.player.error,
            "events": list(self.events)[::-1],
            "logs": list(self.logs)[-30:],
            "library": self.library(),
            "config": self.config,
            "meta": [{"key": k, **m} for k, m in EVENT_META.items()],
            "cfg_status": self.cfg_status,
            "proc": {**self.proc, "since_ago": None if not self.proc.get("since") else round(time.time() - self.proc["since"], 1)},
            "voice": self._voice_brief(),
            "demo": {k: self.demo[k] for k in ("running", "i", "n", "step")},
            "guide": self.config["guide"],
        }

    def api_save_config(self, data):
        with self.lock:
            return self._save_config(data)

    def _save_config(self, data):
        cfg = self.config
        ev = data.get("events") or {}
        new_events = {k: sanitize_clip(k, ev.get(k, cfg["events"][k])) for k in EVENT_META}
        rt = data.get("round_time")
        if isinstance(rt, dict):
            cfg["round_time"] = {str(k): max(0.0, min(600.0, float(v))) for k, v in rt.items()}
        for key, lo, hi in (("bomb_time", 10, 120), ("warning_lead", 0, 30),
                            ("latency_comp", 0, 3), ("master_volume", 0, 1)):
            if key in data:
                cfg[key] = max(lo, min(hi, float(data[key])))
        if "cs2_cfg_dir" in data:
            cfg["cs2_cfg_dir"] = str(data["cs2_cfg_dir"] or "").strip()
        cfg["events"] = new_events
        save_config(cfg)
        self.player.set_master(cfg["master_volume"])
        self.player.preload(cfg["events"])
        self.log("[配置] 已保存")
        return {"ok": True}

    def api_preview(self, data):
        key = data.get("event")
        if key not in EVENT_META:
            raise ValueError("未知事件")
        clip = sanitize_clip(key, data.get("clip") or self.config["events"][key])
        if not clip["file"]:
            raise ValueError("这个事件还没有选择曲目")
        if not self.player.ok:
            raise ValueError("音频设备不可用:" + str(self.player.error))
        ok = self.player.play(key, clip, 10 ** 6, mirror=False)    # 试听只在本地放,不送给队友
        return {"ok": ok, "error": None if ok else "播放失败,看下方日志"}

    def api_stop(self, data):
        self.player.stop(0.12)
        return {"ok": True}

    def api_volume(self, data):
        v = max(0.0, min(1.0, float(data.get("master_volume", 0.7))))
        self.config["master_volume"] = v
        self.player.set_master(v)
        save_config(self.config)
        return {"ok": True}

    def api_delete(self, data):
        from server import safe_name
        name = safe_name(data.get("file"))
        if not name:
            raise ValueError("文件名不合法")
        fp = os.path.join(MUSIC_DIR, name)
        if os.path.isfile(fp):
            os.remove(fp)
        self.on_file_deleted(name)
        changed = False
        for clip in self.config["events"].values():
            if clip["file"] == name:
                clip["file"] = ""
                changed = True
        if changed:
            save_config(self.config)
        self.on_library_changed()
        self.log(f"[音乐库] 已删除 {name}")
        return {"ok": True}

    def api_install_cfg(self, data):
        if "cs2_cfg_dir" in data:
            self.config["cs2_cfg_dir"] = str(data["cs2_cfg_dir"] or "").strip()
            save_config(self.config)
        self.install_cfg()
        return {"ok": self.cfg_status.get("ok", False), **self.cfg_status}

    # ------------------------------------------------------------ 新手教程 / 模拟一局
    def api_guide(self, data):
        g = dict(self.config["guide"])
        for k in ("seen", "mute_done"):                 # 只有这两项由用户手动标记
            if k in data:
                g[k] = bool(data[k])
        self.config["guide"] = sanitize_guide(g)
        save_config(self.config)
        return {"ok": True, "guide": self.config["guide"]}

    def api_demo(self, data):
        if data.get("action") == "stop":
            self.demo["stop"] = True
            return {"ok": True}
        if self.demo["running"]:
            raise ValueError("已经在模拟了")
        with self.lock:
            in_game = self.engine.snapshot(time.monotonic()).get("connected")
        if self.proc.get("running") or in_game:
            raise ValueError("CS2 正在运行,模拟数据会和真实游戏打架。先退出游戏再试")
        self.demo.update(running=True, i=0, n=0, step="准备中", stop=False)
        speed = max(1.0, min(20.0, float(data.get("speed") or 1)))     # 测试时可以加速
        threading.Thread(target=self._demo_worker, args=(speed,), daemon=True).start()
        return {"ok": True}

    def _demo_worker(self, speed=1.0):
        import gsi_fake
        steps = gsi_fake.demo_script()
        self.demo["n"] = len(steps)
        self.log("[模拟] 开始模拟一局(不需要开游戏)")
        try:
            for i, (label, d, wait) in enumerate(steps):
                if self.demo["stop"]:
                    break
                self.demo.update(i=i + 1, step=label)
                self.log(f"[模拟] {label}")
                self.on_gsi(d, real=False)
                end = time.monotonic() + wait / speed
                while time.monotonic() < end and not self.demo["stop"]:
                    time.sleep(0.1)
        finally:
            finished = not self.demo["stop"]
            with self.lock:
                self.engine.reset()                     # 模拟结束:回到“没在游戏里”的状态
                self.player.stop(1.0)
            self.demo.update(running=False, step="", stop=False)
            if finished and not self.config["guide"]["demo_done"]:
                self.config["guide"]["demo_done"] = True
                save_config(self.config)
            self.log("[模拟] " + ("模拟完成" if finished else "已停止模拟"))

    # ------------------------------------------------------------ 队友语音
    def _voice_brief(self):
        m = self.voice.meters()
        return {"enabled": self.config["voice"]["enabled"], "available": self.voice.available,
                **{k: m[k] for k in ("status", "error", "warning", "sending", "speaking")}}

    def api_voice_devices(self, data):
        return {"ok": True, **self.voice.devices(), "voice": self.config["voice"]}

    def api_voice_config(self, data):
        old = self.config["voice"]
        new = sanitize_voice(data.get("voice"), old)
        self.config["voice"] = new
        save_config(self.config)
        reopen = any(new[k] != old[k] for k in ("enabled", "mic_device", "out_device"))
        if not new["enabled"]:
            self.voice.stop()
        elif reopen or self.voice.status != "on":
            if self.voice.start():                   # 换了设备 / 刚打开:重新打开音频流
                self.player.resync_tap()             # 正在放的音乐也接上
        else:
            self.voice.apply_config()                # 只是调音量、阈值:直接生效
        return {"ok": True, "voice": new, "meters": self.voice.meters()}

    def api_voice_meters(self, data):
        return {"ok": True, **self.voice.meters()}

    def api_voice_test(self, data):
        key = data.get("event")
        if key not in EVENT_META:
            raise ValueError("未知站点")
        if self.voice.status != "on":
            raise ValueError("先开启「把音乐播给队友」")
        clip = self.config["events"][key]
        if not clip["file"]:
            raise ValueError(f"「{EVENT_META[key]['name']}」还没有配音乐")
        if not self.player.send_test(key, clip):
            raise ValueError("发送失败,看日志")
        self.log(f"[语音] 测试:把「{EVENT_META[key]['name']}」的音乐送给队友 8 秒(本地不播放)")
        return {"ok": True}

    # ------------------------------------------------------------ AI 分析
    def api_analyze(self, data):
        if self.analysis["status"] == "running":
            raise ValueError("正在分析中,请稍候")
        import importlib.util
        if importlib.util.find_spec("librosa") is None:   # librosa 较大,到真正分析时才加载
            raise ValueError("AI 分析需要额外依赖,请在程序文件夹运行:pip install librosa(exe 版已自带)")
        lib = {f["file"] for f in self.library()}
        files = [f for f in (data.get("files") or sorted(lib)) if f in lib]
        if not files:
            raise ValueError("音乐库里没有可分析的曲目")
        durations = {}
        for k, v in (data.get("durations") or {}).items():
            if k in EVENT_META:
                try:
                    durations[k] = max(0.0, min(120.0, float(v)))
                except (TypeError, ValueError):
                    pass
        self.analysis = {"status": "running", "progress": "准备中…", "done": 0, "total": len(files),
                         "error": None, "songs": [], "reco": None, "failed": []}
        threading.Thread(target=self._analyze_worker, args=(files, durations), daemon=True).start()
        return {"ok": True}

    def _analyze_worker(self, files, durations):
        import analyzer
        cache_dir = os.path.join(APP_DIR, "analysis_cache")
        t0 = time.time()
        songs, summaries, failed = {}, [], []
        for i, f in enumerate(files):
            self.analysis["progress"] = f"正在分析 {f}({i + 1}/{len(files)})"
            try:
                d, cached = analyzer.analyze_cached(os.path.join(MUSIC_DIR, f), cache_dir)
                songs[f] = d
                summaries.append(analyzer.song_summary(f, d))
                self.log(f"[AI] {f}:{d['tempo']} BPM,{d['key']},{len(d['sections'])} 段"
                         + ("(缓存)" if cached else ""))
            except Exception as e:
                failed.append({"file": f, "error": str(e)})
                self.log(f"[AI] {f} 分析失败:{e}")
            self.analysis["done"] = i + 1
        try:
            reco = analyzer.recommend(songs, durations) if songs else None
            self.analysis.update(status="done", songs=summaries, reco=reco, failed=failed,
                                 progress=f"完成,用时 {time.time() - t0:.1f} 秒",
                                 error=None if songs else "没有曲目分析成功")
        except Exception as e:
            self.analysis.update(status="error", error=str(e), progress="")
            self.log(f"[AI] 推荐失败:{e!r}")

    def api_song_info(self, data):
        """剪辑台用:单首歌的段落、小节线、速度(有缓存时很快)。"""
        import importlib.util
        from server import safe_name
        name = safe_name(data.get("file"))
        if not name or not os.path.isfile(os.path.join(MUSIC_DIR, name)):
            raise ValueError("曲库里没有这个文件")
        if importlib.util.find_spec("librosa") is None:
            return {"ok": False, "error": "未安装 librosa,无法识别段落和节拍"}
        import analyzer
        d, cached = analyzer.analyze_cached(os.path.join(MUSIC_DIR, name), os.path.join(APP_DIR, "analysis_cache"))
        info = analyzer.song_summary(name, d)
        info.update(bars=d["bars"], beat_len=d["beat_len"], bar_len=d["bar_len"])
        return {"ok": True, "info": info, "cached": cached}

    def on_library_changed(self):
        self.player.preload(self.config["events"])

    # ------------------------------------------------------------ 游戏进程检测
    def proc_loop(self):
        import procwatch
        w = procwatch.Watcher(self.config.get("game_process_names"))
        self.proc["available"] = w.available
        if not w.available:
            self.log("[游戏] 没有安装 psutil,无法检测 CS2 进程:退出游戏后最多要等 35 秒音乐才会停。"
                     "在程序文件夹运行 pip install psutil 即可开启")
            return
        self.proc["running"] = False
        while True:
            try:
                ev = w.poll()
                if ev == "started":
                    self.on_game_started(w.info)
                elif ev == "stopped":
                    self.on_game_stopped()
            except Exception as e:                  # 检测出问题不能拖垮整个程序
                self.log(f"[游戏] 进程检测出错:{e!r}")
                time.sleep(4)
            time.sleep(1.0)

    def on_game_started(self, info):
        self.proc.update(running=True, pid=info["pid"], since=time.time(), needs_restart=False)
        self.log(f"[游戏] 检测到 CS2 已启动(进程 {info['pid']})")
        self.check_cfg_for_game(info)

    def check_cfg_for_game(self, info):
        import procwatch
        d = procwatch.cfg_dir_from_exe(info.get("exe"))
        path = self.cfg_status.get("gsi_path") if self.cfg_status.get("ok") else None
        if d:
            gsi = os.path.join(d, cs2_setup.GSI_FILE)
            if not os.path.exists(gsi):
                # 正在运行的这份 CS2 里还没有我们的配置(之前没找到安装目录,或装在别的库里)
                self.config["cs2_cfg_dir"] = d
                save_config(self.config)
                self.log(f"[游戏] 从正在运行的 CS2 找到了安装位置:{d}")
                self.install_cfg()
            path = gsi
        if path and info.get("started") and os.path.exists(path) and os.path.getmtime(path) > info["started"] + 1:
            self.proc["needs_restart"] = True
            self.log("[游戏] GSI 配置是在这次 CS2 启动之后才写入的,需要重启一次 CS2 才能收到游戏数据")

    def on_game_stopped(self):
        with self.lock:
            self.engine.reset()
            self.player.stop(0.4)
        self.proc.update(running=False, pid=None, since=time.time(), needs_restart=False)
        self.log("[游戏] 检测到 CS2 已退出,停止音乐")

    # ------------------------------------------------------------ 安装 GSI 配置
    def install_cfg(self):
        d = cs2_setup.find_cfg_dir(self.config.get("cs2_cfg_dir", ""))
        res = cs2_setup.install(d, self.config["port"], self.config["token"])
        self.cfg_status = res
        if res["ok"]:
            if res["changed"]:
                self.log(f"[CS2] 已写入 GSI 配置:{res['gsi_path']}  → 需要重启 CS2 才生效")
            else:
                self.log(f"[CS2] GSI 配置已存在:{res['gsi_path']}")
        else:
            self.log(f"[CS2] {res['error']}。可在面板里手动填写 cfg 目录")


def already_running(port):
    """同一端口上是不是已经有一个音乐盒在运行(比如又双击了一次 exe)。"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-MusicBox": "1"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return "game" in json.loads(r.read())
    except Exception:
        return False


def pause_if_frozen():
    """exe 是双击打开的控制台窗口,出错时停一下让人看清提示,不然窗口一闪就关了。"""
    if FROZEN and sys.stdin and sys.stdin.isatty():
        try:
            input("\n按回车键关闭窗口…")
        except (EOFError, KeyboardInterrupt):
            pass


def set_console_title(title):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


def fix_console_encoding():
    """日志里有中文。输出被重定向到文件/管道时(不是控制台窗口),英文版 Windows 默认编码写不了中文会直接崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and not stream.isatty() and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main():
    fix_console_encoding()
    set_console_title(f"CS2 音乐盒 {VERSION}")
    port = load_config()["port"]
    url = f"http://127.0.0.1:{port}/"
    no_browser = "--no-browser" in sys.argv
    if already_running(port):
        print(f"CS2 音乐盒已经在运行了,直接打开控制面板:{url}")
        if not no_browser:
            webbrowser.open(url)
        time.sleep(1.5)
        return
    app = App()
    app.log(f"[服务] CS2 音乐盒 {VERSION},数据保存在:{APP_DIR}")
    app.install_cfg()
    try:
        httpd = serve(app)
    except OSError as e:
        print(f"端口 {port} 被占用或无法监听:{e}\n"
              f"可以修改 {os.path.join(APP_DIR, 'config.json')} 里的 port,然后重新运行(GSI 配置会自动更新)。")
        pause_if_frozen()
        sys.exit(1)
    threading.Thread(target=app.tick_loop, daemon=True).start()
    threading.Thread(target=app.proc_loop, daemon=True).start()
    if app.config["voice"]["enabled"]:
        threading.Thread(target=app.voice.start, daemon=True).start()
    app.log(f"[服务] 控制面板:{url}   (关闭这个窗口或按 Ctrl+C 退出)")
    if app.config.get("open_browser", True) and not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.voice.stop()
        app.player.stop(0)
        httpd.server_close()
        print("已退出")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()       # 打包后如果有库用到多进程,子进程不会再把整个程序启动一遍
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        print("\n程序出错退出了。把上面的报错内容发给开发者可以帮助排查。")
        pause_if_frozen()
        sys.exit(1)
