# -*- coding: utf-8 -*-
"""Reply-pool / unread / reply-detection audit log (aisec side).

Records middle-platform calls to monitors / inbox / conversations/read / tasks,
plus the actual detection payloads (unread_conversations, has_reply, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("aisec_agent.reply_pool_audit")

_FILE_HANDLER_LOCK = threading.Lock()
_FILE_HANDLER_READY = False

REPLY_POOL_AUDIT_ENABLED = str(os.getenv("AISEC_REPLY_POOL_AUDIT_LOG", "1")).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
REPLY_POOL_AUDIT_REDIS_KEY = os.getenv("AISEC_REPLY_POOL_AUDIT_REDIS_KEY", "reply_pool:audit:entries")
REPLY_POOL_AUDIT_MAX_ENTRIES = max(100, int(os.getenv("AISEC_REPLY_POOL_AUDIT_MAX_ENTRIES", "2000") or 2000))
REPLY_POOL_AUDIT_TTL_SECONDS = max(0, int(os.getenv("AISEC_REPLY_POOL_AUDIT_TTL_SECONDS", str(7 * 24 * 3600)) or 0))
REPLY_POOL_AUDIT_TEXT_LIMIT = max(32, int(os.getenv("AISEC_REPLY_POOL_AUDIT_TEXT_LIMIT", "240") or 240))
REPLY_POOL_AUDIT_CONV_LIMIT = max(1, int(os.getenv("AISEC_REPLY_POOL_AUDIT_CONV_LIMIT", "30") or 30))
REPLY_POOL_AUDIT_RESULT_LIMIT = max(1, int(os.getenv("AISEC_REPLY_POOL_AUDIT_RESULT_LIMIT", "40") or 40))
REPLY_POOL_AUDIT_MONITOR_LIMIT = max(1, int(os.getenv("AISEC_REPLY_POOL_AUDIT_MONITOR_LIMIT", "40") or 40))

# path suffix (normalized) -> action
_PATH_ACTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("/conversation-monitors/sync", "POST", "monitor_sync"),
    ("/conversation-monitors/start", "POST", "monitor_start"),
    ("/conversation-monitors/stop", "POST", "monitor_stop"),
    ("/conversation-monitors/probe-reply", "POST", "monitor_probe_reply"),
    ("/conversation-monitors", "GET", "monitor_list"),
    ("/accounts/inbox", "POST", "account_inbox"),
    ("/accounts/verify", "POST", "account_verify"),
    ("/conversations/read/batch", "POST", "conversation_read_batch"),
    ("/conversations/read", "POST", "conversation_read"),
    ("/private-message/tasks", "POST", "task_submit"),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_log_path() -> Path:
    override = str(os.getenv("AISEC_REPLY_POOL_AUDIT_LOG_FILE") or "").strip()
    if override:
        return Path(override)
    return _project_root() / "runtime" / "logs" / "reply-pool-audit.log"


def _ensure_file_logger() -> None:
    global _FILE_HANDLER_READY
    if _FILE_HANDLER_READY:
        return
    with _FILE_HANDLER_LOCK:
        if _FILE_HANDLER_READY:
            return
        if str(os.getenv("AISEC_REPLY_POOL_AUDIT_FILE", "1")).strip().lower() not in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }:
            _FILE_HANDLER_READY = True
            return
        try:
            log_path = _default_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = TimedRotatingFileHandler(
                str(log_path),
                when="D",
                backupCount=14,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOGGER.setLevel(logging.INFO)
            LOGGER.addHandler(handler)
            LOGGER.propagate = False
        except Exception as exc:
            logging.getLogger("aisec_agent.http").debug("reply_pool file logger init failed: %s", exc)
        _FILE_HANDLER_READY = True


def _clip(value: Any, limit: int = REPLY_POOL_AUDIT_TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def _payload_data(response_data: Any) -> Dict[str, Any]:
    if not isinstance(response_data, dict):
        return {}
    nested = response_data.get("data")
    if isinstance(nested, dict) and (("ok" in response_data) or ("code" in response_data)):
        return nested
    return response_data


def classify_reply_pool_action(method: str, path: str) -> Optional[str]:
    method_u = str(method or "").upper().strip()
    path_text = str(path or "").split("?", 1)[0].rstrip("/")
    if not path_text or "private-message" not in path_text:
        # allow verify / inbox under private-message only
        if "/douyin/private-message/" not in path_text and "/api/v1/douyin/private-message" not in path_text:
            if "/api/douyin/private-message" not in path_text:
                return None
    for suffix, want_method, action in _PATH_ACTIONS:
        if method_u != want_method:
            continue
        if path_text.endswith(suffix.rstrip("/")) or path_text.endswith(suffix):
            # avoid /tasks/{id} matching task_submit
            if action == "task_submit":
                if path_text.endswith("/private-message/tasks") or path_text.endswith("/private-message/tasks/"):
                    return action
                continue
            if action == "conversation_read" and path_text.endswith("/conversations/read/batch"):
                continue
            if action == "monitor_list" and "/conversation-monitors/" in path_text:
                continue
            return action
    return None


def _summarize_conversation(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "peer_nickname": _clip(item.get("peer_nickname"), 80),
        "last_message": _clip(item.get("last_message"), 160),
        "unread_count": int(item.get("unread_count") or 0),
        "conversation_id": _clip(item.get("conversation_id"), 120),
        "peer_sec_uid": _clip(item.get("peer_sec_uid"), 80),
        "peer_profile_url": _clip(item.get("peer_profile_url"), 160),
        "updated_at": _clip(item.get("updated_at"), 40),
    }


def _summarize_conversations(items: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return rows
    for item in items[:REPLY_POOL_AUDIT_CONV_LIMIT]:
        if isinstance(item, dict):
            rows.append(_summarize_conversation(item))
    return rows


def _account_ids_from_body(body: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(body, dict):
        return []
    ids: List[str] = []
    single = str(body.get("account_id") or body.get("account") or body.get("account_name") or "").strip()
    if single:
        ids.append(single)
    accounts = body.get("accounts")
    if isinstance(accounts, list):
        for item in accounts:
            if not isinstance(item, dict):
                continue
            label = str(item.get("account_id") or item.get("account_name") or item.get("account") or "").strip()
            if label and label not in ids:
                ids.append(label)
    return ids[:REPLY_POOL_AUDIT_MONITOR_LIMIT]


def _request_summary(action: str, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    summary: Dict[str, Any] = {}
    for key in (
        "account_id",
        "account_name",
        "account_key",
        "browser_name",
        "browser",
        "headless",
        "poll_seconds",
        "stop_missing",
        "reused_monitor",
        "task_id",
        "lead_id",
        "item_id",
        "target_profile_url",
        "sec_uid",
        "max_items",
        "generate_reply",
        "auto_send",
    ):
        if key in body and body.get(key) is not None and str(body.get(key)).strip() != "":
            if any(token in key for token in ("cookie", "token", "secret", "password")):
                continue
            summary[key] = _clip(body.get(key), 160)
    if "message" in body and str(body.get("message") or "").strip():
        summary["message"] = _clip(body.get("message"), 160)
    if "expected_outgoing" in body and str(body.get("expected_outgoing") or "").strip():
        summary["expected_outgoing"] = _clip(body.get("expected_outgoing"), 160)
    accounts = body.get("accounts")
    if isinstance(accounts, list):
        summary["accounts_count"] = len(accounts)
        summary["account_ids"] = _account_ids_from_body(body)
    items = body.get("items")
    if isinstance(items, list):
        summary["items_count"] = len(items)
        refs: List[str] = []
        for item in items[:REPLY_POOL_AUDIT_RESULT_LIMIT]:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("lead_id") or item.get("item_id") or item.get("task_id") or "").strip()
            if ref:
                refs.append(_clip(ref, 80))
        if refs:
            summary["item_refs"] = refs
    if action == "task_submit":
        summary.setdefault("account_ids", _account_ids_from_body(body))
    return summary


def _detection_for_inbox(payload: Dict[str, Any]) -> Dict[str, Any]:
    conversations = _summarize_conversations(payload.get("unread_conversations"))
    return {
        "ok": bool(payload.get("ok", True)),
        "failure_code": _clip(payload.get("failure_code"), 80),
        "requires_login": bool(payload.get("requires_login")),
        "requires_verification": bool(payload.get("requires_verification")),
        "reused_monitor": bool(payload.get("reused_monitor")),
        "inbox_unread_count": int(payload.get("inbox_unread_count") or payload.get("unread_count") or 0),
        "inbox_unread_people": int(payload.get("inbox_unread_people") or payload.get("unread_people") or 0),
        "unread_conversations": conversations,
        "unread_conversations_count": len(conversations)
        if conversations
        else len(payload.get("unread_conversations") or []),
        "message": _clip(payload.get("message"), 200),
        "browser": _clip(payload.get("browser") or payload.get("resolved_browser"), 40),
    }


def _detection_for_monitor_list(payload: Dict[str, Any]) -> Dict[str, Any]:
    monitors_out: List[Dict[str, Any]] = []
    monitors = payload.get("monitors") if isinstance(payload.get("monitors"), list) else []
    total_unread_people = 0
    total_conversations = 0
    for item in monitors[:REPLY_POOL_AUDIT_MONITOR_LIMIT]:
        if not isinstance(item, dict):
            continue
        conversations = _summarize_conversations(item.get("unread_conversations"))
        people = int(item.get("inbox_unread_people") or item.get("unread_people") or len(conversations) or 0)
        total_unread_people += people
        total_conversations += len(conversations)
        monitors_out.append(
            {
                "account_id": _clip(item.get("account_id") or item.get("account_name"), 80),
                "account_key": _clip(item.get("account_key"), 80),
                "alive": bool(item.get("alive")),
                "status": _clip(item.get("status"), 40),
                "inbox_unread_count": int(item.get("inbox_unread_count") or item.get("unread_count") or 0),
                "inbox_unread_people": people,
                "unread_conversations": conversations,
                "last_error": _clip(item.get("last_error"), 160),
            }
        )
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "counts": {
            "total": int(counts.get("total") or len(monitors)),
            "running": int(counts.get("running") or 0),
            "error": int(counts.get("error") or 0),
        },
        "total_unread_people": total_unread_people,
        "total_unread_conversations": total_conversations,
        "monitors": monitors_out,
        "monitors_truncated": len(monitors) > len(monitors_out),
    }


def _detection_for_monitor_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    def _rows(key: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "account_id": _clip(item.get("account_id") or item.get("account_name"), 80),
                    "account_key": _clip(item.get("account_key"), 80),
                    "status": _clip(item.get("status"), 40),
                    "alive": item.get("alive"),
                    "error": _clip(item.get("error") or item.get("message"), 160),
                    "failure_code": _clip(item.get("failure_code"), 80),
                }
            )
            if len(rows) >= REPLY_POOL_AUDIT_MONITOR_LIMIT:
                break
        return rows

    return {
        "started": _rows("started"),
        "already_running": _rows("already_running"),
        "failed": _rows("failed"),
        "stopped": _rows("stopped") if isinstance(payload.get("stopped"), list) else [],
        "started_count": len(payload.get("started") or []),
        "already_running_count": len(payload.get("already_running") or []),
        "failed_count": len(payload.get("failed") or []),
    }


def _detection_for_monitor_control(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "account_id": _clip(payload.get("account_id") or payload.get("account_name"), 80),
        "account_key": _clip(payload.get("account_key"), 80),
        "status": _clip(payload.get("status"), 40),
        "alive": payload.get("alive"),
        "message": _clip(payload.get("message") or payload.get("last_message"), 200),
        "failure_code": _clip(payload.get("failure_code"), 80),
        "inbox_unread_count": int(payload.get("inbox_unread_count") or payload.get("unread_count") or 0)
        if payload.get("inbox_unread_count") is not None or payload.get("unread_count") is not None
        else None,
        "unread_conversations": _summarize_conversations(payload.get("unread_conversations")),
    }


def _detection_for_read(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok", True)),
        "has_reply": bool(payload.get("has_reply")),
        "outgoing_found": bool(payload.get("outgoing_found")),
        "latest_peer_reply": _clip(payload.get("latest_peer_reply"), 200),
        "peer_replies_count": len(payload.get("peer_replies") or [])
        if isinstance(payload.get("peer_replies"), list)
        else 0,
        "failure_code": _clip(payload.get("failure_code"), 80),
        "message": _clip(payload.get("message"), 200),
        "delivery_verified": bool(payload.get("delivery_verified")),
        "peer_nickname": _clip(payload.get("peer_nickname"), 80),
        "target_profile_url": _clip(payload.get("target_profile_url") or payload.get("profile_url"), 160),
    }


def _detection_for_read_batch(payload: Dict[str, Any]) -> Dict[str, Any]:
    results_out: List[Dict[str, Any]] = []
    for item in (payload.get("results") or [])[:REPLY_POOL_AUDIT_RESULT_LIMIT]:
        if not isinstance(item, dict):
            continue
        results_out.append(
            {
                "ref": _clip(item.get("ref") or item.get("lead_id") or item.get("item_id") or item.get("task_id"), 80),
                "lead_id": _clip(item.get("lead_id"), 80),
                "item_id": _clip(item.get("item_id"), 80),
                "task_id": _clip(item.get("task_id"), 80),
                "ok": bool(item.get("ok")),
                "has_reply": bool(item.get("has_reply")),
                "outgoing_found": bool(item.get("outgoing_found")),
                "latest_peer_reply": _clip(item.get("latest_peer_reply"), 160),
                "failure_code": _clip(item.get("failure_code"), 80),
                "message": _clip(item.get("message"), 160),
            }
        )
    replied_refs = [row["ref"] for row in results_out if row.get("has_reply") and row.get("ref")]
    return {
        "ok": bool(payload.get("ok", True)),
        "total": int(payload.get("total") or len(payload.get("results") or [])),
        "replied": int(payload.get("replied") or 0),
        "no_reply": int(payload.get("no_reply") or 0),
        "failed": int(payload.get("failed") or 0),
        "replied_refs": replied_refs,
        "results": results_out,
        "results_truncated": len(payload.get("results") or []) > len(results_out),
    }


def _detection_for_task_submit(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "accepted": payload.get("accepted"),
        "task_id": _clip(payload.get("task_id"), 80),
        "task_ids": [_clip(x, 80) for x in (payload.get("task_ids") or [])[:20]]
        if isinstance(payload.get("task_ids"), list)
        else None,
        "status": _clip(payload.get("status") or payload.get("queue_status"), 40),
        "queue_status": _clip(payload.get("queue_status"), 40),
        "failure_code": _clip(payload.get("failure_code") or payload.get("error_code"), 80),
        "message": _clip(payload.get("message") or payload.get("error"), 200),
    }


def _detection_for_verify(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok", True)),
        "alive": payload.get("alive"),
        "account_type": _clip(payload.get("account_type") or payload.get("type"), 40),
        "nickname": _clip(payload.get("nickname") or payload.get("account_name"), 80),
        "requires_login": bool(payload.get("requires_login")),
        "requires_verification": bool(payload.get("requires_verification")),
        "failure_code": _clip(payload.get("failure_code"), 80),
        "message": _clip(payload.get("message"), 200),
        "browser": _clip(payload.get("browser") or payload.get("resolved_browser"), 40),
    }


def build_reply_pool_audit_entry(
    *,
    action: str,
    method: str,
    path: str,
    status: int,
    request_id: str,
    timestamp: str,
    elapsed_ms: int,
    remote_ip: str,
    body: Optional[Dict[str, Any]],
    response_data: Any,
) -> Dict[str, Any]:
    payload = _payload_data(response_data)
    account_ids = _account_ids_from_body(body)
    if not account_ids and isinstance(payload, dict):
        label = str(payload.get("account_id") or payload.get("account_name") or "").strip()
        if label:
            account_ids = [label]
        elif isinstance(payload.get("monitors"), list):
            for mon in payload["monitors"]:
                if isinstance(mon, dict):
                    mid = str(mon.get("account_id") or mon.get("account_name") or "").strip()
                    if mid and mid not in account_ids:
                        account_ids.append(mid)
                if len(account_ids) >= REPLY_POOL_AUDIT_MONITOR_LIMIT:
                    break

    if action == "account_inbox":
        detection = _detection_for_inbox(payload)
    elif action == "monitor_list":
        detection = _detection_for_monitor_list(payload)
    elif action == "monitor_sync":
        detection = _detection_for_monitor_sync(payload)
    elif action in {"monitor_start", "monitor_stop", "monitor_probe_reply"}:
        detection = _detection_for_monitor_control(payload)
    elif action == "conversation_read":
        detection = _detection_for_read(payload)
    elif action == "conversation_read_batch":
        detection = _detection_for_read_batch(payload)
    elif action == "task_submit":
        detection = _detection_for_task_submit(payload)
    elif action == "account_verify":
        detection = _detection_for_verify(payload)
    else:
        detection = {"keys": sorted(str(k) for k in payload.keys())[:40]}

    ok = True
    if isinstance(response_data, dict) and "ok" in response_data:
        ok = bool(response_data.get("ok"))
    elif "ok" in payload:
        ok = bool(payload.get("ok"))
    if int(status) >= 400:
        ok = False

    return {
        "request_id": request_id,
        "timestamp": timestamp,
        "elapsed_ms": int(elapsed_ms),
        "remote_ip": _clip(remote_ip, 64),
        "action": action,
        "method": str(method or "").upper(),
        "path": _clip(path, 200),
        "status": int(status),
        "ok": ok,
        "account_ids": account_ids,
        "account_id": account_ids[0] if account_ids else "",
        "failure_code": _clip(
            (
                (detection.get("failure_code") if isinstance(detection, dict) else "")
                or payload.get("failure_code")
                or (response_data.get("error") if isinstance(response_data, dict) else "")
            ),
            80,
        ),
        "request": _request_summary(action, body),
        "detection": detection,
    }


def _redis_conn(redis_client: Optional[Any] = None) -> Any:
    if redis_client is not None:
        return redis_client
    from aisec_agent.web.session_rag_chat import _redis_conn as sr_redis

    return sr_redis()


def store_reply_pool_audit_entry(entry: Dict[str, Any], redis_client: Optional[Any] = None) -> None:
    if not REPLY_POOL_AUDIT_ENABLED or not isinstance(entry, dict):
        return
    try:
        redis_conn = _redis_conn(redis_client)
        payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        redis_conn.lpush(REPLY_POOL_AUDIT_REDIS_KEY, payload)
        redis_conn.ltrim(REPLY_POOL_AUDIT_REDIS_KEY, 0, REPLY_POOL_AUDIT_MAX_ENTRIES - 1)
        if REPLY_POOL_AUDIT_TTL_SECONDS > 0:
            redis_conn.expire(REPLY_POOL_AUDIT_REDIS_KEY, REPLY_POOL_AUDIT_TTL_SECONDS)
    except Exception as exc:
        LOGGER.debug("reply_pool audit redis store failed: %s", exc)
    try:
        _ensure_file_logger()
        # one-line human summary + compact JSON for grep
        account = entry.get("account_id") or ",".join(entry.get("account_ids") or []) or "-"
        detection = entry.get("detection") if isinstance(entry.get("detection"), dict) else {}
        highlight = ""
        action = str(entry.get("action") or "")
        if action in {"account_inbox", "monitor_list"}:
            if action == "account_inbox":
                highlight = (
                    f"unread_people={detection.get('inbox_unread_people')} "
                    f"conversations={detection.get('unread_conversations_count')}"
                )
            else:
                highlight = (
                    f"monitors={((detection.get('counts') or {}).get('total'))} "
                    f"unread_people={detection.get('total_unread_people')} "
                    f"conversations={detection.get('total_unread_conversations')}"
                )
        elif action == "conversation_read":
            highlight = f"has_reply={detection.get('has_reply')} outgoing_found={detection.get('outgoing_found')}"
        elif action == "conversation_read_batch":
            highlight = (
                f"total={detection.get('total')} replied={detection.get('replied')} "
                f"no_reply={detection.get('no_reply')} failed={detection.get('failed')}"
            )
        elif action == "monitor_sync":
            highlight = (
                f"started={detection.get('started_count')} "
                f"running={detection.get('already_running_count')} "
                f"failed={detection.get('failed_count')}"
            )
        elif action == "task_submit":
            highlight = f"task_id={detection.get('task_id')} accepted={detection.get('accepted')}"
        LOGGER.info(
            "%s %s status=%s ok=%s account=%s %s | %s",
            entry.get("method"),
            entry.get("action"),
            entry.get("status"),
            entry.get("ok"),
            account,
            highlight,
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        logging.getLogger("aisec_agent.http").debug("reply_pool audit file log failed: %s", exc)


def maybe_record_reply_pool_audit(
    *,
    method: str,
    path: str,
    status: int,
    request_id: str,
    timestamp: str,
    elapsed_ms: int,
    remote_ip: str,
    body: Optional[Dict[str, Any]],
    response_data: Any,
    redis_client: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    if not REPLY_POOL_AUDIT_ENABLED:
        return None
    action = classify_reply_pool_action(method, path)
    if not action:
        return None
    entry = build_reply_pool_audit_entry(
        action=action,
        method=method,
        path=path,
        status=status,
        request_id=request_id,
        timestamp=timestamp,
        elapsed_ms=elapsed_ms,
        remote_ip=remote_ip,
        body=body,
        response_data=response_data,
    )
    store_reply_pool_audit_entry(entry, redis_client=redis_client)
    return entry


def build_reply_pool_audit_log_list_response(
    redis_client: Optional[Any] = None,
    limit: int = 50,
    offset: int = 0,
    action: str = "",
    account_id: str = "",
) -> Dict[str, Any]:
    from aisec_agent.web.session_rag_chat import _dm_decode_scalar

    redis_conn = _redis_conn(redis_client)
    limit = max(1, min(int(limit or 50), REPLY_POOL_AUDIT_MAX_ENTRIES))
    offset = max(0, int(offset or 0))
    action_filter = str(action or "").strip()
    account_filter = str(account_id or "").strip()

    # When filtering, scan a larger window then slice.
    scan_end = offset + limit - 1
    if action_filter or account_filter:
        scan_end = min(REPLY_POOL_AUDIT_MAX_ENTRIES - 1, offset + limit * 20)
    raw_entries = redis_conn.lrange(REPLY_POOL_AUDIT_REDIS_KEY, 0, scan_end)
    entries: List[Dict[str, Any]] = []
    skipped = 0
    for raw in raw_entries or []:
        try:
            item = json.loads(_dm_decode_scalar(raw))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if action_filter and str(item.get("action") or "") != action_filter:
            continue
        if account_filter:
            ids = [str(x) for x in (item.get("account_ids") or [])]
            if account_filter not in ids and str(item.get("account_id") or "") != account_filter:
                continue
        if skipped < offset:
            skipped += 1
            continue
        entries.append(item)
        if len(entries) >= limit:
            break
    return {
        "entries": entries,
        "count": int(redis_conn.llen(REPLY_POOL_AUDIT_REDIS_KEY)),
        "limit": limit,
        "offset": offset,
        "action": action_filter,
        "account_id": account_filter,
        "key": REPLY_POOL_AUDIT_REDIS_KEY,
        "log_file": str(_default_log_path()),
    }


def build_reply_pool_audit_log_clear_response(redis_client: Optional[Any] = None) -> Dict[str, Any]:
    redis_conn = _redis_conn(redis_client)
    deleted = 0
    try:
        deleted = int(redis_conn.delete(REPLY_POOL_AUDIT_REDIS_KEY) or 0)
    except Exception:
        deleted = 0
    return {
        "cleared": True,
        "deleted_keys": deleted,
        "key": REPLY_POOL_AUDIT_REDIS_KEY,
    }
