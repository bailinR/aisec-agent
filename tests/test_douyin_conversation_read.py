"""Unit tests for Douyin conversation reply detection (pure logic)."""

from __future__ import annotations

import unittest

from aisec_agent.web.douyin_conversation_read import (
    annotate_outgoing_roles,
    build_reply_detection_result,
    extract_latest_peer_reply,
)


class DouyinConversationReadTests(unittest.TestCase):
    def test_extract_peer_after_outgoing(self):
        messages = [
            {"role": "self", "text": "您好，看到您的留言了。"},
            {"role": "peer", "text": "多少钱"},
            {"role": "peer", "text": "还有货吗"},
        ]
        self.assertEqual(
            extract_latest_peer_reply(messages, after_text="您好，看到您的留言了。"),
            "还有货吗",
        )

    def test_no_peer_after_outgoing(self):
        messages = [
            {"role": "peer", "text": "历史消息"},
            {"role": "self", "text": "您好，看到您的留言了。"},
            {"role": "self", "text": "手动再跟一句"},
        ]
        self.assertEqual(
            extract_latest_peer_reply(messages, after_text="您好，看到您的留言了。"),
            "",
        )

    def test_annotate_forces_self_on_outgoing_match(self):
        messages = [
            {"role": "peer", "text": "您好，看到您的留言了。"},
            {"role": "peer", "text": "多少钱"},
        ]
        annotated = annotate_outgoing_roles(messages, "您好，看到您的留言了。")
        self.assertEqual(annotated[0]["role"], "self")
        self.assertEqual(annotated[1]["role"], "peer")

    def test_detection_has_reply(self):
        result = build_reply_detection_result(
            [
                {"role": "self", "text": "您好，看到您的留言了。"},
                {"role": "peer", "text": "多少钱"},
            ],
            expected_outgoing="您好，看到您的留言了。",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["outgoing_found"])
        self.assertTrue(result["has_reply"])
        self.assertEqual(result["latest_peer_reply"], "多少钱")
        self.assertEqual(result["failure_code"], "")

    def test_detection_no_reply(self):
        result = build_reply_detection_result(
            [
                {"role": "self", "text": "您好，看到您的留言了。"},
            ],
            expected_outgoing="您好，看到您的留言了。",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["outgoing_found"])
        self.assertFalse(result["has_reply"])
        self.assertEqual(result["latest_peer_reply"], "")

    def test_detection_outgoing_not_found(self):
        result = build_reply_detection_result(
            [
                {"role": "peer", "text": "历史对方消息"},
                {"role": "self", "text": "别的文案"},
            ],
            expected_outgoing="您好，看到您的留言了。",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["outgoing_found"])
        self.assertFalse(result["has_reply"])
        self.assertEqual(result["failure_code"], "outgoing_not_found")

    def test_without_anchor_does_not_use_historical_peer(self):
        result = build_reply_detection_result(
            [
                {"role": "peer", "text": "历史对方消息"},
            ],
            expected_outgoing="",
        )
        self.assertFalse(result["has_reply"])
        self.assertEqual(result["latest_peer_reply"], "")

    def test_manual_self_after_outgoing_is_not_reply(self):
        result = build_reply_detection_result(
            [
                {"role": "self", "text": "您好，看到您的留言了。"},
                {"role": "self", "text": "我又手动发了一条"},
            ],
            expected_outgoing="您好，看到您的留言了。",
        )
        self.assertTrue(result["outgoing_found"])
        self.assertFalse(result["has_reply"])


if __name__ == "__main__":
    unittest.main()
