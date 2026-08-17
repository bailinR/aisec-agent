# Douyin Private Message Profile Monitor

监控对象仅限私信库「账号管理」里的发送账号（托管账号），不按评论人逐个打开主页。

The conversation monitor can reuse an existing logged-in Playwright profile
without receiving the raw Cookie again.

## Start a receive-only monitor

```powershell
$body = @{
  account_key = "cookie_086dd1e31b59dfc5"
  account_id = "account_cff2e1165f"
  account_name = "奥伯特空气热泵农产品烘干小王"
  browser_name = "chrome"
  headless = $false
  auto_send = $false
  generate_reply = $false
  poll_seconds = 8
} | ConvertTo-Json -Compress

Invoke-WebRequest `
  -Uri "http://127.0.0.1:7860/api/v1/douyin/private-message/conversation-monitors/start" `
  -Method POST `
  -Body $body `
  -ContentType "application/json; charset=utf-8"
```

The profile directory is resolved as:

```text
content/playwright_profiles/accounts/{browser_name}/{account_key}
```

When `account_cookie` is omitted, the directory must already exist. The
monitor does not extract, print, or store the raw Cookie in the API response.

Each running monitor also exposes inbox summary fields for 账号管理展示:

- `inbox_unread_count` / `unread_count`: sum of numeric unread badges in the DM panel
- `inbox_unread_people`: number of conversation rows that currently show an unread badge

comment-kit `/api/private-messages/accounts` merges these values onto the matching
staff account row and shows `未读/回复人数` beside 员工姓名.

## Inspect status

```powershell
Invoke-WebRequest `
  -Uri "http://127.0.0.1:7860/api/v1/douyin/private-message/conversation-monitors"
```

For a receive-only test, expect:

```json
{
  "status": "running",
  "auto_send": false,
  "generate_reply": false,
  "account_cookie_loaded": false,
  "profile_login_reused": true
}
```

`last_message` is updated when the page monitor detects a new message. The
current page monitor keeps the latest detected message in its monitor state;
full conversation persistence still belongs in the database/event pipeline.

## AI 客服即时回复（算力池）

测试页：`http://127.0.0.1:7860/dm-cs-demo`

默认用本机算力池 `http://127.0.0.1:4000/v1`、模型 `chat-pm`。启动监控时打开：

- `generate_reply=true`
- `auto_send=true`
- `reply_backend=gpu_pool`（也可设为 `rag` 走项目知识库链路）

只测算力池、不打开浏览器：

```powershell
$body = @{ message = "你好，这个多少钱？"; gpu_model = "chat-pm" } | ConvertTo-Json -Compress
Invoke-RestMethod `
  -Uri "http://127.0.0.1:7860/api/v1/douyin/private-message/conversation-monitors/probe-reply" `
  -Method POST -Body $body -ContentType "application/json; charset=utf-8"
```
