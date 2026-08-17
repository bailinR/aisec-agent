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
      const texts = Array.from(document.querySelectorAll('div, section, form, button, span, p'))
        .filter(visible)
        .map((el) => normalize(el.innerText || el.textContent || ''))
        .filter(Boolean);
      const joined = texts.slice(0, 160).join(' ');
      const modal = /扫码登录|验证码登录|密码登录|登录后免费|请输入手机号|获取验证码/.test(joined);
      const loginButton = texts.some((text) => text === '登录' || text === '立即登录');
      const alreadyLoggedIn = Array.from(document.querySelectorAll('[class*="im-"], [class*="avatar"], img, button, a'))
        .some((el) => {
          if (!visible(el)) return false;
          const cls = String(el.className || '');
          const aria = String(el.getAttribute('aria-label') || '');
          return /im-entry|im-dialog|avatar|用户头像|消息/.test(cls + ' ' + aria);
        });
      const required = Boolean(modal || (loginButton && /扫码|验证码|手机号/.test(joined) && !alreadyLoggedIn));
      return {
        required,
        reason: required ? (modal ? 'login modal visible' : 'login button visible') : '',
        sampleText: joined.slice(0, 240),
        url: location.href,
        title: document.title || '',
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        data = {"required": False, "reason": str(exc)}
    if not isinstance(data, dict):
        data = {}
    return {
        "required": bool(data.get("required")),
        "reason": str(data.get("reason") or ""),
        "sample_text": str(data.get("sampleText") or ""),
        "url": str(data.get("url") or ""),
        "title": str(data.get("title") or ""),
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
                    const looksIm = /im-dialog|imContainer|imSaas|im-saas|semi-always-dark|imDark/i.test(cls) ||
                      /(消息|私信)/.test(text.slice(0, 80));
                    return looksIm &&
                      rect.left > window.innerWidth * 0.48 &&
                      rect.width >= 240 &&
                      rect.height >= 260 &&
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
    """Click the Douyin top-right 消息 entry via coordinates (JS click is flaky)."""
    try:
        target = page.evaluate(
            """
            () => {
              const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width >= 12 && rect.height >= 12 &&
                  rect.top >= 0 && rect.top < 240 &&
                  rect.left > window.innerWidth * 0.55 &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  style.opacity !== '0';
              };
              const nodes = Array.from(document.querySelectorAll(
                'a, button, [role=button], div, span, [class*=im-entry], [class*=ImEntry], [class*=uoylDJ5V]'
              ))
                .filter(visible)
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  const target = el.closest('a, button, [role=button], [class*=im-entry]') || el;
                  const text = normalize([
                    el.innerText,
                    el.textContent,
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    target.getAttribute('aria-label'),
                  ].filter(Boolean).join(' '));
                  const cls = String(el.className || '') + ' ' + String(target.className || '');
                  let score = 0;
                  if (/im-entry|ImEntry|uoylDJ5V/i.test(cls)) score += 140;
                  if (text === '消息' || text === '私信') score += 100;
                  if (/消息|私信|Message/i.test(text) && text.length <= 16) score += 70;
                  if (/\\d/.test(text) && /消息|私信|im-entry/i.test(text + cls)) score += 20;
                  if (rect.left > window.innerWidth * 0.75) score += 20;
                  if (/发布|登录|搜索|上传|充值|下载|通知|壁纸|投稿|朋友|推荐/.test(text)) score -= 80;
                  return {
                    text,
                    score,
                    x: rect.left + Math.min(rect.width / 2, 12),
                    y: rect.top + Math.min(rect.height / 2, 18),
                  };
                })
                .filter((item) => item.score >= 60)
                .sort((a, b) => b.score - a.score || b.x - a.x);
              return nodes[0] || null;
            }
            """
        )
    except Exception as exc:
        return {"clicked": False, "detail": str(exc)}
    if not isinstance(target, dict) or target.get("x") is None:
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


def _dm_open_douyin_messages_surface(page: Any, timeout_ms: int = 12000) -> List[Dict[str, Any]]:
    sr = _sr()
    steps: List[Dict[str, Any]] = []
    try:
        current_url = str(getattr(page, "url", "") or "")
        if "douyin.com" not in current_url:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=timeout_ms)
            steps.append({"name": "open_douyin_home", "ok": True, "detail": "opened home"})
        try:
            page.wait_for_timeout(min(1500, timeout_ms))
        except Exception:
            pass
        sr._dm_append_optional_step(steps, sr._dm_handle_douyin_login_save_prompt(page, timeout_ms=1500))
        login_state = _dm_detect_douyin_login_required(page)
        if login_state.get("required"):
            steps.append({
                "name": "check_login",
                "ok": False,
                "detail": "login_required: " + str(login_state.get("reason") or "login required"),
            })
            return steps
        if _dm_message_panel_visible(page):
            steps.append({"name": "detect_message_panel", "ok": True, "detail": "message panel already visible"})
            return steps
        if _dm_detect_latest_douyin_message(page).get("has_editor"):
            steps.append({"name": "detect_message_surface", "ok": True, "detail": "editor already visible"})
            return steps
        last_detail = "message entry not found"
        for attempt in range(3):
            clicked = _dm_click_top_right_message_entry(page)
            if not clicked.get("clicked"):
                last_detail = str(clicked.get("detail") or last_detail)
                continue
            steps.append({
                "name": "open_message_center",
                "ok": True,
                "detail": f"clicked DOM control (try {attempt + 1}): " + str(clicked.get("detail") or ""),
            })
            try:
                page.wait_for_timeout(1400)
            except Exception:
                time.sleep(1.2)
            if _dm_message_panel_visible(page) or _dm_detect_latest_douyin_message(page).get("has_editor"):
                steps.append({"name": "confirm_message_panel", "ok": True, "detail": "message panel visible after click"})
                return steps
            last_detail = "clicked but panel not visible"
        steps.append({"name": "open_message_center", "ok": False, "detail": last_detail})
        return steps
    except Exception as exc:
        return [{"name": "open_message_center", "ok": False, "detail": str(exc)}]


def _dm_scan_inbox_summary(page: Any) -> Dict[str, Any]:
    """Summarize Douyin private-message panel unread badges without clicking rows."""
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
      const allVisible = Array.from(document.querySelectorAll('div, section, aside, ul, li, a, button, span')).filter(isVisible);
      const panelCandidates = allVisible
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || '');
          const style = window.getComputedStyle(el);
          const rightSide = rect.left > window.innerWidth * 0.48 && rect.right > window.innerWidth * 0.72;
          const panelSize = rect.width >= 240 && rect.width <= window.innerWidth * 0.6 &&
            rect.height >= 260 && rect.height <= window.innerHeight * 0.98;
          const hasPrivateHeader = /(消息|私信)/.test(text.slice(0, 80)) ||
            /im-dialog|imContainer|imSaas|im-saas|imDark|semi-always-dark/i.test(String(el.className || ''));
          if (!rightSide || !panelSize || !hasPrivateHeader || style.visibility === 'hidden' ||
              style.display === 'none' || style.opacity === '0') return null;
          return {el, rect, text, score: 100 + rect.width - rect.height / 1000};
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score || a.rect.left - b.rect.left);
      const panel = panelCandidates[0];
      if (!panel) return {ok: false, detail: 'private message panel not found', unreadCount: 0, unreadPeople: 0};

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
          if (!numeric || !compact) return null;
          return {rect, text, count: Number(text)};
        })
        .filter(Boolean);

      // Deduplicate badges that share nearly the same center (nested nodes).
      const unique = [];
      for (const badge of badges) {
        const cx = badge.rect.left + badge.rect.width / 2;
        const cy = badge.rect.top + badge.rect.height / 2;
        const dup = unique.some((item) => {
          const ix = item.rect.left + item.rect.width / 2;
          const iy = item.rect.top + item.rect.height / 2;
          return Math.abs(ix - cx) < 12 && Math.abs(iy - cy) < 12;
        });
        if (!dup) unique.push(badge);
      }
      const unreadCount = unique.reduce((sum, item) => sum + (Number(item.count) || 0), 0);
      return {
        ok: true,
        detail: 'scanned private message badges',
        unreadCount,
        unreadPeople: unique.length,
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
    }


def _dm_click_unread_or_latest_chat(page: Any, timeout_ms: int = 5000) -> Dict[str, Any]:
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
          const rightSide = rect.left > window.innerWidth * 0.48 && rect.right > window.innerWidth * 0.72;
          const panelSize = rect.width >= 240 && rect.width <= window.innerWidth * 0.6 &&
            rect.height >= 260 && rect.height <= window.innerHeight * 0.98;
          const hasPrivateHeader = /(消息|私信)/.test(text.slice(0, 80)) ||
            /im-dialog|imContainer|imSaas|im-saas|imDark|semi-always-dark/i.test(String(el.className || ''));
          if (!rightSide || !panelSize || !hasPrivateHeader || style.visibility === 'hidden' ||
              style.display === 'none' || style.opacity === '0') return null;
          return {el, rect, text, score: 100 + rect.width - rect.height / 1000};
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
        self.logs: List[Dict[str, Any]] = []
        self.histories: Dict[str, List[Dict[str, str]]] = {}
        self.send_failed_signatures: Dict[str, int] = {}

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
        with self.lock:
            self.status = "stopping"
            self.updated_at = _sr()._dm_now()
        self.log("已请求停止监控", "info")

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
                self._tick(page)
                self.stop_event.wait(self.poll_seconds)
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
        for step in steps:
            if not step.get("ok"):
                detail = str(step.get("detail") or "")
                if "login_required" in detail:
                    login_required = True
                    login_detail = detail
                    continue
                self.log("打开消息入口未确认：" + detail, "warn", {"step": step.get("name")})
        if login_required:
            extra = self._maybe_capture_diagnostic(page, login_detail)
            with self.lock:
                self.last_error = login_detail
            self.log("账号登录态不可用，需人工扫码/更新 cookie 后再监控：" + login_detail, "warn", extra)
            return
        inbox = _dm_scan_inbox_summary(page)
        if inbox.get("ok"):
            with self.lock:
                self.inbox_unread_count = int(inbox.get("unread_count") or 0)
                self.inbox_unread_people = int(inbox.get("unread_people") or 0)
                self.inbox_summary_updated_at = sr._dm_now()
            self.log(
                f"收件箱摘要：未读{self.inbox_unread_count}，未读会话{self.inbox_unread_people}",
                "info",
            )
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
        signature = f"{detect.get('url')}|{message[-180:]}"
        pending_retry = signature in self.send_failed_signatures and self.auto_send and self.generate_reply
        if signature == self.last_seen_signature and not pending_retry:
            return
        with self.lock:
            self.last_seen_signature = signature
            self.last_message = message
            self.updated_at = sr._dm_now()
        self.log("识别到新消息：" + message[:120], "ok")
        if self.last_reply and message.strip() == self.last_reply.strip():
            self.log("忽略与最近自动回复相同的文本，避免自回环", "info")
            return
        if not self.generate_reply:
            with self.lock:
                self.last_reply = ""
            self.log("已记录新私信；回复生成已关闭", "info")
            return
        peer = ""
        if click_step.get("ok"):
            peer = str(click_step.get("detail") or "").split(" ")[0].strip()
        peer_key = peer or "default"
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
            self.send_failed_signatures.pop(signature, None)
            # Avoid treating the just-sent reply as a new inbound next loop.
            self.last_seen_signature = f"{detect.get('url')}|{reply[-180:]}"
        self.log("已自动回复：" + reply[:120], "ok", {"steps": send_steps, "peer": peer_key})

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
        auto_send=sr._dm_bool_text(normalized.get("auto_send", True)),
        generate_reply=sr._dm_bool_text(normalized.get("generate_reply", True)),
        poll_seconds=sr._payload_float(normalized, "poll_seconds", 8),
        reply_backend=str(normalized.get("reply_backend") or "gpu_pool").strip().lower() or "gpu_pool",
        gpu_model=str(normalized.get("gpu_model") or DEFAULT_GPU_POOL_MODEL).strip() or DEFAULT_GPU_POOL_MODEL,
        gpu_base_url=str(normalized.get("gpu_base_url") or DEFAULT_GPU_POOL_BASE_URL).rstrip("/")
        or DEFAULT_GPU_POOL_BASE_URL,
        gpu_api_key=str(normalized.get("gpu_api_key") or DEFAULT_GPU_POOL_API_KEY),
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
            "generate_reply": True,
            "auto_send": True,
        },
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
