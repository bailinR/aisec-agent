"""Account activity + type verify API for the data middle platform.

Flow:
1. Check whether Cookie can log in (activity).
2. If inactive / login required / verification required -> skip type detection.
3. If active -> detect blue_v vs personal.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _sr():
    from aisec_agent.web import session_rag_chat as sr

    return sr


def shape_douyin_account_verify_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map raw browser/apply result into middle-platform verify fields."""
    sr = _sr()
    payload = dict(raw or {})
    profile_raw = payload.get("account_profile") if isinstance(payload.get("account_profile"), dict) else {}
    profile = sr._douyin_account_profile(
        profile_raw,
        source=str(profile_raw.get("source") or ""),
        error=str(profile_raw.get("reason") or profile_raw.get("error") or ""),
    )

    requires_login = bool(payload.get("requires_login"))
    requires_verification = bool(payload.get("requires_verification"))
    type_skipped = bool(payload.get("account_type_skipped"))
    opened = bool(payload.get("opened"))
    success = payload.get("success")
    if success is None:
        success = True

    detected_type = str(profile.get("account_type") or "unknown").strip() or "unknown"
    type_known = detected_type in {"blue_v", "personal"}

    cookie_valid = False
    activity_status = "unknown"
    activity_reason = ""

    if not success and not opened:
        activity_status = "error"
        activity_reason = str(
            payload.get("failure_summary")
            or payload.get("error")
            or payload.get("message")
            or "浏览器未能打开或应用 Cookie"
        ).strip()
        cookie_valid = False
    elif requires_login and not type_known:
        activity_status = "expired"
        activity_reason = "需要重新登录（Cookie 失活或未登录）"
        cookie_valid = False
        type_skipped = True
    elif requires_verification and not type_known:
        activity_status = "risk"
        activity_reason = "需要二次验证/安全验证"
        cookie_valid = False
        type_skipped = True
    elif type_known or (opened and not requires_login):
        activity_status = "active"
        activity_reason = "Cookie 可登录"
        cookie_valid = True
    else:
        activity_status = "unknown"
        activity_reason = str(
            payload.get("message")
            or profile.get("reason")
            or "未能确认登录态"
        ).strip() or "未能确认登录态"
        cookie_valid = False

    if type_skipped or not cookie_valid:
        account_type = "unknown"
        account_type_label = "未识别"
        account_type_reason = "活性验证失败，未执行账号类型检测"
        is_blue_v = False
    elif type_known:
        account_type = detected_type
        account_type_label = str(profile.get("account_type_label") or (
            "蓝V账号" if detected_type == "blue_v" else "普通账号"
        ))
        account_type_reason = str(profile.get("reason") or "self_profile")
        is_blue_v = bool(profile.get("is_blue_v")) or detected_type == "blue_v"
    else:
        account_type = "unknown"
        account_type_label = "未识别"
        account_type_reason = str(profile.get("reason") or "profile_unavailable")
        is_blue_v = False

    return {
        "ok": True,
        "cookie_valid": cookie_valid,
        "activity_status": activity_status,
        "activity_reason": activity_reason,
        "account_type": account_type,
        "account_type_label": account_type_label,
        "account_type_reason": account_type_reason,
        "is_blue_v": is_blue_v,
        "nickname": str(profile.get("nickname") or ""),
        "douyin_unique_id": str(profile.get("unique_id") or profile.get("short_id") or ""),
        "uid": str(profile.get("uid") or ""),
        "sec_uid": str(profile.get("sec_uid") or ""),
        "account_type_skipped": bool(type_skipped) or not cookie_valid,
        "requires_login": requires_login and not type_known,
        "requires_verification": requires_verification and not type_known,
        "browser": str(payload.get("browser") or payload.get("resolved_browser") or ""),
        "account_cookie_loaded": bool(payload.get("account_cookie_loaded")),
        "account_cookie_count": int(payload.get("account_cookie_count") or 0),
        "account_profile": {
            "nickname": profile.get("nickname") or "",
            "unique_id": profile.get("unique_id") or "",
            "uid": profile.get("uid") or "",
            "sec_uid": profile.get("sec_uid") or "",
            "custom_verify": profile.get("custom_verify") or "",
            "enterprise_verify_reason": profile.get("enterprise_verify_reason") or "",
            "detected_at": profile.get("detected_at") or "",
            "source": profile.get("source") or "",
        },
        "steps": list(payload.get("steps") or []),
        "failure_code": str(payload.get("failure_code") or ""),
        "failure_summary": str(payload.get("failure_summary") or payload.get("error") or ""),
    }


def build_douyin_account_verify_response(
    payload: Dict[str, Any],
    executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Verify Cookie login activity, then detect blue_v / personal when active."""
    sr = _sr()
    normalized = dict(payload or {})
    raw_cookies = sr._dm_cookie_text(
        normalized.get("account_cookies")
        or normalized.get("account_cookie")
        or normalized.get("cookie")
        or normalized.get("cookies")
        or ""
    )
    if not raw_cookies:
        raise sr.WebInputError("account_cookie is required")

    browser = str(normalized.get("browser_name") or normalized.get("browser") or "edge").strip() or "edge"
    # API default: headless closed browser for server-side verify.
    if "headless" in normalized:
        headless = sr._dm_bool_text(normalized.get("headless"))
    else:
        headless = True
    keep_open = sr._dm_bool_text(normalized.get("keep_browser_open", False))

    apply_payload = {
        "account_cookies": raw_cookies,
        "browser_name": browser,
        "browser": browser,
        "headless": headless,
        "keep_browser_open": keep_open,
        "persistent_context": sr._dm_bool_text(normalized.get("persistent_context", False)),
        "user_data_dir": str(normalized.get("user_data_dir") or "").strip(),
        "open_url": str(normalized.get("open_url") or "https://www.douyin.com/").strip() or "https://www.douyin.com/",
        "timeout_ms": int(sr._payload_float(normalized, "timeout_ms", 45000)),
        "slow_mo": int(sr._payload_float(normalized, "slow_mo", 80)),
        "viewport_width": int(sr._payload_float(normalized, "viewport_width", sr.DM_DEFAULT_VIEWPORT_WIDTH)),
        "viewport_height": int(sr._payload_float(normalized, "viewport_height", sr.DM_DEFAULT_VIEWPORT_HEIGHT)),
        "page_zoom_percent": int(sr._payload_float(normalized, "page_zoom_percent", sr.DM_DEFAULT_PAGE_ZOOM_PERCENT)),
        "screenshot_on_failure": sr._dm_bool_text(normalized.get("screenshot_on_failure", True)),
        "screenshot_dir": str(normalized.get("screenshot_dir") or sr.DM_DEBUG_ARTIFACT_DIR),
        "screenshot_prefix": str(normalized.get("screenshot_prefix") or normalized.get("task_id") or "dm-verify"),
    }

    # Inject verify_login_first into options via a thin executor wrapper around apply.
    selected_executor = executor
    if selected_executor is None:
        selected_executor = sr._web_douyin_account_cookie_playwright_executor

    def _verify_executor(raw: str, browser_name: str, options: Dict[str, Any]) -> Dict[str, Any]:
        opts = dict(options or {})
        opts["verify_login_first"] = True
        opts.setdefault("open_url", apply_payload["open_url"])
        return selected_executor(raw, browser_name, opts)

    _verify_executor.needs_raw_cookies = True  # type: ignore[attr-defined]

    apply_result = sr.build_douyin_account_cookie_apply_response(
        apply_payload,
        executor=_verify_executor,
    )
    # build_douyin_account_cookie_apply_response always runs profile shaping;
    # preserve skip flags from executor.
    if bool(apply_result.get("requires_login")) or bool(apply_result.get("account_type_skipped")):
        apply_result["account_type_skipped"] = True
    return shape_douyin_account_verify_result(apply_result)
