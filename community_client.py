"""本地程序 → 社区服务器 的 HTTP 客户端(只用标准库)。登录令牌保存在本地文件里,不会发给网页。"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from config import APP_DIR, VERSION

ACCOUNT_PATH = os.path.join(APP_DIR, "community_account.json")
WAKE_WAIT = 100            # 免费主机(如 Render 免费版)休眠后唤醒约 1 分钟,最多等这么久


class CommunityError(ValueError):
    pass


class ServerWaking(CommunityError):
    """服务器暂时没准备好(休眠中正在唤醒、重启中)。maybe_processed=True 表示请求可能已经被处理了(超时)。"""

    def __init__(self, msg="社区服务器正在唤醒(免费服务器闲置后会休眠,大约要 1 分钟)", maybe_processed=False):
        super().__init__(msg)
        self.maybe_processed = maybe_processed


def load_account():
    try:
        with open(ACCOUNT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_account(acc):
    tmp = ACCOUNT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(acc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCOUNT_PATH)


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    if not p.netloc:
        raise CommunityError("服务器地址格式不对,例如 https://mb.example.com")
    return url


def request(base, method, path, body=None, token=None, params=None, timeout=15):
    if not base:
        raise CommunityError("还没有设置社区服务器地址(在「设置」页填写)")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "CS2-MusicBox/" + VERSION)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, headers = r.read(), r.headers
    except urllib.error.HTTPError as e:
        if e.code in (502, 503, 504):          # 网关报错:服务正在启动 / 唤醒,请求没有到达程序
            raise ServerWaking() from None
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = None
        if isinstance(detail, list):           # 不是我们的服务器格式时,给个通用说明
            detail = None
        raise CommunityError(detail or f"社区服务器返回错误 {e.code}") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise ServerWaking(maybe_processed=False) from None   # 连接阶段就超时,请求没发出去
        raise CommunityError(f"连不上社区服务器({e.reason})") from None
    except TimeoutError:
        raise ServerWaking(maybe_processed=True) from None        # 等回应时超时,可能是在唤醒
    except OSError as e:
        raise CommunityError(f"连接社区服务器失败({e})") from None
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        # Render 在服务唤醒期间可能先给一个“正在加载”的网页
        if _is_render(base, headers):
            raise ServerWaking() from None
        raise CommunityError("这个地址不是音乐盒社区服务器") from None


def _is_render(base, headers):
    host = urllib.parse.urlparse(base).hostname or ""
    return host.endswith(".onrender.com") or bool(headers and headers.get("rndr-id"))


def wake(base, max_wait=WAKE_WAIT):
    """确认服务器醒着;在休眠中就一直等到它醒(最多 max_wait 秒)。返回 /api/health 的内容。"""
    t0 = time.time()
    while True:
        left = max_wait - (time.time() - t0)
        try:
            return request(base, "GET", "/api/health", timeout=max(5.0, min(30.0, left)))
        except ServerWaking:
            if time.time() - t0 + 3 >= max_wait:
                raise CommunityError("社区服务器一直没有响应,请稍后再试") from None
            time.sleep(3)
