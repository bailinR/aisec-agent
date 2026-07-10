# aisec-agent 本地启动说明

这是一个本地会话 RAG 智能体演示项目，当前重点包含：

- 本地聊天页面：模拟用户评论、首次私信、后续私信回复。
- 模型配置页面化：在页面中配置 API Key、模型、接口地址等。
- 项目资料/知识库占位：后续可继续完善资料入库和检索。
- 文件解析预览页面：上传 txt、Word、PDF、Excel 等文件，查看解析后的文本。

## 环境准备

建议使用 Python 3.10+。

在项目根目录执行：

```powershell
cd E:\project\aisec-agent\aisec-agent
python -m pip install -r requirements.txt
```

`requirements.txt` 中的文档解析依赖使用本地 wheel：

```text
E:\docs\file_extractor-0.1.0-py3-none-any.whl
```

如果只是启动当前本地页面，部分重型依赖暂时用不到。PDF 解析依赖已经在 `requirements.txt` 中配置为 `PyMuPDF` 和 `pypdf`；旧版 xls 如需解析，建议安装 `pandas`、`xlrd`，或先另存为 xlsx 后再上传。

## 启动本地页面

在项目根目录执行：

```powershell
cd E:\project\aisec-agent\aisec-agent
python -m aisec_agent.web --host 127.0.0.1 --port 7860 --no-open
```

启动成功后，终端会显示类似：

```text
Session RAG chat page: http://127.0.0.1:7860
```

浏览器打开：

```text
http://127.0.0.1:7860
```

或：

```text
http://127.0.0.1:7860/session-rag-chat
```

## 文件解析页面

启动服务后，打开：

```text
http://127.0.0.1:7860/file-parser
```

这个页面用于上传资料文件并查看解析后的纯文本，方便后续确认是否要导入知识库。

当前支持的常见格式：

- txt、md、log
- csv、json、xml
- html、css、js、py、java
- docx
- xlsx
- pdf
- xls

说明：

- docx、xlsx 已有本地标准库兜底解析。
- pdf 使用 `PyMuPDF` 优先解析，失败时会尝试 `pypdf` 或 `pdfplumber`。
- xls 依赖 pandas、xlrd，或者可先另存为 xlsx 后再上传。

## 页面配置模型

聊天页面右侧可以配置模型信息：

- 模型供应商
- API Key
- 接口 URL
- 模型名称
- 调用函数
- 最大上下文长度

当前可以优先使用第三方 API，例如 MiniMax、DeepSeek；后续也可以切换到 Ollama、本地模型或 OpenAI 兼容接口。

DeepSeek 页面默认预设：

- Provider：DeepSeek
- URL：`https://api.deepseek.com/chat/completions`
- 模型：`deepseek-v4-flash`
- 函数：`deepseek_chat`

在页面右侧选择 DeepSeek 后填写 API Key，点击“保存配置”即可。不同 Provider 的 API Key 会分别保存在本机浏览器配置里，切换模型时会跟随切换。

## 常用入口

```text
聊天页面：http://127.0.0.1:7860/session-rag-chat
文件解析：http://127.0.0.1:7860/file-parser
健康检查：http://127.0.0.1:7860/api/health
```

## Postman API 测试

服务启动后，可直接用 Postman 调用以下接口。

### 招聘/业务回复

```text
POST http://127.0.0.1:7860/api/v1/business/reply
Content-Type: application/json
```

示例 Body：

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
    "provider": "deepseek"
  }
}
```

`user_identity` 必须是对象，不接受字符串。常用字段：

- `name`：候选人真实称呼。未知时可传 `求职者`、`候选人`，接口会先同时确认“怎么称呼”和“应聘岗位”。
- `job`：岗位或产品线索，例如 `销售岗`。
- `platform`：来源平台，例如 `boss直聘`。
- `experience`：背景信息，例如 `3年SaaS销售经验`。
- `note`：其他补充信息。

返回里重点看这些字段：

- `answer`：最终建议发送给候选人的回复。
- `dialogue_list` / `conversation_history`：当前 session 的对话列表，包含历史轮次和本轮消息。
- `prompt_trace.final_prompt`：最终发给 AI 的完整 Prompt。
- `prompt_trace.opening_control`：是否触发“姓名+岗位一起确认”的开场控制。
- `prompt_trace.answer_guard`：如果模型没有按顺序提问，这里会显示是否被程序纠偏以及原因。

### 生成私信

```text
POST http://127.0.0.1:7860/api/v1/private-message/generate
Content-Type: application/json
```

示例 Body：

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
    "provider": "deepseek",
    "api_key": "YOUR_DEEPSEEK_API_KEY"
  }
}
```

常用字段：

- `comment`：用户评论或用户私信内容。
- `video_overview`：视频概述。
- `stage`：`comment` 表示评论转首次私信；`private` / `followup` 表示后续私信维护。
- `project_id`：项目 ID，不传时使用默认项目。
- `session_id`：会话 ID，同一个用户连续沟通应保持一致。
- `account_id`：账号身份配置，例如 `douyin_health_assistant`。
- `product_id`：产品身份配置，例如 `joint_assessment`。
- `model.provider`：`minimax`、`deepseek`、`ollama`、`openai_compatible` 等。
- `model.api_key`：第三方模型 API Key。

### Prompt 预览

只查看会读取哪些资料、组装哪些 prompt，不真正生成回复：

```text
POST http://127.0.0.1:7860/api/v1/private-message/prompt-preview
Content-Type: application/json
```

示例 Body：

```json
{
  "project_id": "project_a85d890a904f",
  "comment": "这个能自动回复抖音评论吗",
  "video_overview": "视频演示评论采集、知识库匹配和私信生成。"
}
```

### 路由调试

查看当前评论会匹配哪个项目场景、哪些知识库文档、什么人员身份：

```text
POST http://127.0.0.1:7860/api/v1/private-message/route-debug
Content-Type: application/json
```

### 测试模型配置

```text
POST http://127.0.0.1:7860/api/v1/model/test
Content-Type: application/json
```

示例 Body：

```json
{
  "model": {
    "provider": "deepseek",
    "api_key": "YOUR_DEEPSEEK_API_KEY"
  }
}
```

## 运行测试

```powershell
cd E:\project\aisec-agent\aisec-agent
python -m unittest tests.test_session_rag_web tests.test_project_materials tests.test_session_rag_chat
```

## 停止服务

在启动服务的终端中按：

```text
Ctrl + C
```
