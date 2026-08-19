# API 接口文档

本文档根据当前代码整理，覆盖两类入口：

- 本地页面服务：`python -m aisec_agent.web --host 127.0.0.1 --port 7860 --no-open`
- Flask API 服务：`aisec_agent.api.entry:app`

## 通用响应格式

本地页面服务和 Flask API 都会返回统一包裹结构，字段略有差异：

```json
{
  "code": 0,
  "msg": "success",
  "data": {}
}
```

Flask API 的 `Ret` 模型给 `code` 配置了 `err_no` 别名；实际返回以运行时序列化结果为准，常见为 `code`，部分场景可能显示别名：

```json
{
  "code": 0,
  "msg": "success",
  "data": {}
}
```

错误响应通常为：

```json
{
  "code": 400,
  "msg": "错误信息",
  "data": null
}
```

流式接口使用 `text/event-stream`，每个事件形如：

```text
data: {"type":"delta","text":"..."}

```

## 一、本地页面服务接口

默认 Base URL：`http://127.0.0.1:7860`

默认模型供应商为 MiniMax：

```json
{
  "provider": "minimax",
  "url": "https://api.minimaxi.com/anthropic",
  "func_name": "minimax_anthropic_chat",
  "model_name": "MiniMax-M3",
  "max_len_input": 16000
}
```

### 1. 页面入口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 聊天页面 |
| GET | `/session-rag-chat` | 聊天页面 |
| GET | `/file-parser` | 文件解析页面 |
| GET | `/project-materials` | 重定向到 `/admin-vue` |
| GET | `/admin` | 管理页面 |
| GET | `/admin-vue` | 管理页面 |
| GET | `/static/{path}` | 静态资源 |

### 2. 健康检查

#### GET `/api/health`

响应体：

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### GET `/api/v1/health`

响应体：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "version": "v1"
  }
}
```

### 3. 预设和项目列表

#### GET `/api/presets`

说明：获取模型供应商预设和页面默认值。

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| providers | object | 可选模型供应商预设，key 为 provider |
| defaults.provider | string | 默认供应商 |
| defaults.session_id | string | 默认会话 ID |
| defaults.user_id | string | 默认用户 ID |
| defaults.project_id | string | 默认项目 ID |
| defaults.scene_id | string | 默认场景 ID |
| defaults.max_len_input | int | 最大上下文长度 |
| defaults.summary_max_chars | int | 会话摘要最大字符数 |
| defaults.raw_max_chars | int | 原始会话最大字符数 |
| defaults.knowledge_size | int | 知识检索数量 |

#### GET `/api/v1/presets`

说明：v1 版本预设接口，字段与 `/api/presets` 类似，默认阶段字段为 `conversation_stage`。

#### GET `/api/projects`

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| projects | array | 项目列表 |
| default_project_id | string | 默认项目 ID |

#### GET `/api/v1/projects`

说明：v1 版本项目列表接口，响应体同 `/api/projects`。

### 4. 模型配置

#### GET `/api/v1/model/configs`

说明：获取已保存的公开模型配置。API Key 会被脱敏，不返回明文。

响应 `data`：按 provider 聚合的模型配置对象。

#### POST `/api/v1/model/config/save`

说明：保存模型配置到 `.env`。如果不传 `api_key` 且本地已有该 provider 的 key，会沿用旧 key。

Content-Type：`application/json`

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| provider | string | 是 | - | 供应商，例如 `minimax`、`deepseek`、`ollama`、`openai_compatible` |
| api_key / key | string | 否 | - | 模型 API Key |
| url | string | 否 | provider 预设或旧配置 | 模型接口地址 |
| func_name | string | 否 | provider 预设或旧配置 | 调用函数，例如默认 `minimax_anthropic_chat` |
| model_name | string | 否 | provider 预设或旧配置 | 模型名称 |
| max_len_input | int | 否 | 16000 | 最大输入长度 |

示例：

```json
{
  "provider": "minimax",
  "api_key": "YOUR_MINIMAX_API_KEY"
}
```

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| config | object | 当前保存的 provider 配置，API Key 脱敏 |
| configs | object | 全量公开模型配置 |

#### POST `/api/v1/model/config/delete`

说明：删除指定 provider 的本地模型配置。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| provider | string | 是 | 供应商 |

响应 `data`：删除后的公开模型配置。

#### POST `/api/v1/model/test`

说明：测试模型配置。会调用模型并要求模型只回复 `OK`。

入参支持两种写法：

```json
{
  "provider": "minimax",
  "api_key": "YOUR_KEY"
}
```

或：

```json
{
  "model": {
    "provider": "minimax",
    "api_key": "YOUR_KEY",
    "url": "https://api.minimaxi.com/anthropic",
    "func_name": "minimax_anthropic_chat",
    "model_name": "MiniMax-M3",
    "max_len_input": 16000
  }
}
```

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| provider | string | 供应商 |
| url | string | 实际调用地址 |
| func_name | string | 调用函数 |
| model_name | string | 模型名称 |
| reply | string | 模型回复，最长 200 字符 |

#### POST `/api/config/test`

说明：页面内部模型测试接口，入参与响应同 `/api/v1/model/test`。

### 5. 私信生成

#### POST `/api/v1/private-message/generate`

说明：根据评论、视频概述、项目资料、场景模板、活动配置和知识库生成私信回复。

Content-Type：`application/json`

兼容字段：

- `question` / `comment` / `user_comment` / `message` / `user_input` 会归一为用户输入。
- `model.provider`、`model.api_key` 等会展开为顶层模型配置。
- `stage` 会映射为 `conversation_stage`：`comment`、`first`、`first_dm`、`首次私信` -> `first_comment`；`private`、`followup`、`私信维护` -> `private_followup`。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| comment / question / message / user_input | string | 是 | - | 用户评论或私信内容 |
| project_id | string | 否 | 默认项目 | 项目 ID |
| session_id | string | 否 | 自动生成 | 会话 ID，同一用户建议固定 |
| user_id | string | 否 | 默认用户 | 用户 ID |
| stage / conversation_stage | string | 否 | `first_comment` | 会话阶段 |
| video_overview / video_summary / video_description | string | 否 | 空 | 视频概述 |
| template_id | string | 否 | 自动匹配 | 场景模板 ID |
| activity_id | string | 否 | 自动匹配 | 活动 ID |
| account_id | string | 否 | 空 | 账号身份配置 ID |
| product_id | string | 否 | 空 | 产品身份配置 ID |
| sender_identity / sender_identity_override | string | 否 | 自动推断 | 私信发送身份 |
| source_platform | string | 否 | 空 | 来源平台 |
| conversion_target | string | 否 | 空 | 转化目标 |
| target_note | string | 否 | 空 | 转化补充说明 |
| global_prompt | string | 否 | 项目全局提示词 | 覆盖全局提示词 |
| enable_knowledge | bool | 否 | false | 是否启用旧知识检索参数 |
| topics | string/array | 否 | [] | 旧知识库主题 |
| summary_max_chars | int | 否 | 4000 | 会话摘要最大字符数 |
| raw_max_chars | int | 否 | 12000 | 原始会话最大字符数 |
| knowledge_size | int | 否 | 4 | 知识检索数量 |
| use_ai_selector | bool | 否 | false | 是否使用 AI 选择文档 |
| model | object | 否 | - | 模型配置对象 |
| provider | string | 否 | `minimax` | 模型供应商 |
| api_key / key | string | 否 | 已保存 key | 模型 API Key |
| url | string | 否 | provider 预设 | 模型接口地址 |
| func_name | string | 否 | provider 预设 | 调用函数 |
| model_name | string | 否 | provider 预设 | 模型名称 |
| max_len_input | int | 否 | 16000 | 最大输入长度 |

示例：

```json
{
  "project_id": "project_a85d890a904f",
  "session_id": "postman-test-001",
  "user_id": "user-postman",
  "stage": "comment",
  "comment": "我妈膝盖上下楼疼，干细胞这个适合吗",
  "video_overview": "视频讲膝骨关节、骨积液和干细胞评估方向。",
  "product_id": "joint_assessment",
  "account_id": "douyin_health_assistant",
  "model": {
    "provider": "minimax",
    "api_key": "YOUR_MINIMAX_API_KEY"
  }
}
```

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| input | object | 归一后的输入信息 |
| reply | string | 生成的私信回复 |
| prompts | array | Prompt 模块列表，包含用户上下文、全局提示词、身份、场景模板、知识库、最终 Prompt |
| config | object | 实际使用的模型配置，包含 provider、api、func、model、max_len、key_source |

#### POST `/api/chat`

说明：页面内部非流式聊天接口，能力与 `/api/v1/private-message/generate` 类似。

入参：同 `/api/v1/private-message/generate`，但推荐使用 `question` 字段。

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| answer | string | 回复文本 |
| result | object | 完整会话生成结果 |
| session_id | string | 会话 ID |
| user_id | string | 用户 ID |
| provider | string | 模型供应商 |
| project | object | 命中的项目和场景 |
| project_documents | object | 命中的知识库文档 |
| sender_identity | string | 发送身份 |
| sender_identity_source | object | 身份来源 |
| scene_template | object | 场景模板 |
| activity_settings | object | 活动配置 |
| config | object | 实际模型与上下文配置 |
| prompt_trace | object | Prompt 追踪信息 |

#### POST `/api/chat/stream`

说明：页面内部流式聊天接口。

入参：同 `/api/chat`。

响应：`text/event-stream`

事件类型：

| type | 说明 |
|---|---|
| status | 状态事件，例如 `selecting_project_documents`、`reading_memory`、`streaming_answer` |
| delta | 回复增量，字段 `text` |
| done | 生成完成，字段 `data` 为完整结果 |
| error | 错误，字段 `error` |

### 6. 招聘/业务回复

#### POST `/api/v1/business/reply`

说明：面向业务调用方的统一回复接口。若 `caller`、`business_scene`、`message`、`user_identity` 中命中招聘语义，会走招聘机器人面试流程；否则退化为通用私信生成。

Content-Type：`application/json`

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| message / content / text / question / user_input | string | 是 | - | 当前用户消息 |
| session_id | string | 是 | - | 会话 ID |
| user_identity | object | 是 | - | 用户身份对象，不接受字符串 |
| project_id | string | 否 | 默认项目 | 项目 ID |
| user_id | string | 否 | 默认用户 | 用户 ID |
| caller / source / source_platform / platform | string | 否 | 空 | 调用方或来源平台 |
| business_scene / scene / scenario | string | 否 | 空 | 业务场景 |
| summary_max_chars | int | 否 | 4000 | 会话摘要最大字符数 |
| raw_max_chars | int | 否 | 12000 | 原始会话最大字符数 |
| model | object | 否 | - | 模型配置 |
| provider/api_key/url/func_name/model_name/max_len_input | mixed | 否 | provider 预设或已保存配置 | 模型配置 |

`user_identity` 常用字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| name | string | 用户姓名或称呼 |
| role | string | 身份角色 |
| job / job_title / position | string | 应聘岗位或意向 |
| platform / source | string | 来源平台 |
| company | string | 公司 |
| city | string | 城市 |
| experience | string | 经验 |
| resume_summary | string | 简历摘要 |
| note | string | 补充信息 |

示例：

```json
{
  "project_id": "project_a85d890a904f",
  "session_id": "boss_user_001",
  "message": "你好，我想了解销售岗位",
  "caller": "boss直聘",
  "business_scene": "机器人面试",
  "user_identity": {
    "name": "求职者",
    "job": "销售岗",
    "platform": "boss直聘",
    "experience": "3年SaaS销售经验",
    "note": "正在找销售岗"
  },
  "model": {
    "provider": "minimax",
    "api_key": "YOUR_MINIMAX_API_KEY"
  }
}
```

响应 `data`：结构同私信生成接口，招聘流程额外会在 `input` 中返回 `caller`、`business_scene`、归一后的 `user_identity`，`prompts` 会包含业务路由、会话上下文、面试问题控制状态、招聘全局提示词、岗位概述、具体业务资料、最终 Prompt。

### 7. Prompt 预览和路由调试

#### POST `/api/v1/private-message/prompt-preview`

说明：只预览资料选择和 Prompt 组装，不调用模型生成回复。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| comment / question / user_input | string | 是 | - | 用户输入 |
| project_id | string | 否 | 默认项目 | 项目 ID |
| video_overview / video_summary / video_description | string | 否 | 空 | 视频概述 |
| context / user_context | string | 否 | 空 | 额外上下文 |
| max_documents | int | 否 | 4 | 最多选择文档数 |
| template_id | string | 否 | 自动选择 | 场景模板 ID |
| activity_id | string | 否 | 自动选择 | 活动 ID |
| account_id | string | 否 | 空 | 账号身份配置 |
| product_id | string | 否 | 空 | 产品身份配置 |
| sender_identity | string | 否 | 自动推断 | 指定发送身份 |
| global_prompt | string | 否 | 项目默认 | 覆盖全局提示词 |
| use_ai_selector | bool | 否 | false | 是否调用模型选择文档 |
| provider/api_key/url/func_name/model_name/max_len_input | mixed | 否 | - | AI 文档选择配置 |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| question | string | 用户输入 |
| selector | object | 文档选择结果 |
| selected_documents | array | 读取的文档 |
| sender_identity | string | 发送身份 |
| sender_identity_source | object | 身份来源 |
| scene_template | object | 场景模板 |
| activity_settings | object | 活动设置 |
| prompt_modules | array | Prompt 模块 |
| final_prompt | string | 最终完整 Prompt |

#### POST `/api/v1/private-message/route-debug`

说明：调试当前输入会命中哪个项目场景、哪些知识库文档、身份、模板和活动。

入参：同 `/api/v1/private-message/prompt-preview`，同时支持 `conversation_stage`、`source_platform`、`conversion_target`、`target_note`。

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| question | string | 用户输入 |
| routing_input | string | 用于路由的输入 |
| conversation_stage | string | 会话阶段 |
| project | object | 命中的项目和场景 |
| project_documents | object | 命中的文档 |
| sender_identity | string | 发送身份 |
| sender_identity_source | object | 身份来源 |
| scene_template | object | 场景模板 |
| activity_settings | object | 活动设置 |
| project_context | string | 运行时项目上下文 |
| prompt | string | 构造出的 Prompt |
| prompt_modules | array | Prompt 模块 |
| selector | object | 文档选择信息 |

#### POST `/api/project-route/debug`

说明：页面内部路由调试接口，入参与响应同 `/api/v1/private-message/route-debug`。

#### POST `/api/open-url`

说明：本地演示用接口，在默认浏览器、Edge 或 Google Chrome 中打开指定链接。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| url | string | 是 | - | 要打开的 `http` / `https` 链接 |
| browser / browser_name | string | 否 | `default` | `default`、`edge`、`chrome` |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| url | string | 已打开链接 |
| browser | string | 请求的浏览器 |
| resolved_browser | string | 实际使用的浏览器，找不到指定浏览器时会回落到默认浏览器 |
| opened | bool | 是否已触发打开 |

#### POST `/api/douyin/private-message/demo`

说明：本地演示用接口。使用 Playwright 打开指定抖音主页，点击页面上的“私信”，通过 Draft.js 粘贴事件把生成的私信文案注入输入框；`auto_send=true` 时继续点击发送按钮。默认只预填不发送。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| profile_url / target_profile_url / url | string | 是 | - | 抖音用户主页链接，必须是 `douyin.com` |
| message / reply / text | string | 是 | - | 要填入私信输入框的文本 |
| browser / browser_name | string | 否 | `default` | `default`、`edge`、`chrome` |
| auto_send | bool | 否 | `false` | 是否粘贴后自动点击发送 |
| account_cookies / account_cookie / cookies / cookie | string/object/array | 否 | - | 账号 Cookie，用于本地 Playwright 调试时跳过登录；支持 `{"cookies":[...]}`、Cookie 数组、单个 Cookie 对象或 `name=value; name2=value2` 字符串。不会在响应中回显原文 |
| options.user_data_dir | string | 否 | `content/playwright_profiles/douyin-{browser}` | Playwright 持久化浏览器目录，用于保存抖音登录态 |
| options.use_cdp | bool | 否 | `true` | 是否启动/复用 remote debugging 浏览器，推荐保持默认 |
| options.cdp_url | string | 否 | - | 可选，连接已开启 remote debugging 的浏览器 |
| options.cdp_port | int | 否 | Edge `9333` 起 / Chrome `9444` 起 | 本地 remote debugging 端口 |

请求示例：

```json
{
  "profile_url": "https://www.douyin.com/user/MS4wLjABAAAA0VPGcVLBTV9KuvOPi18HdpZGEDnltASrLJOsMDqs5cY?from_tab_name=main",
  "message": "您好，看到您刚才评论里提到想了解这个方向，我先把资料发您看看。",
  "browser": "edge",
  "auto_send": false,
  "account_cookies": {
    "cookies": [
      {"name": "sessionid", "value": "你的 Cookie 值", "domain": ".douyin.com", "path": "/"}
    ]
  }
}
```

响应 `data` 示例：

```json
{
  "profile_url": "https://www.douyin.com/user/MS4wLjABAAAA0VPGcVLBTV9KuvOPi18HdpZGEDnltASrLJOsMDqs5cY?from_tab_name=main",
  "browser": "edge",
  "auto_send": false,
  "message_chars": 31,
  "account_cookie_loaded": true,
  "account_cookie_count": 1,
  "success": true,
  "opened": true,
  "prefilled": true,
  "sent": false,
  "resolved_browser": "edge",
  "engine": "playwright",
  "profile_dir": "D:\\project\\sixin\\aisec-agent\\content\\playwright_profiles\\douyin-edge",
  "steps": [
    {"name": "connect_browser", "ok": true, "detail": "http://127.0.0.1:9333"},
    {"name": "open_profile", "ok": true, "detail": "https://www.douyin.com/user/..."},
    {"name": "click_private_button", "ok": true, "detail": "clicked"},
    {"name": "paste_message", "ok": true, "detail": "draftjs-paste"},
    {"name": "send_message", "ok": false, "detail": "auto_send is disabled; message is only prefilled"}
  ]
}
```

#### POST `/api/douyin/account-cookie/apply`

说明：本地演示用接口。只把账号 Cookie 写入当前选择的 Edge/Chrome Playwright 调试会话，不生成或发送私信。默认不打开抖音首页，适合先跳过登录，再回到调试页点击“生成并演示”。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| account_cookies / account_cookie / cookies / cookie | string/object/array | 是 | - | 账号 Cookie，格式同私信演示接口；建议从浏览器插件导出的 `{"cookies":[...]}` JSON 粘贴 |
| browser / browser_name | string | 否 | `default` | `default`、`edge`、`chrome` |
| options.user_data_dir | string | 否 | `content/playwright_profiles/douyin-{browser}` | Playwright 持久化浏览器目录 |
| options.use_cdp | bool | 否 | `true` | 是否启动/复用 remote debugging 浏览器 |
| options.cdp_url | string | 否 | - | 可选，连接已开启 remote debugging 的浏览器 |
| options.cdp_port | int | 否 | Edge `9333` 起 / Chrome `9444` 起 | 本地 remote debugging 端口 |
| options.home_timeout_ms | int | 否 | `0` | 是否打开抖音首页确认，`0` 表示只写入 Cookie 不打开首页；需要确认时可设为 `5000` |

请求示例：

```json
{
  "browser": "edge",
  "account_cookies": {
    "cookies": [
      {"name": "sessionid", "value": "你的 Cookie 值", "domain": ".douyin.com", "path": "/"}
    ]
  }
}
```

响应 `data` 示例：

```json
{
  "browser": "edge",
  "success": true,
  "opened": false,
  "resolved_browser": "edge",
  "engine": "playwright",
  "account_cookie_loaded": true,
  "account_cookie_count": 1,
  "steps": [
    {"name": "connect_browser", "ok": true, "detail": "http://127.0.0.1:9333"},
    {"name": "apply_account_cookies", "ok": true, "detail": "1 cookies"}
  ]
}
```

### 8. 文件解析与项目资料

#### POST `/api/files/parse`

说明：解析上传文件为纯文本预览。

Content-Type：`multipart/form-data`

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file | file | 是 | 待解析文件 |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| file_name | string | 文件名 |
| extension | string | 扩展名 |
| content | string | 解析后的文本 |
| content_chars | int | 文本字符数 |
| line_count | int | 行数 |

#### GET `/api/project-materials?project_id={project_id}`

说明：获取项目资料文件、场景和知识库文件。

Query：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| projects | array | 项目列表 |
| project | object | 当前项目信息 |
| files | array | 可编辑文件列表 |

`files[]`：

| 字段 | 类型 | 说明 |
|---|---|---|
| file_id | string | 文件 ID |
| title | string | 标题 |
| group | string | 分组 |
| type | string | 文件类型 |
| relative_path | string | 项目内相对路径 |
| editable | bool | 是否可编辑 |
| content | string | 文件内容 |
| chars | int | 字符数 |

#### POST `/api/project-materials/save`

说明：保存项目内指定文件内容。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| relative_path | string | 是 | 项目内相对路径 |
| content | string | 否 | 新内容 |

响应 `data`：保存后的文件项。

#### POST `/api/projects/create`

说明：创建新项目，并生成默认资料结构。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| name | string | 是 | 项目名称 |

响应 `data`：同 `/api/project-materials`，额外包含 `created_project`。

#### POST `/api/project-documents/import`

说明：上传文件并导入到项目知识库。

Content-Type：`multipart/form-data`

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file | file | 是 | 待导入文件 |
| project_id | string | 否 | 项目 ID |
| provider/url/api_key/func_name/model_name/max_len_input | string | 否 | 用于辅助生成文档元信息的模型配置 |

响应 `data`：导入结果，包含 `doc_id`、`relative_path`、`sender_identity` 等。

### 9. 后台管理接口

#### GET `/api/admin/state?project_id={project_id}`

说明：获取后台管理页面完整状态。

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| projects | array | 项目列表 |
| project | object | 当前项目 |
| document_descriptions | object | 知识库文档描述 |
| scene_templates | object | 场景模板配置 |
| activity_settings | object | 活动配置 |
| identity_settings | object | 身份配置 |

#### POST `/api/admin/knowledge/upload`

说明：上传文件到后台知识库，并写入 manifest、chunks 和文档描述。

Content-Type：`multipart/form-data`

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file | file | 是 | 上传文件 |
| project_id | string | 否 | 项目 ID |
| knowledge_base | string | 否 | 知识库名称，默认 `公司私信知识库` |
| domain | string | 否 | 领域 |
| section | string | 否 | 板块 |
| provider/url/api_key/func_name/model_name/max_len_input | string | 否 | 用于辅助生成文档元信息的模型配置 |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| document | object | 文档描述元数据 |
| document_descriptions | object | 更新后的文档描述 |
| manifest_document | object | manifest 记录 |
| absolute_path | string | 写入的本地绝对路径 |

#### POST `/api/admin/knowledge/save`

说明：保存完整知识库文档描述 JSON。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| document_descriptions | object | 是 | 文档描述配置 |

响应 `data`：`document_descriptions`。

#### POST `/api/admin/knowledge/delete`

说明：删除知识库文档，同时清理描述、manifest、chunks 和本地文件。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| doc_id | string | 否 | 文档 ID，和 `relative_path` 二选一 |
| relative_path | string | 否 | 相对路径，和 `doc_id` 二选一 |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| document_descriptions | object | 更新后的文档描述 |
| deleted | object | 删除详情 |

#### POST `/api/admin/knowledge/base/update`

说明：更新知识库名称和描述，必要时移动本地文件夹。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| old_name / name | string | 是 | 原知识库名称 |
| new_name | string | 是 | 新知识库名称 |
| description | string | 否 | 描述 |

响应 `data`：`document_descriptions` 和 `updated`。

#### POST `/api/admin/knowledge/base/delete`

说明：删除整个知识库及其文档。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| name | string | 是 | 知识库名称 |

响应 `data`：`document_descriptions` 和 `deleted`。

#### POST `/api/admin/knowledge/domain/update`

说明：更新知识库下的领域名称和描述，必要时移动本地文件夹。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| knowledge_base | string | 是 | 知识库名称 |
| old_name / name | string | 是 | 原领域名称 |
| new_name | string | 是 | 新领域名称 |
| description | string | 否 | 描述 |

响应 `data`：`document_descriptions` 和 `updated`。

#### POST `/api/admin/knowledge/domain/delete`

说明：删除知识库下的领域及其文档。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| knowledge_base | string | 是 | 知识库名称 |
| name | string | 是 | 领域名称 |

响应 `data`：`document_descriptions` 和 `deleted`。

#### POST `/api/admin/open-file-location`

说明：在系统文件管理器中打开项目文件或文件夹位置。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| project_id | string | 否 | 默认项目 | 项目 ID |
| relative_path | string | 是 | - | 项目内相对路径 |
| type | string | 否 | `file` | `file` 或 `folder` |

响应 `data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| relative_path | string | 规范化相对路径 |
| absolute_path | string | 绝对路径 |
| opened | bool | 是否已打开 |

#### POST `/api/admin/scene-template/save`

说明：新增或更新场景模板。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| template | object | 是 | 模板对象 |

`template`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| template_id | string | 否 | 模板 ID，不传自动生成 |
| title | string | 否 | 标题 |
| purpose | string | 否 | 用途 |
| applicable_scene | string | 否 | 适用场景 |
| tags | array | 否 | 标签，最多保留 12 个 |
| steps | array | 否 | 步骤，最多保留 12 个 |

响应 `data`：`scene_templates` 和 `template`。

#### POST `/api/admin/scene-template/delete`

说明：删除场景模板。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| template_id | string | 是 | 模板 ID |

响应 `data`：`scene_templates`。

#### POST `/api/admin/scene-template/generate`

说明：根据目的描述生成场景模板。可使用内置规则兜底，也可使用模型生成。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| purpose | string | 是 | 模板目的 |
| provider/url/api_key/func_name/model_name/max_len_input | mixed | 否 | 模型配置 |

响应 `data`：`template`。

#### POST `/api/admin/activity/save`

说明：新增或更新活动配置。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| activity | object | 是 | 活动对象 |

`activity`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| activity_id | string | 否 | 自动生成 | 活动 ID |
| title | string | 否 | `未命名活动` | 标题 |
| activity_type / type | string | 否 | `活动` | 活动类型 |
| business_board_id / business_board_name | string | 否 | 空 | 所属业务板块（页面必选） |
| business_module_id / business_module_name | string | 否 | 空 | 所属业务模块（可选） |
| applicable_scene | string | 否 | 空 | 适用场景 |
| description | string | 否 | 空 | 活动描述 |
| benefit / offer | string | 否 | 空 | 活动权益 |
| claim_method | string | 否 | 空 | 领取方式 |
| deadline | string | 否 | 空 | 截止时间 |
| quota | string | 否 | 空 | 名额 |
| compliance_note | string | 否 | 空 | 合规提示 |
| tags | array | 否 | [] | 标签，最多保留 12 个 |
| enabled | bool | 否 | true | 是否启用 |

响应 `data`：`activity_settings` 和 `activity`。

#### POST `/api/admin/activity/delete`

说明：删除活动配置。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| project_id | string | 否 | 项目 ID |
| activity_id | string | 是 | 活动 ID |

响应 `data`：`activity_settings`。

#### POST `/api/admin/prompt/restore`

说明：后台 Prompt 还原/预览接口。能力与 `/api/v1/private-message/prompt-preview` 类似。

入参和响应：同 `/api/v1/private-message/prompt-preview`。

## 二、Flask API 服务接口

默认 Base URL 取决于部署，以下仅列路径。

### 1. Chat 模块 `/api/v0.1/chat`

#### POST `/api/v0.1/chat/completions`

说明：通用 LLM 补全。支持普通 JSON 响应和 SSE 流式响应。

入参 `LLMChatForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 问题 |
| stream | bool | 否 | false | 是否流式返回 |
| agent_id | string | 是 | - | prompt ID |
| topics | array | 否 | [] | 主题/知识库名称 |
| url | string | 是 | - | LLM URL |
| key | string | 是 | - | LLM Key |
| function_name | string | 是 | - | LLM 来源/调用函数 |
| model_name | string | 是 | - | 模型名称 |
| agent_func | string | 否 | null | Agent 方法标识 |
| padding_json | array | 否 | [] | 填充模板 JSON |
| max_len_input | int | 是 | - | 最大输入长度 |
| file_name | string | 否 | null | 文件名称 |
| file_content | string | 否 | null | 文件内容 |
| file_path | array<object> | 否 | [] | 文件路径 |
| prompt | string | 否 | null | Prompt |
| history | array | 否 | [] | 聊天记录 |
| imgs | array | 否 | [] | 图片解析为文本后的内容 |
| uid | string | 否 | null | 用户 ID |
| sid | string | 否 | null | 会话 ID |
| aid | string | 否 | null | 应用 ID |
| duck | bool | 否 | false | 是否使用数据分析 |
| source | bool | 否 | false | 是否多源数据查询 |
| trans | string | 否 | null | 翻译目标语言 |
| deep_search | bool | 否 | false | 是否多源联网搜索 |
| report_typ | enum | 否 | null | `html`、`markdown`、`ppt`、`pptlist`、`ppt_json` |

普通响应 `data`：

```json
{
  "answer": "回复内容",
  "reference": []
}
```

流式响应：`text/event-stream`，每个 chunk 为：

```text
data: {"err_no":0,"msg":"success","data":{"answer":"累计内容","reference":[]}}

```

结束事件：

```text
data: {"err_no":0,"msg":"success","data":true}

```

#### POST `/api/v0.1/chat/agent`

说明：Agent 对话接口。入参同 `/completions`，响应 `data.answer` 和 `data.reference`。

#### POST `/api/v0.1/chat/agent/log`

说明：上传日志文件并进行分析。

Content-Type：`multipart/form-data`

入参 `AnalysisLogForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 问题 |
| stream | bool | 是 | - | 是否流式返回 |
| topics | array | 否 | [] | 主题/知识库名称 |
| file_name | string | 是 | - | 文件名 |
| file | file | 是 | - | 上传文件 |
| prompt_id | string | 是 | - | Prompt UUID |

响应：由日志分析逻辑返回。

#### GET `/api/v0.1/chat/select`

说明：查询模型配置。

Query `LLMConfigGetForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| id | int | 否 | null | LLM ID |
| user_id | int | 是 | - | 创建者 ID |

响应 `data`：模型配置。

#### POST `/api/v0.1/chat/select`

说明：新增模型配置。

入参 `LLMConfigAddForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| url | string | 是 | - | API URL |
| key | string | 否 | null | API Key |
| function_name | string | 是 | - | API 来源 |
| model_name | string | 是 | - | 模型名称 |
| type | string | 否 | null | 模型源类型 |
| max_len_input | int | 是 | - | 最大输入长度 |
| model_type | string | 否 | null | 模型类型 |
| user_id | int | 是 | - | 用户 ID |
| is_public | int | 是 | - | 是否公开 |

响应：空成功响应。

#### PUT `/api/v0.1/chat/select`

说明：编辑模型配置。

入参 `LLMConfigEditForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| id | int | 是 | - | LLM ID |
| url | string | 否 | null | API URL |
| key | string | 否 | null | API Key |
| function_name | string | 否 | null | API 来源 |
| model_name | string | 否 | null | 模型名称 |
| type | string | 否 | null | 模型源类型 |
| max_len_input | int | 否 | null | 最大输入长度 |
| model_type | string | 否 | null | 模型类型 |
| is_public | int | 否 | null | 是否公开 |
| user_id | int | 是 | - | 创建者 ID |

响应：空成功响应。

#### DELETE `/api/v0.1/chat/select`

说明：删除模型配置。

Query：同 GET `/api/v0.1/chat/select`。

响应：空成功响应。

#### GET `/api/v0.1/chat/select/all`

说明：获取所有大模型配置。

响应 `data`：模型配置列表。

#### POST `/api/v0.1/chat/ask`

说明：根据 Agent 生成追问。

入参 `LLMChatAgentForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 问题 |
| stream | bool | 否 | false | 是否流式返回 |
| agent_id | string | 是 | - | Prompt ID |
| topics | array | 否 | [] | 主题/知识库名称 |

响应：业务逻辑原样返回。

#### POST `/api/v0.1/chat/sift/agent`

说明：生成筛选 Agent 内容。入参同 `/ask`。

#### POST `/api/v0.1/chat/sift/report/template`

说明：生成筛选报告模板。入参同 `/ask`。

### 2. Knowledge 模块 `/api/v0.1/knowledge`

#### POST `/api/v0.1/knowledge/file`

说明：接收 MinIO 文件路径，并发布知识库文件处理任务。

入参 `KnowledgeFileForm`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file_id | string | 是 | 文件 ID |
| topic | string | 是 | 主题/知识库名称 |
| file_name | string | 是 | 文件名 |
| file_path | string | 是 | 文件路径 |
| bucket | string | 是 | 桶名 |

响应 `data`：

```json
{
  "file_id": "x",
  "message": "文件已加入处理队列"
}
```

#### GET `/api/v0.1/knowledge/file`

说明：查询文件处理任务状态。

Query：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file_id | string | 是 | 文件 ID |

响应 `data`：任务状态。

#### DELETE `/api/v0.1/knowledge/file`

说明：根据文件 ID 删除知识库内容。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file_id | string | 是 | 文件 ID |
| topic | string | 是 | 主题/知识库名称 |

响应：空成功响应。

#### PUT `/api/v0.1/knowledge/file`

说明：创建 ES 知识库主题。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| topic | string | 是 | 主题/知识库名称 |

响应 `data`：创建结果。

#### POST `/api/v0.1/knowledge/search`

说明：检索知识库。

入参 `KnowledgeSearchForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 查询问题 |
| topics | array | 是 | - | 主题/知识库名称 |
| search_conf | object | 否 | null | 搜索配置 |
| search_conf.top_k | int | 否 | null | 返回数量 |
| search_conf.expansion_factor | int | 否 | null | 扩展系数 |

响应 `data`：检索结果列表。

#### GET `/api/v0.1/knowledge/search`

说明：分页查询知识库内容。

Query `KnowledgeQueryForm`：

| 字段 | 类型 | 必填 | 默认值 | 约束 | 说明 |
|---|---|---:|---|---|---|
| file_id | string | 是 | - | - | 文件 ID |
| topic | string | 是 | - | - | 主题/知识库名称 |
| page | int | 否 | 1 | >= 1 | 页码 |
| page_size | int | 否 | 10 | 1-100 | 每页数量 |
| key_word | string | 否 | null | - | 关键词 |

响应 `data`：分页结果。

#### POST `/api/v0.1/knowledge/content`

说明：新增单条知识库内容。

入参 `EmbeddingContentForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| topic | string | 否 | null | 主题/知识库名称 |
| content | string | 否 | null | 需要向量化的文字 |
| file_id | string | 否 | null | 文件 ID |
| file_name | string | 否 | null | 文件名称 |
| id | string | 否 | null | 记录 ID，新增时通常不传 |

响应：空成功响应。

#### PATCH `/api/v0.1/knowledge/content`

说明：更新单条知识库内容。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| id | string | 是 | 记录 ID |
| topic | string | 是 | 主题/知识库名称 |
| content | string | 是 | 新内容 |

响应：空成功响应。

#### DELETE `/api/v0.1/knowledge/content`

说明：删除单条知识库内容。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| id | string | 是 | 记录 ID |
| topic | string | 是 | 主题/知识库名称 |

响应：空成功响应。

#### PUT `/api/v0.1/knowledge/corpus`

说明：导入 Redis 语料库数据。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| topic | string | 是 | 主题名称 |

响应：空成功响应。

#### POST `/api/v0.1/knowledge/corpus`

说明：检索 Redis 语料库。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 用户问题 |
| topics | array | 否 | null | 主题名称 |

响应 `data`：检索结果。

#### POST `/api/v0.1/knowledge/corpus/config`

说明：强制替换指定 topic 的语料配置。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| id | string | 否 | null | 记录 ID |
| topic | string | 否 | null | 主题/知识库名称 |
| corpus | array | 否 | null | 语料 |

响应：空成功响应。

### 3. Agent 模块 `/api/v0.1/agent`

#### POST `/api/v0.1/agent/{agent_name}`

说明：动态调用 `Agents().{agent_name}`。

入参 `AgentForm`：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| content | string | 是 | - | 输入文本 |
| function_name | string | 否 | null | 调用 LLM 方法名称 |
| url | string | 否 | null | 调用 LLM 链接 |
| model_name | string | 否 | null | 调用模型名称 |
| key | string | 否 | null | 调用模型密钥 |

响应 `data`：Agent 执行结果。

#### POST `/api/v0.1/agent/assets`

说明：资产智能查询。入参同 `AgentForm`。

#### POST `/api/v0.1/agent/hr`

说明：HR 数据自然语言查询。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 问题 |
| stream | bool | 否 | false | 是否流式返回 |

响应 `data`：查询结果。

#### POST `/api/v0.1/agent/hr/split`

说明：人事数据拆分。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| question | string | 是 | - | 问题 |
| stream | bool | 否 | false | 是否流式返回 |
| datas | array | 是 | - | 人事数据 |

响应 `data`：拆分结果。

#### POST `/api/v0.1/agent/daily`

说明：日报数据查询。入参同 `/api/v0.1/agent/hr`。

#### POST `/api/v0.1/agent/parse/firewallLog`

说明：解析防火墙日志。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| text | string | 是 | AI 需要处理的文本 |

响应 `data`：解析结果。

#### POST `/api/v0.1/agent/deco/task`

说明：任务拆解。入参同 `/api/v0.1/agent/hr`。

#### POST `/api/v0.1/agent/image2text`

说明：图片转文本描述。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| image_base64 | string | 是 | Base64 编码的图片内容 |

响应 `data`：图片描述结果。

#### POST `/api/v0.1/agent/tidyup`

说明：整理数据库字段。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| db_id | int | 是 | 数据库 ID |
| flag | bool | 是 | 是否立即更新 |

响应 `data`：整理结果。

#### POST `/api/v0.1/agent/pentest/summary`

说明：生成渗透测试笔记摘要。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| plan | string | 是 | 计划名称 |
| note | string | 是 | 笔记内容 |

响应 `data`：摘要结果。

### 4. Task 模块 `/api/v0.1/task`

#### POST `/api/v0.1/task/assets`

说明：根据资产信息查询漏洞。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| content | string | 是 | 资产信息 |

响应 `data`：漏洞查询结果。

#### PUT `/api/v0.1/task/assets`

说明：预处理/创建漏洞数据。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| content | string | 是 | 资产信息 |

响应：空成功响应。

### 5. ASR 模块 `/api/v0.1/asr`

#### POST `/api/v0.1/asr/to_text`

说明：语音文件转文本。

Content-Type：`multipart/form-data`

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| file | file | 是 | 音频文件 |

响应 `data`：识别出的文本。

### 6. Prompt 模块 `/api/v0.1/prompt`

#### GET `/api/v0.1/prompt/config`

说明：读取 Prompt 配置。

Query：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| agent_id | string | 是 | Agent ID |

响应：PromptLogic 原样返回。

#### PUT `/api/v0.1/prompt/config`

说明：修改 Prompt 配置。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| agent_id | string | 是 | Agent ID |
| prompt | string | 是 | 修改后的 Prompt |

响应：PromptLogic 原样返回。

#### GET `/api/v0.1/prompt/config/recover`

说明：读取备份 Prompt 配置。

Query：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| agent_id | string | 是 | Agent ID |

响应：PromptLogic 原样返回。

### 7. MCP 模块 `/api/v0.1/mcp`

#### POST `/api/v0.1/mcp/tools`

说明：获取远程 MCP 服务工具列表。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| services | array<object> | 是 | 远程 MCP 服务列表 |

响应 `data`：工具列表。

#### GET `/api/v0.1/mcp/service`

说明：获取 MCP 服务列表。

响应 `data`：服务列表。

#### POST `/api/v0.1/mcp/call`

说明：调用 MCP 工具。

入参：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| services | array<object> | 是 | - | 远程 MCP 服务列表 |
| question | string | 是 | - | 用户问题 |
| tool_name | string | 否 | null | 工具名称 |

响应 `data`：调用结果。

#### POST `/api/v0.1/mcp/tools/info`

说明：获取 MCP 工具详情。

入参：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| services | array<object> | 是 | 远程 MCP 服务列表 |

响应 `data`：工具详情。

## 三、响应示例汇总

本节给出常用接口的响应样例。实际 `data` 里的业务字段会随项目资料、模型输出、知识库内容变化。

### 1. 本地页面服务响应示例

#### GET `/api/v1/health`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "version": "v1"
  }
}
```

#### GET `/api/v1/presets`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "providers": {
      "minimax": {
        "label": "MiniMax",
        "url": "https://api.minimaxi.com/anthropic",
        "func_name": "minimax_anthropic_chat",
        "model_name": "MiniMax-M3",
        "requires_key": true
      }
    },
    "defaults": {
      "provider": "minimax",
      "session_id": "9d0a9b58b5ab4f3ea2a7859e0f8a8b4e",
      "user_id": "default_user",
      "project_id": "project_a85d890a904f",
      "conversation_stage": "first_comment",
      "max_len_input": 16000,
      "summary_max_chars": 4000,
      "raw_max_chars": 12000,
      "knowledge_size": 4
    }
  }
}
```

#### GET `/api/v1/projects`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "projects": [
      {
        "project_id": "project_a85d890a904f",
        "name": "默认项目",
        "default_scene": "auto"
      }
    ],
    "default_project_id": "project_a85d890a904f"
  }
}
```

#### GET `/api/v1/model/configs`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "minimax": {
      "provider": "minimax",
      "url": "https://api.minimaxi.com/anthropic",
      "func_name": "minimax_anthropic_chat",
      "model_name": "MiniMax-M3",
      "max_len_input": 16000,
      "has_api_key": true,
      "api_key_masked": "sk-m...1234"
    }
  }
}
```

#### POST `/api/v1/model/config/save`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "config": {
      "provider": "minimax",
      "url": "https://api.minimaxi.com/anthropic",
      "func_name": "minimax_anthropic_chat",
      "model_name": "MiniMax-M3",
      "max_len_input": 16000,
      "has_api_key": true,
      "api_key_masked": "sk-m...1234",
      "updated_at": "2026-07-13T10:20:00"
    },
    "configs": {
      "minimax": {
        "provider": "minimax",
        "has_api_key": true,
        "api_key_masked": "sk-m...1234"
      }
    }
  }
}
```

#### POST `/api/v1/model/config/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "minimax": {
      "provider": "minimax",
      "has_api_key": false,
      "api_key_masked": ""
    }
  }
}
```

#### POST `/api/v1/model/test`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "provider": "minimax",
    "url": "https://api.minimaxi.com/anthropic",
    "func_name": "minimax_anthropic_chat",
    "model_name": "MiniMax-M3",
    "reply": "OK"
  }
}
```

#### POST `/api/v1/private-message/generate`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "input": {
      "question": "我妈膝盖上下楼疼，干细胞这个适合吗",
      "session_id": "postman-test-001",
      "user_id": "user-postman",
      "conversation_stage": "first_comment",
      "project_id": "project_a85d890a904f",
      "video_overview": "视频讲膝骨关节、骨积液和干细胞评估方向。"
    },
    "reply": "看您描述像是膝关节不适比较明显，是否适合要先看年龄、片子情况和疼痛时间。您方便说下老人家多大、疼了多久吗？",
    "prompts": [
      {
        "key": "user_context",
        "title": "用户评论以及上下文",
        "content": "用户评论：\n我妈膝盖上下楼疼，干细胞这个适合吗",
        "chars": 31
      },
      {
        "key": "final_prompt",
        "title": "最终完整 Prompt",
        "content": "<user_context>...</user_context>",
        "chars": 12000
      }
    ],
    "config": {
      "provider": "minimax",
      "api": "https://api.minimaxi.com/anthropic",
      "func": "minimax_anthropic_chat",
      "model": "MiniMax-M3",
      "max_len": 16000,
      "key_source": "request"
    }
  }
}
```

#### POST `/api/chat`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "可以先简单了解下情况，膝盖疼多久了？",
    "result": {
      "answer": "可以先简单了解下情况，膝盖疼多久了？",
      "enough_info": true,
      "second_pass": false,
      "missing_info": "",
      "used_knowledge": [],
      "knowledge": [],
      "memory_id": "memory-1",
      "memory_error": ""
    },
    "session_id": "sid-web",
    "user_id": "default_user",
    "provider": "minimax",
    "project": {
      "project_id": "project_a85d890a904f",
      "project_name": "默认项目",
      "scene_id": "health_presales",
      "scene_name": "大健康医疗售前"
    },
    "project_documents": {
      "documents": [
        {
          "doc_id": "healthcare_medical",
          "title": "大健康医疗板块",
          "relative_path": "knowledge/files/公司私信知识库/三大核心业务板块/大健康医疗板块.md"
        }
      ],
      "document_ids": ["healthcare_medical"],
      "reason": "matched_health_keywords"
    },
    "sender_identity": "健康顾问助理",
    "sender_identity_source": {
      "source": "document"
    },
    "scene_template": {
      "template_id": "tpl_first_dm",
      "title": "首次私信承接模板"
    },
    "activity_settings": {},
    "config": {
      "url": "https://api.minimaxi.com/anthropic",
      "func_name": "minimax_anthropic_chat",
      "model_name": "MiniMax-M3",
      "max_len_input": 16000,
      "key_source": "request",
      "conversation_stage": "first_comment"
    },
    "prompt_trace": {
      "final_prompt": "<system>...</system>",
      "prompt_modules": []
    }
  }
}
```

#### POST `/api/chat/stream`

响应头：`Content-Type: text/event-stream; charset=utf-8`

```text
data: {"type":"status","message":"selecting_project_documents"}

data: {"type":"status","message":"reading_memory"}

data: {"type":"status","message":"streaming_answer"}

data: {"type":"delta","text":"可以先了解下"}

data: {"type":"delta","text":"膝盖疼多久了？"}

data: {"type":"done","data":{"answer":"可以先了解下膝盖疼多久了？","session_id":"sid-stream","result":{"answer":"可以先了解下膝盖疼多久了？","second_pass":false}}}

```

#### POST `/api/v1/business/reply`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "input": {
      "question": "你好，我想了解销售岗位",
      "session_id": "boss_user_001",
      "user_id": "default_user",
      "caller": "boss直聘",
      "business_scene": "机器人面试",
      "user_identity": {
        "name": "求职者",
        "job": "销售岗",
        "platform": "boss直聘",
        "experience": "3年SaaS销售经验"
      }
    },
    "reply": "请问怎么称呼？目前应聘的是销售岗对吗？",
    "prompts": [
      {
        "key": "business_route",
        "title": "业务路由",
        "content": "调用方：boss直聘\n业务场景：机器人面试\n知识库：招聘知识库",
        "chars": 46
      },
      {
        "key": "interview_control_state",
        "title": "面试问题控制状态",
        "content": "求职者真实姓名或应聘岗位尚未确认，本轮必须先同时确认姓名和岗位。",
        "chars": 34
      },
      {
        "key": "final_prompt",
        "title": "最终完整 Prompt",
        "content": "<business_route>...</business_route>",
        "chars": 9000
      }
    ],
    "config": {
      "provider": "minimax",
      "api": "https://api.minimaxi.com/anthropic",
      "func": "minimax_anthropic_chat",
      "model": "MiniMax-M3",
      "max_len": 16000,
      "key_source": "request"
    }
  }
}
```

#### POST `/api/v1/private-message/prompt-preview`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "question": "这个能自动回复抖音评论吗",
    "selector": {
      "document_ids": ["ai_private_message_system"],
      "reason": "rule_fallback",
      "used_ai_selector": false
    },
    "selected_documents": [
      {
        "doc_id": "ai_private_message_system",
        "title": "AI私信系统私信资料",
        "relative_path": "knowledge/files/公司私信知识库/评论转私信/相关文档/AI私信系统私信资料.md",
        "content": "..."
      }
    ],
    "sender_identity": "智能客服助理",
    "sender_identity_source": {
      "source": "document"
    },
    "scene_template": {
      "template_id": "tpl_x",
      "title": "评论转私信模板"
    },
    "activity_settings": {},
    "prompt_modules": [
      {
        "key": "final_prompt",
        "title": "最终完整 Prompt",
        "content": "<user_context>...</user_context>",
        "chars": 8500
      }
    ],
    "final_prompt": "<user_context>...</user_context>"
  }
}
```

#### POST `/api/v1/private-message/route-debug`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "question": "想做AI批量剪辑和自动发布",
    "routing_input": "用户评论：\n想做AI批量剪辑和自动发布",
    "conversation_stage": "first_comment",
    "project": {
      "project_id": "project_a85d890a904f",
      "project_name": "默认项目",
      "scene_id": "ai_hardware_presales",
      "scene_name": "AI技术与智能硬件售前",
      "scene_reason": "auto_match_from_latest_input_and_session_summary"
    },
    "project_documents": {
      "document_ids": ["ai_technology_hardware"],
      "documents": [
        {
          "doc_id": "ai_technology_hardware",
          "title": "AI技术与智能硬件板块"
        }
      ],
      "reason": "matched_ai_keywords"
    },
    "sender_identity": "智能客服助理",
    "sender_identity_source": {
      "source": "document"
    },
    "scene_template": {},
    "activity_settings": {},
    "project_context": "<user_context>...</user_context>",
    "prompt": "<system>...</system>",
    "prompt_modules": [],
    "selector": {
      "used_ai_selector": false,
      "reason": "matched_ai_keywords"
    }
  }
}
```

#### POST `/api/files/parse`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "file_name": "demo.txt",
    "extension": ".txt",
    "content": "第一行\n第二行",
    "content_chars": 5,
    "line_count": 2
  }
}
```

#### GET `/api/project-materials`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "projects": [
      {
        "project_id": "project_a85d890a904f",
        "name": "默认项目"
      }
    ],
    "project": {
      "project_id": "project_a85d890a904f",
      "name": "默认项目",
      "default_scene": "auto",
      "path": "D:\\project\\sixin\\aisec-agent\\content\\session_rag_projects\\project_a85d890a904f",
      "scenes": [
        {
          "scene_id": "health_presales",
          "name": "大健康医疗售前"
        }
      ]
    },
    "files": [
      {
        "file_id": "global_prompt.md",
        "title": "全局统一提示词",
        "group": "项目全局",
        "type": "global_prompt",
        "relative_path": "global_prompt.md",
        "editable": true,
        "content": "...",
        "chars": 1200
      }
    ]
  }
}
```

#### POST `/api/project-materials/save`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "file_id": "global_prompt.md",
    "title": "global_prompt.md",
    "group": "已保存",
    "type": "saved_file",
    "relative_path": "global_prompt.md",
    "editable": true,
    "content": "新的提示词内容",
    "chars": 7
  }
}
```

#### POST `/api/projects/create`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "projects": [
      {
        "project_id": "project_f3a1b2c3d4e5",
        "name": "新项目"
      }
    ],
    "project": {
      "project_id": "project_f3a1b2c3d4e5",
      "name": "新项目",
      "default_scene": "auto",
      "path": "D:\\project\\sixin\\aisec-agent\\content\\session_rag_projects\\project_f3a1b2c3d4e5",
      "scenes": []
    },
    "files": [],
    "created_project": {
      "project_id": "project_f3a1b2c3d4e5",
      "name": "新项目"
    }
  }
}
```

#### POST `/api/project-documents/import`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "doc_id": "imported_abc123",
    "title": "关节活动资料",
    "relative_path": "knowledge/files/公司私信知识库/导入资料/默认板块/关节活动资料.md",
    "sender_identity": "健康顾问助理",
    "chunk_count": 3
  }
}
```

#### GET `/api/admin/state`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "projects": [
      {
        "project_id": "project_a85d890a904f",
        "name": "默认项目"
      }
    ],
    "project": {
      "project_id": "project_a85d890a904f",
      "name": "默认项目",
      "path": "D:\\project\\sixin\\aisec-agent\\content\\session_rag_projects\\project_a85d890a904f"
    },
    "document_descriptions": {
      "version": 1,
      "project_id": "project_a85d890a904f",
      "knowledge_bases": []
    },
    "scene_templates": {
      "version": 1,
      "templates": []
    },
    "activity_settings": {
      "version": 1,
      "activities": []
    },
    "identity_settings": {
      "version": 1,
      "accounts": [],
      "products": []
    }
  }
}
```

#### POST `/api/admin/knowledge/upload`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document": {
      "doc_id": "admin_b8c4a2d910ef",
      "title": "AI项目经理招聘说明",
      "knowledge_base": "招聘知识库",
      "domain": "岗位资料",
      "section": "AI项目经理",
      "source_file_name": "AI项目经理招聘说明.txt",
      "relative_path": "knowledge/files/招聘知识库/岗位资料/AI项目经理/AI项目经理招聘说明.md",
      "description": "从 AI项目经理招聘说明.txt 解析入库的资料",
      "summary": "岗位职责包括需求梳理、项目推进、AI工具落地。",
      "tags": ["AI项目经理", "招聘"],
      "sender_identity": "招聘助理",
      "content_chars": 120,
      "chunk_count": 1,
      "updated_at": "2026-07-13T10:20:00"
    },
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": []
    },
    "manifest_document": {
      "doc_id": "admin_b8c4a2d910ef",
      "title": "AI项目经理招聘说明",
      "chunk_count": 1
    },
    "absolute_path": "D:\\project\\sixin\\aisec-agent\\content\\session_rag_projects\\project_a85d890a904f\\knowledge\\files\\招聘知识库\\岗位资料\\AI项目经理\\AI项目经理招聘说明.md"
  }
}
```

#### POST `/api/admin/knowledge/save`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "project_id": "project_a85d890a904f",
      "updated_at": "2026-07-13T10:20:00",
      "knowledge_bases": []
    }
  }
}
```

#### POST `/api/admin/knowledge/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": []
    },
    "deleted": {
      "doc_id": "admin_b8c4a2d910ef",
      "relative_path": "knowledge/files/招聘知识库/岗位资料/AI项目经理/AI项目经理招聘说明.md",
      "file_deleted": true,
      "manifest_removed": true,
      "chunks_removed": 1
    }
  }
}
```

#### POST `/api/admin/knowledge/base/update`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": [
        {
          "name": "招聘知识库",
          "description": "招聘资料"
        }
      ]
    },
    "updated": {
      "type": "knowledge_base",
      "old_name": "招聘资料库",
      "new_name": "招聘知识库",
      "folder_moved": true
    }
  }
}
```

#### POST `/api/admin/knowledge/base/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": []
    },
    "deleted": {
      "type": "knowledge_base",
      "name": "招聘知识库",
      "documents": 3,
      "manifest_removed": 3,
      "chunks_removed": 9,
      "folder_deleted": true
    }
  }
}
```

#### POST `/api/admin/knowledge/domain/update`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": []
    },
    "updated": {
      "type": "domain",
      "knowledge_base": "招聘知识库",
      "old_name": "岗位资料",
      "new_name": "岗位说明",
      "folder_moved": true
    }
  }
}
```

#### POST `/api/admin/knowledge/domain/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "document_descriptions": {
      "version": 1,
      "knowledge_bases": []
    },
    "deleted": {
      "type": "domain",
      "knowledge_base": "招聘知识库",
      "name": "岗位资料",
      "documents": 2,
      "manifest_removed": 2,
      "chunks_removed": 6,
      "folder_deleted": true
    }
  }
}
```

#### POST `/api/admin/open-file-location`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "relative_path": "global_prompt.md",
    "absolute_path": "D:\\project\\sixin\\aisec-agent\\content\\session_rag_projects\\project_a85d890a904f\\global_prompt.md",
    "opened": true
  }
}
```

#### POST `/api/admin/scene-template/save`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "scene_templates": {
      "version": 1,
      "templates": [
        {
          "template_id": "tpl_first_dm",
          "title": "首次私信承接模板"
        }
      ]
    },
    "template": {
      "template_id": "tpl_first_dm",
      "title": "首次私信承接模板",
      "purpose": "首次私信用户，期望回复",
      "applicable_scene": "用户评论后第一次主动私信触达。",
      "tags": ["首次私信", "评论承接"],
      "steps": ["承接评论", "提出一个问题"],
      "updated_at": "2026-07-13T10:20:00"
    }
  }
}
```

#### POST `/api/admin/scene-template/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "scene_templates": {
      "version": 1,
      "templates": []
    }
  }
}
```

#### POST `/api/admin/scene-template/generate`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "template": {
      "template_id": "tpl_abc123def0",
      "title": "抖音评论私信引导模板",
      "purpose": "抖音评论后私信引导用户添加微信 wx_test_001",
      "applicable_scene": "适用于抖音上用户通过评论表达兴趣后，用私信自然承接。",
      "tags": ["抖音", "评论承接", "私信"],
      "steps": [
        "读取用户评论原文，判断用户意图。",
        "承接评论中的具体词和痛点。",
        "自然引导用户添加或前往 wx_test_001。"
      ],
      "updated_at": "2026-07-13T10:20:00"
    }
  }
}
```

#### POST `/api/admin/activity/save`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "activity_settings": {
      "version": 1,
      "activities": [
        {
          "activity_id": "act_trial",
          "title": "AI系统试跑体验名额"
        }
      ]
    },
    "activity": {
      "activity_id": "act_trial",
      "title": "AI系统试跑体验名额",
      "activity_type": "体验名额",
      "applicable_scene": "用户咨询自动回复、评论采集、私信系统时使用。",
      "description": "",
      "benefit": "免费试跑一个视频评论批次。",
      "claim_method": "先确认账号数量和评论量，再安排样例试跑。",
      "deadline": "",
      "quota": "",
      "compliance_note": "",
      "tags": ["AI私信", "自动回复"],
      "enabled": true,
      "updated_at": "2026-07-13T10:20:00"
    }
  }
}
```

#### POST `/api/admin/activity/delete`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "activity_settings": {
      "version": 1,
      "activities": []
    }
  }
}
```

### 2. Flask API 响应示例

#### POST `/api/v0.1/chat/completions`

普通响应：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "这是模型回复内容。",
    "reference": []
  }
}
```

流式响应：

```text
data: {"code":0,"msg":"success","data":{"answer":"这是","reference":[]}}

data: {"code":0,"msg":"success","data":{"answer":"这是模型回复","reference":[]}}

data: {"code":0,"msg":"success","data":true}

```

异常响应：

```json
{
  "code": 500,
  "msg": "Traceback 或异常信息",
  "data": null
}
```

#### POST `/api/v0.1/chat/agent`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "Agent 回复内容",
    "reference": []
  }
}
```

#### POST `/api/v0.1/chat/agent/log`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "日志分析结论",
    "risk_level": "medium",
    "evidence": []
  }
}
```

#### GET `/api/v0.1/chat/select`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "id": 1,
      "url": "https://api.minimaxi.com/anthropic",
      "function_name": "minimax_anthropic_chat",
      "model_name": "MiniMax-M3",
      "type": "minimax",
      "max_len_input": 16000,
      "model_type": "chat",
      "user_id": 1,
      "is_public": 1
    }
  ]
}
```

#### POST `/api/v0.1/chat/select`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### GET `/api/v0.1/chat/select/all`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "id": 1,
      "model_name": "MiniMax-M3",
      "function_name": "minimax_anthropic_chat"
    }
  ]
}
```

#### POST `/api/v0.1/chat/ask`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "questions": [
      "请补充资产所属业务系统。",
      "是否有公网访问地址？"
    ]
  }
}
```

#### POST `/api/v0.1/chat/sift/agent`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "agent": "筛选 Agent 内容",
    "questions": []
  }
}
```

#### POST `/api/v0.1/chat/sift/report/template`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "template": "报告模板内容"
  }
}
```

#### POST `/api/v0.1/knowledge/file`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "file_id": "file_001",
    "message": "文件已加入处理队列"
  }
}
```

#### GET `/api/v0.1/knowledge/file`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "file_id": "file_001",
    "status": "finished",
    "progress": 100,
    "error": ""
  }
}
```

#### PUT `/api/v0.1/knowledge/file`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "topic": "company_kb",
    "created": true
  }
}
```

#### DELETE `/api/v0.1/knowledge/file`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### POST `/api/v0.1/knowledge/search`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "id": "chunk_001",
      "topic": "company_kb",
      "file_id": "file_001",
      "file_name": "公司定位.md",
      "content": "公司定位相关内容",
      "score": 0.89
    }
  ]
}
```

#### GET `/api/v0.1/knowledge/search`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "list": [
      {
        "id": "chunk_001",
        "file_id": "file_001",
        "content": "知识库内容"
      }
    ],
    "page": 1,
    "page_size": 10,
    "total": 1
  }
}
```

#### POST `/api/v0.1/knowledge/content`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### PATCH `/api/v0.1/knowledge/content`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### DELETE `/api/v0.1/knowledge/content`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### PUT `/api/v0.1/knowledge/corpus`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### POST `/api/v0.1/knowledge/corpus`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "id": "corpus_001",
      "topic": "company_kb",
      "content": "命中的语料内容",
      "score": 0.86
    }
  ]
}
```

#### POST `/api/v0.1/knowledge/corpus/config`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### POST `/api/v0.1/agent/{agent_name}`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "result": "指定 Agent 的执行结果"
  }
}
```

#### POST `/api/v0.1/agent/assets`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "sql": "SELECT ...",
    "rows": []
  }
}
```

#### POST `/api/v0.1/agent/hr`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "查询到 3 条相关人事数据。",
    "rows": []
  }
}
```

#### POST `/api/v0.1/agent/hr/split`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "name": "张三",
      "matched": true,
      "reason": "符合查询条件"
    }
  ]
}
```

#### POST `/api/v0.1/agent/daily`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "answer": "日报查询结果",
    "items": []
  }
}
```

#### POST `/api/v0.1/agent/parse/firewallLog`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "source_ip": "192.168.1.10",
    "destination_ip": "10.0.0.2",
    "action": "deny",
    "summary": "疑似异常访问被拦截"
  }
}
```

#### POST `/api/v0.1/agent/deco/task`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "tasks": [
      {
        "title": "收集资产清单",
        "priority": "high"
      }
    ]
  }
}
```

#### POST `/api/v0.1/agent/image2text`

```json
{
  "code": 0,
  "msg": "success",
  "data": "图片中包含一张系统告警截图，主要内容为登录失败次数过多。"
}
```

#### POST `/api/v0.1/agent/tidyup`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "db_id": 1,
    "updated": true,
    "fields": []
  }
}
```

#### POST `/api/v0.1/agent/pentest/summary`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "plan": "内网渗透测试",
    "summary": "本次记录包含资产发现、弱口令验证和风险建议。",
    "risks": []
  }
}
```

#### POST `/api/v0.1/task/assets`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "asset": "nginx 1.18",
      "vuln": "CVE-xx-yyyy",
      "level": "medium",
      "advice": "升级到安全版本"
    }
  ]
}
```

#### PUT `/api/v0.1/task/assets`

```json
{
  "code": 0,
  "msg": "success",
  "data": null
}
```

#### POST `/api/v0.1/asr/to_text`

```json
{
  "code": 0,
  "msg": "success",
  "data": "识别出的音频文本内容"
}
```

#### GET `/api/v0.1/prompt/config`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "agent_id": "chat_agent",
    "prompt": "你是一个安全分析助手..."
  }
}
```

#### PUT `/api/v0.1/prompt/config`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "agent_id": "chat_agent",
    "updated": true
  }
}
```

#### GET `/api/v0.1/prompt/config/recover`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "agent_id": "chat_agent",
    "prompt": "备份 Prompt 内容..."
  }
}
```

#### POST `/api/v0.1/mcp/tools`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "service": "demo-mcp",
      "tools": [
        {
          "name": "search",
          "description": "搜索工具"
        }
      ]
    }
  ]
}
```

#### GET `/api/v0.1/mcp/service`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "name": "demo-mcp",
      "url": "http://127.0.0.1:9000"
    }
  ]
}
```

#### POST `/api/v0.1/mcp/call`

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "tool_name": "search",
    "result": "工具调用结果"
  }
}
```

#### POST `/api/v0.1/mcp/tools/info`

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "service": "demo-mcp",
      "name": "search",
      "description": "搜索工具",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string"
          }
        }
      }
    }
  ]
}

```

### 3. 通用错误响应示例

参数缺失：

```json
{
  "code": 400,
  "msg": "question is required",
  "data": null
}
```

路径不存在：

```json
{
  "code": 404,
  "msg": "not found",
  "data": null
}
```

服务异常：

```json
{
  "code": 500,
  "msg": "exception error ...",
  "data": null
}
```

## HTTP 审计日志

#### GET `/api/admin/http-audit-logs?limit=50&offset=0`

返回最近的请求审计记录，包含 `remote_ip`、`method`、`path`、`query`、`body`、`status`、`response`、`timestamp`。

#### POST `/api/admin/http-audit-logs/clear`

清空审计日志。
