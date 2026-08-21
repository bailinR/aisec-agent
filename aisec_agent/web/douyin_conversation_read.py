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
_LAST_BUBBLE_COLLECT_DETAIL = ""


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
    text = str(value or "")
    # Unify common punctuation variants seen across Douyin DOM vs stored copy.
    for src, dst in (
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\uff1f", "?"),
        ("\uff0c", ","),
        ("\u3002", "."),
        ("\uff01", "!"),
        ("\u2026", "..."),
        ("\u00a0", " "),
    ):
        text = text.replace(src, dst)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(已读|未读)$", "", text).strip()
    # Hover actions appended to bubble text in Douyin DOM.
    text = re.sub(r"(?:\s*(?:点赞|回复|删除|复制|举报|转发))+\s*$", "", text).strip()
    text = re.sub(
        r"^(昨天|今天|\d{1,2}月\d{1,2}日)?\s*\d{1,2}:\d{2}\s*",
        "",
        text,
    ).strip()
    text = re.sub(r"^(\d+分钟前|\d+秒前|刚刚)\s*", "", text).strip()
    return text


def _looks_like_inbox_chrome(text: str) -> bool:
    value = _normalize_text(text)
    if not value:
        return True
    chrome_tokens = (
        "交流群", "群主", "管理员", "修改群名", "日期筛选",
        "感谢您在我们账号下留言", "家庭农场", "下载电脑客户端",
    )
    if any(token in value for token in chrome_tokens):
        return True
    # Pure action / menu labels scraped as bubbles.
    if re.fullmatch(r"(点赞|回复|删除|复制|举报|转发)(\s+(点赞|回复|删除|复制|举报|转发))*", value):
        return True
    # Contaminated rows where action labels dominate short text.
    action_hits = len(re.findall(r"点赞|回复|删除|复制|举报|转发", str(text or "")))
    if action_hits >= 2 and len(value) <= 12:
        return True
    # Douyin suggested-reply chips / drawer chrome frequently scraped as peers.
    if value in {"你好呀", "你好", "您好", "在吗", "在的", "哈喽", "私信"}:
        return True
    # Mega node that concatenates several bubbles + actions.
    if action_hits >= 2 and ("效果怎么样" in str(text or "") or len(str(text or "")) > 80):
        return True
    return False


def _text_matches(candidate: Any, needle: Any) -> bool:
    haystack = _normalize_text(candidate)
    target = _normalize_text(needle)
    if not haystack or not target:
        return False
    if haystack == target or target in haystack or haystack in target:
        return True
    # Long templates are often truncated in the DOM; match head/tail chunks.
    for size in (48, 32, 24, 16):
        head = target[:size]
        if len(head) >= 12 and head in haystack:
            if len(target) <= size + 8:
                return True
            tail = target[-min(24, max(8, len(target) // 4)) :]
            if (not tail) or (tail in haystack) or (target[size : size + 24] in haystack):
                return True
    # Significant overlapping window for mid-truncated bubbles.
    if len(target) >= 24:
        window = target[8:40]
        if window and window in haystack:
            return True
    return False


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


def _bubble_center_x(item: Dict[str, Any]) -> float:
    try:
        x = float(item.get("x") or 0)
        width = float(item.get("width") or 0)
    except (TypeError, ValueError):
        return 0.0
    if width > 0:
        return x + width / 2.0
    return x


def _reclassify_roles_relative_to_anchor(
    messages: List[Dict[str, Any]],
    anchor_idx: int,
) -> List[Dict[str, Any]]:
    """Fix left/right mislabels using geometry relative to our outgoing bubble.

    Douyin peer bubbles sit left of our blue self bubble. When midX is wrong,
    peer replies (e.g. 效果怎么样) get tagged as self and are missed.
    """
    if anchor_idx < 0 or anchor_idx >= len(messages):
        return messages
    items = [dict(item) for item in messages]
    anchor = items[anchor_idx]
    anchor_cx = _bubble_center_x(anchor)
    if anchor_cx <= 0:
        items[anchor_idx]["role"] = "self"
        return items
    for idx, item in enumerate(items):
        cx = _bubble_center_x(item)
        if cx <= 0:
            continue
        if idx == anchor_idx or _text_matches(item.get("text"), anchor.get("text")):
            item["role"] = "self"
            continue
        # Clearly left of our outgoing bubble → peer; clearly right/same → self.
        if cx <= anchor_cx - 36:
            item["role"] = "peer"
        elif cx >= anchor_cx + 36:
            item["role"] = "self"
    items[anchor_idx]["role"] = "self"
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
    anchor_idx = -1
    match_idxs: List[int] = []
    if needle:
        for idx, item in enumerate(annotated):
            if _text_matches(item.get("text"), needle):
                annotated[idx]["role"] = "self"
                match_idxs.append(idx)
                outgoing_found = True
        if match_idxs:
            # Prefer the rightmost match (true chat self bubble over inbox preview).
            anchor_idx = max(
                match_idxs,
                key=lambda i: (_bubble_center_x(annotated[i]), i),
            )
        if not outgoing_found:
            head = needle[:16]
            for idx in range(len(annotated) - 1, -1, -1):
                item = annotated[idx]
                bubble = _normalize_text(item.get("text"))
                if head and head in bubble:
                    outgoing_found = True
                    anchor_idx = idx
                    annotated[idx]["role"] = "self"
                    break
    else:
        outgoing_found = any(str(item.get("role") or "") == "self" for item in annotated)
        for idx in range(len(annotated) - 1, -1, -1):
            if str(annotated[idx].get("role") or "") == "self":
                anchor_idx = idx
                break

    if outgoing_found and anchor_idx >= 0:
        annotated = _reclassify_roles_relative_to_anchor(annotated, anchor_idx)

    peer_replies: List[str] = []
    if outgoing_found and anchor_idx >= 0:
        for item in annotated[anchor_idx + 1 :]:
            if str(item.get("role") or "") != "peer":
                continue
            raw = item.get("text")
            # Chrome check must use raw text; normalize strips action labels first.
            if _looks_like_inbox_chrome(raw):
                continue
            text = _normalize_text(raw)
            if not text:
                continue
            # Skip echoes of our own outgoing template.
            if needle and _text_matches(text, needle):
                continue
            peer_replies.append(text)
    elif not needle:
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
        if latest:
            resolved_message = ""
        else:
            read_receipt = False
            if anchor_idx >= 0:
                read_receipt = str(annotated[anchor_idx].get("read_status") or "") == "read"
            if read_receipt:
                resolved_message = "已读取会话，对方已读，暂无新回复"
            else:
                resolved_message = "已读取会话，暂无新回复"

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
    global _LAST_BUBBLE_COLLECT_DETAIL
    # Prefer the top-level page; also probe same-origin frames if the drawer is framed.
    targets = [page]
    try:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            url = str(getattr(frame, "url", "") or "")
            if "douyin.com" in url or url.startswith("about:") or not url:
                targets.append(frame)
    except Exception:
        targets = [page]

    best: List[Dict[str, Any]] = []
    last_detail = ""
    for target in targets:
        found, detail = _collect_conversation_bubbles_on_target(target)
        if detail:
            last_detail = detail
        if len(found) > len(best):
            best = found
        if best:
            break
        found = _collect_conversation_bubbles_screen_fallback(target)
        if found:
            best = found
            last_detail = f"{last_detail}|screen_fallback={len(found)}"
            break
    _LAST_BUBBLE_COLLECT_DETAIL = last_detail
    if not best and last_detail:
        print(f"_collect_conversation_bubbles empty: {last_detail}")
    return best


def _collect_conversation_bubbles_screen_fallback(page: Any) -> List[Dict[str, Any]]:
    """Collect bubbles from the right-side DM drawer using viewport geometry."""
    script = """
    () => {
      const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 28 && rect.height >= 16 && rect.right > 0 && rect.bottom > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
      };
      const editors = Array.from(document.querySelectorAll(
        '[contenteditable="true"], [contenteditable], textarea, [role="textbox"], .public-DraftEditor-content'
      )).filter(isVisible);
      const scoredEditors = editors.map((el) => {
        const rect = el.getBoundingClientRect();
        let score = 0;
        if (rect.left > window.innerWidth * 0.48) score += 80;
        if (rect.top > window.innerHeight * 0.4) score += 30;
        return {el, rect, score};
      }).filter((item) => item.score >= 50).sort((a, b) => b.score - a.score);
      // Bias midX to the right so white/left peer bubbles are not tagged as self.
      let drawerLeft = window.innerWidth * 0.50;
      if (scoredEditors.length) {
        drawerLeft = Math.max(drawerLeft, scoredEditors[0].rect.left - 40);
      }
      const midX = drawerLeft + Math.max(120, (window.innerWidth - drawerLeft) * 0.55);
      const inEditor = (el) => editors.some((editor) => editor === el || editor.contains(el) || el.contains(editor));
      const raw = [];
      for (const el of document.querySelectorAll('div, span, p, li, article')) {
        if (!isVisible(el) || inEditor(el)) continue;
        let text = normalize(el.innerText || el.textContent || '');
        if (!text || text.length < 1 || text.length > 2000) continue;
        const rect = el.getBoundingClientRect();
        if (rect.left < drawerLeft - 8 || rect.top < 90) continue;
        if (rect.bottom > window.innerHeight - 56) continue;
        if (rect.width > window.innerWidth * 0.95 || rect.height > window.innerHeight * 0.75) continue;
        if (el.childElementCount > 24) continue;
        if (/发送|表情|按住说话|说点什么|输入消息|搜索|下载客户端|实时接收好友消息/.test(text)) continue;
        if (/抖音精选|记录美好生活|推荐|关注|商城|发布作品|相互关注/.test(text)) continue;
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
        text = text.replace(/^(?:\\d+分钟前|\\d+秒前|刚刚)\\s*/, '').trim();
        text = text.replace(/(?:\\s*(?:点赞|回复|删除|复制|举报|转发))+$/g, '').trim();
        if (!text) continue;
        if (/^(点赞|回复|删除|复制|举报|转发)(\\s+(点赞|回复|删除|复制|举报|转发))*$/.test(text)) continue;

        const cx = rect.left + rect.width / 2;
        let role = '';
        if (cx > midX + 8) role = 'self';
        else if (cx < midX - 8) role = 'peer';
        if (!role) continue;
        raw.push({
          text: text.slice(0, 800),
          role,
          read_status: readStatus,
          x: Math.round(rect.left),
          y: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
      raw.sort((a, b) => a.y - b.y || a.x - b.x || (a.width * a.height) - (b.width * b.height));
      const unique = [];
      for (const item of raw) {
        const dupIdx = unique.findIndex((prev) => prev.role === item.role && Math.abs(prev.y - item.y) <= 18 &&
          (prev.text === item.text || prev.text.includes(item.text) || item.text.includes(prev.text)));
        if (dupIdx < 0) unique.push(item);
        else if (item.text.length > unique[dupIdx].text.length) unique[dupIdx] = item;
      }
      unique.sort((a, b) => a.y - b.y || a.x - b.x);
      return {bubbles: unique};
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception:
        return []
    bubbles = data.get("bubbles") if isinstance(data, dict) else []
    result: List[Dict[str, Any]] = []
    for item in bubbles or []:
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


def _collect_conversation_bubbles_on_target(page: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Collect bubbles from one page/frame target."""
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
      if (!editorNodes.length) return {ok: false, detail: 'editor_not_found', bubbles: []};
      const editor = editorNodes[0].el;
      const editorRect = editorNodes[0].rect;
      // Prefer the right-side DM drawer, not the whole page (body midX mislabels roles).
      let panelRoot = editor.closest(
        '[class*="conversation"], [class*="Conversation"], [class*="message"], [class*="Message"], [class*="chat"], [class*="Chat"], [class*="im-"], [class*="Im"], [class*="dialog"], aside, section'
      );
      if (!panelRoot) {
        let node = editor.parentElement;
        while (node && node !== document.body) {
          const rect = node.getBoundingClientRect();
          if (rect.width >= 280 && rect.width <= Math.max(520, window.innerWidth * 0.6) && rect.left > window.innerWidth * 0.35) {
            panelRoot = node;
            break;
          }
          node = node.parentElement;
        }
      }
      panelRoot = panelRoot || editor.parentElement || document.body;
      const panelRect = panelRoot.getBoundingClientRect ? panelRoot.getBoundingClientRect() : editorRect;
      const midX = panelRect.left + Math.max(80, panelRect.width * 0.45);
      const inEditor = (el) => editorNodes.some((node) => node.el === el || node.el.contains(el) || el.contains(node.el));

      const raw = [];
      let skipped = {belowEditor: 0, header: 0, outside: 0, fullWidth: 0, children: 0, chrome: 0, noRole: 0, short: 0};
      for (const el of panelRoot.querySelectorAll('div, span, p, li')) {
        if (!isVisible(el) || inEditor(el)) continue;
        let text = normalize(el.innerText || el.textContent || '');
        if (!text || text.length < 1 || text.length > 2000) { skipped.short += 1; continue; }
        const rect = el.getBoundingClientRect();
        if (rect.bottom >= editorRect.top - 2) { skipped.belowEditor += 1; continue; }
        if (rect.top < panelRect.top + 12) { skipped.header += 1; continue; }
        if (rect.left < panelRect.left - 24 || rect.right > panelRect.right + 24) { skipped.outside += 1; continue; }
        if (rect.width > panelRect.width * 0.995 && text.length > 120) { skipped.fullWidth += 1; continue; }
        if (el.childElementCount > 24) { skipped.children += 1; continue; }
        if (/发送|表情|按住说话|说点什么|输入消息|搜索|下载客户端|实时接收好友消息/.test(text)) { skipped.chrome += 1; continue; }
        if (/抖音精选|记录美好生活|推荐|关注|商城|发布作品/.test(text)) { skipped.chrome += 1; continue; }
        if (/^\\d{1,2}:\\d{2}$/.test(text) || /^(昨天|周一|周二|周三|周四|周五|周六|周日|刚刚|\\d+分钟前|\\d+秒前)$/.test(text)) { skipped.chrome += 1; continue; }

        let readStatus = null;
        if (/\\s已读$/.test(text) || text.endsWith('已读')) {
          readStatus = 'read';
          text = text.replace(/\\s*已读$/, '').trim();
        } else if (/\\s未读$/.test(text) || text.endsWith('未读')) {
          readStatus = 'unread';
          text = text.replace(/\\s*未读$/, '').trim();
        }
        text = text.replace(/^(昨天|今天|\\d{1,2}月\\d{1,2}日)\\s+\\d{1,2}:\\d{2}\\s*/, '').trim();
        text = text.replace(/^(?:\\d+分钟前|\\d+秒前|刚刚)\\s*/, '').trim();
        text = text.replace(/(?:\\s*(?:点赞|回复|删除|复制|举报|转发))+$/g, '').trim();
        if (!text) continue;
        if (/^(点赞|回复|删除|复制|举报|转发)(\\s+(点赞|回复|删除|复制|举报|转发))*$/.test(text)) continue;

        const cx = rect.left + rect.width / 2;
        let role = '';
        if (cx > midX + 8) role = 'self';
        else if (cx < midX - 8) role = 'peer';
        else role = '';
        if (!role) { skipped.noRole += 1; continue; }

        raw.push({
          text: text.slice(0, 800),
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
          // Prefer longer text when nested so truncated outer wrappers win less often.
          for (let i = 0; i < unique.length; i += 1) {
            const prev = unique[i];
            if (prev.role === item.role && Math.abs(prev.y - item.y) <= 18 &&
                (prev.text.includes(item.text) || item.text.includes(prev.text))) {
              if (item.text.length > prev.text.length) unique[i] = item;
              break;
            }
          }
        }
      }
      unique.sort((a, b) => a.y - b.y || a.x - b.x);
      return {
        ok: true,
        detail: `bubbles=${unique.length};raw=${raw.length};panel=${Math.round(panelRect.left)},${Math.round(panelRect.width)};mid=${Math.round(midX)};editor=${Math.round(editorRect.left)},${Math.round(editorRect.top)};skip=${JSON.stringify(skipped)}`,
        bubbles: unique,
      };
    }
    """
    try:
        data = page.evaluate(script) or {}
    except Exception as exc:
        return [], f"evaluate_failed:{exc}"
    if not isinstance(data, dict):
        return [], "bad_payload"
    detail = str(data.get("detail") or "")
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
    return result, detail


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
    # Callers (comment-kit) may force a one-shot cookie browser to avoid clashing
    # with conversation-monitors that already hold the persistent profile.
    if "persistent_context" in payload:
        use_persistent = sr._dm_bool_text(payload.get("persistent_context"))
    else:
        use_persistent = bool(account_key)
    if "keep_browser_open" in payload:
        keep_browser_open = sr._dm_bool_text(payload.get("keep_browser_open"))
    else:
        keep_browser_open = False

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
        "keep_browser_open": keep_browser_open,
        "persistent_context": use_persistent,
        "timeout_ms": timeout_ms,
        "slow_mo": int(sr._payload_float(payload, "slow_mo", 80)),
        "viewport_width": sr.DM_DEFAULT_VIEWPORT_WIDTH,
        "viewport_height": sr.DM_DEFAULT_VIEWPORT_HEIGHT,
        "page_zoom_percent": sr.DM_DEFAULT_PAGE_ZOOM_PERCENT,
    }
    if use_persistent and account_key:
        options["user_data_dir"] = str(_dm_account_browser_user_data_dir(browser_name, account_key))
        if not raw_cookies and not _dm_account_browser_profile_exists(browser_name, account_key):
            raise sr.WebInputError(f"account profile not found for {browser_name}/{account_key}")

    lock = _account_lock(account_key or sr._dm_account_key_from_values(raw_cookies, account_id or target_url))
    with lock:
        playwright = None
        context = None
        browser = None
        page = None
        manager = None
        try:
            if use_persistent and options.get("user_data_dir"):
                # Never reuse `_DM_PLAYWRIGHT_SESSIONS` here: ThreadingHTTPServer
                # serves each request on a different thread, and Playwright sync
                # API is greenlet-bound ("cannot switch to a different thread").
                from pathlib import Path
                from aisec_agent.web.douyin_conversation_monitor import (
                    _launch_account_persistent_context,
                )

                session_key = (
                    f"{str(browser_name or 'chrome').lower()}"
                    f"|{Path(str(options['user_data_dir'])).expanduser().resolve()}"
                )
                stale = sr._DM_PLAYWRIGHT_SESSIONS.pop(session_key, None)
                if stale:
                    sr._dm_stop_playwright_context(
                        stale.get("context"),
                        None,
                        stale.get("playwright"),
                    )
                playwright, context, manager = _launch_account_persistent_context(
                    browser_name, options
                )
            else:
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
                sr._dm_click_profile_private_message(page, timeout_ms=min(timeout_ms, 15000))
            except RuntimeError as exc:
                login_abort = sr._dm_abort_on_login_requirement(page, steps, stage="open_private_message")
                if login_abort:
                    detail = ""
                    if isinstance(login_abort, dict):
                        detail = str(login_abort.get("detail") or login_abort.get("message") or "")
                    return _failure_result("login_required", detail or "账号登录态不可用，需更新 Cookie")
                # One more attempt after a short settle — profile chrome is often slow.
                time.sleep(1.2)
                try:
                    sr._dm_click_profile_private_message(page, timeout_ms=min(timeout_ms, 15000))
                except RuntimeError:
                    code, msg = _map_exception_failure(exc)
                    return _failure_result(code, msg)

            time.sleep(1.0)
            # Wait until the right-side composer appears; otherwise re-click 私信.
            for wait_i in range(8):
                try:
                    sr._dm_visible_message_editor(page, timeout_ms=1500)
                    has_editor = True
                    break
                except Exception:
                    has_editor = False
                    if wait_i in {2, 5}:
                        try:
                            sr._dm_click_profile_private_message(page, timeout_ms=8000)
                        except Exception:
                            pass
                    time.sleep(0.6)

            # Wait for composer / bubbles; Douyin drawer can take several seconds.
            bubbles: List[Dict[str, Any]] = []
            last_collect_detail = ""
            for attempt in range(20):
                bubbles = _collect_conversation_bubbles(page)
                if bubbles:
                    break
                detect = {}
                try:
                    from aisec_agent.web.douyin_conversation_monitor import _dm_detect_latest_douyin_message

                    detect = _dm_detect_latest_douyin_message(page)
                except Exception:
                    detect = {}
                has_editor = bool(detect.get("has_editor")) or has_editor
                if attempt in {6, 12} and not has_editor:
                    try:
                        sr._dm_click_profile_private_message(page, timeout_ms=8000)
                    except Exception:
                        pass
                if attempt in {10, 19}:
                    try:
                        _, last_collect_detail = _collect_conversation_bubbles_on_target(page)
                        last_collect_detail = (
                            f"{last_collect_detail}|global={_LAST_BUBBLE_COLLECT_DETAIL}"
                        )
                    except Exception as exc:
                        last_collect_detail = str(exc)
                time.sleep(0.55)

            if not bubbles:
                collect_detail = last_collect_detail
                try:
                    probe = page.evaluate(
                        """() => {
                          const editors = Array.from(document.querySelectorAll('[contenteditable],[role=textbox],textarea')).length;
                          return {href: location.href, editors, title: document.title||''};
                        }"""
                    ) or {}
                    collect_detail = f"{collect_detail}|probe={probe}"
                except Exception:
                    pass
                if not has_editor:
                    detect = {}
                    try:
                        from aisec_agent.web.douyin_conversation_monitor import _dm_detect_latest_douyin_message

                        detect = _dm_detect_latest_douyin_message(page)
                    except Exception:
                        detect = {}
                    if not detect.get("has_editor"):
                        return _failure_result("conversation_not_found", "未进入私信会话或未找到消息面板")
                result = build_reply_detection_result([], expected_outgoing, ok=True)
                result["task_id"] = str(payload.get("task_id") or "").strip()
                result["target_profile_url"] = target_url
                result["account_key"] = account_key
                result["account_cookie_loaded"] = bool(cookies)
                result["account_cookie_count"] = len(cookies)
                result["message"] = "已进入会话，但未采集到气泡"
                result["failure_code"] = "bubbles_empty"
                result["collect_detail"] = collect_detail
                return result

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
            # Always tear down: conversation-read must not leave a Playwright
            # context bound to a dead HTTP worker thread.
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            try:
                if playwright is not None:
                    playwright.stop()
            except Exception:
                pass
            manager = None


def build_douyin_conversation_read_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """API entry: detect peer reply for one outgoing private message."""
    normalized = dict(payload or {})
    return _run_conversation_read_playwright(normalized)


def build_douyin_conversation_read_batch_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Detect replies for multiple recent outgoing messages under one sending account.

    Middle platform should pass items ordered by recent send time (newest first).
    """
    sr = _sr()
    normalized = dict(payload or {})
    items = normalized.get("items")
    if not isinstance(items, list) or not items:
        raise sr.WebInputError("items is required (non-empty list)")

    max_items = int(sr._payload_float(normalized, "max_items", 10))
    max_items = max(1, min(20, max_items))
    selected = [item for item in items if isinstance(item, dict)][:max_items]
    if not selected:
        raise sr.WebInputError("items must contain objects")

    shared_cookie = (
        normalized.get("account_cookie")
        or normalized.get("account_cookies")
        or normalized.get("cookie")
        or normalized.get("cookies")
        or ""
    )
    shared_key = str(normalized.get("account_key") or "").strip()
    shared_account_id = str(normalized.get("account_id") or normalized.get("account") or "").strip()
    browser_name = str(normalized.get("browser_name") or normalized.get("browser") or "chrome").strip() or "chrome"
    headless = True if normalized.get("headless") is None else sr._dm_bool_text(normalized.get("headless"))

    results: List[Dict[str, Any]] = []
    for index, item in enumerate(selected):
        one = {
            "account_cookie": item.get("account_cookie") or item.get("account_cookies") or shared_cookie,
            "account_key": str(item.get("account_key") or shared_key).strip(),
            "account_id": str(item.get("account_id") or shared_account_id).strip(),
            "browser_name": str(item.get("browser_name") or item.get("browser") or browser_name).strip() or browser_name,
            "headless": headless if item.get("headless") is None else sr._dm_bool_text(item.get("headless")),
            "target_profile_url": item.get("target_profile_url") or item.get("profile_url") or "",
            "sec_uid": item.get("sec_uid") or "",
            "expected_outgoing": (
                item.get("expected_outgoing")
                or item.get("message")
                or item.get("message_content")
                or ""
            ),
            "task_id": item.get("task_id") or item.get("provider_task_id") or "",
            "timeout_ms": item.get("timeout_ms") or normalized.get("timeout_ms"),
        }
        ref = str(
            item.get("item_id")
            or item.get("lead_id")
            or item.get("task_id")
            or item.get("provider_task_id")
            or index
        ).strip()
        try:
            detected = build_douyin_conversation_read_response(one)
            results.append({
                "ref": ref,
                "item_id": item.get("item_id"),
                "lead_id": item.get("lead_id"),
                "task_id": one.get("task_id") or None,
                "ok": bool(detected.get("ok")),
                "has_reply": bool(detected.get("has_reply")),
                "outgoing_found": bool(detected.get("outgoing_found")),
                "latest_peer_reply": detected.get("latest_peer_reply") or "",
                "peer_replies": list(detected.get("peer_replies") or []),
                "failure_code": detected.get("failure_code") or "",
                "message": detected.get("message") or "",
                "delivery_verified": bool(detected.get("delivery_verified")),
            })
        except sr.WebInputError as exc:
            results.append({
                "ref": ref,
                "item_id": item.get("item_id"),
                "lead_id": item.get("lead_id"),
                "task_id": one.get("task_id") or None,
                "ok": False,
                "has_reply": False,
                "outgoing_found": False,
                "latest_peer_reply": "",
                "peer_replies": [],
                "failure_code": "invalid_request",
                "message": str(exc),
                "delivery_verified": False,
            })
        except Exception as exc:
            results.append({
                "ref": ref,
                "item_id": item.get("item_id"),
                "lead_id": item.get("lead_id"),
                "task_id": one.get("task_id") or None,
                "ok": False,
                "has_reply": False,
                "outgoing_found": False,
                "latest_peer_reply": "",
                "peer_replies": [],
                "failure_code": "browser_error",
                "message": str(exc),
                "delivery_verified": False,
            })

    replied = sum(1 for row in results if row.get("has_reply"))
    return {
        "ok": True,
        "total": len(results),
        "replied": replied,
        "no_reply": sum(1 for row in results if row.get("ok") and row.get("outgoing_found") and not row.get("has_reply")),
        "failed": sum(1 for row in results if not row.get("ok")),
        "results": results,
    }


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
