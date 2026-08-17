# -*- coding: utf-8 -*-
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aisec_agent.web.douyin_conversation_monitor import (
    DouyinConversationMonitor,
    _dm_account_browser_user_data_dir,
    build_douyin_conversation_monitor_list_response,
    build_douyin_conversation_monitor_start_response,
    build_douyin_conversation_monitor_stop_response,
    reset_conversation_monitors_for_tests,
)
from aisec_agent.web.session_rag_chat import WebInputError, _dm_account_key_from_values


class DouyinConversationMonitorTests(unittest.TestCase):
    def setUp(self):
        reset_conversation_monitors_for_tests()

    def tearDown(self):
        reset_conversation_monitors_for_tests()

    def test_account_browser_profile_path(self):
        path = _dm_account_browser_user_data_dir("chrome", "cookie_abc123")
        self.assertTrue(str(path).replace("\\", "/").endswith("accounts/chrome/cookie_abc123"))

    def test_start_requires_cookie_or_existing_profile(self):
        with self.assertRaises(WebInputError):
            build_douyin_conversation_monitor_start_response(
                {
                    "account_id": "acc_missing_profile",
                    "account_name": "missing",
                    "browser_name": "chrome",
                    "generate_reply": False,
                    "auto_send": False,
                }
            )

    def test_list_start_stop_with_mocked_thread(self):
        started = threading.Event()

        def fake_start(self):
            self.status = "running"
            self.thread = threading.Thread(target=lambda: None, daemon=True)
            self.thread.start = lambda: None  # type: ignore[method-assign]
            # Make snapshot treat as alive without real loop.
            self.thread.is_alive = lambda: True  # type: ignore[method-assign]
            started.set()

        def fake_stop(self):
            self.status = "stopped"
            self.stop_event.set()
            if self.thread:
                self.thread.is_alive = lambda: False  # type: ignore[method-assign]

        payload = {
            "account_id": "acc_monitor_1",
            "account_name": "发送账号A",
            "browser_name": "chrome",
            "account_cookies": "sessionid=demo; uid_tt=1",
            "auto_send": False,
            "generate_reply": False,
            "poll_seconds": 5,
            "project_id": "project_demo",
            "company_id": "company_demo",
        }
        account_key = _dm_account_key_from_values(payload["account_cookies"], payload["account_id"])

        with patch.object(DouyinConversationMonitor, "start", fake_start), patch.object(
            DouyinConversationMonitor, "stop", fake_stop
        ):
            empty = build_douyin_conversation_monitor_list_response()
            self.assertEqual(empty["counts"]["total"], 0)

            first = build_douyin_conversation_monitor_start_response(payload)
            self.assertTrue(first["started"])
            self.assertEqual(first["monitor"]["account_id"], "acc_monitor_1")
            self.assertEqual(first["monitor"]["account_key"], account_key)
            self.assertEqual(first["monitor"]["status"], "running")
            self.assertFalse(first["monitor"]["auto_send"])
            self.assertFalse(first["monitor"]["generate_reply"])
            self.assertIn("accounts", str(first["monitor"]["user_data_dir"]).replace("\\", "/"))

            listed = build_douyin_conversation_monitor_list_response()
            self.assertEqual(listed["counts"]["total"], 1)
            self.assertEqual(listed["counts"]["running"], 1)

            again = build_douyin_conversation_monitor_start_response(payload)
            self.assertFalse(again["started"])
            self.assertEqual(again["reason"], "already_running")

            stopped = build_douyin_conversation_monitor_stop_response(
                {
                    "account_id": payload["account_id"],
                    "account_cookies": payload["account_cookies"],
                }
            )
            self.assertTrue(stopped["stopped"])
            self.assertEqual(stopped["monitor"]["status"], "stopped")

    def test_tick_records_message_without_reply_when_generate_disabled(self):
        monitor = DouyinConversationMonitor(
            account_id="acc_1",
            account_name="发送账号",
            account_key="cookie_testkey",
            account_cookies="",
            browser_name="chrome",
            headless=True,
            project_id="",
            company_id="",
            source_platform="抖音",
            auto_send=False,
            generate_reply=False,
            poll_seconds=5,
        )
        page = MagicMock()
        with patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_open_douyin_messages_surface",
            return_value=[{"name": "detect_message_surface", "ok": True}],
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_detect_latest_douyin_message",
            return_value={
                "url": "https://www.douyin.com/",
                "has_editor": True,
                "latest_text": "你好，想了解一下",
                "error": "",
            },
        ):
            monitor._tick(page)

        self.assertEqual(monitor.last_message, "你好，想了解一下")
        self.assertEqual(monitor.last_reply, "")
        self.assertEqual(monitor.reply_count, 0)
        self.assertTrue(any("识别到新消息" in item["text"] for item in monitor.logs))

    def test_tick_gpu_pool_generates_and_sends(self):
        monitor = DouyinConversationMonitor(
            account_id="acc_1",
            account_name="发送账号",
            account_key="cookie_testkey",
            account_cookies="",
            browser_name="chrome",
            headless=True,
            project_id="",
            company_id="",
            source_platform="抖音",
            auto_send=True,
            generate_reply=True,
            poll_seconds=5,
            reply_backend="gpu_pool",
            gpu_model="chat-pm",
        )
        page = MagicMock()
        with patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_open_douyin_messages_surface",
            return_value=[{"name": "detect_message_surface", "ok": True}],
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_detect_latest_douyin_message",
            return_value={
                "url": "https://www.douyin.com/",
                "has_editor": True,
                "latest_text": "多少钱",
                "error": "",
            },
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
            return_value={"ok": True, "detail": "用户甲 unread", "preview_text": "多少钱"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._gpu_pool_chat_reply",
            return_value="亲，具体看规格哈，方便说下需求吗？",
        ), patch("aisec_agent.web.douyin_conversation_monitor._sr") as mock_sr:
            sr = MagicMock()
            sr._dm_now.return_value = "2026-08-14T12:00:00"
            sr._dm_fill_message_editor.return_value = {"name": "paste_message", "ok": True}
            sr._dm_message_editor_text.return_value = "亲，具体看规格哈，方便说下需求吗？"
            sr._dm_send_and_confirm_current_message.return_value = [{"name": "send", "ok": True}]
            mock_sr.return_value = sr
            monitor._tick(page)

        self.assertEqual(monitor.last_message, "多少钱")
        self.assertIn("具体看规格", monitor.last_reply)
        self.assertEqual(monitor.reply_count, 1)
        self.assertEqual(monitor.last_peer, "用户甲")
        sr._dm_fill_message_editor.assert_called_once()
        sr._dm_send_and_confirm_current_message.assert_called_once()
        kwargs = sr._dm_send_and_confirm_current_message.call_args.kwargs
        self.assertTrue(kwargs.get("prefer_click_send"))
        self.assertTrue(kwargs.get("accept_editor_cleared"))

    def test_cookie_less_start_reuses_existing_profile_dir(self):
        account_key = "cookie_profile_reuse"
        profile_dir = _dm_account_browser_user_data_dir("chrome", account_key)
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(DouyinConversationMonitor, "start", lambda self: setattr(self, "status", "starting")):
                result = build_douyin_conversation_monitor_start_response(
                    {
                        "account_key": account_key,
                        "account_id": "acc_profile",
                        "account_name": "复用登录态",
                        "browser_name": "chrome",
                        "generate_reply": False,
                        "auto_send": False,
                        "reply_backend": "gpu_pool",
                        "gpu_model": "chat-pm",
                    }
                )
            self.assertTrue(result["started"])
            self.assertTrue(result["monitor"]["profile_login_reused"])
            self.assertFalse(result["monitor"]["account_cookie_loaded"])
            self.assertEqual(result["monitor"]["reply_backend"], "gpu_pool")
            self.assertEqual(result["monitor"]["gpu_model"], "chat-pm")
        finally:
            # leave directory; harmless under content/playwright_profiles
            pass


if __name__ == "__main__":
    unittest.main()
