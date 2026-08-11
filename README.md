# aisec-agent

面向真实业务资料、知识库检索、私信生成和抖音浏览器自动发送的本地/服务器智能体项目。

项目当前已经形成完整链路：业务资料进入知识库，系统根据公司、场景、账号、活动和用户输入组装 Prompt，生成首次私信或后续回复，再通过 Redis 队列交给 worker 执行浏览器发送，并记录任务状态、失败原因和截图。

## 当前能力

### 1. 公司、项目与知识库管理

- 管理公司主体及公司可使用的知识库范围。
- 按“知识库 → 业务领域 → 板块 → 文档”维护资料。
- 上传、编辑、删除知识文档，并自动维护文档描述、索引和文本切片。
- 支持项目全局 Prompt、场景模板、活动配置、账号身份和业务目标。
- 支持 txt、md、json、csv、docx、xlsx、pdf 等常见资料解析。
- 当前业务知识库保存在 `content/session_rag_projects/`，会随 Git 提交和拉取。

### 2. 私信生成与 Prompt 调试

- 根据评论、视频概述、公司、项目、场景和账号生成私信。
- 支持评论转首次私信，以及 `private` / `followup` 后续私信。
- Prompt 按层组装：用户输入、全局规则、发送身份、业务承接规则、流程模板、场景材料、任务规则、活动约束和检索知识。
- 支持 Prompt 预览、路由调试、命中文档查看和最终 Prompt 解释。
- 支持直接指定 `message`，跳过模型生成并发送给定内容。
- 支持 MiniMax、DeepSeek、Ollama 和 OpenAI 兼容模型配置。
- API Key 保存后会复用，查询接口返回时进行脱敏。

### 3. 抖音账号与浏览器发送

- 托管账号配置、Cookie 应用和持久化浏览器目录。
- 检测普通账号与蓝 V 账号。
- 打开目标主页、预填私信和执行真实发送。
- 复用账号浏览器会话，避免每个任务重复创建浏览器。
- 浏览器出现登录、验证码、二次验证或风控时保留现场，支持人工处理。
- 提供私信对话监控入口和账号状态展示。

### 4. Redis 异步任务系统

- Web 服务负责接收任务、写入 Redis、查询状态和提供后台页面。
- worker 消费私信队列，生成内容并执行 Playwright 浏览器操作。
- 支持 `pending`、`queued`、`running`、`success`、`failed`、`manual_required` 和 `dead_letter` 等状态。
- 返回结构化失败字段：`failure_code`、`failure_stage`、`failure_summary` 和失败截图 URL。
- 支持任务列表、单任务查询、队列清理、重试/人工处理信息和 HTTP 审计日志。

### 5. 部署与运行

- Windows 全新电脑从 Git 拉取后双击部署，不要求预装 Python、Redis、pip 或 Playwright。
- 项目自动下载隔离的 Python 3.12、Redis 和 Playwright Chromium 到 `runtime/`。
- Python 包安装不继承电脑全局 pip 镜像；阿里云失败时自动回退官方 PyPI。
- Redis 默认使用 `6389`；端口被占用时自动选择后续空闲端口。
- 后续更新可自动执行 `git pull --ff-only`、校验远程提交并重启。
- 正式服务器继续使用 Docker Web 容器 + worker 容器，不使用 systemd Python 主服务。

## 系统结构

```text
页面 / Postman / 上游评论采集系统
                 |
                 v
        Web 服务（页面与 HTTP API）
                 |
        +--------+---------+
        |                  |
        v                  v
 公司/项目/知识库       Redis 任务队列
                           |
                           v
                 私信 worker（mode=send）
                           |
                           v
              Playwright 浏览器预填/发送
                           |
                           v
               任务结果、失败字段与截图
```

需要特别区分：提交接口返回成功只表示任务已经入队，不代表 worker 已经发送成功。

## Windows 首次部署

适用于 64 位 Windows 10/11。目标电脑只需要安装 Git for Windows，并能够访问 GitHub 和 Python 包下载地址。

```powershell
git clone -b show https://github.com/bailinR/aisec-agent.git
cd aisec-agent
```

双击：

```text
部署并启动.cmd
```

首次运行会自动：

1. 从 `.env.example` 创建本机 `.env`。
2. 下载项目内 Python、Redis 和 Playwright Chromium。
3. 安装运行依赖。
4. 启动 Redis、Web 服务和私信 worker。
5. 调用 `/api/health` 验证服务并打开浏览器。

运行成功后访问：

```text
http://127.0.0.1:7860
```

详细说明见 [Windows 全新电脑一键部署](docs/Windows全新电脑一键部署.md)。

## Windows 后续更新

直接双击：

```text
更新并启动.cmd
```

脚本会执行安全的快进拉取，确认本地提交与远程当前分支一致，然后更新必要的运行依赖并重启项目。存在未提交文件时会停止更新，不会强制覆盖。

也可以手工执行：

```powershell
git pull --ff-only origin show
```

然后双击 `部署并启动.cmd`。

停止项目：

```text
停止项目.cmd
```

## 本地开发启动

本地开发建议使用 Python 3.12 和独立虚拟环境，并准备可用的 Redis。Redis 地址通过 `REDIS_URL` 配置，默认使用本机 DB 11。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install .\form_validate
```

同时启动 Web 和 worker：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -Restart -Open
```

前台调试时分别运行：

```powershell
.\.venv\Scripts\python.exe -m aisec_agent.web --host 127.0.0.1 --port 7860 --no-open
```

```powershell
.\.venv\Scripts\python.exe -m aisec_agent.worker.douyin_dm_worker --mode send
```

只启动 Web 时可以提交和查询任务，但任务会停在 `pending / queued`，不会自动发送。

局域网访问必须使用 `--host 0.0.0.0`，并确认：

```powershell
Get-NetTCPConnection -LocalPort 7860 -State Listen
```

监听地址为 `0.0.0.0` 后，才能使用 `http://192.168.x.x:7860` 从其他设备访问。

## 页面入口

| 页面 | 地址 |
| --- | --- |
| 项目首页 | `http://127.0.0.1:7860` |
| 私信生成 | `http://127.0.0.1:7860/session-rag-chat` |
| 企业工作台 | `http://127.0.0.1:7860/workspace` |
| 项目资料 | `http://127.0.0.1:7860/project-materials` |
| 文件解析 | `http://127.0.0.1:7860/file-parser` |
| 后台管理 | `http://127.0.0.1:7860/admin-vue` |
| 健康检查 | `http://127.0.0.1:7860/api/health` |

## 常用 API

| 功能 | 方法与路径 |
| --- | --- |
| 健康检查 | `GET /api/health` |
| 生成私信 | `POST /api/v1/private-message/generate` |
| Prompt 预览 | `POST /api/v1/private-message/prompt-preview` |
| 路由调试 | `POST /api/v1/private-message/route-debug` |
| 招聘/业务回复 | `POST /api/v1/business/reply` |
| 保存模型配置 | `POST /api/v1/model/config/save` |
| 提交抖音私信任务 | `POST /api/v1/douyin/private-message/tasks` |
| 查询任务列表 | `GET /api/v1/douyin/private-message/tasks` |
| 查询指定任务 | `GET /api/v1/douyin/private-message/tasks/{task_id}` |
| 清空任务队列 | `POST /api/v1/douyin/private-message/tasks/clear` |
| 上传知识文档 | `POST /api/admin/knowledge/upload` |
| 保存知识文档 | `POST /api/admin/knowledge/save` |
| HTTP 审计日志 | `GET /api/admin/http-audit-logs` |

任务提交示例：

```json
{
  "task_id": "dm_task_001",
  "video_info": "视频内容概述",
  "comment_info": "用户评论内容",
  "target_profile_url": "https://www.douyin.com/user/xxxx",
  "project_name": "项目名称"
}
```

如果已经有确定的私信文本，可以传入：

```json
{
  "task_id": "dm_task_002",
  "message": "您好，看到您的留言了，我把相关资料发您看看。",
  "target_profile_url": "https://www.douyin.com/user/xxxx"
}
```

完整字段和响应结构见 [API 接口文档](docs/API.md) 与 [抖音私信任务 API](docs/douyin_private_message_redis_api.md)。

## 任务状态判断

| 状态 | 含义 |
| --- | --- |
| `pending / queued` | 已提交并进入 Redis，worker 尚未消费 |
| `running / processing` | worker 已取到任务，正在生成或操作浏览器 |
| `success` | 任务完成；发送任务还应确认 `sent=true`、`queue_status=done` |
| `failed` | 执行失败，查看结构化失败字段和截图 |
| `manual_required` | 登录、验证、风控等问题需要人工处理 |
| `dead_letter` | 不可重试或多次重试后最终失败 |

排查失败时按以下顺序：

1. 查询任务详情里的 `failure_code`、`failure_stage`、`failure_summary`。
2. 查看 `failure_screenshot_url`。
3. 查看 worker 日志。
4. 判断是否需要人工接管。

## 知识库目录

```text
content/session_rag_projects/{project_id}/
├─ project.json
├─ global_prompt.md
├─ identity_settings.json
├─ account_settings.json
├─ activity_settings.json
├─ business_targets.json
├─ scene_templates.json
├─ scenes/
└─ knowledge/
   ├─ document_descriptions.json
   ├─ manifest.json
   ├─ chunks.jsonl
   └─ files/
```

- 数据库事实数据负责提供可查询的真实记录。
- 知识库负责业务事实、产品资料和对外口径。
- 场景模板负责回复步骤和表达方式。
- 活动与 CTA 配置负责用户下一步承接动作。
- 合规资料负责限制不可承诺、不可编造的内容。

## 配置和运行数据

以下文件不会提交到 Git：

```text
.env
runtime/
content/playwright_profiles/
content/douyin_dm_artifacts/
content/export/
```

不要把模型 API Key、数据库密码、真实 Cookie 或浏览器登录态提交到仓库。

Windows 一键运行日志：

```text
runtime/logs/web.err.log
runtime/logs/worker.err.log
runtime/logs/redis.err.log
```

## 服务器部署

正式服务器以 Docker 为准：

- Web 容器提供页面和 HTTP API。
- worker 容器消费 Redis 队列并执行私信任务。
- 两个容器必须连接同一 `REDIS_URL`。
- `content` 目录需要持久化挂载。
- 容器需要设置 `TZ=Asia/Shanghai`。
- 对外访问可通过 Nginx 代理到 Web 容器的 `7860`。

详细步骤见 [部署说明](docs/部署.md)。

## 测试

主要测试入口：

```powershell
python -m pip install pytest
python -m pytest tests\test_session_rag_web.py -q
python -m pytest tests\test_project_materials.py -q
python -m pytest tests\test_session_rag_chat.py -q
```

提交前还应检查 PowerShell 脚本语法、`git diff --check` 和启动健康检查。

## 文档导航

- [Windows 全新电脑一键部署](docs/Windows全新电脑一键部署.md)
- [本地启动与局域网访问](docs/本地启动.md)
- [服务器 Docker 部署](docs/部署.md)
- [常用接口](docs/常用接口.md)
- [完整 API 文档](docs/API.md)
- [私信生成与 Prompt 说明](docs/私信生成.md)
- [抖音私信任务 Redis API](docs/douyin_private_message_redis_api.md)
- [私信任务联调与生产化要点](docs/私信任务联调与生产化要点.md)
- [运行维护与排查](docs/运行维护与排查.md)
- [浏览器异常人工接管](docs/浏览器异常人工接管.md)
- [真实业务数据接入](docs/真实业务数据接入.md)

## 当前发布分支

当前用于 Windows 拉取和部署的分支为 `show`：

```powershell
git switch show
git pull --ff-only origin show
```
