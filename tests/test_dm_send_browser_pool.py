# -*- coding: utf-8 -*-
"""Tests for DM send browser pool: max browsers, same-account queue, idle close."""

import threading
import time

from aisec_agent.web import session_rag_chat as sr
from aisec_agent.worker.dm_account_browser_pool import DmAccountBrowserPool


def test_task_runtime_pool_assigns_per_account_profile(monkeypatch):
    monkeypatch.setattr(sr, "DM_SEND_BROWSER_POOL", True)
    fields = sr._dm_task_submit_runtime_fields({
        "account_cookie": "sessionid=abc",
        "account_id": "acct-1",
        "target_profile_url": "https://www.douyin.com/user/x",
        "headless": True,
    })
    assert fields["keep_browser_open"] == "true"
    assert fields["persistent_context"] == "true"
    assert fields["account_key"]
    assert "accounts" in fields["user_data_dir"].replace("\\", "/")
    assert fields["account_key"] in fields["user_data_dir"]


def test_task_runtime_pool_skipped_for_alive_monitor(monkeypatch):
    monkeypatch.setattr(sr, "DM_SEND_BROWSER_POOL", True)
    fields = sr._dm_task_submit_runtime_fields({
        "account_cookie": "sessionid=abc",
        "account_id": "acct-2",
        "use_alive_monitor": True,
        "headless": True,
    })
    assert fields["use_alive_monitor"] == "true"
    assert fields["keep_browser_open"] == "false"
    assert fields["persistent_context"] == "false"


def test_enforce_max_open_browsers_lru(monkeypatch):
    monkeypatch.setattr(sr, "DM_MAX_OPEN_BROWSERS", 2)
    sessions = sr._dm_playwright_sessions()
    sessions.clear()
    sessions["a|1"] = {"last_used": 1.0, "context": object()}
    sessions["b|2"] = {"last_used": 2.0, "context": object()}

    dropped = []

    def _fake_drop(key):
        dropped.append(key)
        sessions.pop(key, None)

    monkeypatch.setattr(sr, "_dm_drop_playwright_session", _fake_drop)
    monkeypatch.setattr(sr, "_dm_cleanup_closed_playwright_sessions", lambda: 0)
    monkeypatch.setattr(sr, "_dm_persistent_context_alive", lambda ctx: True)

    sr._dm_enforce_max_open_browsers(retain_key="c|3")
    assert dropped == ["a|1"]
    assert "a|1" not in sessions
    assert "b|2" in sessions


def test_account_pool_same_account_queues_and_max_slots():
    processed = []
    lock = threading.Lock()
    started = threading.Event()

    def process_task(task_id: str):
        with lock:
            processed.append((threading.current_thread().name, task_id))
        started.set()
        time.sleep(0.05)
        return {"task_id": task_id}

    closes = []

    pool = DmAccountBrowserPool(
        max_browsers=2,
        idle_close_seconds=30,
        process_task=process_task,
        close_browsers=lambda: closes.append("close"),
        enable_inline_playwright=lambda _enabled: None,
    )

    assert pool.try_dispatch("acct-a", "t1") is True
    assert pool.try_dispatch("acct-a", "t2") is True  # same account queues
    assert pool.try_dispatch("acct-b", "t3") is True
    assert pool.try_dispatch("acct-c", "t4") is False  # third distinct account blocked
    assert pool.active_accounts == 2

    started.wait(timeout=2)
    deadline = time.time() + 2
    while time.time() < deadline:
        with lock:
            if len(processed) >= 3:
                break
        time.sleep(0.02)

    with lock:
        # t1 and t2 must run on the same account worker (serialized).
        a_names = {name for name, tid in processed if tid in {"t1", "t2"}}
        assert len(a_names) == 1
        assert {"t1", "t2"}.issubset({tid for _, tid in processed})
        assert "t3" in {tid for _, tid in processed}

    pool.shutdown(wait=True, timeout=3)


def test_account_pool_idle_closes_slot():
    closes = []

    pool = DmAccountBrowserPool(
        max_browsers=1,
        idle_close_seconds=0.4,
        process_task=lambda task_id: {"task_id": task_id},
        close_browsers=lambda: closes.append(time.time()),
        enable_inline_playwright=lambda _enabled: None,
    )
    assert pool.try_dispatch("acct-x", "only") is True
    deadline = time.time() + 3
    while time.time() < deadline and pool.active_accounts > 0:
        time.sleep(0.05)
    assert pool.active_accounts == 0
    assert closes, "idle timeout should close browsers"
