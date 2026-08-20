# -*- coding: utf-8 -*-
"""One-shot Douyin inbox unread snapshot for the middle platform.

Unlike conversation-monitors (long-running), this opens the account DM panel
once, scans unread badges, then closes the browser.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def _sr():
    from aisec_agent.web import session_rag_chat as sr

    return sr


def shape_douyin_account_inbox_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize inbox scan output for middle-platform consumption."""
    payload = dict(raw or {})
    ok = bool(payload.get("ok", True))
    failure_code = str(payload.get("failure_code") or "").strip()
    unread_count = int(payload.get("inbox_unread_count") or payload.get("unread_count") or 0)
    unread_people = int(payload.get("inbox_unread_people") or payload.get("unread_people") or 0)
    return {
        "ok": ok and not failure_code,
        "inbox_unread_count": unread_count,
        "unread_count": unread_count,
        "inbox_unread_people": unread_people,
        "unread_people": unread_people,
        "requires_login": bool(payload.get("requires_login")),
        "failure_code": failure_code,
        "message": str(payload.get("message") or payload.get("detail") or "").strip(),
        "account_key": str(payload.get("account_key") or ""),
        "browser": str(payload.get("browser") or payload.get("resolved_browser") or ""),
        "steps": list(payload.get("steps") or []),
    }


def _run_inbox_playwright(payload: Dict[str, Any]) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    from aisec_agent.web.douyin_conversation_monitor import (
        _dm_account_browser_profile_exists,
        _dm_account_browser_user_data_dir,
        _dm_open_douyin_messages_surface,
        _dm_pick_account_cookie_payload,
        _dm_scan_inbox_summary,
    )

    sr = _sr()
    raw_cookies = sr._dm_cookie_text(_dm_pick_account_cookie_payload(payload))
    account_id = str(payload.get("account_id") or payload.get("account") or "").strip()
    account_key = str(payload.get("account_key") or "").strip()
    if raw_cookies:
        account_key = account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    browser_name = str(payload.get("browser_name") or payload.get("browser") or "chrome").strip() or "chrome"
    headless = True if payload.get("headless") is None else sr._dm_bool_text(payload.get("headless"))
    timeout_ms = int(sr._payload_float(payload, "timeout_ms", 45000))

    if not raw_cookies and not account_key:
        raise sr.WebInputError("account_cookie or account_key is required")

    options: Dict[str, Any] = {
        "browser": browser_name,
        "browser_name": browser_name,
        "headless": headless,
        "keep_browser_open": False,
        "persistent_context": bool(account_key),
        "timeout_ms": timeout_ms,
        "slow_mo": int(sr._payload_float(payload, "slow_mo", 80)),
        "viewport_width": sr.DM_DEFAULT_VIEWPORT_WIDTH,
        "viewport_height": sr.DM_DEFAULT_VIEWPORT_HEIGHT,
        "page_zoom_percent": sr.DM_DEFAULT_PAGE_ZOOM_PERCENT,
    }
    if account_key:
        options["user_data_dir"] = str(_dm_account_browser_user_data_dir(browser_name, account_key))
        if not raw_cookies and not _dm_account_browser_profile_exists(browser_name, account_key):
            raise sr.WebInputError(f"account profile not found for {browser_name}/{account_key}")

    playwright = None
    context = None
    browser = None
    try:
        playwright, context, browser, _keep = sr._dm_get_playwright_context(
            sync_playwright, browser_name, options
        )
        cookies = sr._dm_parse_account_cookies(raw_cookies) if raw_cookies else []
        if cookies:
            context.add_cookies(cookies)
        page = sr._dm_message_page(context, "https://www.douyin.com/")
        sr._dm_apply_page_geometry(page, options)
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
        except Exception:
            pass
        sr._dm_append_optional_step([], sr._dm_handle_douyin_login_save_prompt(page))

        steps = _dm_open_douyin_messages_surface(page, timeout_ms=min(timeout_ms, 20000))
        login_detail = ""
        for step in steps:
            if not step.get("ok") and "login_required" in str(step.get("detail") or ""):
                login_detail = str(step.get("detail") or "login_required")
                break
        if login_detail:
            return {
                "ok": False,
                "requires_login": True,
                "failure_code": "login_required",
                "message": login_detail,
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "account_key": account_key,
                "browser": browser_name,
                "steps": steps,
            }

        inbox = _dm_scan_inbox_summary(page)
        if not inbox.get("ok"):
            return {
                "ok": False,
                "requires_login": False,
                "failure_code": "inbox_scan_failed",
                "message": str(inbox.get("detail") or "收件箱摘要失败"),
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "account_key": account_key,
                "browser": browser_name,
                "steps": steps,
            }
        return {
            "ok": True,
            "requires_login": False,
            "failure_code": "",
            "message": str(inbox.get("detail") or "").strip(),
            "inbox_unread_count": int(inbox.get("unread_count") or 0),
            "inbox_unread_people": int(inbox.get("unread_people") or 0),
            "account_key": account_key,
            "browser": browser_name,
            "steps": steps,
        }
    finally:
        sr._dm_stop_playwright_context(context, browser, playwright)


def build_douyin_account_inbox_response(
    payload: Dict[str, Any],
    executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build one-shot inbox unread response for middle platform."""
    runner = executor or _run_inbox_playwright
    raw = dict(runner(dict(payload or {})) or {})
    return shape_douyin_account_inbox_result(raw)
