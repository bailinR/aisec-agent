# -*- coding: utf-8 -*-
"""One-shot Douyin inbox unread snapshot for the middle platform.

Unlike conversation-monitors (long-running), this opens the account DM panel
once, scans unread badges, then closes the browser.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


def _sr():
    from aisec_agent.web import session_rag_chat as sr

    return sr


def _resolve_inbox_account_key(payload: Dict[str, Any]) -> str:
    """Resolve account_key the same way inbox / monitor start do."""
    from aisec_agent.web import douyin_conversation_monitor as monitor

    sr = _sr()
    raw_cookies = sr._dm_cookie_text(monitor._dm_pick_account_cookie_payload(payload))
    account_id = str(payload.get("account_id") or payload.get("account") or "").strip()
    account_key = str(payload.get("account_key") or "").strip()
    if raw_cookies:
        account_key = account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    return account_key


def _temporarily_release_monitor_for_inbox(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Stop an alive monitor that holds the same browser profile, if any.

    Inbox and conversation-monitors share persistent user_data_dir; opening a
    second Chromium on the same profile fails. Pause watch during one-shot sync.
    """
    from aisec_agent.web import douyin_conversation_monitor as monitor

    account_key = _resolve_inbox_account_key(payload)
    if not account_key:
        return None
    with monitor._DM_ACCOUNT_CONTROLLERS_LOCK:
        controller = monitor._DM_ACCOUNT_CONTROLLERS.get(account_key)
    if not controller:
        return None
    snap = controller.snapshot()
    if not snap.get("alive"):
        return None
    resume_payload = {
        "account_id": snap.get("account_id"),
        "account_name": snap.get("account_name"),
        "account_key": account_key,
        "account_cookie": getattr(controller, "account_cookies", "") or "",
        "browser_name": snap.get("browser_name") or "chrome",
        "headless": snap.get("headless", True),
        "generate_reply": snap.get("generate_reply", False),
        "auto_send": snap.get("auto_send", False),
        "poll_seconds": snap.get("poll_seconds", 8),
        "reply_backend": snap.get("reply_backend"),
        "gpu_model": snap.get("gpu_model"),
        "max_auto_replies_before_peer": snap.get("max_auto_replies_before_peer", 2),
        "project_id": getattr(controller, "project_id", "") or "",
        "company_id": getattr(controller, "company_id", "") or "",
        "source_platform": getattr(controller, "source_platform", "") or "抖音",
        "_logic": getattr(controller, "logic", None),
        "_project_store": getattr(controller, "project_store", None),
    }
    controller.stop()
    thread = getattr(controller, "thread", None)
    if thread is not None and thread.is_alive():
        thread.join(timeout=45)
    # Drop any leftover sync-API session on this profile before re-launch.
    sr = _sr()
    browser_name = str(resume_payload.get("browser_name") or "chrome")
    user_data_dir = monitor._dm_account_browser_user_data_dir(browser_name, account_key)
    session_key = f"{browser_name.lower()}|{user_data_dir.resolve()}"
    try:
        sr._dm_drop_playwright_session(session_key)
    except Exception:
        pass
    # Brief settle so Chromium releases the profile lock.
    time.sleep(1.0)
    return resume_payload


def _resume_monitor_after_inbox(resume_payload: Optional[Dict[str, Any]]) -> None:
    if not resume_payload:
        return
    from aisec_agent.web import douyin_conversation_monitor as monitor

    logic = resume_payload.pop("_logic", None)
    project_store = resume_payload.pop("_project_store", None)
    try:
        monitor.build_douyin_conversation_monitor_start_response(
            resume_payload,
            logic=logic,
            project_store=project_store,
        )
    except Exception:
        # Sync already finished; monitor restart is best-effort.
        pass


def _run_inbox_playwright_via_web_runtime(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run inbox Playwright on the dedicated web thread; pause monitor if needed."""
    sr = _sr()
    resume = _temporarily_release_monitor_for_inbox(payload)
    try:
        return sr._run_web_playwright_call(_run_inbox_playwright, payload)
    finally:
        _resume_monitor_after_inbox(resume)


def shape_douyin_account_inbox_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize inbox scan output for middle-platform consumption."""
    payload = dict(raw or {})
    ok = bool(payload.get("ok", True))
    failure_code = str(payload.get("failure_code") or "").strip()
    unread_count = int(payload.get("inbox_unread_count") or payload.get("unread_count") or 0)
    unread_people = int(payload.get("inbox_unread_people") or payload.get("unread_people") or 0)
    conversations = []
    for item in payload.get("unread_conversations") or payload.get("conversations") or []:
        if not isinstance(item, dict):
            continue
        conversations.append(
            {
                "peer_nickname": str(item.get("peer_nickname") or "").strip(),
                "peer_uid": str(item.get("peer_uid") or "").strip(),
                "peer_sec_uid": str(item.get("peer_sec_uid") or "").strip(),
                "peer_profile_url": str(item.get("peer_profile_url") or "").strip(),
                "last_message": str(item.get("last_message") or "").strip(),
                "unread_count": max(1, int(item.get("unread_count") or 1)),
                "conversation_id": str(item.get("conversation_id") or "").strip(),
                "updated_at": str(item.get("updated_at") or "").strip(),
            }
        )
    if conversations and unread_people <= 0:
        unread_people = len(conversations)
    if conversations and unread_count <= 0:
        unread_count = sum(int(item.get("unread_count") or 0) for item in conversations)
    return {
        "ok": ok and not failure_code,
        "account_id": str(payload.get("account_id") or "").strip(),
        "account_key": str(payload.get("account_key") or ""),
        "inbox_unread_count": unread_count,
        "unread_count": unread_count,
        "inbox_unread_people": unread_people,
        "unread_people": unread_people,
        "unread_conversations": conversations,
        "clears_unread": False,
        "requires_login": bool(payload.get("requires_login")),
        "requires_verification": bool(payload.get("requires_verification")),
        "failure_code": failure_code,
        "message": str(payload.get("message") or payload.get("detail") or "").strip(),
        "browser": str(payload.get("browser") or payload.get("resolved_browser") or ""),
        "steps": list(payload.get("steps") or []),
    }


def _run_inbox_playwright(payload: Dict[str, Any]) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    from aisec_agent.web import douyin_conversation_monitor as monitor

    _dm_account_browser_profile_exists = monitor._dm_account_browser_profile_exists
    _dm_account_browser_user_data_dir = monitor._dm_account_browser_user_data_dir
    _dm_detect_douyin_login_required = monitor._dm_detect_douyin_login_required
    _dm_message_panel_visible = monitor._dm_message_panel_visible
    _dm_open_douyin_messages_surface = monitor._dm_open_douyin_messages_surface
    _dm_pick_account_cookie_payload = monitor._dm_pick_account_cookie_payload
    _dm_scan_inbox_summary = monitor._dm_scan_inbox_summary
    scan_unread = getattr(monitor, "_dm_scan_unread_conversations", None)
    if not callable(scan_unread):
        raise RuntimeError(
            "aisec runtime missing _dm_scan_unread_conversations; "
            "restart Web/worker so accounts/inbox loads the reply-pool code"
        )

    sr = _sr()
    raw_cookies = sr._dm_cookie_text(_dm_pick_account_cookie_payload(payload))
    account_id = str(payload.get("account_id") or payload.get("account") or "").strip()
    account_key = str(payload.get("account_key") or "").strip()
    if raw_cookies:
        account_key = account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    browser_name = str(payload.get("browser_name") or payload.get("browser") or "edge").strip() or "edge"
    # Reply-pool sync prefers headed; default remains headless unless caller sets false.
    if "headless" in payload:
        headless = sr._dm_bool_text(payload.get("headless"))
    else:
        headless = True
    keep_open = sr._dm_bool_text(payload.get("keep_browser_open", False))
    timeout_ms = int(sr._payload_float(payload, "timeout_ms", 120000 if not headless else 45000))
    wait_verify_ms = int(sr._payload_float(payload, "wait_verification_ms", max(timeout_ms - 15000, 60000 if not headless else 0)))

    if not raw_cookies and not account_key:
        raise sr.WebInputError("account_cookie or account_key is required")

    options: Dict[str, Any] = {
        "browser": browser_name,
        "browser_name": browser_name,
        "headless": headless,
        "keep_browser_open": keep_open,
        # Headed sync should reuse account profile so post-verify session sticks.
        "persistent_context": bool(account_key) or (not headless),
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
    elif not headless:
        # Ephemeral headed profile keyed by account_id so cookies persist for the wait loop.
        fallback_key = account_id or "inbox_headed"
        options["user_data_dir"] = str(_dm_account_browser_user_data_dir(browser_name, f"inbox_{fallback_key}"))

    playwright = None
    context = None
    browser = None
    try:
        playwright, context, browser, keep_open = sr._dm_get_playwright_context(
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

        def _step_auth_flags(step_list):
            login_detail = ""
            verify_detail = ""
            for step in step_list:
                detail = str(step.get("detail") or "")
                if not step.get("ok") and "requires_verification" in detail and not verify_detail:
                    verify_detail = detail
                if not step.get("ok") and "login_required" in detail and not login_detail:
                    login_detail = detail
            return login_detail, verify_detail

        login_detail, verify_detail = _step_auth_flags(steps)

        # Headed mode: leave the window open and wait for operator to pass captcha.
        if verify_detail and (not headless) and wait_verify_ms > 0:
            steps.append({
                "name": "wait_manual_verification",
                "ok": True,
                "detail": f"headed wait up to {wait_verify_ms}ms for verification",
            })
            deadline = time.time() + (wait_verify_ms / 1000.0)
            while time.time() < deadline:
                time.sleep(2.0)
                state = _dm_detect_douyin_login_required(page)
                if state.get("requires_verification") or state.get("required"):
                    continue
                retry_steps = _dm_open_douyin_messages_surface(page, timeout_ms=min(15000, timeout_ms))
                steps.extend(retry_steps)
                login_detail, verify_detail = _step_auth_flags(retry_steps)
                if (not verify_detail and not login_detail) or _dm_message_panel_visible(page):
                    verify_detail = ""
                    break
            else:
                # timed out still on verification
                pass

        if verify_detail:
            return {
                "ok": False,
                "requires_login": False,
                "requires_verification": True,
                "failure_code": "requires_verification",
                "message": verify_detail if headless else (
                    verify_detail + "；有头等待超时，请在弹出的浏览器完成验证后重试同步"
                ),
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": account_id,
                "account_key": account_key,
                "browser": browser_name,
                "steps": steps,
            }
        if login_detail:
            return {
                "ok": False,
                "requires_login": True,
                "requires_verification": False,
                "failure_code": "login_required",
                "message": login_detail,
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": account_id,
                "account_key": account_key,
                "browser": browser_name,
                "steps": steps,
            }

        conversations_scan = scan_unread(page)
        conversations = list(conversations_scan.get("conversations") or [])
        inbox = _dm_scan_inbox_summary(page)
        unread_count = int(conversations_scan.get("unread_count") or 0)
        unread_people = int(conversations_scan.get("unread_people") or 0)
        if inbox.get("ok"):
            unread_count = max(unread_count, int(inbox.get("unread_count") or 0))
            unread_people = max(unread_people, int(inbox.get("unread_people") or 0))
        if conversations and unread_people < len(conversations):
            unread_people = len(conversations)
        if not conversations_scan.get("ok") and not inbox.get("ok"):
            return {
                "ok": False,
                "requires_login": False,
                "failure_code": "inbox_scan_failed",
                "message": str(
                    conversations_scan.get("detail")
                    or inbox.get("detail")
                    or "收件箱摘要失败"
                ),
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": account_id,
                "account_key": account_key,
                "browser": browser_name,
                "steps": steps,
            }
        detail_parts = []
        if conversations_scan.get("detail"):
            detail_parts.append(str(conversations_scan.get("detail")))
        if inbox.get("detail"):
            detail_parts.append(str(inbox.get("detail")))
        return {
            "ok": True,
            "requires_login": False,
            "requires_verification": False,
            "failure_code": "",
            "message": "；".join(detail_parts).strip(),
            "inbox_unread_count": unread_count,
            "inbox_unread_people": unread_people,
            "unread_conversations": conversations,
            "account_id": account_id,
            "account_key": account_key,
            "browser": browser_name,
            "steps": steps,
        }
    finally:
        if not keep_open:
            sr._dm_stop_playwright_context(context, browser, playwright)


def build_douyin_account_inbox_response(
    payload: Dict[str, Any],
    executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build one-shot inbox unread response for middle platform."""
    # Default path must use the single-thread web Playwright executor so we do
    # not reuse a persistent context created on another thread (greenlet error).
    runner = executor or _run_inbox_playwright_via_web_runtime
    raw = dict(runner(dict(payload or {})) or {})
    return shape_douyin_account_inbox_result(raw)
