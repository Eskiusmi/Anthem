"""本地 HTTP 服务:接收 CS2 的 GSI 推送 + 控制面板 API。只监听 127.0.0.1。"""
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from config import AUDIO_EXTS, MUSIC_DIR, RES_DIR, VERSION

WEB_DIR = os.path.join(RES_DIR, "web")
MAX_UPLOAD = 200 * 1024 * 1024


def safe_name(name):
    name = os.path.basename(unquote(name or "")).strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    if not name or name.startswith("."):
        return None
    if os.path.splitext(name)[1].lower() not in AUDIO_EXTS:
        return None
    return name


def make_handler(app):
    port = app.config["port"]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "CS2MusicBox/" + VERSION

        def log_message(self, fmt, *args):  # 关闭默认访问日志
            pass

        def handle(self):
            # 浏览器切歌/拖动进度时会中途断开音频下载,这是正常现象,不打印报错
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

        # -------------------------------------------------- 工具
        def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _body(self, limit=2 * 1024 * 1024):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("请求过大")
            return self.rfile.read(n) if n else b""

        def _host_ok(self):
            # 防 DNS 重绑定:只接受本机地址访问
            return self.headers.get("Host", "") in allowed_hosts

        def _api_ok(self):
            # 自定义请求头会触发浏览器 CORS 预检,别的网站无法跨域调用这些接口
            return self._host_ok() and self.headers.get("X-MusicBox") == "1"

        # -------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._file(os.path.join(WEB_DIR, "index.html"))
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/analysis":
                return self._json(app.analysis)
            if path.startswith("/music/"):
                name = safe_name(path[len("/music/"):])
                if not name:
                    return self._send(404, b"not found", "text/plain")
                return self._media(os.path.join(MUSIC_DIR, name))
            self._send(404, b"not found", "text/plain")

        def _media(self, fp):
            """音频文件:支持 HTTP Range,浏览器才能直接跳到歌曲中间试听。"""
            if not os.path.isfile(fp):
                return self._send(404, b"not found", "text/plain")
            size = os.path.getsize(fp)
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            rng = self.headers.get("Range", "")
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._stream(fp, 0, size - 1, size, 200, ctype)
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:                                   # bytes=-N:最后 N 个字节
                start, end = max(0, size - int(m.group(2))), size - 1
            if start >= size or start > end:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            self._stream(fp, start, end, size, 206, ctype)

        def _stream(self, fp, start, end, size, code, ctype):
            """分块发送文件的 [start, end] 字节,不把整首歌读进内存。"""
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(fp, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _file(self, fp):
            if not os.path.isfile(fp):
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, ctype)

        # -------------------------------------------------- POST
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if path == "/gsi":
                    return self._gsi()
                if not self._api_ok():
                    return self._json({"ok": False, "error": "forbidden"}, 403)
                if path == "/api/upload":
                    return self._upload()
                data = json.loads(self._body() or b"{}")
                handler = {
                    "/api/config": app.api_save_config,
                    "/api/preview": app.api_preview,
                    "/api/stop": app.api_stop,
                    "/api/delete": app.api_delete,
                    "/api/install_cfg": app.api_install_cfg,
                    "/api/volume": app.api_volume,
                    "/api/analyze": app.api_analyze,
                    "/api/song_info": app.api_song_info,
                    "/api/cm/state": app.api_cm_state,
                    "/api/cm/url": app.api_cm_url,
                    "/api/cm/wake": app.api_cm_wake,
                    "/api/cm/auth": app.api_cm_auth,
                    "/api/cm/logout": app.api_cm_logout,
                    "/api/cm/list": app.api_cm_list,
                    "/api/cm/kit": app.api_cm_kit,
                    "/api/cm/like": app.api_cm_like,
                    "/api/cm/delete": app.api_cm_delete,
                    "/api/cm/report": app.api_cm_report,
                    "/api/cm/import": app.api_cm_import,
                    "/api/cm/undo": app.api_cm_undo,
                    "/api/cm/backup": app.api_cm_backup,
                    "/api/cm/prepare": app.api_cm_prepare,
                    "/api/cm/publish": app.api_cm_publish,
                    "/api/voice/devices": app.api_voice_devices,
                    "/api/voice/config": app.api_voice_config,
                    "/api/voice/meters": app.api_voice_meters,
                    "/api/voice/test": app.api_voice_test,
                    "/api/guide": app.api_guide,
                    "/api/demo": app.api_demo,
                }.get(path)
                if not handler:
                    return self._json({"ok": False, "error": "not found"}, 404)
                return self._json(handler(data))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:  # 不让异常打断服务
                app.log(f"[服务] {path} 出错:{e!r}")
                return self._json({"ok": False, "error": str(e)}, 500)

        def _gsi(self):
            try:
                data = json.loads(self._body(limit=4 * 1024 * 1024) or b"{}")
            except ValueError:
                return self._send(400, b"bad json", "text/plain")
            token = ((data.get("auth") or {}).get("token")) if isinstance(data, dict) else None
            if token != app.config["token"]:
                return self._send(403, b"bad token", "text/plain")
            self._send(200, b"", "text/plain")   # 先回 200,GSI 要求尽快响应
            app.on_gsi(data)

        def _upload(self):
            name = safe_name(self.headers.get("X-Filename", ""))
            if not name:
                raise ValueError("只支持 " + " / ".join(AUDIO_EXTS) + " 文件")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_UPLOAD:
                raise ValueError("文件为空或超过 200MB")
            os.makedirs(MUSIC_DIR, exist_ok=True)
            if self.headers.get("X-Unique") == "1":       # 剪辑台导出的片段:重名时自动加序号
                base, ext = os.path.splitext(name)
                k = 2
                while os.path.exists(os.path.join(MUSIC_DIR, name)):
                    name = f"{base} ({k}){ext}"
                    k += 1
            dest = os.path.join(MUSIC_DIR, name)
            tmp = dest + ".part"
            remaining = n
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                os.remove(tmp)
                raise ValueError("上传中断")
            os.replace(tmp, dest)
            origin = self.headers.get("X-Clip-Origin")      # 剪辑台导出的片段:记录它来自哪首歌的哪一段
            if origin:
                try:
                    app.on_clip_saved(name, json.loads(unquote(origin)))
                except ValueError:
                    pass
            app.log(f"[音乐库] 已导入 {name}")
            app.on_library_changed()
            return self._json({"ok": True, "file": name})

    return Handler


def serve(app, host="127.0.0.1"):
    httpd = ThreadingHTTPServer((host, app.config["port"]), make_handler(app))
    httpd.daemon_threads = True
    return httpd
