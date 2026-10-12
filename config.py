"""配置:事件定义、默认值、读写 config.json。"""
import json
import os
import secrets
import sys
import threading

VERSION = "0.8.1"

# 打包成 exe(PyInstaller)后:
#   RES_DIR  = 程序自带的只读资源(网页界面),在 exe 旁边的 _internal 文件夹里
#   APP_DIR  = 用户数据(配置、曲库、分析缓存、社区登录),放在 exe 旁边;
#              那里不能写(比如装在 Program Files)时改放 %LOCALAPPDATA%\CS2MusicBox
# 直接用 python 运行时两者都是源码所在的文件夹,和以前一样。
FROZEN = bool(getattr(sys, "frozen", False))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", SRC_DIR)


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def _data_dir():
    if not FROZEN:
        return SRC_DIR
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if _writable(exe_dir):
        return exe_dir
    d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "CS2MusicBox")
    os.makedirs(d, exist_ok=True)
    return d


APP_DIR = _data_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
MUSIC_DIR = os.path.join(APP_DIR, "music")
AUDIO_EXTS = (".mp3", ".ogg", ".wav", ".flac")

# key, 中文名, 说明, 优先级, 默认循环, 是否“重置型”事件, 默认片段时长(秒, 0=到结尾)
# 重置型事件:无论当前在放什么都会切换;若没配音乐则停止当前音乐。
EVENTS = [
    ("menu",         "主菜单",          "回到主菜单时播放",               10,  True,  True,  0),
    ("warmup",       "热身",            "进入热身阶段",                   10,  True,  True,  0),
    ("freezetime",   "回合开始",        "冻结时间(购买阶段)开始",         15,  False, True,  15),
    ("round_live",   "行动开始",        "冻结时间结束,回合正式开始",       20,  False, False, 10),
    ("death",        "阵亡",            "你阵亡时(死亡视角)",             30,  False, False, 8),
    ("round_10s",    "回合剩余10秒",    "按回合时长估算",                 40,  False, False, 10),
    ("bomb_planted", "炸弹已安放",      "C4 被安放(不分阵营)",            60,  False, False, 30),
    ("bomb_10s",     "炸弹剩余10秒",    "按安放时间估算",                 70,  False, False, 10),
    ("round_lost",   "回合失败",        "本回合我方输",                   80,  False, False, 8),
    ("round_won",    "回合胜利",        "本回合我方赢",                   80,  False, False, 8),
    ("mvp",          "MVP 主题曲",      "你拿到本回合 MVP",               90,  False, False, 12),
    ("match_lost",   "比赛失败",        "整场比赛结束且我方比分落后",      100, False, True,  20),
    ("match_won",    "比赛胜利",        "整场比赛结束且我方比分领先",      100, False, True,  20),
]
EVENT_META = {
    k: {"name": n, "desc": d, "priority": p, "loop": lp, "reset": rs, "duration": du}
    for k, n, d, p, lp, rs, du in EVENTS
}


def default_clip(key):
    m = EVENT_META[key]
    return {
        "enabled": True,
        "file": "",
        "start": 0.0,
        "duration": float(m["duration"]),
        "volume": 0.8,
        "loop": m["loop"],
        "fade_in": 0.2,
        "fade_out": 1.0,
        "priority": m["priority"],
    }


def default_config():
    return {
        "port": 31777,
        "token": secrets.token_hex(12),
        "master_volume": 0.7,
        # 回合时长(秒),按 GSI 的 map.mode 区分;0 表示该模式不提示“回合剩余10秒”
        "round_time": {"default": 115, "competitive": 115, "casual": 135,
                       "scrimcomp2v2": 90, "deathmatch": 0, "gungameprogressive": 0},
        "bomb_time": 40,
        "warning_lead": 10,
        "latency_comp": 0.2,
        "offline_timeout": 35,
        "cs2_cfg_dir": "",
        "open_browser": True,
        "game_process_names": ["cs2.exe", "cs2"],   # 自动检测的游戏进程名(Windows / Linux)
        "community_url": "",          # 社区服务器地址,例如 https://mb.example.com
        "voice": default_voice(),     # 队友语音:麦克风 + 音乐 → 虚拟声卡
        "guide": dict(GUIDE_DEFAULT), # 新手教程进度
        "events": {k: default_clip(k) for k in EVENT_META},
    }


GUIDE_DEFAULT = {"seen": False, "mute_done": False, "demo_done": False, "cs2_ok": False}


def sanitize_guide(g):
    g = g if isinstance(g, dict) else {}
    return {k: bool(g.get(k, v)) for k, v in GUIDE_DEFAULT.items()}


def default_voice():
    return {
        "enabled": False,
        "mic_device": "",             # 你的真实麦克风;空 = 系统默认麦克风
        "out_device": "",             # 送到哪个设备;空 = 自动找 VB-CABLE 的「CABLE Input」
        "mic_gain_db": 0.0,           # 麦克风音量(分贝)
        "music_level": 0.35,          # 队友听到的音乐音量(0–1),再乘每个站点自己的音量
        "duck": True,                 # 你说话时自动压低音乐
        "duck_db": -12.0,
        "threshold_db": -45.0,        # 说话检测阈值(麦克风电平高于它算在说话)
        "gate": False,                # 噪声门:不说话时把麦克风关掉(开放麦时有用)
        "events": [k for k in EVENT_META],   # 哪些站点的音乐送给队友
    }


def sanitize_voice(v, old=None):
    """校验面板提交的队友语音设置。"""
    d = dict(old or default_voice())
    v = v or {}

    def num(name, lo, hi):
        try:
            x = float(v.get(name, d[name]))
        except (TypeError, ValueError):
            x = d[name]
        return max(lo, min(hi, x))

    return {
        "enabled": bool(v.get("enabled", d["enabled"])),
        "mic_device": str(v.get("mic_device", d["mic_device"]) or "")[:200],
        "out_device": str(v.get("out_device", d["out_device"]) or "")[:200],
        "mic_gain_db": num("mic_gain_db", -20, 30),
        "music_level": num("music_level", 0, 1),
        "duck": bool(v.get("duck", d["duck"])),
        "duck_db": num("duck_db", -40, 0),
        "threshold_db": num("threshold_db", -80, -10),
        "gate": bool(v.get("gate", d["gate"])),
        "events": [k for k in EVENT_META if k in set(v.get("events", d["events"]) or [])],
    }


_lock = threading.Lock()


def _merge(base, override):
    """把用户配置合并到默认值上,补齐新版本新增的字段。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def load_config():
    base = default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg = _merge(base, user)
        except (OSError, ValueError) as e:
            print(f"[配置] config.json 读取失败,使用默认值:{e}")
            cfg = base
    else:
        cfg = base
    # 只保留已知事件
    cfg["events"] = {k: _merge(default_clip(k), cfg["events"].get(k, {})) for k in EVENT_META}
    cfg["voice"] = sanitize_voice(cfg.get("voice"))
    cfg["guide"] = sanitize_guide(cfg.get("guide"))
    save_config(cfg)
    return cfg


def save_config(cfg):
    with _lock:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def sanitize_clip(key, clip):
    """校验面板提交的事件配置,数值裁剪到合理范围。"""
    d = default_clip(key)
    out = {}

    def num(name, lo, hi):
        try:
            v = float(clip.get(name, d[name]))
        except (TypeError, ValueError):
            v = d[name]
        return max(lo, min(hi, v))

    out["enabled"] = bool(clip.get("enabled", d["enabled"]))
    out["file"] = os.path.basename(str(clip.get("file", "") or ""))
    out["start"] = num("start", 0, 3600)
    out["duration"] = num("duration", 0, 3600)
    out["volume"] = num("volume", 0, 1)
    out["loop"] = bool(clip.get("loop", d["loop"]))
    out["fade_in"] = num("fade_in", 0, 10)
    out["fade_out"] = num("fade_out", 0, 10)
    out["priority"] = int(num("priority", 0, 1000))
    return out
