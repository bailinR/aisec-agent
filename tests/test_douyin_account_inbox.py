# -*- coding: utf-8 -*-
from aisec_agent.web.douyin_account_inbox import (
    build_douyin_account_inbox_response,
    shape_douyin_account_inbox_result,
)


def test_shape_inbox_success():
    shaped = shape_douyin_account_inbox_result(
        {
            "ok": True,
            "inbox_unread_count": 3,
            "inbox_unread_people": 2,
            "account_key": "cookie_abc",
            "browser": "chrome",
        }
    )
    assert shaped["ok"] is True
    assert shaped["inbox_unread_count"] == 3
    assert shaped["unread_count"] == 3
    assert shaped["inbox_unread_people"] == 2
    assert shaped["unread_people"] == 2
    assert shaped["failure_code"] == ""


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


def test_build_inbox_uses_executor():
    captured = {}

    def fake_executor(payload):
        captured["cookie"] = payload.get("account_cookie")
        return {
            "ok": True,
            "inbox_unread_count": 5,
            "inbox_unread_people": 4,
            "account_key": "cookie_x",
            "browser": "chrome",
        }

    data = build_douyin_account_inbox_response(
        {"account_cookie": "sessionid=abc", "browser_name": "chrome"},
        executor=fake_executor,
    )
    assert captured["cookie"] == "sessionid=abc"
    assert data["inbox_unread_count"] == 5
    assert data["inbox_unread_people"] == 4
    assert data["ok"] is True
