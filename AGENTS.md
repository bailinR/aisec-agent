# Agent Notes

处理本项目任务时，先读根目录 `keep.md`（全局约束与开发进度），再按问题类型阅读对应文档，不要重新推测已经沉淀过的流程。

## 阅读路由

- 遇到“全局约束、开发进度、start 是否拉 Git、本地提交约定”时，先读 `keep.md`。
- 遇到“评论采集系统调用私信系统、Postman 提交后为什么没发送、异步任务、重试/死信、接口鉴权、HMAC、账号发送限流、生产化改造优先级”时，先读 `docs/私信任务联调与生产化要点.md`。
- 遇到“本地启动、端口 7860、健康检查、本地日志、Windows 后台进程、局域网访问、192.168.x.x 访问不了、0.0.0.0、127.0.0.1、端口监听、Windows 防火墙、映射到局域网”时，先读 `docs/本地启动.md`。
- 遇到“全新 Windows 电脑、无 Python/Redis、Git 拉取、一键部署、双击启动、项目内运行时、环境隔离、start.bat、stop.bat、Gitee”时，先读 `docs/Windows全新电脑一键部署.md`。
- 遇到“服务器部署、Docker、打包上传、Nginx、Redis URL、worker 容器、服务器时区”时，先读 `docs/部署.md`。
- 遇到“接口怎么调、Postman、任务提交、任务查询、模型 key、HTTP 审计日志接口”时，先读 `docs/常用接口.md`。
- 遇到“数据中台未读监控、按最近私信查回复、conversation-monitors/sync、conversations/read/batch、inbox 未读”时，先读 `docs/数据中台对接回复与未读.md`。
- 遇到“任务 pending/queued/running/failed/manual_required、失败截图、失败字段、Redis 队列、worker 没消费、日志怎么看”时，先读 `docs/运行维护与排查.md`。
- 遇到“抖音私信任务 API、Redis 队列 key、failure_code、failure_stage、failure_summary、manual_required、dead_letter”时，先读 `docs/douyin_private_message_redis_api.md`。
- 遇到“服务器无头浏览器、扫码、二次验证、风控、人工机接管、唯一执行点、异常恢复后回到服务器继续执行”时，先读 `docs/浏览器异常人工接管.md`。
- 遇到“真实公司业务数据接入、数据库字段、知识库资料、对外口径、CTA、场景话术、合规边界、验收清单”时，先读 `docs/真实业务数据接入.md`。
- 遇到“私信生成 Prompt、知识库如何进入上下文、场景模板、活动配置、生成结果解释”时，先读 `docs/私信生成.md`。
- 遇到“账号验活、检测活性、蓝V、普通号、accounts/verify、需人工验证、有头无头”时，先读 `docs/数据中台对接账号验活与类型.md`；需人工验证有头改造读 `docs/数据中台对接账号验活-人工验证有头模式.md`。
- 遇到“完整 API 细节、字段定义、接口响应结构”时，先读 `docs/API.md`。

## 工作约定

- 遵守 `keep.md`：改完代码提交本地仓库；`start.bat` 不对齐远程。
- 不要回退用户已有改动。
- 不要把服务器正式运行流程从 systemd Python 重新推回去；当前正式部署以 Docker Web 容器 + worker 容器为准。
- 排查任务失败时，优先看任务详情里的结构化字段，再看截图和 worker 日志。
- 解释私信任务状态时，先区分“提交成功/入队成功”和“worker 已消费/发送成功”。
- 本地启动项目时，默认同时启动 Web 服务和 `aisec_agent.worker.douyin_dm_worker --mode send`；只启动 Web 只能提交/查询任务，不能自动发送私信。
- 涉及真实公司接入时，先区分数据库事实数据、知识库业务口径、场景 CTA、合规边界。
- 本地服务默认 `127.0.0.1:7860` 只能本机访问；需要局域网访问时必须用 `--host 0.0.0.0` 启动，并用 `Get-NetTCPConnection -LocalPort 7860 -State Listen` 确认监听地址是 `0.0.0.0`。
