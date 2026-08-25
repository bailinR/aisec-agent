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
    from aisec_agent.web.douyin_conversation_monitor import _dm_repair_inbox_peer_fields

    repaired = _dm_repair_inbox_peer_fields("白林 2222 ·", "2", unread_count=2)
    assert repaired["peer_nickname"] == "白林"
    assert repaired["last_message"] == "2222"

    repaired2 = _dm_repair_inbox_peer_fields("", "白林 111 ·", unread_count=1)
    assert repaired2["peer_nickname"] == "白林"
    assert repaired2["last_message"] == "111"
