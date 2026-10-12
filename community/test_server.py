"""社区服务器测试。运行:python test_server.py(使用临时 SQLite 数据库,不影响正式数据)

测 PostgreSQL:TEST_DATABASE_URL=postgresql://... python test_server.py
(会清空那个库里的表,只能指向专门的测试库!)
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["MB_DB"] = os.path.join(tmp, "test.db")
PG = os.environ.get("TEST_DATABASE_URL", "")
if PG:
    import psycopg
    with psycopg.connect(PG, autocommit=True) as _c:
        _c.execute("DROP TABLE IF EXISTS reports, downloads, likes, kits, sessions, users CASCADE")
    os.environ["DATABASE_URL"] = PG
os.environ["MB_ADMIN_EMAILS"] = "admin@example.com"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

c = TestClient(server.app)
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("✓ " if cond else "✗ ") + name, extra if not cond else "")
    fails += 0 if cond else 1


def kit(title="我的第一套", **kw):
    k = {"title": title, "description": "EDM 风格", "tags": ["EDM", "燃"],
         "songs": [{"id": "s1", "title": "Demo EDM", "artist": "Tester", "duration": 165.0, "fp": [0.1, 0.5, 0.9]}],
         "events": {"mvp": {"song": "s1", "start": 60.02, "duration": 12, "volume": 0.8},
                    "round_10s": {"song": "s1", "start": 50.02, "duration": 10}}}
    k.update(kw)
    return k


# ---- 注册 / 登录
r = c.post("/api/auth/register", json={"email": "Tim@Example.com", "password": "hunter2hunter", "name": "Tim"})
check("注册成功并直接拿到登录令牌(无需验证)", r.status_code == 200 and r.json()["token"], r.text)
tim = {"Authorization": "Bearer " + r.json()["token"]}
check("邮箱统一转小写", r.json()["user"]["email"] == "tim@example.com")
check("同一邮箱(大小写不同)不能重复注册",
      c.post("/api/auth/register", json={"email": "tim@example.COM", "password": "xxxxxxxx", "name": "Tim2"}).status_code == 409)
check("昵称不能重复", c.post("/api/auth/register", json={"email": "a@b.com", "password": "xxxxxxxx", "name": "tim"}).status_code == 409)
check("邮箱格式校验", c.post("/api/auth/register", json={"email": "not-an-email", "password": "xxxxxxxx", "name": "Bob"}).status_code == 400)
check("密码至少 8 位", c.post("/api/auth/register", json={"email": "b@b.com", "password": "123", "name": "Bob"}).status_code == 422)
r = c.post("/api/auth/register", json={"email": "bob@example.com", "password": "bobpassword", "name": "Bob 玩家"})
bob = {"Authorization": "Bearer " + r.json()["token"]}
check("中文昵称可以", r.status_code == 200, r.text)
check("错误密码登录失败", c.post("/api/auth/login", json={"email": "tim@example.com", "password": "wrong"}).status_code == 401)
r = c.post("/api/auth/login", json={"email": "TIM@example.com", "password": "hunter2hunter"})
check("正确登录", r.status_code == 200 and r.json()["user"]["name"] == "Tim")
tim2 = {"Authorization": "Bearer " + r.json()["token"]}
check("/api/me", c.get("/api/me", headers=tim).json()["user"]["name"] == "Tim")
check("没有令牌访问 /api/me 返回 401", c.get("/api/me").status_code == 401)
check("伪造令牌无效", c.get("/api/me", headers={"Authorization": "Bearer abc"}).status_code == 401)
with server.db() as conn:
    row = conn.execute("SELECT pw_hash, pw_salt FROM users WHERE name='Tim'").fetchone()
    tok_rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
check("数据库里不存明文密码", b"hunter2" not in bytes(row["pw_hash"]) and len(row["pw_salt"]) == 16)
check("数据库里不存明文令牌", all(tim["Authorization"][7:] != t[0] for t in tok_rows))

# ---- 发布 / 浏览
r = c.post("/api/kits", json=kit(), headers=tim)
check("发布音乐盒", r.status_code == 200, r.text)
k1 = r.json()["id"]
check("未登录不能发布", c.post("/api/kits", json=kit()).status_code == 401)
bad = kit(events={"mvp": {"song": "s9", "start": 1, "duration": 5}})
check("引用不存在的歌会被拒绝", c.post("/api/kits", json=bad, headers=tim).status_code == 400)
bad = kit(events={"hack": {"song": "s1", "start": 1, "duration": 5}})
check("未知站点会被拒绝", c.post("/api/kits", json=bad, headers=tim).status_code == 400)
bad = kit(); bad["events"]["mvp"]["volume"] = 5
check("数值越界会被拒绝(并返回中文说明)", c.post("/api/kits", json=bad, headers=tim).status_code == 422)
c.post("/api/kits", json=kit("Bob 的悲伤合集", description="全是慢歌", tags=["抒情"]), headers=bob)
lst = c.get("/api/kits").json()
check("列表按最新排序", [k["title"] for k in lst["kits"]] == ["Bob 的悲伤合集", "我的第一套"], [k["title"] for k in lst["kits"]])
check("列表不泄露邮箱", "example.com" not in str(lst))
check("搜索歌名/标签/作者", [k["title"] for k in c.get("/api/kits", params={"q": "抒情"}).json()["kits"]] == ["Bob 的悲伤合集"]
      and len(c.get("/api/kits", params={"q": "tim"}).json()["kits"]) == 1)
d = c.get(f"/api/kits/{k1}").json()
check("详情包含配方和指纹", d["data"]["events"]["mvp"]["start"] == 60.02 and d["data"]["songs"][0]["fp"] == [0.1, 0.5, 0.9])
check("我的音乐盒", [k["title"] for k in c.get("/api/kits", params={"mine": "true"}, headers=bob).json()["kits"]] == ["Bob 的悲伤合集"])

# ---- 点赞 / 下载计数 / 热门排序
r = c.post(f"/api/kits/{k1}/like", headers=bob)
check("点赞", r.json() == {"liked": True, "likes": 1})
check("再点一次取消", c.post(f"/api/kits/{k1}/like", headers=bob).json() == {"liked": False, "likes": 0})
c.post(f"/api/kits/{k1}/like", headers=bob)
check("详情显示我是否点过赞", c.get(f"/api/kits/{k1}", headers=bob).json()["liked"] is True)
c.post(f"/api/kits/{k1}/download", headers=bob); c.post(f"/api/kits/{k1}/download", headers=bob)
check("同一人同一天重复导入只算一次下载", c.get(f"/api/kits/{k1}").json()["downloads"] == 1)
check("热门排序", c.get("/api/kits", params={"sort": "popular"}).json()["kits"][0]["id"] == k1)

# ---- 修改 / 删除 / 权限
check("不能改别人的音乐盒", c.put(f"/api/kits/{k1}", json=kit("改名"), headers=bob).status_code == 403)
check("可以改自己的", c.put(f"/api/kits/{k1}", json=kit("新名字"), headers=tim).json()["title"] == "新名字")
check("不能删别人的", c.delete(f"/api/kits/{k1}", headers=bob).status_code == 403)
check("举报", c.post(f"/api/kits/{k1}/report", json={"reason": "盗用别人的配方"}, headers=bob).status_code == 200)
check("非管理员不能看举报", c.get("/api/admin/reports", headers=bob).status_code == 403)
r = c.post("/api/auth/register", json={"email": "admin@example.com", "password": "adminadmin", "name": "管理员"})
adm = {"Authorization": "Bearer " + r.json()["token"]}
check("管理员能看到举报", len(c.get("/api/admin/reports", headers=adm).json()["reports"]) == 1)
check("管理员可以删除任何音乐盒", c.delete(f"/api/kits/{k1}", headers=adm).status_code == 200
      and c.get(f"/api/kits/{k1}").status_code == 404)

# ---- 改密码 / 退出
check("改密码需要原密码", c.post("/api/auth/password", json={"old_password": "nope", "new_password": "newpassword1"}, headers=tim).status_code == 401)
check("改密码", c.post("/api/auth/password", json={"old_password": "hunter2hunter", "new_password": "newpassword1"}, headers=tim).status_code == 200)
check("改密码后其他设备的登录失效,当前设备保留", c.get("/api/me", headers=tim2).status_code == 401 and c.get("/api/me", headers=tim).status_code == 200)
check("新密码可以登录", c.post("/api/auth/login", json={"email": "tim@example.com", "password": "newpassword1"}).status_code == 200)
c.post("/api/auth/logout", headers=tim)
check("退出后令牌失效", c.get("/api/me", headers=tim).status_code == 401)

# ---- 限流
for _ in range(12):
    c.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrong-password"})
r = c.post("/api/auth/login", json={"email": "bob@example.com", "password": "bobpassword"})
check("连续输错 10 次后暂时锁定(防暴力破解)", r.status_code == 429, r.status_code)
codes = [c.post("/api/auth/register", json={"email": f"x{i}@y.com", "password": "xxxxxxxx", "name": f"user{i}"}).status_code for i in range(6)]
check("同一 IP 一小时最多注册 5 个(本测试前已注册 3 个)", codes.count(429) >= 1, codes)
h = c.get("/api/health").json()
check("健康检查(并报告存储方式)", h["ok"] and h["storage"] == ("postgres" if PG else "sqlite"), h)
check("搜索里的 % 不会变成通配符", c.get("/api/kits", params={"q": "%"}).json()["kits"] == [])
check("搜索昵称不分大小写", len(c.get("/api/kits", params={"q": "BOB"}).json()["kits"]) == 1)
print("存储:", "PostgreSQL" if PG else "SQLite")

print("\n全部通过" if not fails else f"\n{fails} 项未通过")
raise SystemExit(1 if fails else 0)
