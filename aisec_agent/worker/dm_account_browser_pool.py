#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Per-account Douyin DM send pool: max N browsers, same-account queue, idle close."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


ProcessTaskFn = Callable[[str], Optional[Dict[str, Any]]]
CloseBrowsersFn = Callable[[], None]
EnableInlineFn = Callable[[bool], None]


@dataclass
class _AccountSlot:
    account_key: str
    task_queue: "queue.Queue[Optional[str]]" = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None
    last_used: float = field(default_factory=time.time)


class DmAccountBrowserPool:
    """
    Dispatch send tasks to at most `max_browsers` account worker threads.

    - Different accounts open different browsers (one sticky thread each).
    - Same account tasks share one thread queue (serialized).
    - After `idle_close_seconds` with no tasks, the thread closes browsers and frees the slot.
    """

    def __init__(
        self,
        *,
        max_browsers: int = 3,
        idle_close_seconds: float = 180.0,
        process_task: ProcessTaskFn,
        close_browsers: CloseBrowsersFn,
        enable_inline_playwright: EnableInlineFn,
    ) -> None:
        self.max_browsers = max(1, int(max_browsers or 1))
        self.idle_close_seconds = max(0.2, float(idle_close_seconds if idle_close_seconds is not None else 180.0))
        self._process_task = process_task
        self._close_browsers = close_browsers
        self._enable_inline = enable_inline_playwright
        self._slots: Dict[str, _AccountSlot] = {}
        self._lock = threading.Lock()

    @property
    def active_accounts(self) -> int:
        with self._lock:
            return len(self._slots)

    def try_dispatch(self, account_key: str, task_id: str) -> bool:
        """Queue task for account. Returns False if no free browser slot for a new account."""
        key = str(account_key or "default").strip() or "default"
        tid = str(task_id or "").strip()
        if not tid:
            return True

        with self._lock:
            slot = self._slots.get(key)
            if slot is not None:
                slot.last_used = time.time()
                slot.task_queue.put(tid)
                return True
            if len(self._slots) >= self.max_browsers:
                return False
            slot = _AccountSlot(account_key=key)
            self._slots[key] = slot
            thread = threading.Thread(
                target=self._account_worker,
                name=f"dm-send-{key[:24]}",
                args=(key,),
                daemon=True,
            )
            slot.thread = thread
            thread.start()
            slot.task_queue.put(tid)
            return True

    def _account_worker(self, account_key: str) -> None:
        self._enable_inline(True)
        try:
            while True:
                with self._lock:
                    slot = self._slots.get(account_key)
                if slot is None:
                    return
                try:
                    task_id = slot.task_queue.get(timeout=self.idle_close_seconds)
                except queue.Empty:
                    logger.info(
                        "DM browser idle timeout for account=%s after %.0fs; closing browser",
                        account_key,
                        self.idle_close_seconds,
                    )
                    try:
                        self._close_browsers()
                    except Exception:
                        logger.exception("failed closing browsers for account=%s", account_key)
                    with self._lock:
                        current = self._slots.get(account_key)
                        if current is slot and current.task_queue.empty():
                            self._slots.pop(account_key, None)
                        elif current is slot:
                            # New task arrived while closing; keep slot and continue.
                            continue
                    return

                if task_id is None:
                    try:
                        self._close_browsers()
                    except Exception:
                        pass
                    with self._lock:
                        self._slots.pop(account_key, None)
                    return

                with self._lock:
                    if account_key in self._slots:
                        self._slots[account_key].last_used = time.time()
                try:
                    result = self._process_task(task_id)
                    logger.info("DM pool processed account=%s task=%s result=%s", account_key, task_id, result)
                except Exception:
                    logger.exception("DM pool failed account=%s task=%s", account_key, task_id)
        finally:
            self._enable_inline(False)

    def shutdown(self, wait: bool = False, timeout: float = 5.0) -> None:
        with self._lock:
            slots = list(self._slots.values())
            for slot in slots:
                slot.task_queue.put(None)
        if not wait:
            return
        deadline = time.time() + max(0.0, timeout)
        for slot in slots:
            thread = slot.thread
            if thread is None or not thread.is_alive():
                continue
            remaining = max(0.0, deadline - time.time())
            thread.join(timeout=remaining)
