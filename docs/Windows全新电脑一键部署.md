# Windows Git 拉取后一键部署

日期：2026-08-09

## 1. 使用目标

项目采用“Git 管源码、项目目录管理本机运行时”的方式。目标电脑只需要安装 Git for Windows，并能访问 Python、PyPI、GitHub 和 Playwright 下载地址，不需要预装 Python、Redis、pip 或 Playwright。

首次部署：

```powershell
git clone https://github.com/bailinR/aisec-agent.git
cd aisec-agent
git switch show
```

然后双击 `部署并启动.cmd`。

以后更新可直接双击 `更新并启动.cmd`。该脚本会安全保存本地已暂存、未暂存和未跟踪文件，再执行快进更新，并在本地提交与远程当前分支提交完全一致后重启项目。本地修改不会自动合并回新代码，脚本结束时会显示对应的 stash 名称和恢复命令。

## 2. 首次运行

`部署并启动.cmd` 会自动执行：

1. 缺少 `.env` 时，从 `.env.example` 创建本机配置文件。
2. 下载官方 CPython 3.12 嵌入式运行时。
3. 安装项目运行依赖和文件解析依赖。
4. 下载 Redis for Windows 5.0.14.1。
5. 安装 Playwright 1.54.0 及匹配的 Chromium。
6. 启动项目内 Redis、Web 服务和私信 worker。
7. 检查 `http://127.0.0.1:7860/api/health`，成功后打开页面。

首次安装可能需要几分钟。下载缓存、Python、Redis、浏览器、日志和 PID 都位于 Git 忽略的 `runtime` 目录，不会被提交或影响后续 `git pull`。

安装器不会继承目标电脑的全局 pip 镜像配置。Python 包优先从阿里云镜像下载，失败后自动切换到官方 PyPI，避免本机遗留的清华镜像或公司镜像返回 403 后反复失败。

## 3. 更新与完整性确认

双击 `更新并启动.cmd` 等价于：

```powershell
git fetch --prune origin
git pull --ff-only origin <当前分支>
```

如果工作区有本地修改，拉取前还会自动执行：

```powershell
git stash push --include-untracked --message "automatic backup before updating ..."
```

更新后使用脚本输出的 stash 提交 SHA 检查或恢复，例如：

```powershell
git stash show --stat <stash提交SHA>
git stash apply <stash提交SHA>
```

脚本不会自动执行 `stash pop`，避免把旧修改合并进刚拉取的新代码。确认不再需要后，可根据脚本显示的 stash 名称手动删除。

脚本随后比较：

```powershell
git rev-parse HEAD
git rev-parse origin/<当前分支>
```

两个提交 SHA 相同且 `git status --porcelain` 为空，表示本机受 Git 管理的全部文件与远程当前分支一致。依赖清单发生变化时，启动脚本会根据 SHA256 自动更新项目内运行时；普通代码更新不会重复下载运行时。

## 4. 启动内容

- Redis：默认 `127.0.0.1:6389`，数据库 `11`；端口已被其他程序占用时，启动脚本会自动选择后续空闲端口，并让 Web 与 worker 使用同一地址。
- Web：固定为 `0.0.0.0:7860`；端口被非本项目进程占用时停止启动，不会自动换到其他 Web 端口。
- worker：只保留一个 `aisec_agent.worker.douyin_dm_worker --mode send` 实例。

每次带 `-Restart` 启动时，脚本会先清理遗留的 Web 和 worker 进程，再校验最终只有一个 Web 监听进程和一个 worker。Redis 仍使用仅本机可访问的内部端口，默认是 `127.0.0.1:6389`。

启动脚本还会停止并禁用旧的 `AisecDmWatchdog` 每分钟计划任务。该旧任务以交互方式启动 PowerShell，会导致窗口每分钟弹出；当前 Web 和 worker 已由项目启动脚本直接管理，不再依赖此 watchdog。

停止全部服务时双击 `停止项目.cmd`。

## 5. 本机配置与数据

以下内容不会参与 Git 更新：

```text
.env
runtime/
content/playwright_profiles/
content/douyin_dm_artifacts/
content/export/
```

知识库和场景模板保存在 `content/session_rag_projects/`，属于需要发布的项目内容，会随 Git 提交、推送和拉取。

## 6. 日志

```text
runtime/logs/web.err.log
runtime/logs/worker.err.log
runtime/logs/redis.err.log
runtime/data/redis
runtime/state
```

私信任务提交成功只代表任务已经入队。任务能自动发送还要求 worker 日志中出现：

```text
Douyin DM worker started: mode=send once=False
```

## 7. 常用命令

部署并启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-start.ps1 -Restart -Open
```

指定端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-start.ps1 -Restart -Open -Port 7861
```

只允许本机访问：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-start.ps1 -Restart -Open -HostAddress 127.0.0.1
```

强制重装运行时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-runtime.ps1 -Force
```

## 8. 注意事项

- Git 只能拉取已经 commit 并 push 的文件；开发机未提交的修改不会出现在其他电脑。
- `.env` 中不得提交真实 API Key、密码或其他凭据。
- 第一次监听 `0.0.0.0:7860` 时，Windows 防火墙可能要求确认网络权限。
- Redis 只绑定 `127.0.0.1`，不会暴露给局域网。
- 当前自动运行时支持 x64 Windows 10/11，不支持 32 位或 ARM64 Windows。
- 首次安装失败后先拉取最新代码，再重新双击 `部署并启动.cmd`；已成功下载的 Python、Redis 等文件会从 `runtime/cache/downloads` 复用。
