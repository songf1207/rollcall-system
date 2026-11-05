# 课堂点名系统：给队长的交接说明

这是当前后端源码，队长可以在此基础上接入前端、修改和继续开发。正式版本请放入两人共用的 Educoder 仓库，并保留各自真实提交记录。

## 压缩包目录

```text
先读我.md             本说明
render.yaml           总仓库的后端部署蓝图
backend/              后端源码、测试、样例和文档
```

将已有前端放在同级 frontend/ 中即可。Render 蓝图假设仓库根目录下存在 backend/，其 Root Directory 为 backend。

## 第一次运行（Windows PowerShell）

需要本机安装 Python（推荐 3.12 或更新版本；原环境验证版本为 3.14.3）。进入解压后的 backend 目录，按顺序执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints-tested.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe scripts/init_db.py
.\.venv\Scripts\python.exe scripts/generate_teacher_key.py
.\start_backend.cmd
```

打开 http://127.0.0.1:8000/docs 。复制本机生成的 backend/.teacher-key.txt 中的口令，点击右上角 Authorize，粘贴口令并确认，再测试 API。
默认配置使用本地 SQLite，会生成 dev.db。通过 POST /api/students/import 上传 samples/sample_roster.xlsx，即可得到演示名单。
以后双击 start_backend.cmd 启动即可；服务运行期间终端窗口保持开启。

## 前端如何接入

完整接口说明见 backend/docs/frontend-integration.md。

- 本机前端的 Base URL 使用 http://127.0.0.1:8000。
- 所有 /api 请求都需要请求头 X-Teacher-Key。前端增加教师口令输入框，当前会话使用教师实际输入的值；不要把口令写进源码。
- 各自电脑生成的口令对应各自的本地后端；访问部署后的后端时，使用部署环境配置的口令。
- 先联调导入、名单、点名、记分、改判、统计图、个人曲线、概率盘、历史和导出。
- 点名动画最终结果使用服务端返回的学生，不在前端另做随机抽取。
- 创新展示可接入 GET /api/rollcalls/{id}/audit，制作抽签复核面板。详见接口说明中的各复核状态；这只是保存记录的一致性复核。
- 导出需带口令 fetch 获取文件 blob，不能直接打开导出 URL。

## 当前状态与验证

后端原环境已接通 TiDB Cloud，具有 6 张业务表和 1 张事务锁表；95 个本地测试通过，真实云端流程和并发记分已验收。
这些是原开发环境的结果。交接包没有包含原机器的配置和数据库，接收方需按上面步骤重新初始化本机环境。
验收报告见 backend/docs/cloud-validation.md；设计、算法和性能材料见 backend/docs/blog-implementation.md。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 配置与交付边界

- 包内包含 .env.example 和 tidb.env.example 作为占位配置。
- 原机器的 .env、备份配置、数据库密码、教师口令、证书、数据库文件、.venv、缓存和临时安装文件均不随包交付。
- 云数据库连接配置在部署时由后端负责人单独配置，前端联调可以先使用本地 SQLite。
- 日常初始化使用 scripts/init_db.py。sql/schema.sql 是重建脚本，会删除现有业务表，不用于已有数据的日常启动。
- 95 个用例里有一条 Starlette/httpx 弃用提示，未影响原环境测试通过。
- Educoder 仓库、前端最终页面、公网后端部署及演示视频仍需两人协作完成。

收到后可以继续开发；改变接口字段、路径、评分规则或教师口令方式时请同步更新联调文档，并运行回归测试。
