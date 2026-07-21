# 抖音私信任务 API

Base URL: `http://127.0.0.1:7860`

这套接口的目标很简单：

1. 上游提交任务。
2. 任务先进 Redis 队列。
3. worker 消费任务并执行。
4. 查询接口直接返回任务当前状态和失败原因。

生产化联调、鉴权、重试、死信、账号限流的完整说明见：`docs/私信任务联调与生产化要点.md`。

## 1. 提交任务

`POST /api/v1/douyin/private-message/tasks`

请求体示例：

```json
{
  "task_id": "postman_dm_test_001",
  "video_info": "视频讲膝盖疼、上下楼疼、骨积液和干细胞评估方向。",
  "account_cookie": "sessionid=xxx; sid_guard=xxx",
  "comment_info": "我二舅膝盖上下楼疼，这个适合吗",
  "target_profile_url": "https://www.douyin.com/user/xxxx",
  "project_name": "大健康AI跨境综合企业服务平台"
}
```

账号管理说明：`account_id` 可以由调用方自己维护，例如 `douyin_health_01`，但不是必须字段。若不传 `account_id`，服务端会优先从完整 Cookie 里的 `uid_tt` / `uid_tt_ss` 生成匿名稳定账号标识；因此同一个账号刷新 Cookie 后仍可复用原账号浏览器。若 Cookie 只有 `sessionid` / `sid_guard`，无法稳定识别账号，建议显式传 `account_id`。

指定发送账号时，给每个账号准备一份从已登录浏览器导出的完整 Cookie，并在提交该账号任务时传入对应 Cookie。接口同时支持 `account_cookie` / `account_cookies` / `cookie` / `cookies` 字段，值可以是 `name=value; name2=value2` 字符串，也可以是浏览器插件导出的 `{"cookies":[...]}` JSON 对象或数组。外部系统要用 A 账号发送就传 A 账号 Cookie，要用 B 账号发送就传 B 账号 Cookie。

完整 Cookie JSON 示例：

```json
{
  "task_id": "postman_dm_account_a_001",
  "video_info": "视频讲膝盖疼、上下楼疼、骨积液和干细胞评估方向。",
  "account_cookies": {
    "cookies": [
      {"name": "sessionid", "value": "账号A的sessionid", "domain": ".douyin.com", "path": "/"},
      {"name": "sid_guard", "value": "账号A的sid_guard", "domain": ".douyin.com", "path": "/"}
    ]
  },
  "comment_info": "我二舅膝盖上下楼疼，这个适合吗",
  "target_profile_url": "https://www.douyin.com/user/xxxx",
  "project_name": "大健康AI跨境综合企业服务平台"
}
```

说明：

- `task_id` 可不传，系统会自动生成。
- 正式任务默认会进入 `send` 模式，并自动进入自动队列。
- `account_cookie` 会被存入任务，但查询接口会做脱敏返回。
- worker 消费任务时才会把 Cookie 注入新的 Playwright 浏览器上下文；`accepted: 1` 只代表入队成功，不代表已经发送。

## 1.1 按账号常驻浏览器发送

如果希望避免每条任务都切换登录态和关闭浏览器，可以把 worker 启动为账号浏览器池模式：

```bash
python -m aisec_agent.worker.douyin_dm_worker --mode send --account-browser-pool --max-account-browsers 3 --account-browser edge
```

这个模式下：

- 系统会根据任务里的 `account_id`，或完整 Cookie 中的 `uid_tt` / `uid_tt_ss` 计算稳定的 `account_key`。
- 每个 `account_key` 对应一个独立持久化浏览器 profile，目录在 `content/playwright_profiles/accounts/{browser}/{account_key}`。
- 账号浏览器已打开时，该账号后续任务复用同一个浏览器并排队执行。
- 账号浏览器未打开且当前池未满时，worker 会为该账号打开新浏览器并注入 Cookie。
- 默认最多同时保留 3 个账号浏览器；第 4 个账号的任务会留在自动队列里，等有可用槽位后再处理。
- 单个 worker 仍然一次只执行一条任务，避免同一账号并发操作；需要更高吞吐时应先设计账号级锁和 worker 分片。

任务接口调用方式不变。外部系统只需要按目标发送账号传对应 Cookie：A 账号任务传 A Cookie，B 账号任务传 B Cookie。

返回示例：

```json
{
  "accepted": 1,
  "task_ids": ["postman_dm_test_001"],
  "queue": "dm:queue:pending",
  "task_key_prefix": "dm:task:",
  "account_queues": ["cookie_xxx"]
}
```

## 2. 查询任务

`GET /api/v1/douyin/private-message/tasks/{task_id}`

这个接口是排查失败原因的重点。返回里会带两层数据：

- `task`：Redis 里原始任务的脱敏视图
- `result`：已经整理好的失败诊断结果

`result` 常见字段：

| 字段 | 说明 |
|---|---|
| `status` | `pending` / `running` / `success` / `failed` / `retry_wait` / `manual_required` |
| `queue_status` | 当前队列状态 |
| `error` | 原始错误文本 |
| `error_code` | 内部错误码 |
| `failure_code` | 更具体的失败码 |
| `failure_stage` | 出错阶段 |
| `failure_category` | 失败分类 |
| `failure_reason` | 人能直接看懂的原因 |
| `failure_hint` | 处理建议 |
| `failure_summary` | 原因 + 建议 + 细节 |
| `failure_step` | 如果来自 Playwright demo，最后失败的步骤名 |
| `failure_step_detail` | 最后一步的具体报错 |
| `failure_trace` | 步骤轨迹 JSON |
| `retry_count` | 已重试次数 |
| `max_retries` | 最大重试次数 |
| `next_retry_at` | 下次重试时间 |
| `retry_delay_seconds` | 下次重试延迟秒数 |
| `manual_required` | 是否需要人工处理 |
| `dead_letter` | 是否进入死信队列 |
| `failure_screenshot_url` | 失败截图访问地址 |
| `failure_screenshot_exists` | 失败截图文件是否存在 |
| `manual_takeover` | 人工接管提示、浏览器和目标 URL |
| `first_private_message_status` | 首条私信状态 |

## 3. 任务列表

`GET /api/v1/douyin/private-message/tasks?limit=20`

会返回各队列的任务快照，适合快速扫一眼当前卡在哪个队列。

## 4. 队列说明

| Key | 含义 |
|---|---|
| `dm:queue:pending` | 全局待处理队列 |
| `dm:queue:auto_pending` | 自动执行队列 |
| `dm:queue:pending:{account_key}` | 按账号拆分的待处理队列 |
| `dm:queue:processing` | 处理中 |
| `dm:queue:done` | 成功历史 |
| `dm:queue:failed` | 失败历史 |
| `dm:queue:retry_wait` | 等待重试 |
| `dm:queue:manual_required` | 需要人工处理 |
| `dm:queue:dead_letter` | 最终失败死信 |
| `dm:sent_targets:{account_key}` | 账号级去重记录 |

## 5. 失败码示例

- `missing_model_api_key`：模型配置缺少 `api_key`
- `playwright_missing`：运行环境缺少 Playwright
- `message_send_unconfirmed`：点击发送后，页面没有确认真的发出
- `verification_required`：触发二次验证
- `login_required`：登录态失效
- `account_risk`：风控或发送限制
- `automation_changed`：页面结构变化，自动化入口找不到
- `cookie_invalid`：Cookie 无效、过期或未正确加载
- `browser_closed`：浏览器或页面已关闭
- `rate_limited`：命中限流或验证码

## 6. 这些字段为什么重要

之前任务失败常常只看到一句 `failed`，现在查询接口会尽量把原因拆开：

- 是配置问题，还是浏览器环境问题
- 是登录问题，还是风控问题
- 是页面结构变了，还是发送确认失败
- 是可重试，还是必须人工介入

这能直接减少“看到了失败，但不知道下一步改什么”的情况。
