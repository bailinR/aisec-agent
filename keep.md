# keep — 全局约束与开发进度

本文件是本仓库的**长期备忘**：全局约束、当前进度、已知坑。改代码或推进需求时先读再改；有新约定或进度变化时同步更新本文件。

---

## 全局约束

1. **每次改完代码必须提交本地 Git**（`git add` + `git commit`）。不要求立刻 `push`，但本地仓库不能长期脏着。
2. **双击 `start.bat` = 纯本地启动**：不 `fetch` / `pull` / stash，不对齐远程版本。需要跟 Gitee 对齐时用 `update-and-start.bat` 或 `scripts\start.ps1`（不加 `-SkipGitUpdate`）。
3. **不要回退用户已有改动**；不要用 `start.bat` 的自动 stash 当「保存」手段。
4. 本地启动默认同时起 **Web + worker**（`douyin_dm_worker --mode send`）；只起 Web 不能自动发私信。
5. 正式服务器部署以 **Docker Web + worker** 为准，不要推回 systemd Python 主路径。
6. 未读（账号维度）与回复检测（线索维度）分开，不要混用。

---

## 启停约定

| 动作 | 怎么做 |
|---|---|
| 日常本地启动 | 双击 `start.bat`（内部 `-SkipGitUpdate`） |
| 停止 | 双击 `stop.bat` |
| 要从远程更新再启动 | 双击 `update-and-start.bat` |
| 开发机已有 venv 时 | `scripts\start-local.ps1 -Restart` |

Web：`0.0.0.0:7860` → `http://127.0.0.1:7860`

---

## 当前开发进度（2026-08-26）

### 私信发送：多账号浏览器池

- 不同账号打开不同浏览器（按 `account_key` 独立 profile：`content/playwright_profiles/accounts/{browser}/{account_key}`）
- 最多同时打开 `AISEC_DM_MAX_OPEN_BROWSERS`（默认 3）个账号浏览器；同账号任务在该账号线程内排队
- 发送结束后空闲 `AISEC_DM_BROWSER_IDLE_CLOSE_MS`（默认 180000ms / 3 分钟）无任务则关闭该账号浏览器并释放槽位
- 开关：`AISEC_DM_SEND_BROWSER_POOL=1`（默认开）；worker 可用 `--no-browser-pool` 退回串行
- 实现：`aisec_agent/worker/dm_account_browser_pool.py` + `douyin_dm_worker.py`；会话线程本地 + LRU 见 `session_rag_chat.py`
- 测试：`tests/test_dm_send_browser_pool.py` 通过

### 回复池发信跨进程复用盯号浏览器

- `POST /api/v1/douyin/private-message/conversation-monitors/send`：在 Web 进程内对 alive 盯号执行 `request_send_dm`
- DM worker 处理 `use_alive_monitor` 时：先本进程查找，再 HTTP 调 Web（`AISEC_WEB_BASE_URL` 默认 `127.0.0.1:7860`）
- 测试：`tests/test_monitor_send_and_auto_close.py` 5 项通过
- **部署后须同时重启 Web + worker**（只重启 worker 无效）

## 当前开发进度（2026-08-21）

### 已完成（aisec）

- 账号验活：`POST .../accounts/verify`
- 一次性未读：`POST .../accounts/inbox`
- 盯号 sync（好账号批量开 watch-only）：`POST .../conversation-monitors/sync`
- 批量回复检测：`POST .../conversations/read/batch`
- 单条回复检测 / 任务 replies：已有
- 对接说明：`docs/数据中台对接回复与未读.md`
- `start.bat` 改为本地启动，不再强制 Git 对齐
- 私密账号主页态：识别「私密账号 / 发起关注请求…」→ `recipient_privacy_restriction`，不再误报 `automation_changed` /「发送结果未确认」

### 进行中 / 待办

- [x] 将未推送的本地提交（含 inbox / sync / batch / start 行为）push 到 Gitee（`99c1f20`）
- [x] 机 23 部署到 `99c1f20`（路径 `C:\sixin\aisec\new\aisec-agent`，Web+worker 已重启）
- [x] 机 23 部署到 `d03c613`（私密账号 → `recipient_privacy_restriction`）
- [x] 知识库更新已提交并 push；机 23 部署到 `1f56bd6`（`starc` 交互会话重启）
- [x] 机 23 部署到 `4146fb1`（私信调试 UI 双栏 + 滚动修复；`starc` `/IT` 重启）
- [x] 机 23 部署到 `ce1942d`（私信调试默认主页 + 保存按钮）
- [x] 机 23 部署到 `2d54d9f`（修复手动关浏览器后 Playwright asyncio 冲突）
- [x] 本地 `192.168.31.162:7860` 修复 `accounts/verify` Playwright 同步运行时冲突（manager teardown + verify 重试）
- [x] 机 23 部署到 `14e6df3`（`accounts/verify` Playwright 同步运行时冲突修复）
- [x] 机 23 部署到 `5d0d8bb`（验活 chrome 无头启动失败回退系统 Edge）
- [x] 机 23 部署到 `96d0d71`（验活 profile API 未登录识别 + 个人号昵称识别）
- [x] 机 23 部署到 `e67fba8`（验活文档 + profile/个人号修复批次）
- [x] 回复池协作：inbox / 盯号补齐 `unread_conversations`；回传见 `docs/回复池协作-私信侧回传.md`
- [x] 修复 inbox 500（运行时未加载新函数）+ 未登录误判导致扫不到消息面板
- [x] 修复 inbox HTTP 500：Playwright 跨线程复用（走 web executor）+ 同步前暂停同 profile 盯号
- [x] 修复未读列表昵称/预览粘连（如「白林 2222 ·」→ 昵称白林 / 正文 2222）
- [x] inbox「同步未读」优先复用 alive 盯号浏览器（不再 stop/另开/restart）；无盯号才临时开窗
- [x] 回复池专用审计日志：记录 monitors/inbox/read/batch/tasks 调用 + 未读明细/`has_reply`；`GET /api/admin/reply-pool-audit-logs`；文件 `runtime/logs/reply-pool-audit.log`
- [x] 盯号/inbox 扫列表后强制填充 `unread_conversations`（至少 nickname+preview+count）；角标有未读但行解析失败时列表预览兜底
- [x] 盯号全量刷新 unread_conversations：紧凑行优先、列表滚顶、不因父节点含「群聊」丢白林；人数与明细条数分离便于 incomplete 提示
- [x] 修 unread_details_missing：列表行 Y 聚类关联角标、面板抗 feed 误判、抑制 top_entry 虚高；明细空/不完整时 peek 补全
- [ ] 数据中台（comment-kit）按回复池协作文档接盯号 + inbox 明细 + tasks 跟进发送；本机可二需有效 Cookie 后复测同步
- [x] 知识库文件树「打开本地文件夹」按钮：不再因局域网 IP 隐藏；按当前选中资料/模块打开对应目录
- [x] 机 23 部署到 `9392049`（奥伯特截流话术入库 + 知识库打开本地文件夹；bundle 快进 + starc /IT 计划任务重启）
- [x] 离线一键部署包：`build-deploy-package.bat` + `scripts/build-portable-package.ps1`（lite/full）；说明 `docs/离线部署包.md`

### 生产机（机 23 / ALEX001）

- 路径：`C:\sixin\aisec\new\aisec-agent`
- 分支：`master` @ `9392049`
- 访问：`http://192.168.18.100:7860`
- 启停：便携运行时；远程重启须以交互用户 `starc` 跑 `start.ps1 -SkipGitUpdate`（可用 `/IT` 计划任务），**禁止** `schtasks /RU SYSTEM`（SYSTEM 下 Playwright 会 `browser_closed`）
- Gitee HTTPS 在机 23 无交互凭证，更新代码用 git bundle 快进
- 2026-08-21 部署 `d03c613` 后曾误用 SYSTEM 重启，导致中台发私信批量 `browser_closed`；已改回 `ALEX001\starc` 运行

- 本机曾出现 `master` **ahead of origin**（如 `6ce245c` 未 push），旧版 `start.bat` 会因此报 Code verification failed。
- 未提交改动被旧逻辑 stash 后不会自动还原；开发 WIP 应用 `start.bat`（现已 SkipGit）或 `-SkipGitUpdate`。

---

## 接口速查（中台相关）

| 能力 | 路径 |
|---|---|
| 验活 | `POST /api/v1/douyin/private-message/accounts/verify` |
| 未读快照 | `POST /api/v1/douyin/private-message/accounts/inbox`（含 `unread_conversations`） |
| 盯号同步 | `POST /api/v1/douyin/private-message/conversation-monitors/sync` |
| 盯号列表 | `GET /api/v1/douyin/private-message/conversation-monitors`（含 `unread_conversations`） |
| 批量查回复 | `POST /api/v1/douyin/private-message/conversations/read/batch` |
| 回复池回传 | `docs/回复池协作-私信侧回传.md` |
| 回复池调用日志 | `GET /api/admin/reply-pool-audit-logs`（文件 `runtime/logs/reply-pool-audit.log`） |

---

## 更新规则

- 改约束 → 改本节「全局约束」
- 完成/搁置需求 → 改「当前开发进度」
- Agent 处理本仓库任务时：先读 `keep.md` 与 `AGENTS.md`
