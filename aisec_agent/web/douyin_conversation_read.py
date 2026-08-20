"""Detect whether a sent Douyin private message has a peer reply.

Provides the capability API used by the data middle platform (comment-kit):
given account login + target profile + expected outgoing text, open the
conversation, parse bubbles, and return has_reply / latest_peer_reply.

Scheduling (which leads, when to call) stays in comment-kit.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_ACCOUNT_READ_LOCKS: Dict[str, threading.Lock] = {}
_ACCOUNT_READ_LOCKS_GUARD = threading.RLock()


def _sr():
    from aisec_agent.web import session_rag_chat as sr

    return sr


def _account_lock(account_key: str) -> threading.Lock:
    key = str(account_key or "default").strip() or "default"
    with _ACCOUNT_READ_LOCKS_GUARD:
        lock = _ACCOUNT_READ_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ACCOUNT_READ_LOCKS[key] = lock
        return lock


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text_matches(candidate: Any, needle: Any) -> bool:
    haystack = _normalize_text(candidate)
    target = _normalize_text(needle)
    if not haystack or not target:
        return False
    if haystack == target or target in haystack or haystack in target:
        return True
    head = target[:64]
    if not head or head not in haystack:
        return False
    if len(target) > 96:
        tail = target[-32:]
        return (not tail) or (tail in haystack)
    return True


def extract_latest_peer_reply(
    messages: Optional[List[Dict[str, Any]]],
    after_text: Optional[str] = None,
) -> str:
    """Pick the latest peer bubble, optionally only after a known outgoing text."""
    items = list(messages or [])
    if not items:
        return ""
    start_idx = 0
    needle = _normalize_text(after_text)
    if needle:
        for idx, item in enumerate(items):
            if str(item.get("role") or "") == "self" and _text_matches(item.get("text"), needle):
                start_idx = idx + 1
    peer_texts = [
        _normalize_text(item.get("text"))
        for item in items[start_idx:]
        if str(item.get("role") or "") == "peer" and _normalize_text(item.get("text"))
    ]
    return peer_texts[-1] if peer_texts else ""


def annotate_outgoing_roles(
    messages: Optional[List[Dict[str, Any]]],
    expected_outgoing: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Force role=self when bubble text matches the known outgoing message."""
    items = [dict(item) for item in (messages or [])]
    needle = _normalize_text(expected_outgoing)
    if not needle:
        return items
    for item in items:
        if _text_matches(item.get("text"), needle):
            item["role"] = "self"
    return items


def build_reply_detection_result(
    messages: Optional[List[Dict[str, Any]]],
    expected_outgoing: str = "",
    *,
    ok: bool = True,
    failure_code: str = "",
    message: str = "",
) -> Dict[str, Any]:
    """Pure detection from bubble list (unit-testable)."""
    annotated = annotate_outgoing_roles(messages, expected_outgoing)
    needle = _normalize_text(expected_outgoing)
    outgoing_found = False
    if needle:
        outgoing_found = any(
            str(item.get("role") or "") == "self" and _text_matches(item.get("text"), needle)
            for item in annotated
        )
    else:
        outgoing_found = any(str(item.get("role") or "") == "self" for item in annotated)

    peer_replies: List[str] = []
    if needle and outgoing_found:
        start_idx = 0
        for idx, item in enumerate(annotated):
            if str(item.get("role") or "") == "self" and _text_matches(item.get("text"), needle):
                start_idx = idx + 1
        peer_replies = [
            _normalize_text(item.get("text"))
            for item in annotated[start_idx:]
            if str(item.get("role") or "") == "peer" and _normalize_text(item.get("text"))
        ]
    elif not needle:
        # Without an anchor, do not invent a reply from historical peer bubbles.
        peer_replies = []

    latest = peer_replies[-1] if peer_replies else ""
    resolved_failure = str(failure_code or "").strip()
    resolved_message = str(message or "").strip()
    if not ok:
        resolved_failure = resolved_failure or "browser_error"
        resolved_message = resolved_message or "conversation read failed"
    elif needle and not outgoing_found:
        resolved_failure = resolved_failure or "outgoing_not_found"
        resolved_message = resolved_message or "会话中未找到我方发出的文案锚点"
    else:
        resolved_failure = ""
        resolved_message = ""

    return {
        "ok": bool(ok),
        "outgoing_found": bool(outgoing_found),
        "has_reply": bool(latest),
        "latest_peer_reply": latest,
        "peer_replies": peer_replies,
        "peer_reply": latest,
        "reply_text": latest,
        "delivery_verified": bool(outgoing_found),
        "messages": annotated,
        "failure_code": resolved_failure,
        "message": resolved_message,
    }


def _collect_conversation_bubbles(page: Any) -> List[Dict[str, Any]]:
    """Collect visible chat bubbles with self/peer roles from the open DM panel."""
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 24 && rect.height >= 14 && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
      };
      const editors = Array.from(document.querySelectorAll(
        '[contenteditable="true"], [contenteditable="plaintext-only"], textarea, [role="textbox"], .public-DraftEditor-content'
      )).filter(isVisible);
      if (!editors.length) return {ok: false, detail: 'editor_not_found', bubbles: []};
      const editor = editors.sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.left + br.top) - (ar.left + ar.top);
      })[0];
      const editorRect = editor.getBoundingClientRect();
      const panelRoot = editor.closest('[class*="im"], [class*="Im"], [class*="dialog"], aside, section') || document.body;
      const panelRect = panelRoot.getBoundingClientRect ? panelRoot.getBoundingClientRect() : editorRect;
      const midX = (panelRect.left + panelRect.right) / 2;
      const inEditor = (el) => editors.some((node) => node === el || node.contains(el) || el.contains(node));

      const raw = [];
      for (const el of panelRoot.querySelectorAll('div, span, p, li')) {
        if (!isVisible(el) || inEditor(el)) continue;
        let text = normalize(el.innerText || el.textContent || '');
        if (!text || text.length < 1 || text.length > 500) continue;
        const rect = el.getBoundingClientRect();
        if (rect.bottom >= editorRect.top - 2) continue;
        if (rect.top < panelRect.top + 36) continue;
        if (rect.left < panelRect.left - 12 || rect.right > panelRect.right + 12) continue;
        if (rect.width > panelRect.width * 0.98) continue;
        if (el.childElementCount > 8) continue;
        if (/发送|表情|按住说话|说点什么|输入消息|搜索|下载客户端|实时接收好友消息/.test(text)) continue;
        if (/抖音精选|记录美好生活|推荐|关注|商城|发布作品/.test(text)) continue;
        if (/^\\d{1,2}:\\d{2}$/.test(text) || /^(昨天|周一|周二|周三|周四|周五|周六|周日|刚刚|\\d+分钟前|\\d+秒前)$/.test(text)) continue;

        let readStatus = null;
        if (/\\s已读$/.test(text) || text.endsWith('已读')) {
          readStatus = 'read';
          text = text.replace(/\\s*已读$/, '').trim();
        } else if (/\\s未读$/.test(text) || text.endsWith('未读')) {
          readStatus = 'unread';
          text = text.replace(/\\s*未读$/, '').trim();
        }
        text = text.replace(/^(昨天|今天|\\d{1,2}月\\d{1,2}日)\\s+\\d{1,2}:\\d{2}\\s*/, '').trim();
        if (!text) continue;

        const cx = rect.left + rect.width / 2;
        let role = '';
        if (cx > midX + 18) role = 'self';
        else if (cx < midX - 18) role = 'peer';
        else role = '';
        // Skip ambiguous center chrome; do not default to peer.
        if (!role) continue;

        raw.push({
          text: text.slice(0, 400),
          role,
          read_status: readStatus,
          x: Math.round(rect.left),
          y: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }

      // Dedupe nested nodes: keep the smallest bubble near the same text/y.
      raw.sort((a, b) => a.y - b.y || a.x - b.x || (a.width * a.height) - (b.width * b.height));
      const unique = [];
      for (const item of raw) {
        const dup = unique.some((prev) => {
          if (prev.role !== item.role) return false;
          if (Math.abs(prev.y - item.y) > 18) return false;
          if (prev.text === item.text) return true;
          if (prev.text.includes(item.text) || item.text.includes(prev.text)) {
            return Math.abs(prev.x - item.x) < 40;
          }
          return false;
        });
        if (!dup) unique.push(item);
        else {
          // Prefer shorter text (inner bubble) when nested.
          for (let i = 0; i < unique.length; i += 1) {
            const prev = unique[i];
            if (prev.role === item.role && Math.abs(prev.y - item.y) <= 18 &&
                (prev.text.includes(item.text) || item.text.includes(prev.text))) {
              if (item.text.length < prev.text.length) unique[i] = item;
              break;
            }
          }
        }
      }
      unique.sort((a, b) => a.y - b.y || a.x - b.x);
      return {ok: true, detail: `bubbles=${unique.length}`, bubbles: unique};
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        return []
    if not isinstance(data, dict):
        return []
    bubbles = data.get("bubbles") or []
    result: List[Dict[str, Any]] = []
    for item in bubbles:
        if not isinstance(item, dict):
            continue
        text = _normalize_text(item.get("text"))
        role = str(item.get("role") or "").strip()
        if not text or role not in {"self", "peer"}:
            continue
        result.append({
            "text": text,
            "role": role,
            "read_status": item.get("read_status"),
            "x": int(item.get("x") or 0),
            "y": int(item.get("y") or 0),
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
        })
    return result


def _resolve_target_profile_url(payload: Dict[str, Any]) -> str:
    url = str(
        payload.get("target_profile_url")
        or payload.get("profile_url")
        or payload.get("url")
        or ""
    ).strip()
    if url:
        return url
    sec_uid = str(payload.get("sec_uid") or "").strip()
    if sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}"
    return ""


def _pick_cookie_payload(payload: Dict[str, Any]) -> Any:
    for key in ("account_cookies", "account_cookie", "cookie", "cookies", "cookie_text"):
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return ""


def _failure_result(code: str, message: str, *, messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "outgoing_found": False,
        "has_reply": False,
        "latest_peer_reply": "",
        "peer_replies": [],
        "peer_reply": "",
        "reply_text": "",
        "delivery_verified": False,
        "messages": list(messages or []),
        "failure_code": str(code or "browser_error"),
        "message": str(message or ""),
    }


def _map_exception_failure(exc: BaseException) -> Tuple[str, str]:
    text = str(exc or "").strip()
    lower = text.lower()
    if "login" in lower or "登录" in text:
        return "login_required", text or "账号登录态不可用"
    if "private message button" in lower or "message button" in lower:
        return "message_button_failed", text or "无法点击私信按钮"
    if "editor" in lower or "input" in lower:
        return "conversation_not_found", text or "未找到私信会话输入框"
    return "browser_error", text or "浏览器自动化异常"


def _run_conversation_read_playwright(payload: Dict[str, Any]) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    sr = _sr()
    target_url = _resolve_target_profile_url(payload)
    expected_outgoing = _normalize_text(
        payload.get("expected_outgoing")
        or payload.get("message")
        or payload.get("message_content")
        or ""
    )
    raw_cookies = sr._dm_cookie_text(_pick_cookie_payload(payload))
    account_id = str(payload.get("account_id") or payload.get("account") or "").strip()
    account_key = str(payload.get("account_key") or "").strip()
    if raw_cookies:
        account_key = account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    browser_name = str(payload.get("browser_name") or payload.get("browser") or "chrome").strip() or "chrome"
    headless = True if payload.get("headless") is None else sr._dm_bool_text(payload.get("headless"))
    timeout_ms = int(sr._payload_float(payload, "timeout_ms", 45000))

    if not target_url:
        raise sr.WebInputError("target_profile_url or sec_uid is required")
    if not expected_outgoing:
        raise sr.WebInputError("expected_outgoing is required")
    if not raw_cookies and not account_key:
        raise sr.WebInputError("account_cookie or account_key is required")

    from aisec_agent.web.douyin_conversation_monitor import (
        _dm_account_browser_profile_exists,
        _dm_account_browser_user_data_dir,
    )

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

    lock = _account_lock(account_key or sr._dm_account_key_from_values(raw_cookies, account_id or target_url))
    with lock:
        playwright = None
        context = None
        browser = None
        page = None
        try:
            playwright, context, browser, _keep = sr._dm_get_playwright_context(
                sync_playwright, browser_name, options
            )
            cookies = sr._dm_parse_account_cookies(raw_cookies) if raw_cookies else []
            if cookies:
                context.add_cookies(cookies)
            page = sr._dm_message_page(context, target_url)
            sr._dm_apply_page_geometry(page, options)
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 12000))
            except Exception:
                pass
            sr._dm_append_optional_step([], sr._dm_handle_douyin_login_save_prompt(page))
            steps: List[Dict[str, Any]] = []
            login_abort = sr._dm_abort_on_login_requirement(page, steps, stage="after_open_profile")
            if login_abort:
                detail = ""
                if isinstance(login_abort, dict):
                    detail = str(login_abort.get("detail") or login_abort.get("message") or "")
                return _failure_result("login_required", detail or "账号登录态不可用，需更新 Cookie")

            try:
                sr._dm_click_profile_private_message(page, timeout_ms=min(timeout_ms, 12000))
            except RuntimeError as exc:
                login_abort = sr._dm_abort_on_login_requirement(page, steps, stage="open_private_message")
                if login_abort:
                    detail = ""
                    if isinstance(login_abort, dict):
                        detail = str(login_abort.get("detail") or login_abort.get("message") or "")
                    return _failure_result("login_required", detail or "账号登录态不可用，需更新 Cookie")
                code, msg = _map_exception_failure(exc)
                return _failure_result(code, msg)

            time.sleep(1.0)
            # Wait briefly for composer / bubbles.
            bubbles: List[Dict[str, Any]] = []
            for _ in range(8):
                bubbles = _collect_conversation_bubbles(page)
                if bubbles:
                    break
                time.sleep(0.45)

            if not bubbles:
                # Editor missing often means conversation surface failed.
                detect = {}
                try:
                    from aisec_agent.web.douyin_conversation_monitor import _dm_detect_latest_douyin_message

                    detect = _dm_detect_latest_douyin_message(page)
                except Exception:
                    detect = {}
                if not detect.get("has_editor"):
                    return _failure_result("conversation_not_found", "未进入私信会话或未找到消息面板")

            result = build_reply_detection_result(bubbles, expected_outgoing, ok=True)
            result["task_id"] = str(payload.get("task_id") or "").strip()
            result["target_profile_url"] = target_url
            result["account_key"] = account_key
            result["account_cookie_loaded"] = bool(cookies)
            result["account_cookie_count"] = len(cookies)
            return result
        except sr.WebInputError:
            raise
        except Exception as exc:
            code, msg = _map_exception_failure(exc)
            return _failure_result(code, msg)
        finally:
            # Non-persistent contexts must be closed; persistent sessions are reused.
            if not options.get("persistent_context"):
                sr._dm_stop_playwright_context(context, browser, playwright)


def build_douyin_conversation_read_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """API entry: detect peer reply for one outgoing private message."""
    normalized = dict(payload or {})
    return _run_conversation_read_playwright(normalized)


def build_douyin_task_replies_response(
    task_id: str,
    redis_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Load a stored DM task and run the same conversation read detection."""
    sr = _sr()
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise sr.WebInputError("task_id is required")
    redis_conn = sr._redis_conn(redis_client)
    task = sr._dm_redis_hash_all(redis_conn, sr._dm_task_key(clean_task_id))
    if not task:
        raise sr.WebInputError("task not found")

    payload = {
        "task_id": clean_task_id,
        "account_cookie": task.get("account_cookie") or task.get("account_cookies") or "",
        "account_key": task.get("account_key") or "",
        "account_id": task.get("account_id") or "",
        "browser_name": task.get("browser") or task.get("browser_name") or "chrome",
        "headless": True,
        "target_profile_url": task.get("target_profile_url") or "",
        "expected_outgoing": task.get("message") or task.get("reply") or "",
    }
    if not str(payload["expected_outgoing"] or "").strip():
        raise sr.WebInputError("task has no outgoing message to use as expected_outgoing")
    if not str(payload["target_profile_url"] or "").strip():
        raise sr.WebInputError("task has no target_profile_url")
    if not str(payload["account_cookie"] or "").strip() and not str(payload["account_key"] or "").strip():
        raise sr.WebInputError("task has no account_cookie or account_key")

    result = build_douyin_conversation_read_response(payload)
    result["task_id"] = clean_task_id
    return result
