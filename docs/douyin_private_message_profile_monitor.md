# Douyin Private Message Profile Monitor

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
