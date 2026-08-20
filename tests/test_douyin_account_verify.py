"""Tests for Douyin account activity + type verify API shaping."""

from __future__ import annotations

import unittest

from aisec_agent.web.douyin_account_verify import (
    build_douyin_account_verify_response,
    shape_douyin_account_verify_result,
)


class DouyinAccountVerifyTests(unittest.TestCase):
    def test_shape_expired_skips_type(self):
        result = shape_douyin_account_verify_result(
            {
                "success": True,
                "opened": True,
                "requires_login": True,
                "account_type_skipped": True,
                "account_profile": {},
                "account_cookie_loaded": True,
                "account_cookie_count": 3,
                "steps": [{"name": "detect_login_state", "ok": False}],
            }
        )
        self.assertFalse(result["cookie_valid"])
        self.assertEqual(result["activity_status"], "expired")
        self.assertEqual(result["account_type"], "unknown")
        self.assertEqual(result["account_type_reason"], "活性验证失败，未执行账号类型检测")
        self.assertTrue(result["account_type_skipped"])
        self.assertFalse(result["is_blue_v"])

    def test_shape_active_blue_v(self):
        result = shape_douyin_account_verify_result(
            {
                "success": True,
                "opened": True,
                "requires_login": False,
                "account_profile": {
                    "nickname": "企业号",
                    "unique_id": "brand_01",
                    "enterprise_verify_reason": "品牌官方账号",
                },
                "account_cookie_loaded": True,
                "account_cookie_count": 10,
            }
        )
        self.assertTrue(result["cookie_valid"])
        self.assertEqual(result["activity_status"], "active")
        self.assertEqual(result["account_type"], "blue_v")
        self.assertEqual(result["account_type_label"], "蓝V账号")
        self.assertTrue(result["is_blue_v"])
        self.assertFalse(result["account_type_skipped"])
        self.assertEqual(result["douyin_unique_id"], "brand_01")

    def test_shape_active_personal(self):
        result = shape_douyin_account_verify_result(
            {
                "success": True,
                "opened": True,
                "requires_login": False,
                "account_profile": {
                    "nickname": "路人甲",
                    "unique_id": "user_01",
                    "custom_verify": "",
                },
            }
        )
        self.assertTrue(result["cookie_valid"])
        self.assertEqual(result["account_type"], "personal")
        self.assertEqual(result["account_type_label"], "普通账号")
        self.assertFalse(result["is_blue_v"])

    def test_shape_verification_required_skips_type(self):
        result = shape_douyin_account_verify_result(
            {
                "success": True,
                "opened": True,
                "requires_verification": True,
                "account_type_skipped": True,
                "account_profile": {},
            }
        )
        self.assertFalse(result["cookie_valid"])
        self.assertEqual(result["activity_status"], "risk")
        self.assertEqual(result["account_type"], "unknown")
        self.assertTrue(result["account_type_skipped"])

    def test_build_verify_requires_cookie(self):
        with self.assertRaises(Exception) as ctx:
            build_douyin_account_verify_response({})
        self.assertIn("account_cookie", str(ctx.exception))

    def test_build_verify_uses_executor_and_shapes(self):
        def executor(raw_cookies, browser, options):
            self.assertTrue(options.get("verify_login_first"))
            self.assertIn("sessionid", raw_cookies)
            return {
                "success": True,
                "opened": True,
                "resolved_browser": browser,
                "engine": "playwright",
                "account_cookie_loaded": True,
                "account_cookie_count": 1,
                "requires_login": True,
                "account_type_skipped": True,
                "steps": [{"name": "before_account_activity_check", "ok": False}],
                "account_profile": {},
            }

        response = build_douyin_account_verify_response(
            {
                "account_cookie": "sessionid=SECRET; sid_guard=x",
                "browser_name": "chrome",
            },
            executor=executor,
        )
        self.assertFalse(response["cookie_valid"])
        self.assertEqual(response["activity_status"], "expired")
        self.assertEqual(response["account_type"], "unknown")
        self.assertTrue(response["account_type_skipped"])


if __name__ == "__main__":
    unittest.main()
