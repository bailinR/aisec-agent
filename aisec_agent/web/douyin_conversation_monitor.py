# -*- coding: utf-8 -*-
"""Conversation monitors for Douyin managed sending accounts.

Only monitors accounts from private-message 账号管理 (payload supplies
account_id / account_cookies / browser_name). Polls the account chat surface
for inbound messages and optionally generates/sends private_followup replies.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic


DM_CONVERSATION_MONITOR_DIAGNOSTIC_INTERVAL_SECONDS = 45
DEFAULT_GPU_POOL_BASE_URL = os.getenv("AISEC_GPU_POOL_BASE_URL", "http://127.0.0.1:4000/v1").rstrip("/")
DEFAULT_GPU_POOL_API_KEY = os.getenv("AISEC_GPU_POOL_API_KEY", "sk-gpu-pool-local")
DEFAULT_GPU_POOL_MODEL = os.getenv("AISEC_GPU_POOL_PM_MODEL", "chat-pm")
CS_SYSTEM_PROMPT = (
    "你是抖音私信客服助理，语气自然、简短、像真人。\n"
    "规则：\n"
    "1. 只根据用户最新私信和少量上下文回复，不要编造未提供的事实。\n"
    "2. 一次只回 1-3 句，优先 80 字以内。\n"
    "3. 不要说自己是 AI、模型或机器人。\n"
    "4. 需要联系方式或资料时，礼貌引导对方补充情况。\n"
    "5. 只输出要发送的私信正文，不要 Markdown，不要解释。"
)
_DM_ACCOUNT_CONTROLLERS: Dict[str, Any] = {}
_DM_ACCOUNT_CONTROLLERS_LOCK = threading.RLock()
_PEER_TIME_RE = re.compile(
    r"^(?P<name>.{1,32}?)\s+(?:刚刚|\d+\s*秒前|\d+\s*分钟前|\d+\s*小时前|昨天|\d{1,2}:\d{2}|周[一二三四五六日])\b"
)
_GROUP_OR_SYSTEM_RE = re.compile(
    r"群聊|群公告|进群|入群|被设置为管理员|群成员|无法查看历史|v\.douyin\.com/group"
)


def _dm_normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dm_peer_key_from_row(detail: str, preview: str = "") -> str:
    """Stable nickname from a conversation-row, not the preview/message body."""
    raw = _dm_normalize_text(detail)
    preview_text = _dm_normalize_text(preview)
    match = _PEER_TIME_RE.match(raw)
    if match:
        name = _dm_normalize_text(match.group("name"))
        if name and name != preview_text[: len(name)]:
            return name[:32]
    token = raw.split(" ")[0].strip("·-—") if raw else ""
    if not token or token == preview_text or re.fullmatch(r"\d+", token):
        return ""
    if len(token) > 24:
        return ""
    return token


def _dm_is_group_or_system_chat(*parts: Any) -> bool:
    blob = " ".join(_dm_normalize_text(part) for part in parts if part)
    return bool(_GROUP_OR_SYSTEM_RE.search(blob))


def _dm_is_group_unread_row(nickname: str, last_message: str, row_text: str = "") -> bool:
    """Skip clear group/system rows without discarding a private row whose parent text is noisy."""
    nick = _dm_normalize_text(nickname)
    msg = _dm_normalize_text(last_message)
    if nick and _GROUP_OR_SYSTEM_RE.search(nick):
        return True
    if msg and _GROUP_OR_SYSTEM_RE.search(msg):
        return True
    # Only use row_text when nickname is missing — oversized parents often contain「群聊」.
    if not nick and _GROUP_OR_SYSTEM_RE.search(_dm_normalize_text(row_text)):
        return True
    return False


def _dm_is_own_outbound(text: str, last_reply: str) -> bool:
    left = _dm_normalize_text(text)
    right = _dm_normalize_text(last_reply)
    if not left or not right:
        return False
    if left == right:
        return True
    return left in right or right in left


def _dm_inbound_fingerprint(text: str) -> str:
    return _dm_normalize_text(text)[-180:]


def _dm_scroll_message_list_to_top(page: Any) -> None:
    """Scroll the IM conversation list to top so newest unread rows are in DOM view."""
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 80 && rect.height >= 80 &&
          style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
      };
      const nodes = Array.from(document.querySelectorAll('div, section, aside, ul'));
      const panels = nodes.filter((el) => {
        if (!isVisible(el)) return false;
        const rect = el.getBoundingClientRect();
        const text = normalize(el.innerText || '').slice(0, 160);
        const cls = String(el.className || '');
        const rightSide = rect.left > window.innerWidth * 0.38;
        const sizeOk = rect.width >= 200 && rect.height >= 200;
        const header = /(消息|私信)/.test(text) || /im-dialog|imContainer|imSaas|conversation|Message|chat-list/i.test(cls);
        return rightSide && sizeOk && header;
      }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
      const panel = panels[0];
      if (!panel) return {ok: false};
      const scrollables = [panel, ...Array.from(panel.querySelectorAll('div, ul, section'))]
        .filter((el) => el && el.scrollHeight > el.clientHeight + 40)
        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
      let moved = 0;
      for (const el of scrollables.slice(0, 4)) {
        if (el.scrollTop > 0) {
          el.scrollTop = 0;
          moved += 1;
        }
      }
      return {ok: true, moved};
    }
    """
    try:
        page.evaluate(script)
        page.wait_for_timeout(280)
    except Exception:
        pass


def _sr() -> Any:
    """Lazy import to avoid circular import with session_rag_chat handlers."""
    from aisec_agent.web import session_rag_chat as module

    return module


def _gpu_pool_chat_reply(
    *,
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: str = DEFAULT_GPU_POOL_MODEL,
    base_url: str = DEFAULT_GPU_POOL_BASE_URL,
    api_key: str = DEFAULT_GPU_POOL_API_KEY,
    timeout_seconds: float = 180.0,
) -> str:
    """Call the local GPU pool OpenAI-compatible chat API."""
    messages: List[Dict[str, str]] = [{"role": "system", "content": CS_SYSTEM_PROMPT}]
    for item in history or []:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(user_message or "").strip()})
    payload = {
        "model": str(model or DEFAULT_GPU_POOL_MODEL).strip() or DEFAULT_GPU_POOL_MODEL,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 220,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"gpu_pool http {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"gpu_pool request failed: {exc}") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("gpu_pool returned empty choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = str((message or {}).get("content") or "").strip()
    content = re.sub(r"^```(?:\w+)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()
    return content


def _dm_account_browser_user_data_dir(browser_name: str, account_key: str) -> Path:
    root = _sr().DM_PLAYWRIGHT_PROFILE_ROOT
    browser_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(browser_name or "edge").strip().lower() or "edge")
    safe_account_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(account_key or "default").strip() or "default")
    return root / "accounts" / browser_key / safe_account_key


def _dm_account_browser_profile_exists(browser_name: str, account_key: str) -> bool:
    user_data_dir = _dm_account_browser_user_data_dir(browser_name, account_key)
    return user_data_dir.exists() and user_data_dir.is_dir()


def _dm_pick_account_cookie_payload(payload: Dict[str, Any]) -> Any:
    for key in ("account_cookies", "account_cookie", "cookie", "cookies", "cookie_text"):
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return ""


def _dm_reusable_context_page(context: Any):
    try:
        for candidate in list(context.pages or []):
            try:
                if not candidate.is_closed():
                    return candidate
            except Exception:
                continue
    except Exception:
        pass
    return context.new_page()


def _dm_detect_latest_douyin_message(page: Any) -> Dict[str, Any]:
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 18 && rect.height >= 12 && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
      };
      const editorNodes = Array.from(document.querySelectorAll(
        '[contenteditable="true"], [contenteditable="plaintext-only"], [contenteditable], textarea, [role="textbox"], .public-DraftEditor-content, [class*="DraftEditor"]'
      )).filter(isVisible).map((el) => {
        const rect = el.getBoundingClientRect();
        const ph = String(el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || el.getAttribute('aria-label') || '');
        let score = 0;
        if (rect.left > window.innerWidth * 0.48) score += 80;
        if (rect.top > window.innerHeight * 0.4) score += 30;
        if (/搜索|search/i.test(ph)) score -= 150;
        if (/发送消息|输入|私信|说点什么|消息/i.test(ph)) score += 70;
        return {el, rect, ph, score};
      }).filter((item) => item.score >= 50)
        .sort((a, b) => b.score - a.score);
      if (!editorNodes.length) {
        return {
          url: location.href,
          title: document.title || '',
          hasEditor: false,
          unreadCount: 0,
          latestText: '',
          latestCandidate: null,
        };
      }
      const editor = editorNodes[0].el;
      const editorRect = editorNodes[0].rect;
      const inEditor = (el) => editorNodes.some((node) => node.el === el || node.el.contains(el) || el.contains(node.el));
      const panelRoot = editor.closest('[class*="im"], [class*="Im"], [class*="dialog"], aside, section') || document.body;
      const panelRect = panelRoot.getBoundingClientRect ? panelRoot.getBoundingClientRect() : editorRect;
      const midX = (panelRect.left + panelRect.right) / 2;
      const candidates = Array.from(panelRoot.querySelectorAll('div, span, p, li'))
        .filter((el) => isVisible(el) && !inEditor(el))
        .map((el) => {
          const text = normalize(el.innerText || el.textContent || '');
          const rect = el.getBoundingClientRect();
          const cls = String(el.className || '');
          let score = 0;
          if (!text || text.length < 1 || text.length > 280) return null;
          if (rect.bottom >= editorRect.top - 4) return null;
          if (rect.top < panelRect.top + 40) return null;
          if (rect.left < panelRect.left - 8 || rect.right > panelRect.right + 8) return null;
          if (text.length <= 80) score += 24;
          if (text.length <= 40) score += 10;
          if (rect.bottom < editorRect.top - 8) score += 20;
          if (rect.left + rect.width / 2 <= midX) score += 35; // inbound usually left
          if (rect.left + rect.width / 2 > midX) score -= 15; // outbound usually right
          if (/message|chat|bubble|msg|content|text/i.test(cls)) score += 18;
          if (el.childElementCount <= 4) score += 8;
          if (/发送|表情|按住说话|说点什么|输入消息|搜索|下载客户端|实时接收好友消息/.test(text)) score -= 50;
          if (/抖音精选|记录美好生活|推荐|关注|商城|发布作品/.test(text)) score -= 50;
          if (/^\\d{1,2}:\\d{2}$/.test(text) || /^(昨天|周一|周二|周三|周四|周五|周六|周日|刚刚|\\d+分钟前)$/.test(text)) score -= 40;
          if (rect.width > panelRect.width * 0.95) score -= 20;
          return {
            text,
            score,
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          };
        })
        .filter((item) => item && item.score >= 30)
        .sort((a, b) => b.score - a.score || b.y - a.y);
      return {
        url: location.href,
        title: document.title || '',
        hasEditor: true,
        unreadCount: 0,
        latestText: candidates[0] ? candidates[0].text : '',
        latestCandidate: candidates[0] || null,
        editorScore: editorNodes[0].score,
        editorPlaceholder: editorNodes[0].ph || '',
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        data = {"error": str(exc)}
    if not isinstance(data, dict):
        data = {}
    latest_text = str(data.get("latestText") or "").strip()
    return {
        "url": str(data.get("url") or ""),
        "title": str(data.get("title") or ""),
        "has_editor": bool(data.get("hasEditor")),
        "unread_count": int(data.get("unreadCount") or 0),
        "latest_text": latest_text,
        "latest_candidate": data.get("latestCandidate") if isinstance(data.get("latestCandidate"), dict) else {},
        "editor_score": data.get("editorScore"),
        "editor_placeholder": str(data.get("editorPlaceholder") or ""),
        "error": str(data.get("error") or ""),
    }


def _dm_detect_douyin_login_required(page: Any) -> Dict[str, Any]:
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const visible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 24 && rect.height >= 14 && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
      };
      const title = String(document.title || '');
      const bodyText = String(document.body && document.body.innerText || '').slice(0, 4000);
      const texts = Array.from(document.querySelectorAll('div, section, form, button, span, p, a'))
        .filter(visible)
        .map((el) => normalize(el.innerText || el.textContent || ''))
        .filter(Boolean);
      const joined = texts.slice(0, 200).join(' ') || bodyText;
      const verificationPage = /验证码中间页|验证中间页|安全验证|请完成下列验证|滑块验证|captcha/i.test(title + joined);
      const modal = /扫码登录|验证码登录|密码登录|登录后免费|请输入手机号|获取验证码/.test(joined);
      const loginButton = texts.some((text) => text === '登录' || text === '立即登录');
      // Top-right primary 登录 button (logged-out chrome) — do not confuse with message drawer.
      const topRightLogin = Array.from(document.querySelectorAll('button, a, [role=button], div, span'))
        .filter(visible)
        .some((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const topBar = rect.top >= 0 && rect.top < 120;
          const rightSide = rect.left > window.innerWidth * 0.7;
          return topBar && rightSide && (text === '登录' || text === '立即登录');
        });
      const loggedOutHint = /未登录/.test(joined);
      const alreadyLoggedIn = Array.from(document.querySelectorAll('[class*="im-"], [class*="avatar"], img, button, a'))
        .some((el) => {
          if (!visible(el)) return false;
          const cls = String(el.className || '');
          const aria = String(el.getAttribute('aria-label') || '');
          return /im-entry|im-dialog|avatar|用户头像/.test(cls + ' ' + aria);
        }) && !topRightLogin && !verificationPage;
      const required = Boolean(
        (!verificationPage) && (
          modal ||
          topRightLogin ||
          (loginButton && loggedOutHint) ||
          (loginButton && /扫码|验证码|手机号/.test(joined) && !alreadyLoggedIn)
        )
      );
      return {
        required,
        requiresVerification: verificationPage,
        reason: verificationPage
          ? 'verification intermediate page'
          : (required
            ? (modal ? 'login modal visible' : (topRightLogin ? 'top-right login button visible' : 'login button visible'))
            : ''),
        sampleText: joined.slice(0, 240),
        url: location.href,
        title,
        topRightLogin,
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        data = {"required": False, "reason": str(exc)}
    if not isinstance(data, dict):
        data = {}
    # Title-only fallback when body evaluate is empty (common on captcha intermediate page).
    title = str(data.get("title") or getattr(page, "title", lambda: "")() or "")
    if not data.get("requiresVerification") and re.search(r"验证码中间页|验证中间页|安全验证|captcha", title, re.I):
        data["requiresVerification"] = True
        data["reason"] = "verification intermediate page"
    return {
        "required": bool(data.get("required")),
        "requires_verification": bool(data.get("requiresVerification")),
        "reason": str(data.get("reason") or ""),
        "sample_text": str(data.get("sampleText") or ""),
        "url": str(data.get("url") or ""),
        "title": title,
    }


def _dm_message_panel_visible(page: Any) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => Array.from(document.querySelectorAll('div, section, aside'))
                  .some((el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const text = String(el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const cls = String(el.className || '');
                    const looksIm = /im-dialog|imContainer|imSaas|im-saas|semi-always-dark|imDark|conversation|MessageList|chat-list/i.test(cls) ||
                      /(消息|私信)/.test(text.slice(0, 80));
                    const rightish = rect.left > window.innerWidth * 0.28;
                    return looksIm &&
                      rightish &&
                      rect.width >= 200 &&
                      rect.height >= 200 &&
                      style.visibility !== 'hidden' &&
                      style.display !== 'none' &&
                      style.opacity !== '0';
                  })
                """
            )
        )
    except Exception:
        return False


def _dm_click_top_right_message_entry(page: Any) -> Dict[str, Any]:
    """Click Douyin 消息 entry (top-right / nav) via coordinates; JS click is flaky."""
    try:
        target = page.evaluate(
            """
            () => {
              const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width >= 10 && rect.height >= 10 &&
                  rect.right > 0 && rect.bottom > 0 &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  Number(style.opacity || '1') > 0.05;
              };
              const nodes = Array.from(document.querySelectorAll(
                'a, button, [role=button], div, span, svg, path, [class*=im-entry], [class*=ImEntry], [class*=uoylDJ5V], [class*=message], [class*=Message]'
              ))
                .filter(visible)
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  const target = el.closest('a, button, [role=button], [class*=im-entry], [class*=ImEntry]') || el;
                  const text = normalize([
                    el.innerText,
                    el.textContent,
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    el.getAttribute('alt'),
                    target.getAttribute('aria-label'),
                    target.getAttribute('title'),
                  ].filter(Boolean).join(' '));
                  const cls = String(el.className || '') + ' ' + String(target.className || '') +
                    ' ' + String(el.getAttribute('data-e2e') || '');
                  let score = 0;
                  const topBar = rect.top >= 0 && rect.top < 280;
                  const rightSide = rect.left > window.innerWidth * 0.42;
                  const leftNav = rect.left < 220 && rect.width <= 120;
                  if (/im-entry|ImEntry|uoylDJ5V|data-e2e=.?message/i.test(cls)) score += 160;
                  if (text === '消息' || text === '私信') score += 120;
                  if (/^消息\\d*$|^私信\\d*$|^消息\\s*\\d+|^私信\\s*\\d+/.test(text)) score += 110;
                  if (/消息|私信|Message|Messages|Inbox/i.test(text) && text.length <= 20) score += 80;
                  if (topBar && rightSide) score += 50;
                  if (leftNav && /消息|私信|Message/i.test(text + cls)) score += 70;
                  if (rect.left > window.innerWidth * 0.72) score += 25;
                  if (/发布|登录|搜索|上传|充值|下载|通知|壁纸|投稿|朋友|推荐|首页|关注|放映厅|直播|精选|同城/.test(text)) score -= 100;
                  if (!topBar && !leftNav) score -= 40;
                  return {
                    text,
                    score,
                    x: rect.left + Math.min(Math.max(rect.width / 2, 6), 18),
                    y: rect.top + Math.min(Math.max(rect.height / 2, 6), 18),
                  };
                })
                .filter((item) => item.score >= 50)
                .sort((a, b) => b.score - a.score || b.x - a.x);
              return nodes[0] || null;
            }
            """
        )
    except Exception as exc:
        return {"clicked": False, "detail": str(exc)}
    if not isinstance(target, dict) or target.get("x") is None:
        # Playwright text locator fallback (hash class names change often).
        try:
            loc = page.get_by_text("消息", exact=True).first
            if loc.count() > 0:
                box = loc.bounding_box()
                if box and box.get("y", 999) < 280 and box.get("x", 0) > 200:
                    page.mouse.click(float(box["x"]) + min(float(box["width"]) / 2, 12), float(box["y"]) + min(float(box["height"]) / 2, 18))
                    return {"clicked": True, "detail": "消息(locator)", "x": box.get("x"), "y": box.get("y")}
        except Exception:
            pass
        return {"clicked": False, "detail": "top-right message control not found"}
    try:
        page.mouse.click(float(target["x"]), float(target["y"]))
        return {
            "clicked": True,
            "detail": str(target.get("text") or "im-entry")[:80],
            "x": target.get("x"),
            "y": target.get("y"),
        }
    except Exception as exc:
        return {"clicked": False, "detail": str(exc)}


def _dm_try_open_messages_via_url(page: Any, timeout_ms: int = 12000) -> Dict[str, Any]:
    """Fallback: open known Douyin message entry URLs when DOM click fails."""
    candidates = [
        "https://www.douyin.com/?modal_id=0&enter_from_merge=message",
        "https://www.douyin.com/user/self?from_tab_name=main&showTab=message",
        "https://www.douyin.com/message",
    ]
    last_detail = "url open failed"
    for url in candidates:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 20000))
            try:
                page.wait_for_timeout(1200)
            except Exception:
                time.sleep(1.0)
            if _dm_message_panel_visible(page) or _dm_detect_latest_douyin_message(page).get("has_editor"):
                return {"ok": True, "detail": f"opened via {url}"}
            # Some routes land on home with drawer still closed; try one click.
            clicked = _dm_click_top_right_message_entry(page)
            if clicked.get("clicked"):
                try:
                    page.wait_for_timeout(1200)
                except Exception:
                    time.sleep(1.0)
                if _dm_message_panel_visible(page) or _dm_detect_latest_douyin_message(page).get("has_editor"):
                    return {"ok": True, "detail": f"opened via {url} + click"}
            last_detail = f"no panel after {url}"
        except Exception as exc:
            last_detail = str(exc)
    return {"ok": False, "detail": last_detail}


def _dm_open_douyin_messages_surface(page: Any, timeout_ms: int = 12000) -> List[Dict[str, Any]]:
    sr = _sr()
    steps: List[Dict[str, Any]] = []
    try:
        current_url = str(getattr(page, "url", "") or "")
        if "douyin.com" not in current_url:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=timeout_ms)
            steps.append({"name": "open_douyin_home", "ok": True, "detail": "opened home"})
        try:
            page.wait_for_timeout(min(1800, timeout_ms))
        except Exception:
            pass
        # Headless / slow networks: wait for top chrome a bit longer.
        try:
            page.wait_for_selector("body", timeout=min(3000, timeout_ms))
        except Exception:
            pass
        sr._dm_append_optional_step(steps, sr._dm_handle_douyin_login_save_prompt(page, timeout_ms=1500))
        login_state = _dm_detect_douyin_login_required(page)
        if login_state.get("requires_verification"):
            steps.append({
                "name": "check_login",
                "ok": False,
                "detail": "requires_verification: " + str(login_state.get("reason") or "verification required"),
            })
            return steps
        if login_state.get("required"):
            steps.append({
                "name": "check_login",
                "ok": False,
                "detail": "login_required: " + str(login_state.get("reason") or "login required"),
            })
            return steps
        # 404 / soft pages never expose IM chrome.
        try:
            title = str(page.title() or "")
            body_snip = str(page.evaluate("() => String(document.body && document.body.innerText || '').slice(0, 200)") or "")
            if re.search(r"页面不见|404", title + body_snip):
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1500)
                login_state = _dm_detect_douyin_login_required(page)
                if login_state.get("requires_verification"):
                    steps.append({
                        "name": "check_login",
                        "ok": False,
                        "detail": "requires_verification: " + str(login_state.get("reason") or "verification required"),
                    })
                    return steps
                if login_state.get("required"):
                    steps.append({
                        "name": "check_login",
                        "ok": False,
                        "detail": "login_required: " + str(login_state.get("reason") or "login required"),
                    })
                    return steps
        except Exception:
            pass
        if _dm_message_panel_visible(page):
            steps.append({"name": "detect_message_panel", "ok": True, "detail": "message panel already visible"})
            return steps
        if _dm_detect_latest_douyin_message(page).get("has_editor"):
            steps.append({"name": "detect_message_surface", "ok": True, "detail": "editor already visible"})
            return steps
        last_detail = "message entry not found"
        for attempt in range(4):
            clicked = _dm_click_top_right_message_entry(page)
            if not clicked.get("clicked"):
                last_detail = str(clicked.get("detail") or last_detail)
                # Scroll top / nudge viewport once if entry missing.
                if attempt == 1:
                    try:
                        page.evaluate("() => window.scrollTo(0, 0)")
                        vp = page.viewport_size or {}
                        width = int(vp.get("width") or 1280)
                        page.mouse.move(max(40, int(width * 0.9)), 40)
                    except Exception:
                        pass
                continue
            steps.append({
                "name": "open_message_center",
                "ok": True,
                "detail": f"clicked DOM control (try {attempt + 1}): " + str(clicked.get("detail") or ""),
            })
            try:
                page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.2)
            if _dm_message_panel_visible(page) or _dm_detect_latest_douyin_message(page).get("has_editor"):
                steps.append({"name": "confirm_message_panel", "ok": True, "detail": "message panel visible after click"})
                return steps
            last_detail = "clicked but panel not visible"
        url_open = _dm_try_open_messages_via_url(page, timeout_ms=timeout_ms)
        if url_open.get("ok"):
            steps.append({"name": "open_message_center_url", "ok": True, "detail": str(url_open.get("detail") or "")})
            return steps
        # Final auth/risk re-check: captcha pages often lack IM controls entirely.
        login_state = _dm_detect_douyin_login_required(page)
        if login_state.get("requires_verification"):
            steps.append({
                "name": "check_login",
                "ok": False,
                "detail": "requires_verification: " + str(login_state.get("reason") or "verification required"),
            })
            return steps
        if login_state.get("required"):
            steps.append({
                "name": "check_login",
                "ok": False,
                "detail": "login_required: " + str(login_state.get("reason") or "login required"),
            })
            return steps
        steps.append({
            "name": "open_message_center",
            "ok": False,
            "detail": last_detail + " | url_fallback=" + str(url_open.get("detail") or ""),
        })
        return steps
    except Exception as exc:
        return [{"name": "open_message_center", "ok": False, "detail": str(exc)}]


def _dm_scan_inbox_summary(page: Any) -> Dict[str, Any]:
    """Summarize Douyin private-message unread without clicking rows.

    Detects both numeric badges and red-dot markers inside the IM panel, and
    also the top-right 「消息」entry badge as a fallback.
    """
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el, minW = 4, minH = 4) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= minW && rect.height >= minH && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
      };
      const isRedish = (style) => {
        const colors = [style.backgroundColor, style.color, style.borderColor].join(' ');
        const m = colors.match(/rgba?\\((\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/i);
        if (!m) return /#f{0,1}[ef].{0,1}[0-4]|#e[0-9a-f]{2}[0-4]|red|tomato|crimson/i.test(colors);
        const r = Number(m[1]), g = Number(m[2]), b = Number(m[3]);
        return r >= 180 && g <= 120 && b <= 120 && (r - g) >= 50;
      };
      const parseBadgeCount = (text) => {
        const t = normalize(text);
        if (!t) return 1; // red-dot without number => at least 1
        if (/^99\\+?$/.test(t)) return 99;
        if (/^[1-9]\\d?$/.test(t)) return Number(t);
        const m = t.match(/(?:未读)?([1-9]\\d?)\\+?/);
        return m ? Number(m[1]) : 0;
      };

      const allNodes = Array.from(document.querySelectorAll(
        'div, section, aside, ul, li, a, button, span, i, em, b, strong, [class*=badge], [class*=Badge], [class*=unread], [class*=Unread], [class*=dot], [class*=Dot], [class*=red]'
      ));
      const panelCandidates = allNodes
        .filter((el) => isVisible(el, 36, 24))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const style = window.getComputedStyle(el);
          const rightSide = rect.left > window.innerWidth * 0.35 && rect.right > window.innerWidth * 0.55;
          const panelSize = rect.width >= 200 && rect.width <= window.innerWidth * 0.78 &&
            rect.height >= 200 && rect.height <= window.innerHeight * 0.99;
          const cls = String(el.className || '');
          const hasImClass = /im-dialog|imContainer|imSaas|im-saas|imDark|semi-always-dark|conversation|Message/i.test(cls);
          const hasPrivateHeader = /(消息|私信)/.test(text.slice(0, 120)) || hasImClass;
          if (!rightSide || !panelSize || !hasPrivateHeader || style.visibility === 'hidden' ||
              style.display === 'none' || style.opacity === '0') return null;
          let score = 100 + rect.width - rect.height / 1000;
          if (hasImClass) score += 80;
          if (/^(消息|私信)/.test(text.slice(0, 12))) score += 40;
          if (/(学习\\d|剪辑|vlog|点赞|推荐|关注|朋友|直播|#\\S)/i.test(text.slice(0, 100))) score -= 120;
          if (score < 40) return null;
          return {el, rect, text, score};
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score || a.rect.left - b.rect.left);
      const panel = panelCandidates[0] || null;

      const collectBadges = (predicate) => {
        const badges = [];
        for (const el of allNodes) {
          if (!isVisible(el, 4, 4) || !predicate(el)) continue;
          const rect = el.getBoundingClientRect();
          const style = window.getComputedStyle(el);
          const text = normalize(el.innerText || el.textContent || '');
          const cls = String(el.className || '') + ' ' + String(el.getAttribute('aria-label') || '');
          const compact = rect.width <= 48 && rect.height <= 48 && rect.width >= 5 && rect.height >= 5;
          if (!compact) continue;
          const numeric = /^[1-9]\\d?$|^99\\+?$/.test(text);
          const named = /badge|unread|red-?dot|未读/i.test(cls + ' ' + text);
          const redDot = (!text || text.length <= 3) && isRedish(style) && rect.width <= 28 && rect.height <= 28;
          if (!(numeric || named || redDot)) continue;
          const count = numeric || /\\d/.test(text) ? parseBadgeCount(text) : 1;
          if (!count) continue;
          badges.push({
            rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height},
            text: text || '(dot)',
            count,
            kind: numeric ? 'num' : (redDot ? 'dot' : 'named'),
          });
        }
        const unique = [];
        for (const badge of badges) {
          const cx = badge.rect.left + badge.rect.width / 2;
          const cy = badge.rect.top + badge.rect.height / 2;
          const dup = unique.some((item) => {
            const ix = item.rect.left + item.rect.width / 2;
            const iy = item.rect.top + item.rect.height / 2;
            return Math.abs(ix - cx) < 14 && Math.abs(iy - cy) < 14;
          });
          if (!dup) unique.push(badge);
        }
        return unique;
      };

      let unique = [];
      let source = 'none';
      if (panel) {
        const panelRect = panel.rect;
        unique = collectBadges((el) => {
          const rect = el.getBoundingClientRect();
          return rect.left >= panelRect.left - 8 && rect.right <= panelRect.right + 8 &&
            rect.top >= panelRect.top + 24 && rect.bottom <= panelRect.bottom + 8;
        });
        source = 'panel';
      }

      // Fallback: top-right 「消息」entry badge (global unread). Keep only compact
      // low counts — feed timestamps like 「41」must not inflate unread_people.
      if (!unique.length) {
        unique = collectBadges((el) => {
          const rect = el.getBoundingClientRect();
          return rect.top >= 0 && rect.top < 220 && rect.left > window.innerWidth * 0.55;
        }).filter((b) => {
          const count = Number(b.count) || 0;
          const w = Number(b.rect.width) || 0;
          const h = Number(b.rect.height) || 0;
          if (b.kind === 'dot') return true;
          return count > 0 && count <= 9 && w <= 28 && h <= 28;
        });
        if (unique.length) source = 'top_entry';
      }

      const unreadCount = unique.reduce((sum, item) => sum + (Number(item.count) || 0), 0);
      // top_entry is a single bubble — people is at most 1 unless panel scan succeeded.
      const unreadPeople = source === 'top_entry' ? Math.min(1, unique.length) : unique.length;
      return {
        ok: true,
        detail: panel
          ? (`scanned ${source}; panel=` + normalize(panel.text).slice(0, 40))
          : (`scanned ${source}; panel missing`),
        unreadCount: source === 'top_entry' ? Math.min(unreadCount, 9) : unreadCount,
        unreadPeople,
        badgeSample: unique.slice(0, 8).map((b) => ({text: b.text, count: b.count, kind: b.kind})),
        panelFound: Boolean(panel),
        source,
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        return {
            "ok": False,
            "detail": str(exc),
            "unread_count": 0,
            "unread_people": 0,
        }
    if not isinstance(data, dict):
        data = {}
    return {
        "ok": bool(data.get("ok")),
        "detail": str(data.get("detail") or ""),
        "unread_count": int(data.get("unreadCount") or 0),
        "unread_people": int(data.get("unreadPeople") or 0),
        "badge_sample": data.get("badgeSample") or [],
        "panel_found": bool(data.get("panelFound")),
    }


def _dm_repair_inbox_peer_fields(
    nickname: str,
    last_message: str,
    unread_count: int = 1,
    row_text: str = "",
) -> Dict[str, str]:
    """Split mashed Douyin list labels like '白林 2222 ·' with badge '2' as reply."""
    nick = _dm_normalize_text(nickname)
    msg = _dm_normalize_text(last_message)
    raw = _dm_normalize_text(row_text) or nick or msg
    label = nick or msg or raw
    matched = re.match(r"^(?P<name>.+?)\s+(?P<preview>\S+)\s*·\s*$", label)
    badge_like = bool(re.fullmatch(r"[1-9]\d?|99\+?", msg or "")) and (
        not msg or msg == str(int(unread_count or 1)) or len(msg) <= 3
    )
    if matched:
        split_name = _dm_normalize_text(matched.group("name"))
        split_preview = _dm_normalize_text(matched.group("preview"))
        if split_preview and (
            not nick
            or not msg
            or badge_like
            or msg == split_preview
            or msg == label
            or nick == label
        ):
            return {"peer_nickname": split_name or nick, "last_message": split_preview}
    if badge_like and nick and " " in nick and "·" not in nick:
        parts = nick.split()
        if len(parts) >= 2 and not re.fullmatch(r"[1-9]\d?", parts[0] or ""):
            return {
                "peer_nickname": parts[0],
                "last_message": " ".join(parts[1:]).strip(" ·"),
            }
    if badge_like:
        msg = ""
    if (not nick or not msg) and raw:
        peer = _dm_peer_key_from_row(raw, msg)
        if peer and not nick:
            nick = peer
        # "昵称 时间 预览" / "昵称 预览"
        time_match = _PEER_TIME_RE.match(raw)
        if time_match:
            if not nick:
                nick = _dm_normalize_text(time_match.group("name"))
            rest = _dm_normalize_text(raw[time_match.end() :])
            rest = re.sub(r"\s+[1-9]\d?\s*$", "", rest).strip(" ·")
            if rest and (not msg or badge_like):
                msg = rest
        elif not msg:
            parts = [p for p in raw.split(" ") if p and not re.fullmatch(r"[1-9]\d?", p)]
            if len(parts) >= 2:
                if not nick:
                    nick = parts[0][:32]
                msg = " ".join(parts[1:]).strip(" ·")
            elif len(parts) == 1 and not nick:
                nick = parts[0][:32]
    if not nick and msg and " " in msg:
        parts = msg.split()
        if len(parts) >= 2 and not re.fullmatch(r"[1-9]\d?", parts[0] or ""):
            nick = parts[0][:32]
            msg = " ".join(parts[1:]).strip(" ·")
    return {"peer_nickname": nick, "last_message": msg}


def _dm_normalize_unread_conversation_items(raw_items: Any) -> List[Dict[str, Any]]:
    """Normalize scanner rows into reply-pool fields (nickname + preview + count)."""
    conversations: List[Dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        nickname = _dm_normalize_text(item.get("peer_nickname"))
        last_message = _dm_normalize_text(item.get("last_message"))
        row_text = _dm_normalize_text(item.get("row_text") or item.get("raw") or "")
        peer_sec_uid = _dm_normalize_text(item.get("peer_sec_uid"))
        peer_uid = _dm_normalize_text(item.get("peer_uid"))
        peer_profile_url = _dm_normalize_text(item.get("peer_profile_url"))
        try:
            unread_count = max(1, int(item.get("unread_count") or 1))
        except Exception:
            unread_count = 1
        repaired = _dm_repair_inbox_peer_fields(nickname, last_message, unread_count, row_text=row_text)
        nickname = repaired["peer_nickname"]
        last_message = repaired["last_message"]
        if not nickname and row_text:
            nickname = _dm_peer_key_from_row(row_text, last_message)
        if not last_message and row_text:
            # Strip nickname/time/badge leftovers as a last-resort preview.
            work = row_text
            if nickname and work.startswith(nickname):
                work = work[len(nickname) :].strip()
            work = re.sub(
                r"^(刚刚|\d+\s*秒前|\d+\s*分钟前|\d+\s*小时前|昨天|\d{1,2}:\d{2}|周[一二三四五六日])\s*",
                "",
                work,
            ).strip()
            work = re.sub(r"\s+[1-9]\d?\s*$", "", work).strip(" ·")
            if work and work != nickname and not re.fullmatch(r"[1-9]\d?|99\+?", work):
                last_message = work[:200]
        if not nickname and not last_message and not peer_sec_uid and not peer_uid:
            continue
        if _dm_is_group_unread_row(nickname, last_message, row_text):
            continue
        # Reply pool needs both labels; fill placeholders only when still blank.
        if not nickname:
            nickname = peer_sec_uid[:16] if peer_sec_uid else (peer_uid or "未读用户")
        if not last_message:
            last_message = "(未读)"
        # Keep long digit previews like 「8888888」— only strip 1~2 digit badge leftovers.
        if re.fullmatch(r"[1-9]\d?|99\+?", last_message):
            last_message = "(未读)"
        if not peer_profile_url and peer_sec_uid:
            peer_profile_url = f"https://www.douyin.com/user/{peer_sec_uid}"
        conversations.append(
            {
                "peer_nickname": nickname[:64],
                "peer_uid": peer_uid,
                "peer_sec_uid": peer_sec_uid,
                "peer_profile_url": peer_profile_url,
                "last_message": last_message[:200],
                "unread_count": unread_count,
                "conversation_id": _dm_normalize_text(item.get("conversation_id"))
                or peer_sec_uid
                or peer_uid
                or nickname,
                "updated_at": _dm_normalize_text(item.get("updated_at")),
            }
        )
    return conversations


def _dm_scan_unread_conversations(page: Any) -> Dict[str, Any]:
    """List unread DM conversations from the inbox panel without clicking rows.

    Full-refreshes against the current visible list (scroll to top first). Does not
    open chats. Each unread row should yield peer_nickname + last_message + unread_count.
    """
    _dm_scroll_message_list_to_top(page)
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el, minW = 4, minH = 4) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= minW && rect.height >= minH && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
      };
      const isRedish = (style) => {
        const colors = [style.backgroundColor, style.color, style.borderColor].join(' ');
        const m = colors.match(/rgba?\\((\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/i);
        if (!m) return /#f{0,1}[ef].{0,1}[0-4]|#e[0-9a-f]{2}[0-4]|red|tomato|crimson/i.test(colors);
        const r = Number(m[1]), g = Number(m[2]), b = Number(m[3]);
        return r >= 180 && g <= 120 && b <= 120 && (r - g) >= 50;
      };
      const parseBadgeCount = (text) => {
        const t = normalize(text);
        if (!t) return 1;
        if (/^99\\+?$/.test(t)) return 99;
        if (/^[1-9]\\d?$/.test(t)) return Number(t);
        const m = t.match(/(?:未读)?([1-9]\\d?)\\+?/);
        return m ? Number(m[1]) : 0;
      };
      const timeToken = /^(刚刚|\\d+\\s*秒前|\\d+\\s*分钟前|\\d+\\s*小时前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])$/;
      const allNodes = Array.from(document.querySelectorAll(
        'div, section, aside, ul, li, a, button, span, i, em, b, strong, p, [class*=badge], [class*=Badge], [class*=unread], [class*=Unread], [class*=dot], [class*=Dot], [class*=red], [class*=conversation], [class*=Conversation]'
      ));
      const panelCandidates = allNodes
        .filter((el) => isVisible(el, 36, 24))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const style = window.getComputedStyle(el);
          const rightSide = rect.left > window.innerWidth * 0.35 && rect.right > window.innerWidth * 0.55;
          const panelSize = rect.width >= 200 && rect.width <= window.innerWidth * 0.78 &&
            rect.height >= 200 && rect.height <= window.innerHeight * 0.99;
          const cls = String(el.className || '');
          const hasImClass = /im-dialog|imContainer|imSaas|im-saas|imDark|semi-always-dark|conversation|Message|chat-list|ChatList/i.test(cls);
          const hasPrivateHeader = /(消息|私信)/.test(text.slice(0, 160)) || hasImClass;
          if (!rightSide || !panelSize || !hasPrivateHeader || style.visibility === 'hidden' ||
              style.display === 'none' || style.opacity === '0') return null;
          let score = 100 + rect.width - rect.height / 1000;
          if (hasImClass) score += 80;
          if (/^(消息|私信)/.test(text.slice(0, 12))) score += 40;
          // Reject feed / recommendation chrome mistaken as DM drawer.
          if (/(学习\\d|剪辑|vlog|点赞|推荐|关注|朋友|直播|#\\S)/i.test(text.slice(0, 100))) score -= 120;
          if (score < 40) return null;
          return {el, rect, text, score};
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score || a.rect.left - b.rect.left);
      const panel = panelCandidates[0] || null;
      if (!panel) {
        return {ok: false, detail: 'private message panel not found', conversations: []};
      }
      const panelRect = panel.rect;
      const withinPanel = (el) => {
        const rect = el.getBoundingClientRect();
        return rect.left >= panelRect.left - 12 && rect.right <= panelRect.right + 12 &&
          rect.top >= panelRect.top + 12 && rect.bottom <= panelRect.bottom + 12;
      };
      const isTightRow = (rect, text) => (
        rect.width >= panelRect.width * 0.35 &&
        rect.width <= panelRect.width * 1.15 &&
        rect.height >= 28 &&
        rect.height <= 160 &&
        text.length >= 2 &&
        text.length <= 320 &&
        !/^(消息|私信|全部|未读|陌生人|联系人|抖音|通知)$/.test(text)
      );
      const rowScore = (row) => {
        let score = 0;
        const text = row.text || '';
        if (/(刚刚|\\d+\\s*秒前|\\d+\\s*分钟前|\\d+\\s*小时前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])/.test(text)) score += 50;
        if (/·/.test(text)) score += 8;
        score += Math.max(0, 50 - Math.abs((row.rect.height || 0) - 72));
        if ((row.rect.height || 0) > 120) score -= 25;
        if (text.length > 160) score -= 12;
        if (text.length < 6) score -= 8;
        return score;
      };
      const isBadgeEl = (el) => {
        if (!isVisible(el, 3, 3) || !withinPanel(el)) return null;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const text = normalize(el.innerText || el.textContent || '');
        const aria = String(el.getAttribute('aria-label') || '');
        const cls = String(el.className || '') + ' ' + aria;
        const compact = rect.width <= 56 && rect.height <= 56 && rect.width >= 3 && rect.height >= 3;
        if (!compact) return null;
        const numeric = /^[1-9]\\d?$|^99\\+?$/.test(text);
        const named = /badge|unread|red-?dot|未读/i.test(cls + ' ' + text + ' ' + aria);
        const redDot = (!text || text.length <= 3) && isRedish(style) && rect.width <= 32 && rect.height <= 32;
        if (!(numeric || named || redDot)) return null;
        const count = numeric || /\\d/.test(text)
          ? parseBadgeCount(text)
          : (named && /\\d/.test(aria) ? parseBadgeCount(aria) : 1);
        if (!count) return null;
        return {el, rect, text: text || '(dot)', count};
      };
      const badges = [];
      for (const el of allNodes) {
        const badge = isBadgeEl(el);
        if (badge) badges.push(badge);
      }
      const uniqueBadges = [];
      for (const badge of badges) {
        const cx = badge.rect.left + badge.rect.width / 2;
        const cy = badge.rect.top + badge.rect.height / 2;
        const dup = uniqueBadges.some((item) => {
          const ix = item.rect.left + item.rect.width / 2;
          const iy = item.rect.top + item.rect.height / 2;
          return Math.abs(ix - cx) < 14 && Math.abs(iy - cy) < 14;
        });
        if (!dup) uniqueBadges.push(badge);
      }
      // Prefer mid-size conversation rows clustered by Y — leaf-only filter often
      // drops the real row (nickname+preview) when nested avatar/text nodes score as leaves.
      const candidateRows = [];
      for (const el of allNodes) {
        if (!isVisible(el, 28, 24) || !withinPanel(el)) continue;
        const rect = el.getBoundingClientRect();
        const text = normalize(el.innerText || el.textContent || '');
        if (!isTightRow(rect, text)) continue;
        candidateRows.push({node: el, rect, text, area: rect.width * rect.height});
      }
      candidateRows.sort((a, b) => a.rect.top - b.rect.top || a.area - b.area);
      const leafRows = [];
      for (const row of candidateRows) {
        const hit = leafRows.find((item) => Math.abs(item.rect.top - row.rect.top) < 24);
        if (!hit) {
          leafRows.push(row);
          continue;
        }
        if (rowScore(row) > rowScore(hit)) {
          hit.node = row.node;
          hit.rect = row.rect;
          hit.text = row.text;
          hit.area = row.area;
        }
      }
      leafRows.sort((a, b) => a.rect.top - b.rect.top);

      const badgeInside = (rowEl, rowRect) => {
        let best = null;
        for (const badge of uniqueBadges) {
          const by = badge.rect.top + badge.rect.height / 2;
          const bx = badge.rect.left + badge.rect.width / 2;
          if (by < rowRect.top - 8 || by > rowRect.bottom + 8) continue;
          if (bx < rowRect.left - 12 || bx > rowRect.right + 12) continue;
          if (!best || badge.count > best.count) best = badge;
        }
        if (best) return best;
        const kids = Array.from(rowEl.querySelectorAll(
          'div, span, i, em, b, strong, [class*=badge], [class*=Badge], [class*=unread], [class*=dot], [class*=red]'
        ));
        for (const el of kids) {
          const badge = isBadgeEl(el);
          if (!badge) continue;
          if (!best || badge.count > best.count) best = badge;
        }
        return best;
      };
      const rowForBadge = (badge) => {
        let best = null;
        const by = badge.rect.top + badge.rect.height / 2;
        for (const row of leafRows) {
          if (by < row.rect.top - 10 || by > row.rect.bottom + 10) continue;
          const dy = Math.abs(by - (row.rect.top + row.rect.height / 2));
          const score = rowScore(row) + 1000 - dy * 10;
          if (!best || score > best.score) best = Object.assign({}, row, {score});
        }
        if (best) return best;
        let node = badge.el;
        let ancestor = null;
        for (let depth = 0; node && depth < 14; depth += 1, node = node.parentElement) {
          if (!withinPanel(node)) continue;
          const rect = node.getBoundingClientRect();
          const text = normalize(node.innerText || node.textContent || '');
          const looseOk = (
            rect.width >= panelRect.width * 0.30 &&
            rect.height >= 24 &&
            rect.height <= 200 &&
            text.length >= 2 &&
            text.length <= 400
          );
          if (!looseOk) continue;
          const area = rect.width * rect.height;
          const cand = {node, rect, text, area};
          if (!ancestor || rowScore(cand) > rowScore(ancestor) || area < ancestor.area * 0.85) {
            ancestor = cand;
          }
        }
        return ancestor;
      };
      const extractPeer = (rawText, badgeCount) => {
        const raw = normalize(rawText);
        let work = raw;
        const badgeNum = Number(badgeCount) || 0;
        if (badgeNum > 0) {
          work = work.replace(new RegExp('\\\\s+' + String(badgeNum) + '\\\\s*$'), '').trim();
        }
        work = work.replace(/\\s+[1-9]\\d?\\s*$/, '').trim();
        const mash = work.match(/^(.+?)\\s+(\\S+)\\s*·\\s*$/);
        if (mash) {
          const nick = normalize(mash[1]);
          const preview = normalize(mash[2]);
          if (nick && preview && !/^[1-9]\\d?$/.test(nick)) {
            return {nickname: nick.slice(0, 64), lastMessage: preview.slice(0, 200), timeLabel: '', raw: raw.slice(0, 240)};
          }
        }
        const parts = work.split(/\\s+/).filter(Boolean);
        let nickname = '';
        let lastMessage = work;
        let timeLabel = '';
        const timeIdx = parts.findIndex((p) => timeToken.test(p));
        if (timeIdx >= 0) {
          nickname = parts.slice(0, timeIdx).join(' ').trim();
          timeLabel = parts[timeIdx];
          lastMessage = parts.slice(timeIdx + 1).join(' ').trim();
        } else {
          nickname = parts[0] || '';
          lastMessage = parts.slice(1).join(' ').trim();
        }
        lastMessage = lastMessage
          .replace(/^转发\\[.*?\\]:\\s*/, '')
          .replace(/\\s*·\\s*$/, '')
          .trim();
        if (!nickname || nickname.length > 48 || /^[1-9]\\d?$/.test(nickname)) {
          nickname = '';
        }
        if (/^[1-9]\\d?$|^99\\+?$/.test(lastMessage)) {
          lastMessage = '';
        }
        return {nickname, lastMessage: lastMessage.slice(0, 200), timeLabel, raw: raw.slice(0, 240)};
      };
      const extractIdentity = (rowEl) => {
        let peerUid = '';
        let peerSecUid = '';
        let peerProfileUrl = '';
        const anchors = Array.from(rowEl.querySelectorAll('a[href]'));
        for (const a of anchors) {
          const href = String(a.getAttribute('href') || '');
          if (!href) continue;
          const abs = href.startsWith('http') ? href : ('https://www.douyin.com' + (href.startsWith('/') ? href : ('/' + href)));
          const userMatch = abs.match(/douyin\\.com\\/user\\/([A-Za-z0-9_-]+)/i);
          if (userMatch) {
            peerSecUid = userMatch[1];
            peerProfileUrl = 'https://www.douyin.com/user/' + peerSecUid;
            break;
          }
          const uidMatch = abs.match(/[?&](?:uid|user_id)=(\\d+)/i);
          if (uidMatch) peerUid = uidMatch[1];
          const secMatch = abs.match(/[?&]sec_uid=([A-Za-z0-9_-]+)/i);
          if (secMatch) {
            peerSecUid = secMatch[1];
            peerProfileUrl = 'https://www.douyin.com/user/' + peerSecUid;
          }
        }
        return {peerUid, peerSecUid, peerProfileUrl};
      };
      const conversations = [];
      const seenTops = [];
      const seenNames = new Set();
      const pushConversation = (row, unreadCount) => {
        if (!row) return false;
        const parsed = extractPeer(row.text, unreadCount);
        if (!parsed) return false;
        const top = Math.round(row.rect.top);
        if (seenTops.some((y) => Math.abs(y - top) < 16)) return false;
        const nameKey = (parsed.nickname || parsed.raw || '').slice(0, 40);
        if (nameKey && seenNames.has(nameKey)) return false;
        seenTops.push(top);
        if (nameKey) seenNames.add(nameKey);
        const identity = extractIdentity(row.node);
        conversations.push({
          peer_nickname: parsed.nickname,
          peer_uid: identity.peerUid,
          peer_sec_uid: identity.peerSecUid,
          peer_profile_url: identity.peerProfileUrl,
          last_message: parsed.lastMessage,
          unread_count: Number(unreadCount) || 1,
          conversation_id: identity.peerSecUid || identity.peerUid || parsed.nickname || '',
          updated_at: parsed.timeLabel || '',
          row_text: parsed.raw,
        });
        return true;
      };

      for (const row of leafRows) {
        const badge = badgeInside(row.node, row.rect);
        if (!badge) continue;
        pushConversation(row, badge.count);
      }
      for (const badge of uniqueBadges) {
        const by = badge.rect.top + badge.rect.height / 2;
        const already = leafRows.some((row) => {
          return seenTops.some((y) => Math.abs(y - Math.round(row.rect.top)) < 2) &&
            by >= row.rect.top - 6 && by <= row.rect.bottom + 6;
        });
        if (already) continue;
        pushConversation(rowForBadge(badge), badge.count);
      }

      conversations.sort((a, b) => (b.unread_count || 0) - (a.unread_count || 0));
      return {
        ok: true,
        detail: 'unread conversations=' + conversations.length +
          '; badges=' + uniqueBadges.length +
          '; rows=' + leafRows.length,
        conversations,
        unreadCount: conversations.reduce((sum, item) => sum + (Number(item.unread_count) || 0), 0),
        unreadPeople: conversations.length,
        badgeCount: uniqueBadges.length,
        rowCount: leafRows.length,
        panelFound: true,
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        return {
            "ok": False,
            "detail": str(exc),
            "conversations": [],
            "unread_count": 0,
            "unread_people": 0,
        }
    if not isinstance(data, dict):
        data = {}
    conversations = _dm_normalize_unread_conversation_items(data.get("conversations") or [])
    unread_people = len(conversations)
    unread_count = sum(int(item.get("unread_count") or 0) for item in conversations)
    badge_count = int(data.get("badgeCount") or 0)
    detail = str(data.get("detail") or "")
    if badge_count > unread_people:
        detail = (detail + f"; incomplete_details badges={badge_count} details={unread_people}").strip("; ")
    return {
        "ok": bool(data.get("ok")) if conversations or data.get("ok") is False else bool(conversations),
        "detail": detail,
        "conversations": conversations,
        "unread_count": max(int(data.get("unreadCount") or 0), unread_count),
        # Keep badge people when details lag, so middle-platform can flag incomplete.
        "unread_people": max(int(data.get("unreadPeople") or 0), unread_people, badge_count),
        "panel_found": bool(data.get("panelFound")),
        "badge_count": badge_count,
        "row_count": int(data.get("rowCount") or 0),
    }


def _dm_supplement_unread_conversations_from_peek(
    page: Any,
    conversations: List[Dict[str, Any]],
    *,
    unread_count: int = 1,
    unread_people: int = 1,
) -> List[Dict[str, Any]]:
    """When badge says unread but details lag, peek list rows (no click) to fill gaps."""
    existing = list(conversations or [])
    need = max(1, int(unread_people or 0), 1 if int(unread_count or 0) > 0 else 0)
    if existing and len(existing) >= need:
        return existing
    peek = _dm_click_unread_or_latest_chat(page, click=False)
    if not peek.get("ok"):
        return existing
    preview = _dm_normalize_text(peek.get("preview_text"))
    detail = _dm_normalize_text(peek.get("detail"))
    message = preview or detail
    if not message or re.fullmatch(r"[1-9]\d?|99\+?", message):
        return existing
    peer_key = _dm_peer_key_from_row(detail, preview or message)
    fallback_rows = _dm_normalize_unread_conversation_items(
        [
            {
                "peer_nickname": peer_key,
                "last_message": preview or message,
                "unread_count": max(1, int(unread_count or 1) if int(unread_people or 0) <= 1 else 1),
                "row_text": detail,
            }
        ]
    )
    if not fallback_rows:
        return existing
    known = {
        (
            _dm_normalize_text(item.get("peer_nickname")),
            _dm_normalize_text(item.get("conversation_id")),
        )
        for item in existing
    }
    for row in fallback_rows:
        key = (
            _dm_normalize_text(row.get("peer_nickname")),
            _dm_normalize_text(row.get("conversation_id")),
        )
        if key in known:
            continue
        if any(
            _dm_normalize_text(item.get("peer_nickname")) == key[0]
            and key[0]
            for item in existing
        ):
            continue
        existing.append(row)
        known.add(key)
    return existing


def _dm_click_unread_or_latest_chat(page: Any, timeout_ms: int = 5000, *, click: bool = True) -> Dict[str, Any]:
    sr = _sr()
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 36 && rect.height >= 24 && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
      };
      const allVisible = Array.from(document.querySelectorAll('div, section, aside, ul, li, a, button')).filter(isVisible);
      const panelCandidates = allVisible
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const style = window.getComputedStyle(el);
          const rightSide = rect.left > window.innerWidth * 0.35 && rect.right > window.innerWidth * 0.55;
          const panelSize = rect.width >= 200 && rect.width <= window.innerWidth * 0.78 &&
            rect.height >= 200 && rect.height <= window.innerHeight * 0.99;
          const hasPrivateHeader = /(消息|私信)/.test(text.slice(0, 80)) ||
            /im-dialog|imContainer|imSaas|im-saas|imDark|semi-always-dark/i.test(String(el.className || ''));
          if (!rightSide || !panelSize || !hasPrivateHeader || style.visibility === 'hidden' ||
              style.display === 'none' || style.opacity === '0') return null;
          let score = 100 + rect.width - rect.height / 1000;
          if (/(学习\\d|剪辑|vlog|点赞|推荐)/i.test(text.slice(0, 80))) score -= 120;
          if (score < 40) return null;
          return {el, rect, text, score};
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score || a.rect.left - b.rect.left);
      const panel = panelCandidates[0];
      if (!panel) return {clicked: false, detail: 'private message panel not found'};

      // Prefer the 私信 tab inside the message drawer when present.
      const tabHits = allVisible
        .filter((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          return text === '私信' &&
            rect.left >= panel.rect.left - 8 &&
            rect.right <= panel.rect.right + 8 &&
            rect.top >= panel.rect.top - 4 &&
            rect.top <= panel.rect.top + 120 &&
            rect.height <= 48;
        })
        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
      let tabPoint = null;
      if (tabHits[0]) {
        const rect = tabHits[0].getBoundingClientRect();
        tabPoint = {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
      }

      const panelRect = panel.rect;
      const withinPanel = (el) => {
        const rect = el.getBoundingClientRect();
        return rect.left >= panelRect.left - 4 && rect.right <= panelRect.right + 4 &&
          rect.top >= panelRect.top + 40 && rect.bottom <= panelRect.bottom + 4;
      };
      const badges = allVisible
        .filter((el) => withinPanel(el))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const compact = rect.width <= 42 && rect.height <= 42;
          const numeric = /^[1-9][0-9]?$/.test(text);
          return {el, rect, text, score: (numeric ? 60 : 0) + (compact ? 30 : 0)};
        })
        .filter((item) => item.score >= 60)
        .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top);

      const rowForBadge = (badge) => {
        let node = badge.el;
        let best = null;
        for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
          if (!withinPanel(node)) continue;
          const rect = node.getBoundingClientRect();
          const text = normalize(node.innerText || node.textContent || '');
          if (rect.width >= panelRect.width * 0.68 && rect.height >= 44 && rect.height <= 150 &&
              text.length >= 1 && text.length <= 320) {
            const score = rect.width - Math.abs(rect.top - badge.rect.top) * 0.5;
            if (!best || score > best.score) best = {node, rect, text, score};
          }
        }
        return best;
      };

      const badgeRows = badges.map(rowForBadge).filter(Boolean);
      const scoredRows = allVisible
        .filter((el) => withinPanel(el))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          if (rect.width < panelRect.width * 0.62 || rect.height < 44 || rect.height > 160) return null;
          if (text.length < 2 || text.length > 360) return null;
          let score = 40 - Math.abs(rect.height - 72) * 0.2;
          score += Math.max(0, 80 - (rect.top - panelRect.top)); // prefer top rows
          if (/刚刚|\\d+\\s*秒前|\\d+\\s*分钟前/.test(text)) score += 80;
          if (/今天|昨天|\\d{1,2}:\\d{2}/.test(text)) score += 10;
          if (/转发|群聊|被设置为管理员|http|v\\.douyin\\.com/.test(text)) score -= 25;
          return {node: el, rect, text, score};
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score || a.rect.top - b.rect.top);
      const rows = badgeRows.length ? badgeRows : scoredRows.slice(0, 1);

      const row = rows[0];
      if (!row) return {clicked: false, detail: 'no conversation row found in private message panel', tabPoint};
      const raw = String(row.text || '');
      let preview = raw
        .replace(/\\s+/g, ' ')
        .replace(/^(刚刚|\\d+\\s*秒前|\\d+\\s*分钟前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])\\s*/g, '')
        .trim();
      // Drop leading nickname chunk (first token-ish before time/preview).
      preview = preview.replace(/^.{1,24}?\\s+(?=刚刚|\\d+\\s*秒前|\\d+\\s*分钟前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])/, '').trim();
      preview = preview
        .replace(/^(刚刚|\\d+\\s*秒前|\\d+\\s*分钟前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])\\s*/, '')
        .replace(/\\s+[1-9]\\d?\\s*$/, '')
        .replace(/^转发\\[.*?\\]:\\s*/, '')
        .trim();
      if (!preview || preview.length > 80) {
        const parts = raw.split(/\\s+/).filter(Boolean);
        const timeIdx = parts.findIndex((p) => /^(刚刚|\\d+秒前|\\d+分钟前|昨天|\\d{1,2}:\\d{2}|周[一二三四五六日])$/.test(p));
        if (timeIdx >= 0 && timeIdx + 1 < parts.length) {
          preview = parts.slice(timeIdx + 1).join(' ').replace(/\\s+[1-9]\\d?\\s*$/, '').trim();
        }
      }
      return {
        clicked: true,
        detail: raw.slice(0, 120),
        preview_text: preview.slice(0, 120),
        unread: badges.length > 0,
        tabPoint,
        clickPoint: {
          x: row.rect.left + Math.min(row.rect.width / 2, 80),
          y: row.rect.top + Math.min(row.rect.height / 2, 24),
        },
        panel: {
          x: Math.round(panelRect.left),
          y: Math.round(panelRect.top),
          width: Math.round(panelRect.width),
          height: Math.round(panelRect.height),
        },
      };
    }
    """
    try:
        result = page.evaluate(script) or {}
    except Exception as exc:
        result = {"clicked": False, "detail": str(exc)}
    if isinstance(result, dict):
        if not click and result.get("clicked"):
            return {
                "name": "peek_unread_chat",
                "ok": True,
                "detail": str(result.get("detail") or "peeked"),
                "preview_text": str(result.get("preview_text") or "").strip(),
                "unread": bool(result.get("unread")),
            }
        tab_point = result.get("tabPoint") if isinstance(result.get("tabPoint"), dict) else None
        if tab_point and tab_point.get("x") is not None:
            try:
                page.mouse.click(float(tab_point["x"]), float(tab_point["y"]))
                page.wait_for_timeout(500)
            except Exception:
                pass
        click_point = result.get("clickPoint") if isinstance(result.get("clickPoint"), dict) else None
        if result.get("clicked") and click_point and click_point.get("x") is not None:
            try:
                page.mouse.click(float(click_point["x"]), float(click_point["y"]))
                page.wait_for_timeout(700)
            except Exception as exc:
                return {"name": "open_latest_chat", "ok": False, "detail": f"row click failed: {exc}"}
            try:
                sr._dm_visible_message_editor(page, timeout_ms=min(1800, timeout_ms))
            except Exception:
                pass
            return {
                "name": "open_latest_chat",
                "ok": True,
                "detail": str(result.get("detail") or "clicked"),
                "preview_text": str(result.get("preview_text") or "").strip(),
                "unread": bool(result.get("unread")),
            }
    return {
        "name": "open_latest_chat",
        "ok": False,
        "detail": str((result or {}).get("detail") if isinstance(result, dict) else result),
    }


def _dm_collect_message_entry_candidates(page: Any) -> List[Dict[str, Any]]:
    script = """
    () => {
      const asText = (value) => {
        if (!value) return '';
        if (typeof value === 'string') return value;
        if (typeof value.baseVal === 'string') return value.baseVal;
        return String(value);
      };
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const visible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 12 && rect.height >= 12 &&
          rect.top >= 0 && rect.top < Math.max(220, window.innerHeight * 0.28) &&
          rect.left > window.innerWidth * 0.55 &&
          style.visibility !== 'hidden' &&
          style.display !== 'none' &&
          style.opacity !== '0';
      };
      const labelFor = (el) => normalize([
        el.innerText,
        el.textContent,
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        el.getAttribute('alt'),
      ].filter(Boolean).join(' '));
      return Array.from(document.querySelectorAll('a, button, [role=button], [aria-label], [title], div, span, svg, path'))
        .filter(visible)
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const target = el.closest('a, button, [role=button]') || el;
          const text = labelFor(el);
          const targetText = labelFor(target);
          const attrs = normalize([
            asText(el.id),
            asText(el.className),
            el.getAttribute('data-e2e'),
            el.getAttribute('data-testid'),
            target.getAttribute('aria-label'),
            target.getAttribute('title'),
            target.getAttribute('href'),
          ].filter(Boolean).join(' '));
          const combined = `${text} ${targetText} ${attrs}`;
          let score = 0;
          if (/消息|私信|聊天|会话|Message|Messages|Chat|IM/i.test(combined)) score += 100;
          if (/notice|notify|notification|message|msg|chat|im|conversation/i.test(combined)) score += 50;
          if (/\\b(icon|message|chat|notice|notify|im)\\b/i.test(attrs)) score += 12;
          if (rect.width <= 160 && rect.height <= 120) score += 20;
          if (text.length <= 12) score += 10;
          if (/发布|登录|搜索|上传|充值|下载|菜单/.test(combined)) score -= 70;
          return {
            text: targetText || text,
            attrs: attrs.slice(0, 180),
            score,
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          };
        })
        .filter((item) => item.score >= 40)
        .sort((a, b) => b.score - a.score || b.x - a.x || a.y - b.y)
        .slice(0, 12);
    }
    """
    try:
        data = page.evaluate(script) or []
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict)]


def _dm_capture_conversation_monitor_diagnostic(page: Any, account_key: str, reason: str = "") -> Dict[str, Any]:
    sr = _sr()
    prefix_seed = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(account_key or "conversation"))[:48]
    artifact = sr._dm_screenshot_failure(
        page,
        {
            "screenshot_on_failure": True,
            "screenshot_dir": str(sr.DM_DEBUG_ARTIFACT_DIR),
            "screenshot_prefix": f"conversation_{prefix_seed}",
        },
        reason,
    )
    try:
        artifact["page_url"] = str(getattr(page, "url", "") or "")
    except Exception:
        artifact["page_url"] = ""
    try:
        artifact["page_title"] = str(page.title() or "")
    except Exception:
        artifact["page_title"] = ""
    artifact["message_entry_candidates"] = _dm_collect_message_entry_candidates(page)
    return artifact


def _launch_account_persistent_context(browser_name: str, options: Dict[str, Any]):
    """Launch Playwright in the calling thread; do not share web-executor sessions."""
    from playwright.sync_api import sync_playwright

    sr = _sr()
    user_data_dir = Path(str(options.get("user_data_dir") or "")).expanduser()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    manager = sync_playwright()
    playwright = manager.start()
    launch_options = {
        "headless": bool(options.get("headless")),
        "slow_mo": int(options.get("slow_mo") or 0),
        "viewport": {
            "width": int(options.get("viewport_width") or sr.DM_DEFAULT_VIEWPORT_WIDTH),
            "height": int(options.get("viewport_height") or sr.DM_DEFAULT_VIEWPORT_HEIGHT),
        },
        "device_scale_factor": float(options.get("device_scale_factor") or 1.0),
    }
    channel = sr._dm_browser_channel(browser_name)
    if channel:
        launch_options["channel"] = channel
    try:
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
    except Exception:
        try:
            playwright.stop()
        except Exception:
            pass
        raise
    return playwright, context, manager


class DouyinConversationMonitor:
    def __init__(
        self,
        *,
        account_id: str,
        account_name: str,
        account_key: str,
        account_cookies: str,
        browser_name: str,
        headless: bool,
        project_id: str,
        company_id: str,
        source_platform: str,
        auto_send: bool,
        generate_reply: bool,
        poll_seconds: float,
        reply_backend: str = "gpu_pool",
        gpu_model: str = DEFAULT_GPU_POOL_MODEL,
        gpu_base_url: str = DEFAULT_GPU_POOL_BASE_URL,
        gpu_api_key: str = DEFAULT_GPU_POOL_API_KEY,
        max_auto_replies_before_peer: int = 2,
        logic: Optional[SessionRAGChatLogic] = None,
        project_store: Optional[ProjectMaterialStore] = None,
    ):
        self.account_id = account_id
        self.account_name = account_name or account_id or account_key
        self.account_key = account_key
        self.account_cookies = account_cookies
        self.browser_name = browser_name or "edge"
        self.headless = bool(headless)
        self.project_id = project_id
        self.company_id = company_id
        self.source_platform = source_platform or "抖音"
        self.auto_send = bool(auto_send)
        self.generate_reply = bool(generate_reply)
        self.poll_seconds = max(3.0, float(poll_seconds or 8))
        self.reply_backend = str(reply_backend or "gpu_pool").strip().lower() or "gpu_pool"
        self.gpu_model = str(gpu_model or DEFAULT_GPU_POOL_MODEL).strip() or DEFAULT_GPU_POOL_MODEL
        self.gpu_base_url = str(gpu_base_url or DEFAULT_GPU_POOL_BASE_URL).rstrip("/") or DEFAULT_GPU_POOL_BASE_URL
        self.gpu_api_key = str(gpu_api_key or DEFAULT_GPU_POOL_API_KEY)
        try:
            self.max_auto_replies_before_peer = max(1, int(max_auto_replies_before_peer or 2))
        except (TypeError, ValueError):
            self.max_auto_replies_before_peer = 2
        self.logic = logic
        self.project_store = project_store
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()
        self.status = "stopped"
        self.last_error = ""
        self.last_message = ""
        self.last_reply = ""
        self.last_peer = ""
        self.last_seen_signature = ""
        self.last_diagnostic: Dict[str, Any] = {}
        self.last_diagnostic_at = 0.0
        self.started_at = ""
        self.updated_at = ""
        self.loop_count = 0
        self.reply_count = 0
        self.inbox_unread_count = 0
        self.inbox_unread_people = 0
        self.inbox_summary_updated_at = ""
        self.unread_conversations: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.histories: Dict[str, List[Dict[str, str]]] = {}
        self.send_failed_signatures: Dict[str, int] = {}
        # 每个对方：自其上次发言后，已连续自动回复条数（达到上限则等对方再回）
        self.peer_auto_replies_since_peer: Dict[str, int] = {}
        self.peer_last_inbound_signature: Dict[str, str] = {}
        self.peer_last_outbound: Dict[str, str] = {}
        # On-demand inbox reuse: HTTP sync asks monitor thread to scan its open page.
        self._wake_event = threading.Event()
        self._inbox_req_lock = threading.Lock()
        self._inbox_pending = False
        self._inbox_result: Optional[Dict[str, Any]] = None
        self._inbox_done = threading.Event()
        # On-demand DM send via the same monitor browser (reply-pool follow-up).
        self._send_req_lock = threading.Lock()
        self._send_pending = False
        self._send_payload: Optional[Dict[str, Any]] = None
        self._send_result: Optional[Dict[str, Any]] = None
        self._send_done = threading.Event()

    def log(self, text: str, level: str = "info", extra: Optional[Dict[str, Any]] = None) -> None:
        item = {
            "time": _sr()._dm_now(),
            "level": level,
            "text": str(text or ""),
            **dict(extra or {}),
        }
        with self.lock:
            self.updated_at = item["time"]
            self.logs.append(item)
            self.logs = self.logs[-80:]

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.status = "running"
                return
            self.stop_event.clear()
            self.status = "starting"
            self.started_at = _sr()._dm_now()
            self.updated_at = self.started_at
            self.thread = threading.Thread(
                target=self._run,
                name=f"douyin-conversation-{self.account_key[:10]}",
                daemon=True,
            )
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._wake_event.set()
        with self.lock:
            self.status = "stopping"
            self.updated_at = _sr()._dm_now()
        self.log("已请求停止监控", "info")

    def request_fresh_inbox(self, timeout_seconds: float = 90.0) -> Dict[str, Any]:
        """Scan unread on the already-open monitor browser (same Playwright thread)."""
        if not (self.thread and self.thread.is_alive()):
            return {
                "ok": False,
                "requires_login": False,
                "requires_verification": False,
                "failure_code": "monitor_not_alive",
                "message": "盯号未在运行，无法复用浏览器",
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": self.account_id,
                "account_key": self.account_key,
                "browser": self.browser_name,
                "steps": [],
                "reused_monitor": False,
            }
        with self._inbox_req_lock:
            self._inbox_pending = True
            self._inbox_result = None
            self._inbox_done.clear()
        self._wake_event.set()
        wait_s = max(5.0, float(timeout_seconds or 90.0))
        if not self._inbox_done.wait(timeout=wait_s):
            with self._inbox_req_lock:
                self._inbox_pending = False
            return {
                "ok": False,
                "requires_login": False,
                "requires_verification": False,
                "failure_code": "monitor_inbox_timeout",
                "message": f"复用盯号浏览器扫未读超时（{int(wait_s)}s）",
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": self.account_id,
                "account_key": self.account_key,
                "browser": self.browser_name,
                "steps": [],
                "reused_monitor": True,
            }
        with self._inbox_req_lock:
            result = dict(self._inbox_result or {})
        result.setdefault("reused_monitor", True)
        result.setdefault("account_id", self.account_id)
        result.setdefault("account_key", self.account_key)
        result.setdefault("browser", self.browser_name)
        return result

    def request_send_dm(self, payload: Dict[str, Any], timeout_seconds: float = 120.0) -> Dict[str, Any]:
        """Send a private message using the already-open monitor browser."""
        if not (self.thread and self.thread.is_alive()):
            return {
                "success": False,
                "opened": False,
                "prefilled": False,
                "sent": False,
                "error": "盯号未在运行，无法复用浏览器发送",
                "failure_code": "monitor_not_alive",
                "reused_monitor": False,
                "resolved_browser": self.browser_name,
                "engine": "monitor",
                "steps": [],
            }
        with self._send_req_lock:
            self._send_pending = True
            self._send_payload = dict(payload or {})
            self._send_result = None
            self._send_done.clear()
        self._wake_event.set()
        wait_s = max(15.0, float(timeout_seconds or 120.0))
        if not self._send_done.wait(timeout=wait_s):
            with self._send_req_lock:
                self._send_pending = False
            return {
                "success": False,
                "opened": False,
                "prefilled": False,
                "sent": False,
                "error": f"复用盯号浏览器发送超时（{int(wait_s)}s）",
                "failure_code": "monitor_send_timeout",
                "reused_monitor": True,
                "resolved_browser": self.browser_name,
                "engine": "monitor",
                "steps": [],
            }
        with self._send_req_lock:
            result = dict(self._send_result or {})
        result.setdefault("reused_monitor", True)
        result.setdefault("resolved_browser", self.browser_name)
        result.setdefault("engine", "monitor")
        return result

    def _send_dm_on_page(self, page: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Open target profile on the monitor page and send message (+ optional followup)."""
        sr = _sr()
        profile_url = str(
            payload.get("profile_url")
            or payload.get("target_profile_url")
            or ""
        ).strip()
        message = str(payload.get("message") or payload.get("reply") or "").strip()
        followup_message = str(payload.get("followup_message") or "").strip()
        auto_send = True if payload.get("auto_send") is None else sr._dm_bool_text(payload.get("auto_send"))
        timeout_ms = int(sr._payload_float(payload, "timeout_ms", 45000))
        steps: List[Dict[str, Any]] = []
        if not profile_url:
            return {
                "success": False,
                "opened": False,
                "prefilled": False,
                "sent": False,
                "error": "target_profile_url is required",
                "steps": steps,
                "reused_monitor": True,
            }
        if not message:
            return {
                "success": False,
                "opened": False,
                "prefilled": False,
                "sent": False,
                "error": "message is required",
                "steps": steps,
                "reused_monitor": True,
            }
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=timeout_ms)
            steps.append({"name": "open_profile", "ok": True, "detail": "opened_via_monitor"})
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 12000))
            except Exception:
                pass
            login_abort = sr._dm_abort_on_login_requirement(page, steps, stage="after_open_profile")
            if login_abort:
                return {
                    "success": False,
                    "opened": True,
                    "prefilled": False,
                    "sent": False,
                    "error": str(login_abort.get("error") or "login_required"),
                    "requires_login": bool(login_abort.get("requires_login")),
                    "requires_verification": bool(login_abort.get("requires_verification")),
                    "steps": steps,
                    "reused_monitor": True,
                }
            steps.append(sr._dm_click_profile_private_message(page, timeout_ms=min(timeout_ms, 12000)))
            steps.append(sr._dm_fill_message_editor(page, message, timeout_ms=min(timeout_ms, 12000)))
            sent = False
            if auto_send:
                send_steps = sr._dm_send_and_confirm_current_message(
                    page,
                    message,
                    timeout_ms=min(timeout_ms, 15000),
                    prefer_click_send=True,
                    accept_editor_cleared=True,
                )
                steps.extend(list(send_steps or []))
                sent = True
            followup_sent = False
            followup_status = ""
            followup_detail = ""
            if sent and followup_message:
                try:
                    fill_step = sr._dm_fill_message_editor(
                        page, followup_message, timeout_ms=min(timeout_ms, 12000)
                    )
                    fill_step["name"] = "paste_followup_message"
                    steps.append(fill_step)
                    followup_steps = sr._dm_send_and_confirm_current_message(
                        page,
                        followup_message,
                        timeout_ms=min(timeout_ms, 10000),
                        retry_enter_before_click=True,
                    )
                    for item in followup_steps or []:
                        item["name"] = f"followup_{item.get('name') or 'send'}"
                    steps.extend(list(followup_steps or []))
                    followup_sent = True
                    followup_status = "sent"
                    followup_detail = "followup private message sent via monitor"
                except Exception as followup_exc:
                    followup_status = "failed_ignored"
                    followup_detail = str(followup_exc)
                    steps.append({
                        "name": "followup_send_error",
                        "ok": False,
                        "detail": followup_detail,
                    })
            # Return to messages surface so monitor loop can continue watch-only.
            try:
                _dm_open_douyin_messages_surface(page, timeout_ms=15000)
            except Exception:
                pass
            return {
                "success": bool(sent) if auto_send else True,
                "opened": True,
                "prefilled": True,
                "sent": bool(sent),
                "followup_private_message": followup_sent,
                "followup_private_message_status": followup_status,
                "followup_private_message_detail": followup_detail,
                "steps": steps,
                "reused_monitor": True,
                "keep_browser_open": True,
                "error": "" if (sent or not auto_send) else "message send was not confirmed",
            }
        except Exception as exc:
            steps.append({"name": "monitor_send_error", "ok": False, "detail": str(exc)})
            return {
                "success": False,
                "opened": any(s.get("name") == "open_profile" and s.get("ok") for s in steps),
                "prefilled": any(s.get("name") == "paste_message" and s.get("ok") for s in steps),
                "sent": False,
                "error": str(exc),
                "steps": steps,
                "reused_monitor": True,
                "keep_browser_open": True,
            }

    def _scan_inbox_snapshot_on_page(self, page: Any) -> Dict[str, Any]:
        """One inbox-shaped snapshot using the monitor's live page (no new browser)."""
        sr = _sr()
        steps = _dm_open_douyin_messages_surface(page, timeout_ms=20000)
        login_detail = ""
        verify_detail = ""
        for step in steps:
            detail = str(step.get("detail") or "")
            if not step.get("ok") and "requires_verification" in detail and not verify_detail:
                verify_detail = detail
            if not step.get("ok") and "login_required" in detail and not login_detail:
                login_detail = detail
        if verify_detail:
            with self.lock:
                self.last_error = verify_detail
            return {
                "ok": False,
                "requires_login": False,
                "requires_verification": True,
                "failure_code": "requires_verification",
                "message": verify_detail,
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": self.account_id,
                "account_key": self.account_key,
                "browser": self.browser_name,
                "steps": steps,
                "reused_monitor": True,
            }
        if login_detail:
            with self.lock:
                self.last_error = login_detail
            return {
                "ok": False,
                "requires_login": True,
                "requires_verification": False,
                "failure_code": "login_required",
                "message": login_detail,
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": self.account_id,
                "account_key": self.account_key,
                "browser": self.browser_name,
                "steps": steps,
                "reused_monitor": True,
            }
        conversations_scan = _dm_scan_unread_conversations(page)
        conversations = list(conversations_scan.get("conversations") or [])
        inbox = _dm_scan_inbox_summary(page)
        unread_count = int(conversations_scan.get("unread_count") or 0)
        unread_people = int(conversations_scan.get("unread_people") or 0)
        if inbox.get("ok"):
            # Prefer panel-sourced counts; top_entry alone is a weak signal.
            inbox_detail = str(inbox.get("detail") or "")
            if "top_entry" not in inbox_detail or conversations or inbox.get("panel_found"):
                unread_count = max(unread_count, int(inbox.get("unread_count") or 0))
                unread_people = max(unread_people, int(inbox.get("unread_people") or 0))
            elif int(inbox.get("unread_count") or 0) > 0 and unread_count <= 0:
                unread_count = max(unread_count, 1)
                unread_people = max(unread_people, 1)
        if conversations:
            unread_people = max(unread_people, len(conversations))
            unread_count = max(
                unread_count,
                sum(int(item.get("unread_count") or 0) for item in conversations),
            )
        if (not conversations and (unread_people > 0 or unread_count > 0)) or (
            unread_people > len(conversations)
        ):
            conversations = _dm_supplement_unread_conversations_from_peek(
                page,
                conversations,
                unread_count=unread_count,
                unread_people=unread_people,
            )
            if conversations:
                unread_people = max(unread_people, len(conversations))
                unread_count = max(
                    unread_count,
                    sum(int(item.get("unread_count") or 0) for item in conversations),
                )
        with self.lock:
            self.inbox_unread_count = unread_count
            self.inbox_unread_people = unread_people
            self.inbox_summary_updated_at = sr._dm_now()
            self.unread_conversations = list(conversations)
            self.updated_at = self.inbox_summary_updated_at
        if not conversations_scan.get("ok") and not inbox.get("ok") and not conversations:
            return {
                "ok": False,
                "requires_login": False,
                "requires_verification": False,
                "failure_code": "inbox_scan_failed",
                "message": str(
                    conversations_scan.get("detail") or inbox.get("detail") or "收件箱摘要失败"
                ),
                "inbox_unread_count": 0,
                "inbox_unread_people": 0,
                "unread_conversations": [],
                "account_id": self.account_id,
                "account_key": self.account_key,
                "browser": self.browser_name,
                "steps": steps,
                "reused_monitor": True,
            }
        detail_parts = []
        if conversations_scan.get("detail"):
            detail_parts.append(str(conversations_scan.get("detail")))
        if inbox.get("detail"):
            detail_parts.append(str(inbox.get("detail")))
        detail_parts.append("reused_monitor=1")
        return {
            "ok": True,
            "requires_login": False,
            "requires_verification": False,
            "failure_code": "",
            "message": "；".join(detail_parts).strip(),
            "inbox_unread_count": unread_count,
            "inbox_unread_people": unread_people,
            "unread_conversations": conversations,
            "account_id": self.account_id,
            "account_key": self.account_key,
            "browser": self.browser_name,
            "steps": steps,
            "reused_monitor": True,
        }

    def snapshot(self) -> Dict[str, Any]:
        sr = _sr()
        with self.lock:
            alive = bool(self.thread and self.thread.is_alive())
            if self.status in {"running", "starting", "stopping"} and not alive:
                self.status = "stopped"
            cookie_text = sr._dm_cookie_text(self.account_cookies)
            return {
                "account_id": self.account_id,
                "account_name": self.account_name,
                "account_key": self.account_key,
                "browser_name": self.browser_name,
                "headless": self.headless,
                "status": self.status,
                "alive": alive,
                "auto_send": self.auto_send,
                "generate_reply": self.generate_reply,
                "max_auto_replies_before_peer": self.max_auto_replies_before_peer,
                "reply_backend": self.reply_backend,
                "gpu_model": self.gpu_model,
                "account_cookie_loaded": bool(cookie_text),
                "account_cookie_count": len(sr._dm_parse_account_cookies(self.account_cookies)) if cookie_text else 0,
                "profile_login_reused": _dm_account_browser_profile_exists(self.browser_name, self.account_key),
                "user_data_dir": str(_dm_account_browser_user_data_dir(self.browser_name, self.account_key)),
                "poll_seconds": self.poll_seconds,
                "last_error": self.last_error,
                "last_message": self.last_message,
                "last_reply": self.last_reply,
                "last_peer": self.last_peer,
                "last_seen_signature": self.last_seen_signature,
                "last_diagnostic": dict(self.last_diagnostic),
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "loop_count": self.loop_count,
                "reply_count": self.reply_count,
                "inbox_unread_count": self.inbox_unread_count,
                "inbox_unread_people": self.inbox_unread_people,
                "unread_count": self.inbox_unread_count,
                "inbox_summary_updated_at": self.inbox_summary_updated_at,
                "unread_conversations": list(self.unread_conversations),
                "logs": list(self.logs[-30:]),
            }

    def _run(self) -> None:
        sr = _sr()
        page = None
        context = None
        playwright = None
        try:
            options = {
                "browser": self.browser_name,
                "browser_name": self.browser_name,
                "headless": self.headless,
                "slow_mo": 120,
                "user_data_dir": str(_dm_account_browser_user_data_dir(self.browser_name, self.account_key)),
                "viewport_width": sr.DM_DEFAULT_VIEWPORT_WIDTH,
                "viewport_height": sr.DM_DEFAULT_VIEWPORT_HEIGHT,
            }
            playwright, context, _manager = _launch_account_persistent_context(self.browser_name, options)
            cookies = sr._dm_parse_account_cookies(self.account_cookies)
            if cookies:
                context.add_cookies(cookies)
            page = _dm_reusable_context_page(context)
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=45000)
            self.log("已打开账号浏览器，开始监控新私信", "ok")
            with self.lock:
                self.status = "running"
                self.updated_at = sr._dm_now()

            while not self.stop_event.is_set():
                with self.lock:
                    self.loop_count += 1
                    self.updated_at = sr._dm_now()
                do_inbox = False
                do_send = False
                send_payload: Dict[str, Any] = {}
                with self._send_req_lock:
                    do_send = bool(self._send_pending)
                    if do_send:
                        send_payload = dict(self._send_payload or {})
                with self._inbox_req_lock:
                    do_inbox = bool(self._inbox_pending)
                if do_send:
                    try:
                        result = self._send_dm_on_page(page, send_payload)
                        self.log(
                            "复用盯号浏览器发送："
                            + str(result.get("error") or ("ok" if result.get("sent") else "not_sent")),
                            "ok" if result.get("sent") else "warn",
                        )
                    except Exception as send_exc:
                        result = {
                            "success": False,
                            "opened": False,
                            "prefilled": False,
                            "sent": False,
                            "error": str(send_exc),
                            "failure_code": "monitor_send_error",
                            "steps": [],
                            "reused_monitor": True,
                        }
                        self.log("复用盯号发送异常：" + str(send_exc), "error")
                    with self._send_req_lock:
                        self._send_result = result
                        self._send_pending = False
                        self._send_payload = None
                        self._send_done.set()
                elif do_inbox:
                    try:
                        result = self._scan_inbox_snapshot_on_page(page)
                        self.log(
                            "复用盯号浏览器扫未读："
                            + str(result.get("message") or result.get("failure_code") or "ok"),
                            "ok" if result.get("ok") else "warn",
                        )
                    except Exception as scan_exc:
                        result = {
                            "ok": False,
                            "requires_login": False,
                            "requires_verification": False,
                            "failure_code": "monitor_inbox_error",
                            "message": str(scan_exc),
                            "inbox_unread_count": 0,
                            "inbox_unread_people": 0,
                            "unread_conversations": [],
                            "account_id": self.account_id,
                            "account_key": self.account_key,
                            "browser": self.browser_name,
                            "steps": [],
                            "reused_monitor": True,
                        }
                        self.log("复用盯号扫未读异常：" + str(scan_exc), "error")
                    with self._inbox_req_lock:
                        self._inbox_result = result
                        self._inbox_pending = False
                        self._inbox_done.set()
                else:
                    self._tick(page)
                # Wait poll interval, but wake early for on-demand inbox/send or stop.
                self._wake_event.clear()
                deadline = time.time() + self.poll_seconds
                while time.time() < deadline and not self.stop_event.is_set():
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    if self._wake_event.wait(timeout=min(0.5, remaining)):
                        self._wake_event.clear()
                        break
                    if self.stop_event.is_set():
                        break
        except Exception as exc:
            with self.lock:
                self.status = "error"
                self.last_error = str(exc)
                self.updated_at = sr._dm_now()
            self.log("监控异常：" + str(exc), "error")
        finally:
            sr._dm_stop_playwright_context(context, None, playwright)
            with self.lock:
                if self.status != "error":
                    self.status = "stopped"
                self.updated_at = sr._dm_now()
            self.log("监控已停止", "info")

    def _tick(self, page: Any) -> None:
        sr = _sr()
        steps = _dm_open_douyin_messages_surface(page)
        login_required = False
        login_detail = ""
        verify_required = False
        verify_detail = ""
        for step in steps:
            if step.get("ok"):
                continue
            detail = str(step.get("detail") or "")
            if "requires_verification" in detail:
                verify_required = True
                verify_detail = detail
                continue
            if "login_required" in detail:
                login_required = True
                login_detail = detail
                continue
            self.log("打开消息入口未确认：" + detail, "warn", {"step": step.get("name")})
        if verify_required:
            extra = self._maybe_capture_diagnostic(page, verify_detail)
            with self.lock:
                self.last_error = verify_detail
            self.log("命中验证中间页，需有头人工验证后再盯号：" + verify_detail, "warn", extra)
            return
        if login_required:
            extra = self._maybe_capture_diagnostic(page, login_detail)
            with self.lock:
                self.last_error = login_detail
            self.log("账号登录态不可用，需人工扫码/更新 cookie 后再监控：" + login_detail, "warn", extra)
            return
        inbox = _dm_scan_inbox_summary(page)
        conversations_scan = _dm_scan_unread_conversations(page)
        conversations = list(conversations_scan.get("conversations") or [])
        if inbox.get("ok") or conversations_scan.get("ok"):
            unread_count = max(
                int(conversations_scan.get("unread_count") or 0),
                int(inbox.get("unread_count") or 0) if "top_entry" not in str(inbox.get("detail") or "") or conversations or inbox.get("panel_found") else 0,
            )
            unread_people = max(
                int(conversations_scan.get("unread_people") or 0),
                int(inbox.get("unread_people") or 0) if "top_entry" not in str(inbox.get("detail") or "") or conversations or inbox.get("panel_found") else 0,
                len(conversations),
            )
            if (
                not conversations
                and (int(inbox.get("unread_count") or 0) > 0 or int(inbox.get("unread_people") or 0) > 0)
                and "top_entry" in str(inbox.get("detail") or "")
            ):
                unread_count = max(unread_count, 1)
                unread_people = max(unread_people, 1)
            # Prefer list-row detail when present, but never hide badge incompleteness.
            if conversations:
                unread_count = max(
                    unread_count,
                    sum(int(item.get("unread_count") or 0) for item in conversations),
                )
                unread_people = max(unread_people, len(conversations))
            if (not conversations and (unread_people > 0 or unread_count > 0)) or (
                unread_people > len(conversations)
            ):
                before = len(conversations)
                conversations = _dm_supplement_unread_conversations_from_peek(
                    page,
                    conversations,
                    unread_count=unread_count,
                    unread_people=unread_people,
                )
                if len(conversations) > before:
                    unread_people = max(unread_people, len(conversations))
                    unread_count = max(
                        unread_count,
                        sum(int(item.get("unread_count") or 0) for item in conversations),
                    )
                    self.log(
                        "未读明细已补全："
                        + ",".join(
                            str(item.get("peer_nickname") or "")[:12]
                            for item in conversations[:6]
                        ),
                        "ok",
                    )
            # Always full-replace snapshot from this tick (no merge with stale rows).
            with self.lock:
                self.inbox_unread_count = unread_count
                self.inbox_unread_people = unread_people
                self.inbox_summary_updated_at = sr._dm_now()
                self.unread_conversations = list(conversations)
            sample = inbox.get("badge_sample") or []
            sample_text = ",".join(
                f"{item.get('kind')}:{item.get('text')}x{item.get('count')}"
                for item in sample[:4]
                if isinstance(item, dict)
            )
            names = ",".join(
                str(item.get("peer_nickname") or "")[:12]
                for item in conversations[:6]
                if isinstance(item, dict)
            )
            self.log(
                f"收件箱摘要：未读{self.inbox_unread_count}，未读会话{self.inbox_unread_people}"
                + (f"，明细{len(conversations)}条" if conversations else "")
                + (f"[{names}]" if names else "")
                + (f"（{inbox.get('detail') or conversations_scan.get('detail') or ''}）" if (inbox.get("detail") or conversations_scan.get("detail")) else "")
                + (f" badges=[{sample_text}]" if sample_text else ""),
                "info",
            )
            if unread_people > len(conversations):
                self.log(
                    f"未读明细不完整：角标/人数={unread_people}，回传明细={len(conversations)}",
                    "warn",
                    {
                        "badge_count": conversations_scan.get("badge_count"),
                        "scan_detail": conversations_scan.get("detail"),
                    },
                )
            if (unread_people > 0 or unread_count > 0) and not conversations:
                self.log(
                    "有未读角标但未解析到 unread_conversations 明细，将尝试列表行兜底",
                    "warn",
                    {
                        "badge_count": conversations_scan.get("badge_count"),
                        "inbox_detail": inbox.get("detail"),
                        "scan_detail": conversations_scan.get("detail"),
                    },
                )
        else:
            self.log(
                "收件箱摘要失败："
                + str(conversations_scan.get("detail") or inbox.get("detail") or "unknown"),
                "warn",
            )
        # 盯号模式（不生成/不发送）：只扫收件箱角标 + 列表预览，禁止点开会话。
        # 点开会话会清未读，中台气泡会被实时同步成长期 00/00。
        if not self.generate_reply:
            unread_people = int(self.inbox_unread_people or 0)
            unread_count = int(self.inbox_unread_count or 0)
            if conversations:
                first = conversations[0]
                message = str(first.get("last_message") or "").strip()
                peer_key = str(first.get("peer_nickname") or first.get("conversation_id") or "").strip()
                if message and message != "(未读)":
                    inbound = _dm_inbound_fingerprint(message)
                    signature = f"{peer_key}|{inbound}"
                    if signature != self.last_seen_signature:
                        with self.lock:
                            self.last_seen_signature = signature
                            self.last_message = message
                            self.last_peer = peer_key
                            self.updated_at = sr._dm_now()
                        self.log("识别到新消息（会话明细）：" + message[:120], "ok", {"peer": peer_key or ""})
                return
            if unread_people <= 0 and unread_count <= 0:
                return
            peek = _dm_click_unread_or_latest_chat(page, click=False)
            if not peek.get("ok"):
                detail = str(peek.get("detail") or "unknown")
                if unread_people > 0 or unread_count > 0:
                    extra = self._maybe_capture_diagnostic(page, detail)
                    self.log("有未读但未能读取列表预览：" + detail, "warn", extra)
                return
            preview = str(peek.get("preview_text") or "").strip()
            detail = str(peek.get("detail") or "").strip()
            message = preview or detail
            if not message:
                self.log("有未读会话，但未解析到消息预览", "warn")
                return
            if re.fullmatch(r"[1-9]\d?", message) and not preview:
                self.log("忽略疑似未读角标文本：" + message, "info")
                return
            peer_key = _dm_peer_key_from_row(detail, preview or message)
            # Fallback: still expose at least one unread_conversations row for reply-pool sync.
            fallback_rows = _dm_normalize_unread_conversation_items(
                [
                    {
                        "peer_nickname": peer_key,
                        "last_message": preview or message,
                        "unread_count": max(1, unread_count if unread_people <= 1 else 1),
                        "row_text": detail,
                    }
                ]
            )
            if fallback_rows:
                with self.lock:
                    self.unread_conversations = fallback_rows
                    self.inbox_unread_people = max(int(self.inbox_unread_people or 0), len(fallback_rows))
                    self.inbox_unread_count = max(
                        int(self.inbox_unread_count or 0),
                        sum(int(item.get("unread_count") or 0) for item in fallback_rows),
                    )
                self.log(
                    "未读明细兜底写入 unread_conversations："
                    + str(fallback_rows[0].get("peer_nickname") or "")
                    + " / "
                    + str(fallback_rows[0].get("last_message") or "")[:80],
                    "ok",
                )
            inbound = _dm_inbound_fingerprint(message)
            signature = f"{peer_key}|{inbound}"
            if signature == self.last_seen_signature:
                return
            with self.lock:
                self.last_seen_signature = signature
                self.last_message = message
                self.last_peer = peer_key
                self.updated_at = sr._dm_now()
            self.log("识别到新消息（列表预览）：" + message[:120], "ok", {"peer": peer_key or ""})
            return
        detect = _dm_detect_latest_douyin_message(page)
        # Always prefer an unread/recent conversation row first; staying in an old
        # open chat would otherwise skip new inbound messages.
        click_step = _dm_click_unread_or_latest_chat(page)
        preview_text = ""
        if click_step.get("ok"):
            preview_text = str(click_step.get("preview_text") or "").strip()
            self.log("已打开疑似新会话：" + str(click_step.get("detail") or ""), "ok")
            time.sleep(1.2)
            detect = _dm_detect_latest_douyin_message(page)
            # Wait briefly for the chat composer to mount after row click.
            for _ in range(6):
                if detect.get("has_editor"):
                    break
                time.sleep(0.45)
                detect = _dm_detect_latest_douyin_message(page)
        elif not detect.get("has_editor"):
            detail = str(click_step.get("detail") or "")
            extra = self._maybe_capture_diagnostic(page, detail)
            self.log("暂无可自动打开的新私信：" + detail, "info", extra)
            return
        message = str(detect.get("latest_text") or "").strip() or preview_text
        if not message:
            self.log("已进入私信页面，但未识别到用户新消息", "warn")
            return
        # Ignore pure system/list chrome leftovers.
        if re.fullmatch(r"[1-9]\d?", message) and not preview_text:
            self.log("忽略疑似未读角标文本：" + message, "info")
            return
        peer_key = _dm_peer_key_from_row(
            str(click_step.get("detail") or ""),
            preview_text or message,
        )
        inbound = _dm_inbound_fingerprint(message)
        signature = f"{peer_key}|{inbound}"
        pending_retry = signature in self.send_failed_signatures and self.auto_send and self.generate_reply
        if signature == self.last_seen_signature and not pending_retry:
            return
        with self.lock:
            self.last_seen_signature = signature
            self.last_message = message
            self.last_peer = peer_key
            self.updated_at = sr._dm_now()
        self.log("识别到新消息：" + message[:120], "ok", {"peer": peer_key or ""})
        last_reply = str(self.last_reply or "").strip()
        if peer_key:
            last_reply = str(self.peer_last_outbound.get(peer_key) or last_reply).strip() or last_reply
        if last_reply and _dm_is_own_outbound(message, last_reply):
            self.log("忽略与最近自动回复相同的文本，避免自回环", "info")
            return
        if preview_text and last_reply and _dm_is_own_outbound(preview_text, last_reply):
            self.log("会话预览仍是我方上次回复，对方尚未新回复，跳过", "info")
            return
        if not self.generate_reply:
            with self.lock:
                self.last_reply = ""
            self.log("已记录新私信；回复生成已关闭", "info")
            return
        if _dm_is_group_or_system_chat(click_step.get("detail"), preview_text, message):
            self.log("跳过群聊/系统会话，避免对未回复客户群发：" + (peer_key or message[:40]), "warn")
            return
        if self.auto_send and not click_step.get("unread"):
            self.log("无未读角标，不主动给未回复客户发私信：" + (peer_key or "unknown"), "info")
            return
        if self.auto_send and not peer_key:
            self.log("无法识别会话对方，已停止自动发送以免误发", "warn")
            return
        peer_key = peer_key or "unknown"
        with self.lock:
            last_inbound = str(self.peer_last_inbound_signature.get(peer_key) or "")
            if inbound and inbound != last_inbound and not _dm_is_own_outbound(inbound, last_reply):
                self.peer_last_inbound_signature[peer_key] = inbound
                self.peer_auto_replies_since_peer[peer_key] = 0
            streak = int(self.peer_auto_replies_since_peer.get(peer_key) or 0)
        if streak >= self.max_auto_replies_before_peer:
            self.log(
                f"对方未回复前已达自动回复上限 {self.max_auto_replies_before_peer} 条，等待对方回复："
                + peer_key,
                "warn",
            )
            return
        history = list(self.histories.get(peer_key) or [])[-8:]
        # Reuse previous generated reply when retrying a failed send.
        reply = ""
        if pending_retry and self.last_reply and self.last_peer == peer_key:
            reply = str(self.last_reply).strip()
            self.log("复用上次生成内容重试发送：" + reply[:80], "info")
        else:
            try:
                if self.reply_backend == "rag":
                    payload = {
                        "question": message,
                        "conversation_stage": "private_followup",
                        "session_id": "douyin_conversation_" + self.account_key + "_" + peer_key,
                        "user_id": self.account_key,
                        "project_id": self.project_id,
                        "company_id": self.company_id,
                        "source_platform": self.source_platform,
                        "account_id": self.account_id,
                    }
                    response = sr.build_chat_response(
                        payload,
                        logic=self.logic,
                        project_store=self.project_store,
                        use_saved_model_config=True,
                    )
                    reply = str(response.get("reply") or response.get("answer") or "").strip()
                else:
                    reply = _gpu_pool_chat_reply(
                        user_message=message,
                        history=history,
                        model=self.gpu_model,
                        base_url=self.gpu_base_url,
                        api_key=self.gpu_api_key,
                    )
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                self.log("生成回复失败：" + str(exc), "error")
                return
            reply = str(reply or "").strip()
            if not reply:
                self.log("模型返回空回复，已跳过发送", "error")
                return
            with self.lock:
                self.last_reply = reply
                self.last_peer = peer_key
                history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
                self.histories[peer_key] = history[-12:]
        if not self.auto_send:
            self.log("已生成回复，自动发送关闭：" + reply[:120], "info")
            return
        fail_count = int(self.send_failed_signatures.get(signature) or 0)
        if fail_count >= 3:
            self.log("同一条消息发送已失败多次，跳过避免刷屏：" + message[:80], "warn")
            return
        try:
            fill_step = sr._dm_fill_message_editor(page, reply, timeout_ms=12000)
            time.sleep(0.4)
            editor_text = sr._dm_message_editor_text(page)
            if reply[:8] not in editor_text and editor_text[:8] not in reply:
                editor = sr._dm_visible_message_editor(page, timeout_ms=4000)
                editor.click(timeout=4000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.insert_text(reply)
                time.sleep(0.45)
                editor_text = sr._dm_message_editor_text(page)
            if not editor_text:
                raise RuntimeError("message editor empty after fill")
            send_steps = sr._dm_send_and_confirm_current_message(
                page,
                reply,
                timeout_ms=10000,
                prefer_click_send=True,
                accept_editor_cleared=True,
            )
            send_steps = [fill_step, {"name": "verify_editor", "ok": True, "detail": editor_text[:80]}] + list(send_steps)
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
                self.send_failed_signatures[signature] = fail_count + 1
                # Keep signature sticky but allow retry via send_failed_signatures.
            self.log("自动发送失败：" + str(exc), "error")
            return
        with self.lock:
            self.reply_count += 1
            self.last_peer = peer_key
            self.peer_last_outbound[peer_key] = reply
            self.peer_auto_replies_since_peer[peer_key] = int(
                self.peer_auto_replies_since_peer.get(peer_key) or 0
            ) + 1
            self.send_failed_signatures.pop(signature, None)
            # Avoid treating the just-sent reply as a new inbound next loop.
            self.last_seen_signature = f"{peer_key}|{_dm_inbound_fingerprint(reply)}"
            self.last_reply = reply
        self.log(
            "已自动回复：" + reply[:120],
            "ok",
            {
                "steps": send_steps,
                "peer": peer_key,
                "auto_replies_since_peer": self.peer_auto_replies_since_peer.get(peer_key),
                "max_auto_replies_before_peer": self.max_auto_replies_before_peer,
            },
        )

    def _maybe_capture_diagnostic(self, page: Any, reason: str) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            if self.last_diagnostic and now - self.last_diagnostic_at < DM_CONVERSATION_MONITOR_DIAGNOSTIC_INTERVAL_SECONDS:
                return {"diagnostic": dict(self.last_diagnostic)}
        diagnostic = _dm_capture_conversation_monitor_diagnostic(page, self.account_key, reason)
        compact = {
            key: diagnostic.get(key)
            for key in (
                "failure_screenshot_path",
                "failure_screenshot_name",
                "failure_screenshot_url",
                "failure_screenshot_exists",
                "page_url",
                "page_title",
                "message_entry_candidates",
            )
            if key in diagnostic
        }
        with self.lock:
            self.last_diagnostic = compact
            self.last_diagnostic_at = now
        return {"diagnostic": dict(compact)}


def _dm_controller_from_payload(payload: Dict[str, Any]) -> DouyinConversationMonitor:
    sr = _sr()
    normalized = dict(payload or {})
    raw_cookies = sr._dm_cookie_text(_dm_pick_account_cookie_payload(normalized))
    account_id = str(
        normalized.get("account_id") or normalized.get("account") or normalized.get("account_name") or ""
    ).strip()
    browser_name = str(normalized.get("browser_name") or normalized.get("browser") or "edge").strip() or "edge"
    requested_account_key = str(normalized.get("account_key") or "").strip()
    if raw_cookies:
        account_key = requested_account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    else:
        account_key = requested_account_key
        if not account_key:
            raise sr.WebInputError("account_cookie or account_key is required")
        if not _dm_account_browser_profile_exists(browser_name, account_key):
            raise sr.WebInputError(f"account profile not found for {browser_name}/{account_key}")
    return DouyinConversationMonitor(
        account_id=account_id or account_key,
        account_name=str(normalized.get("account_name") or account_id or account_key).strip(),
        account_key=account_key,
        account_cookies=raw_cookies,
        browser_name=browser_name,
        headless=sr._dm_bool_text(normalized.get("headless")),
        project_id=str(normalized.get("project_id") or "").strip(),
        company_id=str(normalized.get("company_id") or "").strip(),
        source_platform=str(normalized.get("source_platform") or "抖音").strip() or "抖音",
        auto_send=sr._dm_bool_text(normalized.get("auto_send", False)),
        generate_reply=sr._dm_bool_text(
            normalized.get("generate_reply", normalized.get("auto_send", False))
        ),
        poll_seconds=sr._payload_float(normalized, "poll_seconds", 8),
        reply_backend=str(normalized.get("reply_backend") or "gpu_pool").strip().lower() or "gpu_pool",
        gpu_model=str(normalized.get("gpu_model") or DEFAULT_GPU_POOL_MODEL).strip() or DEFAULT_GPU_POOL_MODEL,
        gpu_base_url=str(normalized.get("gpu_base_url") or DEFAULT_GPU_POOL_BASE_URL).rstrip("/")
        or DEFAULT_GPU_POOL_BASE_URL,
        gpu_api_key=str(normalized.get("gpu_api_key") or DEFAULT_GPU_POOL_API_KEY),
        max_auto_replies_before_peer=int(
            normalized.get("max_auto_replies_before_peer")
            or normalized.get("max_auto_replies")
            or 2
        ),
        logic=normalized.get("_logic"),
        project_store=normalized.get("_project_store"),
    )


def build_douyin_conversation_monitor_start_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    normalized = dict(payload or {})
    normalized["_logic"] = logic
    normalized["_project_store"] = project_store
    controller = _dm_controller_from_payload(normalized)
    with _DM_ACCOUNT_CONTROLLERS_LOCK:
        existing = _DM_ACCOUNT_CONTROLLERS.get(controller.account_key)
        if existing and existing.snapshot().get("alive"):
            return {"started": False, "monitor": existing.snapshot(), "reason": "already_running"}
        _DM_ACCOUNT_CONTROLLERS[controller.account_key] = controller
    controller.start()
    return {"started": True, "monitor": controller.snapshot()}


def build_douyin_conversation_monitor_stop_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    sr = _sr()
    normalized = dict(payload or {})
    raw_cookies = sr._dm_cookie_text(_dm_pick_account_cookie_payload(normalized))
    account_id = str(
        normalized.get("account_id") or normalized.get("account") or normalized.get("account_name") or ""
    ).strip()
    account_key = str(normalized.get("account_key") or "").strip() or sr._dm_account_key_from_values(
        raw_cookies, account_id
    )
    if not account_key:
        raise sr.WebInputError("account_key or account_cookie is required")
    with _DM_ACCOUNT_CONTROLLERS_LOCK:
        controller = _DM_ACCOUNT_CONTROLLERS.get(account_key)
    if not controller:
        return {"stopped": False, "account_key": account_key, "reason": "not_found"}
    controller.stop()
    return {"stopped": True, "monitor": controller.snapshot()}


def build_douyin_conversation_monitor_list_response() -> Dict[str, Any]:
    with _DM_ACCOUNT_CONTROLLERS_LOCK:
        monitors = [controller.snapshot() for controller in _DM_ACCOUNT_CONTROLLERS.values()]
    return {
        "monitors": monitors,
        "counts": {
            "total": len(monitors),
            "running": sum(1 for item in monitors if item.get("alive") and item.get("status") == "running"),
            "error": sum(1 for item in monitors if item.get("status") == "error"),
        },
        "defaults": {
            "reply_backend": "gpu_pool",
            "gpu_model": DEFAULT_GPU_POOL_MODEL,
            "gpu_base_url": DEFAULT_GPU_POOL_BASE_URL,
            "poll_seconds": 5,
            "generate_reply": False,
            "auto_send": False,
            "max_auto_replies_before_peer": 2,
        },
    }


def find_alive_monitor_for_account(payload: Dict[str, Any]) -> Optional[DouyinConversationMonitor]:
    """Locate a running monitor for inbox reuse (by account_key or account_id)."""
    sr = _sr()
    normalized = dict(payload or {})
    raw_cookies = sr._dm_cookie_text(_dm_pick_account_cookie_payload(normalized))
    account_id = str(
        normalized.get("account_id") or normalized.get("account") or normalized.get("account_name") or ""
    ).strip()
    account_key = str(normalized.get("account_key") or "").strip()
    if raw_cookies:
        account_key = account_key or sr._dm_account_key_from_values(raw_cookies, account_id)
    with _DM_ACCOUNT_CONTROLLERS_LOCK:
        controllers = list(_DM_ACCOUNT_CONTROLLERS.values())
    if account_key:
        for controller in controllers:
            if controller.account_key == account_key and controller.snapshot().get("alive"):
                return controller
    if account_id:
        for controller in controllers:
            if str(controller.account_id or "").strip() == account_id and controller.snapshot().get("alive"):
                return controller
    return None


def build_douyin_conversation_monitor_send_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a DM on the alive conversation-monitor browser (Web process only).

    Worker processes must call this via HTTP; in-memory controllers are not shared.
    """
    sr = _sr()
    normalized = dict(payload or {})
    controller = find_alive_monitor_for_account(normalized)
    if controller is None:
        return {
            "success": False,
            "opened": False,
            "prefilled": False,
            "sent": False,
            "error": "未找到运行中的盯号浏览器",
            "failure_code": "monitor_not_alive",
            "reused_monitor": False,
            "engine": "monitor",
            "steps": [],
        }
    message = str(normalized.get("message") or normalized.get("reply") or "").strip()
    if not message:
        raise sr.WebInputError("message is required")
    profile_url = str(
        normalized.get("target_profile_url")
        or normalized.get("profile_url")
        or ""
    ).strip()
    if not profile_url:
        raise sr.WebInputError("target_profile_url is required")
    timeout_ms = int(sr._payload_float(normalized, "timeout_ms", 45000))
    auto_send = True if normalized.get("auto_send") is None else sr._dm_bool_text(normalized.get("auto_send"))
    return controller.request_send_dm(
        {
            "profile_url": profile_url,
            "target_profile_url": profile_url,
            "message": message,
            "followup_message": str(normalized.get("followup_message") or "").strip(),
            "auto_send": auto_send,
            "timeout_ms": timeout_ms,
        },
        timeout_seconds=max(30.0, timeout_ms / 1000.0 + 30.0),
    )


def build_douyin_conversation_monitor_sync_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    """Ensure watch-only monitors for the given healthy sending accounts.

    Middle platform should pass currently eligible accounts (active Cookie).
    Defaults: generate_reply=false, auto_send=false (unread monitoring only).
    """
    sr = _sr()
    normalized = dict(payload or {})
    accounts = normalized.get("accounts")
    if accounts is None and (
        normalized.get("account_cookie")
        or normalized.get("account_cookies")
        or normalized.get("account_key")
    ):
        accounts = [normalized]
    if not isinstance(accounts, list) or not accounts:
        raise sr.WebInputError("accounts is required (non-empty list)")

    shared = {
        "browser_name": str(normalized.get("browser_name") or normalized.get("browser") or "chrome").strip() or "chrome",
        "headless": normalized.get("headless", True),
        "generate_reply": normalized.get("generate_reply", False),
        "auto_send": normalized.get("auto_send", False),
        "poll_seconds": normalized.get("poll_seconds", 8),
        "project_id": normalized.get("project_id"),
        "company_id": normalized.get("company_id"),
        "source_platform": normalized.get("source_platform") or "抖音",
    }
    stop_missing = sr._dm_bool_text(normalized.get("stop_missing", False))

    started: List[Dict[str, Any]] = []
    already_running: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    desired_keys: List[str] = []

    for raw_account in accounts:
        if not isinstance(raw_account, dict):
            failed.append({"error": "account item must be an object"})
            continue
        item = dict(shared)
        item.update({k: v for k, v in raw_account.items() if v is not None})
        # Force watch-only unless caller explicitly enables reply generation.
        if "generate_reply" not in raw_account and "generate_reply" not in normalized:
            item["generate_reply"] = False
        if "auto_send" not in raw_account and "auto_send" not in normalized:
            item["auto_send"] = False
        account_label = str(
            item.get("account_id") or item.get("account_name") or item.get("account_key") or ""
        ).strip()
        try:
            result = build_douyin_conversation_monitor_start_response(
                item,
                logic=logic,
                project_store=project_store,
            )
            monitor = result.get("monitor") if isinstance(result.get("monitor"), dict) else {}
            account_key = str(monitor.get("account_key") or "").strip()
            if account_key:
                desired_keys.append(account_key)
            entry = {
                "account_id": monitor.get("account_id") or item.get("account_id") or account_label,
                "account_key": account_key,
                "started": bool(result.get("started")),
                "reason": result.get("reason") or "",
                "monitor": monitor,
            }
            if result.get("started"):
                started.append(entry)
            else:
                already_running.append(entry)
        except Exception as exc:
            failed.append({
                "account_id": account_label,
                "account_key": str(item.get("account_key") or "").strip(),
                "error": str(exc),
            })

    stopped: List[Dict[str, Any]] = []
    if stop_missing:
        desired = set(desired_keys)
        with _DM_ACCOUNT_CONTROLLERS_LOCK:
            extras = [
                controller
                for key, controller in _DM_ACCOUNT_CONTROLLERS.items()
                if key not in desired
            ]
        for controller in extras:
            snap = controller.snapshot()
            controller.stop()
            stopped.append({
                "account_id": snap.get("account_id"),
                "account_key": snap.get("account_key"),
                "stopped": True,
            })

    listing = build_douyin_conversation_monitor_list_response()
    return {
        "ok": len(failed) == 0,
        "mode": "watch_only",
        "started": started,
        "already_running": already_running,
        "failed": failed,
        "stopped": stopped,
        "monitors": listing.get("monitors") or [],
        "counts": listing.get("counts") or {},
        "desired_account_keys": desired_keys,
    }


def build_douyin_cs_probe_reply_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate one CS-style reply via GPU pool without opening a browser."""
    sr = _sr()
    normalized = dict(payload or {})
    message = str(normalized.get("message") or normalized.get("question") or "").strip()
    if not message:
        raise sr.WebInputError("message is required")
    model = str(normalized.get("gpu_model") or DEFAULT_GPU_POOL_MODEL).strip() or DEFAULT_GPU_POOL_MODEL
    base_url = str(normalized.get("gpu_base_url") or DEFAULT_GPU_POOL_BASE_URL).rstrip("/") or DEFAULT_GPU_POOL_BASE_URL
    api_key = str(normalized.get("gpu_api_key") or DEFAULT_GPU_POOL_API_KEY)
    started = time.time()
    reply = _gpu_pool_chat_reply(
        user_message=message,
        history=list(normalized.get("history") or []),
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    return {
        "reply": reply,
        "model": model,
        "base_url": base_url,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def reset_conversation_monitors_for_tests() -> None:
    """Stop and clear in-memory monitors; for unit tests only."""
    with _DM_ACCOUNT_CONTROLLERS_LOCK:
        controllers = list(_DM_ACCOUNT_CONTROLLERS.values())
        _DM_ACCOUNT_CONTROLLERS.clear()
    for controller in controllers:
        try:
            controller.stop()
        except Exception:
            pass
