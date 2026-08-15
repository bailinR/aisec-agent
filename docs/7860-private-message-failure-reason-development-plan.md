# 7860 私信发送失败原因识别功能修改计划

## 1. 修改目标

本计划只描述 `aisec-agent` 7860 服务侧的功能代码修改，不包含时间排期、数据中台改造或部署排期。

当抖音页面在本次发送后明确提示接收方不允许当前账号发送私信时，系统需要立即识别并返回：

```text
failure_code: recipient_privacy_restriction
failure_stage: send_confirm
failure_category: recipient
failure_type: final
retryable: false
manual_required: false
```

任务必须进入 `failed` 终态并保留失败截图；不得进入重试队列、人工接管队列，也不得冷却、停用或标记发送账号异常。

## 2. 修改文件和职责

### `aisec_agent/web/session_rag_chat.py`

在现有私信发送链路中完成以下修改：

- `DM_FAILURE_PROFILES`：增加接收方隐私限制的失败元数据。
- 新增发送后平台提示检测函数：只读取本次发送相关的可见 DOM 提示。
- `_dm_wait_message_sent()`：将平台明确失败提示纳入发送确认轮询。
- `_dm_infer_failure_code()`：增加稳定错误码推断规则，并避免与账号风控规则冲突。
- `process_douyin_dm_task_once()`：将新错误归类为不可重试的最终失败。
- `_dm_failure_metadata()` / `_dm_task_failure_fields()`：确保失败字段由稳定错误码生成。
- `_dm_task_result()`：确保任务查询接口返回新失败字段和截图 URL。

### `tests/test_session_rag_web.py`

- 使用现有 Fake Page、Fake Locator 和 Fake Redis 编写单元测试。
- 覆盖提示识别、成功/失败优先级、误判防护、队列状态和接口结果。
- 不依赖真实抖音网络或真实浏览器登录态。

## 3. 具体代码修改步骤

### 步骤一：增加失败配置

在 `DM_FAILURE_PROFILES` 中加入：

```python
"recipient_privacy_restriction": {
    "stage": "send_confirm",
    "category": "recipient",
    "reason": "对方设置了仅互关用户可发送私信",
    "hint": "该用户当前不可接收本账号私信，无需重试或冷却发送账号",
},
```

配置要求：

- `reason` 是稳定、可展示的中文业务原因。
- `hint` 明确说明无需重试且不影响发送账号。
- `failure_summary` 继续由 `_dm_failure_metadata()` 组合生成，首句必须是 `reason`。
- 不新增本机路径或不可稳定复现的页面原文作为机器判断字段。

### 步骤二：实现发送后平台提示检测

新增职责单一的函数，建议接口如下：

```python
def _dm_detect_send_failure_notice(
    page: Any,
    baseline: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, str]]:
    ...
```

函数行为：

1. 优先检查发送编辑器附近的 toast、提示条、弹层和可见 `[role=alert]` 元素。
2. 只读取可见元素的文本，并过滤空文本、隐藏元素和尺寸异常元素。
3. 通过发送前基线排除旧提示；发送后只接受新出现、重新出现或内容发生变化的提示。
4. 首批匹配以下文本及等价变体：
   - `对方设置仅允许互关的人发消息`
   - `对方仅允许互关的人发消息`
   - `由于对方的隐私设置，你无法向对方发送消息`
   - `当前用户无法给对方发送消息`
   - `暂时无法给该用户发送消息`（仅在上下文明确为接收方限制时匹配）
5. 匹配成功时只返回稳定错误码和诊断文本，例如：

```python
{
    "failure_code": "recipient_privacy_restriction",
    "message": "对方设置仅允许互关的人发消息",
}
```

6. 不扫描整个 `document.body.innerText`，不使用单独的“隐私”或“限制”关键词作为命中条件。

如需读取页面 DOM，使用一次 `page.evaluate()` 返回结构化的可见提示列表，再在 Python 中做文案匹配，避免把选择器细节直接扩散到任务分类逻辑。

### 步骤三：接入发送确认轮询

修改 `_dm_wait_message_sent()` 的循环判断，保留现有编辑器清空和新消息气泡基线逻辑，并按以下顺序执行：

```text
发送前建立提示基线
        |
循环：编辑器清空 + 新消息气泡？ -- 是 --> 返回成功
        |
        否
        |
本次发送相关的明确平台失败提示？ -- 是 --> 抛出带稳定错误码的异常
        |
        否
        |
继续等待，直到超时
        |
超时 --> 保持 message_send_unconfirmed
```

抛出异常时不能只依赖不稳定的页面原文。兼容现有异常体系时可使用：

```python
raise RuntimeError(
    "recipient_privacy_restriction: 对方设置仅允许互关的人发消息"
)
```

如果现有代码已有结构化异常载体，则优先复用，但最终必须能被 `_dm_infer_failure_code()` 稳定解析。

关键行为：

- 成功出现本次新消息气泡时立即返回成功，即使页面其他位置存在无关“隐私”文字。
- 明确平台错误提示优先于通用 `message_send_unconfirmed`。
- 没有成功气泡、没有明确提示时，超时行为保持不变。

### 步骤四：扩展失败码推断

在 `_dm_infer_failure_code()` 中加入新规则，并放在 `account_risk`、`automation_changed`、`message_send_unconfirmed` 等模糊规则之前：

```python
if "recipient_privacy_restriction" in text or any(
    marker in text
    for marker in (
        "仅允许互关",
        "无法向对方发送消息",
        "无法给对方发送消息",
    )
):
    return "recipient_privacy_restriction"
```

实现时应同时：

- 对中文提示做统一空白和大小写归一化。
- 不使用单一“限制”“隐私”“blocked”等宽泛词匹配。
- 保证账号风控、限流、登录失效和页面结构变化仍命中原有错误码。
- 保持传入稳定 `error_code` 时优先返回对应 profile 的行为兼容。

### 步骤五：设置任务终态

在 `process_douyin_dm_task_once()` 的异常分类中，将新错误判断放在账号风控模糊关键词之前：

```python
error_code = "recipient_privacy_restriction"
failure_type = "final"
retryable = False
manual_required = False
```

确认异常分支满足：

- 不调用 `zadd(DM_REDIS_RETRY_ZSET, ...)`。
- 不 `rpush` 到 `DM_REDIS_MANUAL_QUEUE`。
- 进入失败队列和死信队列的现有最终失败路径，任务状态为 `failed/dead_letter`。
- `result_ready=true`、`next_retry_at=""`、`manual_required=false`、`dead_letter=true`。
- 不写入账号 cooldown、risk 或 disabled 状态。
- `retry_count` 可以沿用现有失败计数，但不能产生下一次重试时间。

### 步骤六：核对失败字段返回

核对 `_dm_failure_metadata()`、`_dm_task_failure_fields()` 和 `_dm_task_result()`：

- `failure_code`、`error_code` 均能稳定得到 `recipient_privacy_restriction`。
- `failure_stage=send_confirm`。
- `failure_category=recipient`。
- `failure_reason`、`failure_hint`、`failure_summary` 来自 profile，不被英文异常覆盖。
- `failure_screenshot_url` 使用现有授权访问前缀。
- `failure_screenshot_path` 只保留内部诊断用途，不能出现在对外展示字段中。
- 查询接口仍返回 HTTP 成功响应，业务失败放在 `data.result` 中。

## 4. 测试修改计划

在 `tests/test_session_rag_web.py` 至少增加以下测试：

1. 基础提示 `对方设置仅允许互关的人发消息` 返回新错误码。
2. “由于对方的隐私设置，你无法向对方发送消息”等变体返回相同错误码。
3. `_dm_infer_failure_code()` 对新错误文本返回 `recipient_privacy_restriction`。
4. `_dm_failure_metadata()` 返回 `send_confirm` 和 `recipient`。
5. 新错误使完整任务结果为 `failed`、`result_ready=true`、`failure_type=final`。
6. 新错误的 `next_retry_at` 为空，且 Redis 重试队列无任务。
7. 新错误不进入人工接管队列，`manual_required=false`。
8. 新错误不被归类为 `account_risk`。
9. 聊天历史中出现“隐私”或“限制”但没有新可见提示时不能误判。
10. 成功出现本次新消息气泡时仍返回成功。
11. 没有成功气泡也没有明确提示时仍返回 `message_send_unconfirmed`。
12. 失败截图存在时返回 URL，不返回服务器本地绝对路径。

## 5. 验证顺序

1. 先运行提示检测和失败码推断的单元测试。
2. 再运行 `_dm_wait_message_sent()` 的成功、失败、超时回归测试。
3. 再运行 Fake Redis 下的完整 `process_douyin_dm_task_once()` 任务流程测试。
4. 最后运行整个 `tests` 测试集，确认原有账号风控、登录失效、限流和发送成功场景无回归。
5. 真实联调时确认 Web API 与 worker 使用同一 Redis，并分别记录入队成功、worker 消费和最终任务结果。

## 6. 功能完成标准

- 代码新增 `recipient_privacy_restriction` profile、页面提示检测和发送确认接入。
- 平台明确隐私限制提示不再等待到通用确认超时。
- 任务查询稳定返回 `failure_code=recipient_privacy_restriction`、`failure_stage=send_confirm`、`failure_category=recipient`。
- 任务不可重试、不需要人工接管、不影响发送账号状态。
- 失败截图沿用现有机制并通过授权 URL 返回。
- 原有成功气泡、账号风控、登录失效、限流和页面变化判断保持正确。
- 自动化测试覆盖上述行为，真实联调产出脱敏响应 JSON 和失败截图。

## 7. 明确不修改的部分

- 不修改数据中台数据库和页面。
- 不修改数据中台调用 7860 的网络错误识别和超时策略。
- 不修改 Redis 队列命名或全局重试参数。
- 不把所有抖音平台提示一次性改造成新异常框架。
- 不使用 OCR 替代 DOM 文本检测，不自动绕过接收方隐私设置。
