# -*- coding: utf-8 -*-
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aisec_agent.web.douyin_conversation_monitor import (
    DouyinConversationMonitor,
    _dm_account_browser_user_data_dir,
    _dm_is_group_or_system_chat,
    _dm_peer_key_from_row,
    build_douyin_conversation_monitor_list_response,
    build_douyin_conversation_monitor_start_response,
    build_douyin_conversation_monitor_stop_response,
    build_douyin_conversation_monitor_sync_response,
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

    def test_sync_starts_watch_only_and_skips_already_running(self):
        def fake_start(self):
            self.status = "running"
            self.thread = threading.Thread(target=lambda: None, daemon=True)
            self.thread.start = lambda: None  # type: ignore[method-assign]
            self.thread.is_alive = lambda: True  # type: ignore[method-assign]

        def fake_stop(self):
            self.status = "stopped"
            self.stop_event.set()
            if self.thread:
                self.thread.is_alive = lambda: False  # type: ignore[method-assign]

        accounts = [
            {
                "account_id": "acc_sync_1",
                "account_name": "账号1",
                "account_cookies": "sessionid=sync1; uid_tt=1",
            },
            {
                "account_id": "acc_sync_2",
                "account_name": "账号2",
                "account_cookies": "sessionid=sync2; uid_tt=2",
            },
        ]
        with patch.object(DouyinConversationMonitor, "start", fake_start), patch.object(
            DouyinConversationMonitor, "stop", fake_stop
        ):
            first = build_douyin_conversation_monitor_sync_response(
                {
                    "accounts": accounts,
                    "browser_name": "chrome",
                    "headless": True,
                    "poll_seconds": 8,
                }
            )
            self.assertTrue(first["ok"])
            self.assertEqual(first["mode"], "watch_only")
            self.assertEqual(len(first["started"]), 2)
            self.assertEqual(len(first["already_running"]), 0)
            self.assertFalse(first["started"][0]["monitor"]["generate_reply"])
            self.assertFalse(first["started"][0]["monitor"]["auto_send"])

            again = build_douyin_conversation_monitor_sync_response({"accounts": accounts})
            self.assertTrue(again["ok"])
            self.assertEqual(len(again["started"]), 0)
            self.assertEqual(len(again["already_running"]), 2)
            self.assertEqual(again["counts"]["running"], 2)

    def test_tick_watch_only_peeks_preview_without_open_or_reply(self):
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
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_inbox_summary",
            return_value={"ok": True, "unread_count": 1, "unread_people": 1, "detail": "scanned"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_unread_conversations",
            return_value={"ok": True, "conversations": [], "unread_count": 0, "unread_people": 0, "detail": "empty"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
            return_value={"ok": True, "unread": True, "detail": "用户甲 刚刚 多少钱", "preview_text": "多少钱"},
        ) as mock_click:
            monitor._tick(page)

        self.assertEqual(monitor.last_message, "多少钱")
        self.assertEqual(monitor.last_reply, "")
        self.assertEqual(monitor.reply_count, 0)
        self.assertEqual(len(monitor.unread_conversations), 1)
        self.assertEqual(monitor.unread_conversations[0]["peer_nickname"], "用户甲")
        self.assertEqual(monitor.unread_conversations[0]["last_message"], "多少钱")
        self.assertGreaterEqual(int(monitor.unread_conversations[0]["unread_count"] or 0), 1)
        mock_click.assert_called_once_with(page, click=False)

    def test_tick_watch_only_skips_peek_when_no_unread(self):
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
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_inbox_summary",
            return_value={"ok": True, "unread_count": 0, "unread_people": 0, "detail": "scanned"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_unread_conversations",
            return_value={"ok": True, "conversations": [], "unread_count": 0, "unread_people": 0},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
        ) as mock_click:
            monitor._tick(page)

        self.assertEqual(monitor.last_message, "")
        self.assertEqual(monitor.unread_conversations, [])
        mock_click.assert_not_called()

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
            return_value={"ok": True, "unread": True, "detail": "用户甲 刚刚 多少钱", "preview_text": "多少钱"},
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

    def test_peer_key_and_group_detection(self):
        self.assertEqual(_dm_peer_key_from_row("yang 12分钟前 好的，可以给预估价", "好的，可以给预估价"), "yang")
        self.assertEqual(_dm_peer_key_from_row("用户甲 刚刚 多少钱", "多少钱"), "用户甲")
        self.assertTrue(_dm_is_group_or_system_chat("进群", "https://v.douyin.com/group/287976598570"))
        self.assertFalse(_dm_is_group_or_system_chat("用户甲 刚刚 多少钱"))

    def _make_send_monitor(self):
        return DouyinConversationMonitor(
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
            max_auto_replies_before_peer=2,
        )

    def test_does_not_auto_send_without_unread_badge(self):
        monitor = self._make_send_monitor()
        page = MagicMock()
        with patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_open_douyin_messages_surface",
            return_value=[{"name": "detect_message_surface", "ok": True}],
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_inbox_summary",
            return_value={"ok": True, "unread_count": 0, "unread_people": 0},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_detect_latest_douyin_message",
            return_value={"url": "https://www.douyin.com/", "has_editor": True, "latest_text": "多少钱", "error": ""},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
            return_value={"ok": True, "unread": False, "detail": "用户甲 昨天 多少钱", "preview_text": "多少钱"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._gpu_pool_chat_reply",
            return_value="亲，具体看规格哈",
        ), patch("aisec_agent.web.douyin_conversation_monitor._sr") as mock_sr:
            sr = MagicMock()
            sr._dm_now.return_value = "now"
            mock_sr.return_value = sr
            monitor._tick(page)
        self.assertEqual(monitor.reply_count, 0)
        sr._dm_fill_message_editor.assert_not_called()

    def test_stops_after_two_auto_replies_without_new_inbound(self):
        monitor = self._make_send_monitor()
        page = MagicMock()
        with patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_open_douyin_messages_surface",
            return_value=[{"name": "detect_message_surface", "ok": True}],
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_inbox_summary",
            return_value={"ok": True, "unread_count": 1, "unread_people": 1},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_detect_latest_douyin_message",
            return_value={"url": "https://www.douyin.com/", "has_editor": True, "latest_text": "多少钱", "error": ""},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
            return_value={"ok": True, "unread": True, "detail": "用户甲 刚刚 多少钱", "preview_text": "多少钱"},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._gpu_pool_chat_reply",
            return_value="亲，具体看规格哈",
        ), patch("aisec_agent.web.douyin_conversation_monitor._sr") as mock_sr:
            sr = MagicMock()
            sr._dm_now.return_value = "now"
            sr._dm_fill_message_editor.return_value = {"name": "paste_message", "ok": True}
            sr._dm_message_editor_text.return_value = "亲，具体看规格哈"
            sr._dm_send_and_confirm_current_message.return_value = [{"name": "send", "ok": True}]
            mock_sr.return_value = sr
            monitor._tick(page)
            monitor._tick(page)
            monitor._tick(page)
        self.assertEqual(monitor.reply_count, 2)
        self.assertTrue(any("已达自动回复上限" in item["text"] for item in monitor.logs))

    def test_skips_group_invite_auto_send(self):
        monitor = self._make_send_monitor()
        page = MagicMock()
        with patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_open_douyin_messages_surface",
            return_value=[{"name": "detect_message_surface", "ok": True}],
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_scan_inbox_summary",
            return_value={"ok": True, "unread_count": 1, "unread_people": 1},
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_detect_latest_douyin_message",
            return_value={
                "url": "https://www.douyin.com/",
                "has_editor": True,
                "latest_text": "https://v.douyin.com/group/287976598570",
                "error": "",
            },
        ), patch(
            "aisec_agent.web.douyin_conversation_monitor._dm_click_unread_or_latest_chat",
            return_value={
                "ok": True,
                "unread": True,
                "detail": "杨洋 刚刚 进群",
                "preview_text": "https://v.douyin.com/group/287976598570",
            },
        ), patch("aisec_agent.web.douyin_conversation_monitor._sr") as mock_sr:
            sr = MagicMock()
            sr._dm_now.return_value = "now"
            mock_sr.return_value = sr
            monitor._tick(page)
        self.assertEqual(monitor.reply_count, 0)
        self.assertTrue(any("群聊" in item["text"] for item in monitor.logs))


if __name__ == "__main__":
    unittest.main()
