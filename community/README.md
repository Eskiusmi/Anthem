# CS2 音乐盒 · 社区服务器

让大家分享和导入音乐盒“配方”。服务器只保存配方(每个站点用哪首歌、从几秒切、切多长、淡入淡出)和声音指纹(一串响度数字,无法还原成音乐),**不保存任何音频**。

- 账号:邮箱 + 密码,注册后直接可用(不发验证邮件、不做两步验证)
- 技术:Python + FastAPI,一个文件 `server.py`;数据存 SQLite(默认)或 PostgreSQL(设置 `DATABASE_URL`)
- 密码用 scrypt 加盐哈希存储;登录令牌只存哈希;公开列表里不显示邮箱
- 防滥用:登录连续失败锁定 15 分钟、同一 IP 每小时最多注册 5 个、每人每天最多发布 30 次、举报功能

## 1. 先在自己电脑 / 局域网试用

```bash
cd community
pip install -r requirements.txt
python server.py            # 默认监听 0.0.0.0:8787
```

本机在音乐盒程序「设置 › 社区服务器」里填 `127.0.0.1:8787`;同一个局域网里的朋友填 `你的局域网IP:8787`(Windows 防火墙可能要放行 8787 端口)。

## 2. 部署到公网

**必须满足两点**:
1. **HTTPS**:不然登录密码会明文传输。下面的方式都会自动提供 HTTPS。
2. **持久化存储**:要么把数据库文件 `/data/community.db` 放在不会随重启清空的磁盘上,要么用外部 PostgreSQL(设置 `DATABASE_URL`)。否则每次重启/重新部署数据都会丢。打开服务器首页会显示当前用的存储方式,有问题会标红警告。

### 部署到 Render(推荐,有一键配置)

项目根目录的 `render.yaml` 是 Render 的一键部署配置(Blueprint),**完全免费**的方案:Render 免费网页服务 + Neon 免费 PostgreSQL。
一步步的图文说明见 **[RENDER.md](RENDER.md)**。

### 方式 A:自己的 VPS(Docker + Caddy 自动 HTTPS)

```bash
cd community
docker build -t mb-community .
docker run -d --name mb-community --restart unless-stopped \
  -p 127.0.0.1:8787:8787 -v mb-data:/data \
  -e MB_ADMIN_EMAILS=你的邮箱@example.com \
  mb-community
```

Caddy 配置(`/etc/caddy/Caddyfile`,域名先解析到 VPS):

```
mb.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

### 方式 B:其他云平台(Fly.io / Railway 等)

用本文件夹的 `Dockerfile` 创建一个 Docker 服务即可,平台会提供 `PORT` 环境变量和 HTTPS。注意:
- 存储二选一:挂一块**持久化磁盘 / Volume 到 `/data`**;或者设置 `DATABASE_URL` 连一个 PostgreSQL(免费套餐没有持久化磁盘时用这个)。
- 用 SQLite 时只跑**一个实例**(SQLite 不适合多实例同时写);用 PostgreSQL 没有这个限制。
- 设置环境变量 `MB_ADMIN_EMAILS`。
- 平台的健康检查路径填 `/api/ping`(不访问数据库,不会把免费数据库一直唤醒)。

部署好后打开 `https://你的域名/api/health`,看到 `"ok": true` 就成功了,`storage` 字段显示用的是哪种存储。

## 3. 环境变量

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 设置后改用 PostgreSQL,例如 `postgresql://用户:密码@主机/库名?sslmode=require`(Neon、Supabase、Render Postgres 等都行)。不设置就用 SQLite |
| `MB_DB` | SQLite 数据库路径(没设置 `DATABASE_URL` 时才用)。Docker 里默认 `/data/community.db`,直接运行默认 `./data/community.db` |
| `MB_ADMIN_EMAILS` | 管理员邮箱,逗号分隔。管理员在程序里能删除任何音乐盒,也能查看举报 |
| `MB_TRUST_PROXY` | 部署在反向代理 / 云平台后面时设为 `1`(Docker 镜像里默认就是 1),用 `X-Forwarded-For` 识别真实 IP 来限流。**直接暴露在公网、前面没有代理时设为 0**,否则别人可以伪造 IP 绕过限流 |
| `PORT` | 监听端口,默认 8787 |

## 4. 管理

- **看举报**:用管理员账号登录后,`GET /api/admin/reports`(带 `Authorization: Bearer 令牌`)。也可以直接查数据库:`sqlite3 community.db "select * from reports"`
- **删除音乐盒**:管理员在程序的社区页面打开任意音乐盒,会多一个「删除(管理员)」按钮
- **备份**:SQLite 用 `sqlite3 /data/community.db ".backup backup.db"`(运行中也可以安全备份);PostgreSQL 用 `pg_dump "$DATABASE_URL" > backup.sql`
- **忘记密码**:目前没有邮件找回功能(因为不发邮件)。需要的话由管理员在数据库里删除该用户,让他重新注册

## 5. 测试

```bash
python test_server.py      # 用临时 SQLite 数据库跑 46 项接口测试,不影响正式数据
TEST_DATABASE_URL=postgresql://... python test_server.py   # 对 PostgreSQL 跑同样的测试(会清空那个库,只能用测试库!)
```
