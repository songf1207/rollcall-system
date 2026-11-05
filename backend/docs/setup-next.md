# 接下来操作：在线数据库、代码仓库、部署

当前已完成后端实现、95 个回归测试，以及 TiDB 云数据库接入。云端已建 7 张表（6 张业务表和事务锁表），保留 40 名演示学生，详情见 cloud-validation.md。
Educoder 仓库推送、前端联调及后端公网部署尚未完成；下面第 1、2 节保留为环境重建说明。

## 1. 先让本地可演示

```powershell
Set-Location 'E:\studywork\软工\backend'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe scripts/init_db.py
.\.venv\Scripts\python.exe scripts/generate_teacher_key.py
.\start_backend.cmd
```

打开 http://127.0.0.1:8000/docs，在导入接口上传 samples/sample_roster.xlsx。它是演示名单，不是真实同学信息。
先走“导入→随机点名→记分→改判→排名→导出”，再验证顺序点名及换轮。

## 2. 注册 TiDB Cloud 免费数据库（当前选择）

2026-11-03 查询时，旧方案的 db4free 首页发生无关跳转，未继续访问或提交账号。不要再沿用旧版 README 中针对该域名的注册步骤。

2026-11-04 按用户选择改用 TiDB Cloud Starter 的 MySQL 兼容模式。原 Aiven 实例创建成 Developer-1，切换免费档时页面报错；不继续以该实例作为本项目的部署目标。
TiDB 官方说明：Starter 的 MySQL 兼容模式在 Spending Limit 为 0 时保持免费，超过免费额度会限制使用；无需开通正数付费额度。
来源：[创建实例](https://docs.pingcap.com/tidbcloud/create-tidb-cluster-serverless/?plan=starter)、[额度与限制](https://docs.pingcap.com/tidbcloud/serverless-limitations/)。

1. 从 [TiDB Cloud 控制台](https://tidbcloud.com/) 注册或登录。
2. 进入 My TiDB，点击 Create Resource，套餐选择 Starter，兼容模式选择 MySQL Compatible。
3. 实例名称填写 classroom-rollcall，选择页面当前可用的云和区域。将 Spending Limit 保持为 0，再点击 Create。
4. 实例就绪后点击 Connect，选择 PyMySQL 或 SQLAlchemy，获取实际 Host、Port、完整用户名和数据库名。用户名可能包含前缀，必须完整复制；端口以控制台为准，不假定为 3306。
5. 在连接页生成/设置密码，保存在本地，不发送聊天截图。控制台提供的数据库名若为 test，可以先使用 test。
6. 编辑本地 .env，使用真实连接值。参考 tidb.env.example；该文件只有占位符，不包含账号：

```dotenv
DATABASE_URL=mysql+pymysql://用户名:URL编码后的密码@主机:端口/库名?charset=utf8mb4
DB_SSL=1
AUTO_CREATE_TABLES=1
ALPHA=1.0
CORS_ORIGINS=http://localhost:5173
```

TiDB Starter 的公网连接要求 TLS；现有后端设置 DB_SSL=1 后使用系统信任库并校验主机名。若此前本地 .env 里留有 Aiven 的 DB_SSL_CA，需要删除那一行或设为空，不能沿用旧服务的证书。
若本机遇到 CA 错误，再按照 TiDB 控制台和官方说明配置正确 CA，不能关闭证书校验。
密码包含 @、:、/、#、% 等字符时需 URL 编码，不要把原始密码贴入聊天、截图或 Git。

```powershell
.\.venv\Scripts\python.exe scripts/init_db.py
.\.venv\Scripts\python.exe scripts/check_db.py
.\start_backend.cmd
```

init_db.py 只创建缺失表，不改造已有列。再通过导入 API 上传 Excel，关闭并重启服务，确认学生与积分仍在云端库。
sql/schema.sql 包含 DROP TABLE，已有数据的库不要拿它做日常初始化。

验收证据：云端 7 张表截图（隐藏连接凭据）、check_db 输出、导入记录、重启后的查询结果、导出的五列表格。
连接资料参考 [官方 PyMySQL 示例](https://docs.pingcap.com/developer/dev-guide-sample-application-python-pymysql/)。
当前项目继续使用 mysql+pymysql 驱动；已使用控制台的完整用户名及新密码完成 TLS 1.3 登录，建立独立 classroom_rollcall 业务库，未在 sys 系统库中建业务表。
云端实际建表、Excel 导入、点名、记分、改判、排名、概率、个人曲线、抽签复算及导出均已通过；原始记录见 cloud-validation.md。
需要更新连接密码时，将新密码保存到已忽略的 .tidb-password.txt，再运行 scripts/setup_tidb.py --host 实际主机 --user 完整用户名。脚本会在连接及建表成功后更新 .env；不会删库，原配置备份为 .env.before-tidb。
新服务接通后处理原 Aiven Developer-1 实例的停用；涉及删除时先确认没有需要保留的数据。

## 3. 建立 Educoder 仓库

在作业指定的 [Educoder 代码平台](https://code.educoder.net/projects) 登录并创建项目，复制它显示的 Git 克隆地址。
先和队友约定仓库结构：推荐一个总仓库包含 frontend/ 和 backend/，避免双方各自上传不同版本的接口约定。
当前 backend 的父目录已有 .gitignore 和 render.yaml，尚无 Git 仓库。需要决定总仓库位置后再初始化，避免嵌套 Git。

以下为总仓库位于“软工”目录的示例，等仓库建立后再执行：

```powershell
Set-Location 'E:\studywork\软工'
git init
git branch -M main
git add .
git status --short
# 确认列表里没有 .env、数据库文件、证书和缓存
git commit -m "Implement roll call backend and regression tests"
git remote add origin <Educoder页面提供的Git地址>
git push -u origin main
```

不要照抄尖括号占位符。提交身份使用本人真实配置；不伪造作者、提交时间或历史。
后续按真实工作阶段提交联调、MySQL 验证、部署、文档改进，用 git log 的实际输出写博客 3.7。

## 4. 联调与公网部署

将 docs/frontend-integration.md 交给队友。跨设备本地联调需要服务监听可访问地址，防火墙按实际情况配置。
本地流程与云端数据库验收后再部署后端；Render 需要它支持的代码托管源，Educoder 作业仓库继续保留，可另设部署镜像。

按照 [Render 官方 FastAPI 部署说明](https://render.com/docs/deploy-fastapi) 新建 Web Service：

| 项目 | 值 |
| --- | --- |
| Root Directory | 总仓库结构时为 backend；若仓库根目录就是后端则留空 |
| Build Command | pip install -r requirements.txt -c constraints-tested.txt |
| Start Command | uvicorn app.main:app --host 0.0.0.0 --port $PORT |
| Health Check Path | /health |
| 环境变量 | DATABASE_URL、DB_SSL=1、TEACHER_API_KEY、CORS_ORIGINS、ALPHA、AUTO_CREATE_TABLES |

服务商要求私有 CA 时，通过部署平台的秘密文件方式配置证书，并将 DB_SSL_CA 指向该文件。不要把证书/密钥处理成源码中的数据库密码。
前端部署后将真实 Origin 加入 CORS_ORIGINS。公网验收要实际导入、点名、记分、排名、下载导出，不能只看部署页面绿灯。
最新蓝图副本在 deploy/render.yaml，已同步至当前总项目目录的 render.yaml。教师口令只在部署平台环境变量中保存，由教师实际输入前端，不能打包进前端源码。数据库故障时 /health 返回 503。

## 5. 尚待真实补充

- 在线数据库已完成接入；演示和部署前可运行 scripts/check_db.py 复查。
- Educoder 仓库、真实提交记录、队友协作记录。
- 前端页面联调和公网访问地址。
- 与题目示例不同的原创玩法、原型截图、两人实际讨论照片、实际耗时及个人体会。
- 视频链接与最终提交，截止 2026-11-08 23:00（北京时间）。
