"""本地程序的“社区”接口:登录、浏览、导入、分享。作为 App 的混入类使用。"""
import importlib.util
import json
import os
import re
import time

import community_client as cc
import kit_share
from config import APP_DIR, EVENT_META, MUSIC_DIR, save_config

CLIPS_PATH = os.path.join(APP_DIR, "clips.json")
BACKUP_DIR = os.path.join(APP_DIR, "kit_backups")
CLIP_NAME = re.compile(r" 片段 .*\.wav$")


class CommunityMixin:
    # ------------------------------------------------------------ 剪辑片段的来源记录
    def clips(self):
        try:
            with open(CLIPS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_clips(self, d):
        tmp = CLIPS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CLIPS_PATH)

    def on_clip_saved(self, name, origin):
        try:
            o = {"file": os.path.basename(str(origin["file"])), "start": float(origin["start"]),
                 "end": float(origin["end"]), "fade_in": float(origin.get("fade_in", 0)),
                 "fade_out": float(origin.get("fade_out", 0)), "gain_db": float(origin.get("gain_db", 0))}
        except (KeyError, TypeError, ValueError):
            return
        if o["end"] <= o["start"]:
            return
        d = self.clips()
        d[name] = o
        self._save_clips(d)

    def on_file_deleted(self, name):
        d = self.clips()
        if name in d:
            del d[name]
            self._save_clips(d)

    def originals(self):
        """曲库里的原曲(不含剪辑台导出的片段),导入配方时只匹配这些。"""
        clips = self.clips()
        return [f["file"] for f in self.library() if f["file"] not in clips and not CLIP_NAME.search(f["file"])]

    # ------------------------------------------------------------ 分析与时长
    def _analyze_fn(self):
        if importlib.util.find_spec("librosa") is None:
            return None
        import analyzer
        cache = os.path.join(APP_DIR, "analysis_cache")
        return lambda f: analyzer.analyze_cached(os.path.join(MUSIC_DIR, f), cache)[0]

    def _durations(self, files):
        out = {}
        try:
            import soundfile
        except ImportError:
            return out
        for f in files:
            try:
                out[f] = float(soundfile.info(os.path.join(MUSIC_DIR, f)).duration)
            except Exception:
                pass
        return out

    # ------------------------------------------------------------ 账号
    def _acc(self):
        acc = cc.load_account()
        url = self.config.get("community_url", "")
        if acc.get("url") != url:          # 换了服务器,旧的登录作废
            acc = {"url": url}
        return acc

    _cm_last_ok = 0.0          # 上次成功联系上社区服务器的时间

    def _ensure_awake(self, url, force=False):
        """免费主机闲置 15 分钟会休眠。超过 10 分钟没联系过就先确认它醒着(醒着时很快)。"""
        if force or time.time() - self._cm_last_ok > 600:
            health = cc.wake(url)
            self._cm_last_ok = time.time()
            return health
        return None

    def _call(self, method, path, body=None, params=None, auth=True):
        acc = self._acc()
        url, token = acc.get("url", ""), acc.get("token") if auth else None
        if url:
            self._ensure_awake(url)
        try:
            try:
                r = cc.request(url, method, path, body, token, params)
            except cc.ServerWaking as e:
                # 服务器刚好在休眠/重启:等它醒了再试一次。
                # 写操作如果是“等回应超时”,服务器可能已经处理过了,不重发,免得重复发布
                if method != "GET" and e.maybe_processed:
                    raise cc.CommunityError("社区服务器响应太慢,操作可能已经完成,刷新一下看看") from None
                self._ensure_awake(url, force=True)
                r = cc.request(url, method, path, body, token, params)
        except cc.CommunityError as e:
            if "请先登录" in str(e) and acc.get("token"):
                cc.save_account({"url": acc.get("url", "")})        # 令牌过期,清掉
            raise
        self._cm_last_ok = time.time()
        return r

    def api_cm_wake(self, data):
        url = self._acc().get("url", "")
        if not url:
            raise ValueError("还没有设置社区服务器地址")
        self._ensure_awake(url, force=True)      # 打开社区页时总是确认一次(醒着时只要零点几秒)
        return {"ok": True}

    def api_cm_state(self, data):
        acc = self._acc()
        return {"ok": True, "url": acc.get("url", ""), "user": acc.get("user") if acc.get("token") else None}

    def api_cm_url(self, data):
        raw = str(data.get("url") or "").strip()
        url, health = cc.normalize_url(raw), None
        if url:
            # 没写 http(s):// 时先试 https,不通再试 http(自己电脑或局域网里搭的服务器通常是 http)
            tries = [url] if raw.startswith(("http://", "https://")) else [url, "http://" + url[len("https://"):]]
            err = None
            for u in tries:
                try:
                    try:
                        health = cc.request(u, "GET", "/api/health", timeout=8)
                    except cc.ServerWaking:
                        health = cc.wake(u)           # 免费主机在休眠:等它醒(约 1 分钟)
                    url, err = u, None
                    break
                except cc.CommunityError as e:
                    err = e
            if err:
                if "返回错误 404" in str(err) or "不是音乐盒社区" in str(err):
                    raise ValueError("这个地址不是音乐盒社区服务器")
                raise err
            if health.get("service") != "cs2-musicbox-community":
                raise ValueError("这个地址不是音乐盒社区服务器")
        self.config["community_url"] = url
        save_config(self.config)
        self._cm_last_ok = time.time() if url else 0.0
        self.log(f"[社区] 服务器地址:{url or '(未设置)'}")
        warning = None
        if health and health.get("persistent") is False:
            warning = "注意:这个服务器的数据库放在临时磁盘上,服务器重启或休眠后账号和音乐盒都会丢失。请服务器管理员挂载持久磁盘或改用 PostgreSQL。"
            self.log("[社区] " + warning)
        return {"ok": True, "url": url, "health": health, "warning": warning}

    def api_cm_auth(self, data):
        mode = data.get("mode")
        body = {"email": str(data.get("email", "")).strip(), "password": str(data.get("password", ""))}
        if mode == "register":
            body["name"] = str(data.get("name", "")).strip()
        elif mode != "login":
            raise ValueError("未知操作")
        r = self._call("POST", f"/api/auth/{mode}", body, auth=False)
        cc.save_account({"url": self.config.get("community_url", ""), "token": r["token"], "user": r["user"]})
        self.log(f"[社区] 已{'注册并' if mode == 'register' else ''}登录:{r['user']['name']}")
        return {"ok": True, "user": r["user"]}

    def api_cm_logout(self, data):
        try:
            self._call("POST", "/api/auth/logout")
        except cc.CommunityError:
            pass
        cc.save_account({"url": self.config.get("community_url", "")})
        return {"ok": True}

    # ------------------------------------------------------------ 浏览
    def api_cm_list(self, data):
        params = {"sort": data.get("sort", "new"), "q": data.get("q", ""), "page": int(data.get("page") or 1)}
        if data.get("mine"):
            params["mine"] = "true"
        return {"ok": True, **self._call("GET", "/api/kits", params=params)}

    def api_cm_kit(self, data):
        kit = self._call("GET", f"/api/kits/{int(data.get('id'))}")
        lib = self.originals()
        matches = kit_share.match_songs(kit["data"]["songs"], lib, self._analyze_fn(), self._durations(lib))
        for s in kit["data"]["songs"]:
            s.pop("fp", None)                   # 指纹不发给网页,太大也没用
        return {"ok": True, "kit": kit, "matches": matches, "library": lib}

    def api_cm_like(self, data):
        return {"ok": True, **self._call("POST", f"/api/kits/{int(data.get('id'))}/like")}

    def api_cm_delete(self, data):
        self._call("DELETE", f"/api/kits/{int(data.get('id'))}")
        return {"ok": True}

    def api_cm_report(self, data):
        self._call("POST", f"/api/kits/{int(data.get('id'))}/report", {"reason": str(data.get("reason", ""))})
        return {"ok": True}

    # ------------------------------------------------------------ 导入
    def api_cm_import(self, data):
        kit_id = int(data.get("id"))
        kit = self._call("GET", f"/api/kits/{kit_id}")
        lib = set(self.originals()) | {f["file"] for f in self.library()}
        mapping = {}
        for sid, m in (data.get("mapping") or {}).items():
            f = (m or {}).get("file")
            if f and f in lib:
                mapping[sid] = {"file": f, "offset": max(-600.0, min(600.0, float(m.get("offset") or 0)))}
        if not mapping:
            raise ValueError("没有一首歌对应上本地文件,先在列表里为每首歌选好文件")
        with self.lock:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            with open(os.path.join(BACKUP_DIR, "last.json"), "w", encoding="utf-8") as f:
                json.dump({"time": time.time(), "kit": kit["title"], "events": self.config["events"]},
                          f, ensure_ascii=False, indent=2)
            new, applied, skipped = kit_share.apply_kit(self.config["events"], kit["data"], mapping,
                                                        bool(data.get("replace")))
            self.config["events"] = new
            save_config(self.config)
        self.player.preload(self.config["events"])
        try:
            self._call("POST", f"/api/kits/{kit_id}/download")
        except cc.CommunityError:
            pass
        names = "、".join(EVENT_META[k]["name"] for k in applied)
        self.log(f"[社区] 已导入「{kit['title']}」:{names}")
        return {"ok": True, "applied": applied, "skipped": skipped, "config": self.config}

    def api_cm_undo(self, data):
        p = os.path.join(BACKUP_DIR, "last.json")
        if not os.path.exists(p):
            raise ValueError("没有可以撤销的导入")
        with open(p, "r", encoding="utf-8") as f:
            b = json.load(f)
        with self.lock:
            self.config["events"] = b["events"]
            save_config(self.config)
        os.remove(p)
        self.player.preload(self.config["events"])
        self.log(f"[社区] 已撤销导入「{b.get('kit', '')}」")
        return {"ok": True, "config": self.config}

    def api_cm_backup(self, data):
        p = os.path.join(BACKUP_DIR, "last.json")
        if not os.path.exists(p):
            return {"ok": True, "backup": None}
        with open(p, "r", encoding="utf-8") as f:
            b = json.load(f)
        return {"ok": True, "backup": {"time": b.get("time"), "kit": b.get("kit")}}

    # ------------------------------------------------------------ 分享
    def _recipe(self, meta=None):
        lib = [f["file"] for f in self.library()]
        return kit_share.build_recipe(self.config["events"], self.clips(), set(lib), self._analyze_fn(),
                                      self._durations(lib), meta)

    def api_cm_prepare(self, data):
        songs, events, warnings = self._recipe()
        return {"ok": True, "warnings": warnings,
                "songs": [{"id": s["id"], "file": s["file"], "title": s["title"], "artist": s["artist"],
                           "duration": s["duration"], "has_fp": bool(s["fp"])} for s in songs],
                "events": {k: {"song": e["song"], "start": e["start"], "duration": e["duration"]} for k, e in events.items()}}

    def api_cm_publish(self, data):
        meta = {}
        for f, m in (data.get("meta") or {}).items():
            if isinstance(m, dict):
                meta[os.path.basename(f)] = {"title": str(m.get("title", "")), "artist": str(m.get("artist", ""))}
        songs, events, warnings = self._recipe(meta)
        if not events:
            raise ValueError("还没有可以分享的站点:先给站点配上音乐")
        for s in songs:
            s.pop("file", None)                 # 不把本地文件名发出去
            fp, rate = s.get("fp"), s.get("fp_rate", kit_share.FP_RATE)
            while fp and len(fp) > 7000:        # 超长的歌降低指纹精度
                fp, rate = fp[::2], rate / 2
            s["fp"], s["fp_rate"] = fp, rate
        body = {"title": str(data.get("title", "")).strip(), "description": str(data.get("description", "")).strip(),
                "tags": [str(t) for t in (data.get("tags") or [])][:5], "songs": songs, "events": events}
        kid = data.get("update_id")
        r = self._call("PUT", f"/api/kits/{int(kid)}", body) if kid else self._call("POST", "/api/kits", body)
        self.log(f"[社区] 已{'更新' if kid else '发布'}「{r['title']}」({len(events)} 个站点)")
        return {"ok": True, "kit": r, "warnings": warnings}
