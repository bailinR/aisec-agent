# -*- coding: utf-8 -*-
from aisec_agent.web.douyin_account_inbox import (
    build_douyin_account_inbox_response,
    shape_douyin_account_inbox_result,
)


def test_shape_inbox_success():
    shaped = shape_douyin_account_inbox_result(
        {
            "ok": True,
            "account_id": "acc_1",
            "inbox_unread_count": 3,
            "inbox_unread_people": 2,
            "account_key": "cookie_abc",
            "browser": "chrome",
            "unread_conversations": [
                {
                    "peer_nickname": "用户甲",
                    "peer_sec_uid": "MS4wLjABAAAA",
                    "peer_profile_url": "https://www.douyin.com/user/MS4wLjABAAAA",
                    "last_message": "你好",
                    "unread_count": 2,
                    "conversation_id": "MS4wLjABAAAA",
                    "updated_at": "刚刚",
                }
            ],
        }
    )
    assert shaped["ok"] is True
    assert shaped["account_id"] == "acc_1"
    assert shaped["inbox_unread_count"] == 3
    assert shaped["unread_count"] == 3
    assert shaped["inbox_unread_people"] == 2
    assert shaped["unread_people"] == 2
    assert shaped["failure_code"] == ""
    assert shaped["clears_unread"] is False
    assert len(shaped["unread_conversations"]) == 1
    assert shaped["unread_conversations"][0]["peer_nickname"] == "用户甲"
    assert shaped["unread_conversations"][0]["peer_sec_uid"] == "MS4wLjABAAAA"


def test_shape_inbox_login_required():
    shaped = shape_douyin_account_inbox_result(
        {
            "ok": False,
            "requires_login": True,
            "failure_code": "login_required",
            "message": "login_required: need login",
            "inbox_unread_count": 0,
            "inbox_unread_people": 0,
        }
    )
    assert shaped["ok"] is False
    assert shaped["requires_login"] is True
    assert shaped["failure_code"] == "login_required"
    assert shaped["unread_conversations"] == []


def test_build_inbox_uses_executor():
    captured = {}

    def fake_executor(payload):
        captured["cookie"] = payload.get("account_cookie")
        return {
            "ok": True,
            "account_id": "acc_x",
            "inbox_unread_count": 5,
            "inbox_unread_people": 4,
            "account_key": "cookie_x",
            "browser": "chrome",
            "unread_conversations": [
                {
                    "peer_nickname": "乙",
                    "last_message": "在吗",
                    "unread_count": 1,
                }
            ],
        }

    data = build_douyin_account_inbox_response(
        {"account_cookie": "sessionid=abc", "browser_name": "chrome"},
        executor=fake_executor,
    )
    assert captured["cookie"] == "sessionid=abc"
    assert data["inbox_unread_count"] == 5
    assert data["inbox_unread_people"] == 4
    assert data["ok"] is True
    assert data["unread_conversations"][0]["peer_nickname"] == "乙"


def test_repair_inbox_peer_fields_splits_mash():
    from aisec_agent.web.douyin_conversation_monitor import (
        _dm_normalize_unread_conversation_items,
        _dm_repair_inbox_peer_fields,
    )

    repaired = _dm_repair_inbox_peer_fields("白林 2222 ·", "2", unread_count=2)
    assert repaired["peer_nickname"] == "白林"
    assert repaired["last_message"] == "2222"

    repaired2 = _dm_repair_inbox_peer_fields("", "白林 111 ·", unread_count=1)
    assert repaired2["peer_nickname"] == "白林"
    assert repaired2["last_message"] == "111"

    rows = _dm_normalize_unread_conversation_items(
        [
            {
                "peer_nickname": "",
                "last_message": "",
                "unread_count": 2,
                "row_text": "白林 刚刚 你好还在吗 2",
            },
            {
                "peer_nickname": "用户乙",
                "last_message": "",
                "unread_count": 1,
                "row_text": "用户乙 报价咨询",
            },
        ]
    )
    assert len(rows) == 2
    assert rows[0]["peer_nickname"] == "白林"
    assert "你好" in rows[0]["last_message"] or rows[0]["last_message"]
    assert rows[0]["unread_count"] == 2
    assert rows[1]["peer_nickname"] == "用户乙"
    assert rows[1]["last_message"] in {"报价咨询", "(未读)"} or rows[1]["last_message"]
    assert rows[1]["unread_count"] == 1


def test_inbox_reuses_alive_monitor(monkeypatch):
    class _FakeMonitor:
        def request_fresh_inbox(self, timeout_seconds=90.0):
            assert timeout_seconds >= 15
            return {
                "ok": True,
                "account_id": "13",
                "account_key": "cookie_k",
                "inbox_unread_count": 1,
                "inbox_unread_people": 1,
                "unread_conversations": [
                    {
                        "peer_nickname": "白林",
                        "last_message": "2222",
                        "unread_count": 1,
                    }
                ],
                "reused_monitor": True,
                "browser": "edge",
                "message": "reused_monitor=1",
            }

    monkeypatch.setattr(
        "aisec_agent.web.douyin_conversation_monitor.find_alive_monitor_for_account",
        lambda payload: _FakeMonitor(),
    )
    data = build_douyin_account_inbox_response({"account_id": "13", "timeout_ms": 30000})
    assert data["ok"] is True
    assert data["reused_monitor"] is True
    assert data["unread_conversations"][0]["peer_nickname"] == "白林"
    assert data["unread_conversations"][0]["last_message"] == "2222"
