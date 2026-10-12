"""检测 CS2 游戏进程的启动和退出。

GSI 只有游戏活着时才会推送数据,游戏一关就没有任何“我退出了”的通知,
只能等心跳超时(最长 35 秒)。所以另外每秒检查一次 cs2.exe 进程还在不在,
退出后一秒内就能停掉音乐。依赖 psutil(没装时自动退回只靠 GSI 超时)。
"""
import os

DEFAULT_NAMES = ["cs2.exe", "cs2"]          # Windows / Linux


class Watcher:
    def __init__(self, names=None):
        self.names = {n.lower() for n in (names or DEFAULT_NAMES)}
        self.proc = None
        self.info = None
        try:
            import psutil
            self.ps = psutil
            self.available = True
        except ImportError:
            self.ps = None
            self.available = False

    def _find(self):
        for p in self.ps.process_iter(["name"]):
            try:
                n = (p.info.get("name") or "").lower()
            except Exception:
                continue
            if n in self.names:
                try:
                    if p.status() == self.ps.STATUS_ZOMBIE:
                        continue
                except self.ps.Error:
                    continue
                return p
        return None

    def _alive(self):
        try:
            return self.proc.is_running() and self.proc.status() != self.ps.STATUS_ZOMBIE
        except self.ps.Error:
            return False

    def poll(self):
        """返回 "started" / "stopped" / None(没有变化)。"""
        if not self.available:
            return None
        if self.proc is not None:
            if self._alive():
                return None
            self.proc = None
            self.info = None
            return "stopped"
        p = self._find()
        if p is None:
            return None
        info = {"pid": p.pid, "name": p.info.get("name"), "exe": "", "started": None}
        try:
            info["exe"] = p.exe() or ""
        except self.ps.Error:
            pass
        try:
            info["started"] = p.create_time()
        except self.ps.Error:
            pass
        self.proc = p
        self.info = info
        return "started"


def cfg_dir_from_exe(exe):
    """从 cs2.exe 的路径找到 cfg 目录:
    ...\\Counter-Strike Global Offensive\\game\\bin\\win64\\cs2.exe → ...\\game\\csgo\\cfg"""
    if not exe:
        return None
    d = os.path.dirname(exe)
    for _ in range(6):
        c = os.path.join(d, "csgo", "cfg")
        if os.path.isdir(c):
            return c
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None
