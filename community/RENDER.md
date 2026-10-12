# 把社区服务器部署到 Render

整个过程大约 15 分钟,**全程免费**,不需要信用卡(Render 免费服务 + Neon 免费数据库)。

## 先了解:为什么还要一个 Neon

Render 免费版的网页服务(Free web service)**没有持久磁盘(persistent disk)**:服务每次重启、重新部署、闲置休眠,容器里的文件都会被清空。社区默认用的 SQLite 是一个文件,放在那里账号和音乐盒很快就会全丢。

所以免费方案把数据存到 **Neon**(一个提供免费 PostgreSQL 云数据库的服务)。社区服务器已经支持:只要设置环境变量 `DATABASE_URL`,就自动改用 PostgreSQL。

| | 免费方案(本文) | 付费方案 |
|---|---|---|
| 费用 | 0 | 约 7.25 美元/月(实例 7 + 1 GB 磁盘 0.25) |
| 闲置休眠 | 15 分钟没人访问就休眠,下次访问约 1 分钟唤醒 | 不休眠 |
| 数据存在 | Neon PostgreSQL | Render 持久磁盘上的 SQLite |

> 免费服务休眠期间,有人打开社区页时,本地程序会显示「社区服务器正在唤醒…」,等它醒来后自动继续,不需要手动重试。

## 第 1 步:把项目放到 GitHub

Render 从 GitHub 仓库(repository)拉代码来部署。仓库可以是**私有(private)**的。

1. 在 [github.com/new](https://github.com/new) 新建一个空仓库,例如 `cs2-musicbox`,选 Private,其他都不勾。
2. 在项目文件夹(有 `main.py` 和 `render.yaml` 的那一层)打开命令行:

```bash
git init
git add .
git commit -m "CS2 音乐盒 v0.7"
git branch -M main
git remote add origin https://github.com/你的用户名/cs2-musicbox.git
git push -u origin main
```

项目里的 `.gitignore` 已经排除了你的个人数据(`config.json`、曲库 `music/`、社区登录令牌 `community_account.json` 等),不会被传上去。不习惯命令行的话,用 [GitHub Desktop](https://desktop.github.com/) 把这个文件夹添加为仓库再 Publish 也一样。

## 第 2 步:在 Neon 建一个免费数据库

1. 打开 [neon.com](https://neon.com) 注册(可以直接用 GitHub 账号登录)。
2. 新建项目(Create project):名字随便填;**区域(Region)选 AWS US West 2 (Oregon)**,和下一步 Render 的 `oregon` 区域在同一个地方,速度最快。
3. 建好后点 **Connect**,复制连接串(connection string),长这样:

```
postgresql://neondb_owner:xxxxxxxx@ep-xxxx-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

这串里有数据库密码,**不要发给别人,也不要写进代码提交到 GitHub**。表结构不用自己建,社区服务器第一次启动时会自动创建。

> Neon 免费额度(以 [官网](https://neon.com/pricing) 为准):每个项目 1 GB 存储、每月 100 CU-小时计算量,闲置 5 分钟自动暂停。社区只存配方和响度指纹,一套音乐盒大约几十到一百多 KB,1 GB 够存几千套。

## 第 3 步:在 Render 用 Blueprint 一键部署

1. 登录 [dashboard.render.com](https://dashboard.render.com),点右上角 **New → Blueprint**。
2. 连接你的 GitHub 账号,选刚才的仓库。Render 会读到根目录的 `render.yaml`,显示要创建的服务 `cs2-musicbox-community`。
3. 它会让你填两个值:
   - `DATABASE_URL`:粘贴第 2 步复制的 Neon 连接串
   - `MB_ADMIN_EMAILS`:你自己准备注册社区用的邮箱(这个邮箱的账号是管理员,能看举报、删除任何音乐盒;多个用逗号隔开)
4. 点 **Deploy Blueprint**(或 Apply),等 2–4 分钟。在服务的 **Logs** 里看到 `Application startup complete` 就好了。

`render.yaml` 里已经配好:用 `community/Dockerfile` 构建、免费套餐、健康检查 `/api/ping`、只有 `community/` 里的代码变了才重新部署(改本地程序不会触发)。

## 第 4 步:检查,然后连上

1. 服务页面顶部有网址,类似 `https://cs2-musicbox-community.onrender.com`(重名时 Render 会加一串后缀)。
2. 浏览器打开它,应该看到「CS2 音乐盒社区服务器正在运行 · 存储:PostgreSQL」,**没有红色警告**。
   再打开 `网址/api/health`,能看到 `"storage":"postgres"`。
3. 本地程序 → **社区** 页 → 填这个网址 → 连接 → 用第 3 步填的管理员邮箱注册。
4. 把网址发给朋友,他们在自己的程序里填同一个网址就能互相分享音乐盒。

## 以后

- **更新社区服务器**:改了 `community/` 里的代码,`git push` 后 Render 自动重新部署。数据在 Neon 里,不受影响。
- **不想等唤醒**:在 Render 把这个服务的 Instance Type 改成付费的 `0.5c-512mb`(7 美元/月)即可,继续用 Neon,不用加磁盘。
- **想换成纯 Render(不用 Neon)**:按 `render.yaml` 末尾的注释改成付费套餐 + 磁盘,并删掉 `DATABASE_URL`。注意这样会换一个新的空数据库。
- **备份**:电脑上装了 PostgreSQL 客户端的话,`pg_dump "Neon连接串" > backup.sql`。
- **免费额度**:Render 免费服务每个工作区每月 750 小时(一个服务全天在线也够),工作区每月含 5 GB 出站流量;超出的话没有绑卡会暂停到下个月。小圈子用远远够。

## 常见问题

| 现象 | 原因和处理 |
|---|---|
| 首页有红色警告「数据库在临时文件系统上」 | 没设置 `DATABASE_URL`。服务页 → Environment → 添加 `DATABASE_URL` → Save(会自动重新部署) |
| Logs 里有 `password authentication failed` 或 `could not translate host name` | 连接串复制错了或不完整。到 Neon 重新复制,在 Environment 里改 |
| 部署一直卡在健康检查失败 | 看 Logs 最后的报错;多半是数据库连不上(同上) |
| 本地程序提示「这个地址不是音乐盒社区服务器」 | 网址填错了,只填 `https://xxx.onrender.com`,后面不要带路径 |
| 第一次打开社区页要等一会 | 正常,免费服务在休眠,约 1 分钟醒来 |
| 换了网址后要重新登录 | 正常,登录是按服务器地址记的 |

参考:[Render 免费服务说明](https://render.com/docs/free) · [Render 持久磁盘](https://render.com/docs/disks) · [Blueprint 格式](https://render.com/docs/blueprint-spec) · [Neon 免费额度](https://neon.com/faqs/free-plan-limits-and-quotas)
