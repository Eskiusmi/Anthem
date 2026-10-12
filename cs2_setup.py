"""定位 CS2 的 cfg 目录,写入 GSI 配置和“静音游戏内音乐”配置。

查找方式按 Valve 开发者文档:注册表 SteamPath → steamapps/libraryfolders.vdf
里列出的每个库 → steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg
"""
import os
import re

GSI_FILE = "gamestate_integration_musicbox.cfg"
MUTE_FILE = "musicbox_mute.cfg"
CS2_REL = os.path.join("steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg")


def _steam_roots():
    roots = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                p, _ = winreg.QueryValueEx(k, "SteamPath")
                roots.append(os.path.normpath(p))
        except OSError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, ".steam", "steam"),
                  os.path.join(home, "Library", "Application Support", "Steam")]
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(os.path.abspath(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _libraries(root):
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(m.group(1).replace("\\\\", "\\"))
    except OSError:
        pass
    return libs


def find_cfg_dir(override=""):
    if override:
        return override if os.path.isdir(override) else None
    for root in _steam_roots():
        for lib in _libraries(root):
            d = os.path.join(lib, CS2_REL)
            if os.path.isdir(d):
                return d
    return None


def gsi_text(port, token):
    # 注意:文件不能带 UTF-8 BOM,否则 CS2 不会加载(Valve 文档说明)
    return f'''"CS2 MusicBox"
{{
 "uri" "http://127.0.0.1:{port}/gsi"
 "timeout" "5.0"
 "buffer"  "0.0"
 "throttle" "0.1"
 "heartbeat" "10.0"
 "auth"
 {{
   "token" "{token}"
 }}
 "data"
 {{
   "provider"            "1"
   "map"                 "1"
   "round"               "1"
   "player_id"           "1"
   "player_state"        "1"
   "player_match_stats"  "1"
 }}
}}
'''


MUTE_TEXT = """// CS2 MusicBox:把游戏内各类音乐音量设为 0(不影响脚步、枪声等音效)
// 用法:在控制台输入  exec musicbox_mute
// 恢复:到 设置 > 音频 > 音乐 把各项滑块调回你原来的值
snd_menumusic_volume 0
snd_roundstart_volume 0
snd_roundend_volume 0
snd_mapobjective_volume 0
snd_tensecondwarning_volume 0
snd_deathcamera_volume 0
snd_mvp_volume 0
echo "[MusicBox] in-game music muted"
"""


def _write_if_changed(path, text):
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # utf-8 不带 BOM
        f.write(text)
    return True


def install(cfg_dir, port, token):
    """返回 dict:{ok, cfg_dir, gsi_path, changed, error}"""
    if not cfg_dir or not os.path.isdir(cfg_dir):
        return {"ok": False, "cfg_dir": cfg_dir, "error": "没有找到 CS2 的 cfg 目录"}
    try:
        gsi_path = os.path.join(cfg_dir, GSI_FILE)
        changed = _write_if_changed(gsi_path, gsi_text(port, token))
        _write_if_changed(os.path.join(cfg_dir, MUTE_FILE), MUTE_TEXT)
        return {"ok": True, "cfg_dir": cfg_dir, "gsi_path": gsi_path, "changed": changed}
    except OSError as e:
        return {"ok": False, "cfg_dir": cfg_dir, "error": str(e)}
