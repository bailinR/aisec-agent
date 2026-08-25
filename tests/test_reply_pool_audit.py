# -*- coding: utf-8 -*-
import unittest

from aisec_agent.web.reply_pool_audit import (
    REPLY_POOL_AUDIT_REDIS_KEY,
    build_reply_pool_audit_entry,
    build_reply_pool_audit_log_clear_response,
    build_reply_pool_audit_log_list_response,
    classify_reply_pool_action,
    store_reply_pool_audit_entry,
)


class FakeRedis:
    def __init__(self):
        self.lists = {}

    def lpush(self, key, value):
        self.lists.setdefault(key, [])
        self.lists[key].insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start : end + 1]
        return True

    def expire(self, key, ttl):
        return True

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end < 0:
            end = len(items) - 1
        return items[start : end + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))

    def delete(self, key):
        if key in self.lists:
            del self.lists[key]
            return 1
        return 0


class ReplyPoolAuditTests(unittest.TestCase):
    def test_classify_actions(self):
        self.assertEqual(
            classify_reply_pool_action("GET", "/api/v1/douyin/private-message/conversation-monitors"),
            "monitor_list",
        )
        self.assertEqual(
            classify_reply_pool_action("POST", "/api/v1/douyin/private-message/conversation-monitors/sync"),
            "monitor_sync",
        )
        self.assertEqual(
            classify_reply_pool_action("POST", "/api/douyin/private-message/accounts/inbox"),
            "account_inbox",
        )
        self.assertEqual(
            classify_reply_pool_action("POST", "/api/v1/douyin/private-message/conversations/read/batch"),
            "conversation_read_batch",
        )
        self.assertEqual(
            classify_reply_pool_action("POST", "/api/v1/douyin/private-message/conversations/read"),
            "conversation_read",
        )
        self.assertEqual(
            classify_reply_pool_action("POST", "/api/v1/douyin/private-message/tasks"),
            "task_submit",
        )
        self.assertIsNone(
            classify_reply_pool_action("GET", "/api/v1/douyin/private-message/tasks/dm_1"),
        )
        self.assertIsNone(classify_reply_pool_action("GET", "/api/health"))

    def test_inbox_detection_entry(self):
        entry = build_reply_pool_audit_entry(
            action="account_inbox",
            method="POST",
            path="/api/v1/douyin/private-message/accounts/inbox",
            status=200,
            request_id="req1",
            timestamp="2026-08-25 16:00:00",
            elapsed_ms=1200,
            remote_ip="127.0.0.1",
            body={"account_id": "acc_1", "account_cookie": "secret", "browser_name": "edge"},
            response_data={
                "ok": True,
                "data": {
                    "ok": True,
                    "account_id": "acc_1",
                    "inbox_unread_count": 3,
                    "inbox_unread_people": 2,
                    "unread_conversations": [
                        {
                            "peer_nickname": "用户甲",
                            "last_message": "你好",
                            "unread_count": 2,
                            "conversation_id": "cid1",
                        }
                    ],
                    "reused_monitor": True,
                },
            },
        )
        self.assertEqual(entry["action"], "account_inbox")
        self.assertEqual(entry["account_id"], "acc_1")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["detection"]["inbox_unread_people"], 2)
        self.assertEqual(entry["detection"]["unread_conversations"][0]["peer_nickname"], "用户甲")
        self.assertEqual(entry["request"].get("browser_name"), "edge")
        self.assertNotIn("account_cookie", entry["request"])

    def test_read_batch_detection_entry(self):
        entry = build_reply_pool_audit_entry(
            action="conversation_read_batch",
            method="POST",
            path="/api/v1/douyin/private-message/conversations/read/batch",
            status=200,
            request_id="req2",
            timestamp="2026-08-25 16:01:00",
            elapsed_ms=3000,
            remote_ip="127.0.0.1",
            body={"account_id": "acc_2", "items": [{"lead_id": "lead_1"}, {"lead_id": "lead_2"}]},
            response_data={
                "ok": True,
                "data": {
                    "ok": True,
                    "total": 2,
                    "replied": 1,
                    "no_reply": 1,
                    "failed": 0,
                    "results": [
                        {"ref": "lead_1", "lead_id": "lead_1", "ok": True, "has_reply": True, "latest_peer_reply": "在的"},
                        {"ref": "lead_2", "lead_id": "lead_2", "ok": True, "has_reply": False, "outgoing_found": True},
                    ],
                },
            },
        )
        self.assertEqual(entry["detection"]["replied"], 1)
        self.assertEqual(entry["detection"]["replied_refs"], ["lead_1"])
        self.assertTrue(entry["detection"]["results"][0]["has_reply"])

    def test_store_list_clear(self):
        redis = FakeRedis()
        entry = build_reply_pool_audit_entry(
            action="monitor_list",
            method="GET",
            path="/api/v1/douyin/private-message/conversation-monitors",
            status=200,
            request_id="req3",
            timestamp="2026-08-25 16:02:00",
            elapsed_ms=50,
            remote_ip="127.0.0.1",
            body={},
            response_data={
                "ok": True,
                "data": {
                    "monitors": [
                        {
                            "account_id": "acc_9",
                            "alive": True,
                            "status": "running",
                            "inbox_unread_count": 1,
                            "inbox_unread_people": 1,
                            "unread_conversations": [
                                {"peer_nickname": "乙", "last_message": "报价", "unread_count": 1}
                            ],
                        }
                    ],
                    "counts": {"total": 1, "running": 1, "error": 0},
                },
            },
        )
        store_reply_pool_audit_entry(entry, redis_client=redis)
        listed = build_reply_pool_audit_log_list_response(redis_client=redis, limit=10, offset=0)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["entries"][0]["action"], "monitor_list")
        self.assertEqual(listed["entries"][0]["detection"]["total_unread_people"], 1)
        filtered = build_reply_pool_audit_log_list_response(
            redis_client=redis, limit=10, offset=0, account_id="acc_9"
        )
        self.assertEqual(len(filtered["entries"]), 1)
        cleared = build_reply_pool_audit_log_clear_response(redis_client=redis)
        self.assertTrue(cleared["cleared"])
        self.assertEqual(redis.lrange(REPLY_POOL_AUDIT_REDIS_KEY, 0, -1), [])


if __name__ == "__main__":
    unittest.main()
