# 抖音 Web IM 私信接收实验

本实验适配器参考公开仓库 `cv-cat/DouYin_Spider` 的接收实现，放在
`aisec_agent.integrations.douyin_web_im`。它使用抖音网页版登录态和 Web IM
WebSocket/protobuf 协议，不是抖音开放平台官方客服 API，也不会产生
`open_id` 或官方 Webhook 事件。

当前支持：

- 根据 `device_id`、`sessionid` 构造 Web IM WebSocket 地址；
- 使用完整 Cookie 建立连接；
- 解包 `PushFrame -> Response -> new_message_notify.message`；
- 标准化文本、图片、语音、表情等消息的基础字段；
- 按服务端消息 ID 去重；没有消息 ID 时按会话、序号和发送者去重；
- 断线指数退避重连；
- 通过回调把事件交给入库/队列层。

## 最小调用

```python
from aisec_agent.integrations.douyin_web_im import DouyinWebIMReceiver


def on_event(event):
    print(event.direction, event.conversation_id, event.message_type, event.content)


receiver = DouyinWebIMReceiver(
    account_id="douyin_bluev_01",
    session_id="从登录 Cookie 中取出的 sessionid",
    device_id="通过抖音 Web 用户接口获取的 device_id",
    cookie_header="完整的抖音 Cookie 字符串",
    own_user_id="当前账号数字用户 ID",
    on_event=on_event,
)

try:
    receiver.run_forever()
except KeyboardInterrupt:
    receiver.stop()
```

## 本地监听命令

先在当前 PowerShell 进程中设置登录态，不要把 Cookie 写进命令历史：

```powershell
$env:DOUYIN_WEB_IM_COOKIE = "完整 Cookie 字符串"
$env:DOUYIN_WEB_IM_DEVICE_ID = "device_id"
$env:DOUYIN_WEB_IM_ACCOUNT_ID = "douyin_bluev_01"
$env:DOUYIN_WEB_IM_OWN_USER_ID = "当前账号数字用户 ID"
python -m aisec_agent.worker.douyin_web_im_receiver
```

也可以显式传入非敏感参数：

```powershell
python -m aisec_agent.worker.douyin_web_im_receiver `
  --account-id douyin_bluev_01 `
  --device-id device_id
```

这个入口只监听和打印标准化事件，不会自动回复，也不会调用当前私信发送
worker。终止监听使用 `Ctrl+C`。

`own_user_id` 用于把自己发送的消息标成 `outbound`；不传时事件默认按
`inbound` 处理。先不传也可以观察原始接收结果。

## 事件结构

```json
{
  "event_type": "message",
  "direction": "inbound",
  "account_id": "douyin_bluev_01",
  "sender_id": "用户数字 ID",
  "conversation_id": "0:1:账号ID:用户ID",
  "conversation_short_id": 123,
  "server_message_id": 456,
  "index_in_conversation": 7,
  "message_type": 7,
  "content": {"text": "用户消息"},
  "dedupe_key": "server:456"
}
```

建议回调只做两件事：把原始事件和标准字段写入数据库，或立即投递到
Redis；AI 判断和自动发送应在独立 worker 中执行，不能阻塞 WebSocket 回调。

## 当前限制

- 适配器只实现接收，不接入现有首次私信发送 worker；
- 需要有效的网页登录 Cookie、`device_id` 和当前 Web IM 协议参数；
- Web IM 协议是网页内部协议，字段、签名和风控策略可能变化；
- 该链路不等于官方蓝 V客服 API，不能据此确认企业开发者权限；
- 不要把 Cookie 写入日志、提交到 Git 或放进前端；
- 先用单账号、低频、只读监听验证稳定性，再考虑接自动回复。
