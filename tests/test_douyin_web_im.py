import json
import unittest

from aisec_agent.integrations.douyin_web_im import (
    DouyinWebIMReceiver,
    DouyinWebIMEvent,
    build_web_im_access_key,
    build_web_im_url,
    parse_web_im_frame,
)
from aisec_agent.integrations.douyin_web_im_proto import Live_pb2, Response_pb2


def build_message_frame(
    *,
    sender=2002,
    server_message_id=9001,
    conversation_id="0:1:1001:2002",
    content=None,
    message_type=7,
    index=4,
):
    response = Response_pb2.Response()
    message = response.body.new_message_notify.message
    message.conversation_id = conversation_id
    message.conversation_type = 1
    message.server_message_id = server_message_id
    message.index_in_conversation = index
    message.conversation_short_id = 777
    message.message_type = message_type
    message.sender = sender
    message.content = json.dumps(content or {"aweType": 700, "text": "hello"}, ensure_ascii=False)

    frame = Live_pb2.PushFrame()
    frame.payloadType = "pb"
    frame.payload = response.SerializeToString()
    return frame.SerializeToString()


class DouyinWebIMTests(unittest.TestCase):
    def test_access_key_matches_reference_algorithm(self):
        expected = "2b162a5124682ac6cc4376b07204ff9e"
        self.assertEqual(
            build_web_im_access_key("123456", app_key="app", suffix="suffix", fpid="9"),
            expected,
        )

    def test_websocket_url_contains_only_connection_parameters(self):
        url = build_web_im_url("123456", "session-abc", ws_url="wss://example.test/ws")
        self.assertIn("device_id=123456", url)
        self.assertIn("token=session-abc", url)
        self.assertIn("access_key=", url)
        self.assertNotIn("cookie=", url)

    def test_parse_text_message_as_inbound_event(self):
        event = parse_web_im_frame(
            build_message_frame(),
            account_id="bluev-1",
            own_user_id="1001",
        )
        self.assertIsInstance(event, DouyinWebIMEvent)
        self.assertEqual(event.event_type, "message")
        self.assertEqual(event.direction, "inbound")
        self.assertEqual(event.account_id, "bluev-1")
        self.assertEqual(event.sender_id, "2002")
        self.assertEqual(event.server_message_id, 9001)
        self.assertEqual(event.content["text"], "hello")
        self.assertEqual(event.dedupe_key, "server:9001")

    def test_parse_own_message_as_outbound_event(self):
        event = parse_web_im_frame(
            build_message_frame(sender=1001, server_message_id=9002),
            account_id="bluev-1",
            own_user_id="1001",
        )
        self.assertEqual(event.direction, "outbound")

    def test_ignore_non_pb_frame(self):
        frame = Live_pb2.PushFrame()
        frame.payloadType = "text/json"
        frame.payload = b'{"heartbeat":true}'
        self.assertIsNone(parse_web_im_frame(frame.SerializeToString(), account_id="bluev-1"))

    def test_ignore_response_without_new_message_notification(self):
        response = Response_pb2.Response()
        frame = Live_pb2.PushFrame()
        frame.payloadType = "pb"
        frame.payload = response.SerializeToString()
        self.assertIsNone(parse_web_im_frame(frame.SerializeToString(), account_id="bluev-1"))

    def test_receiver_deduplicates_events_before_callback(self):
        received = []
        receiver = DouyinWebIMReceiver(
            account_id="bluev-1",
            session_id="session-1",
            device_id="device-1",
            cookie_header="sessionid=session-1; msToken=token-1",
            on_event=received.append,
        )
        frame = build_message_frame(server_message_id=9010)
        receiver._on_message(None, frame)
        receiver._on_message(None, frame)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].dedupe_key, "server:9010")


if __name__ == "__main__":
    unittest.main()
