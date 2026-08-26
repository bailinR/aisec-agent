#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import logging
import time

from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic
from aisec_agent.web.session_rag_chat import (
    DM_BROWSER_IDLE_CLOSE_MS,
    DM_MAX_OPEN_BROWSERS,
    DM_REDIS_AUTO_PENDING_QUEUE,
    DM_SEND_BROWSER_POOL,
    InProcessSessionMemoryManager,
    PlaceholderKnowledgeLogic,
    SimpleLLMChatTools,
    _dm_account_key_from_values,
    _dm_decode_scalar,
    _dm_enable_inline_playwright,
    _dm_redis_hash_all,
    _dm_reset_all_playwright_sessions,
    _dm_task_key,
    _dm_task_submit_runtime_fields,
    _redis_conn,
    process_douyin_dm_task_once,
    reconcile_douyin_dm_queue_state,
)
from aisec_agent.worker.dm_account_browser_pool import DmAccountBrowserPool


def build_logic() -> SessionRAGChatLogic:
    return SessionRAGChatLogic(
        memory_manager=InProcessSessionMemoryManager(),
        knowledge_logic=PlaceholderKnowledgeLogic(),
        llm_tools=SimpleLLMChatTools(),
    )


def _peek_task_account_key(redis_conn, task_id: str) -> str:
    task = _dm_redis_hash_all(redis_conn, _dm_task_key(task_id))
    if not task:
        return "default"
    key = str(task.get("account_key") or "").strip()
    if key:
        return key
    fields = _dm_task_submit_runtime_fields(task)
    return str(fields.get("account_key") or _dm_account_key_from_values(
        str(task.get("account_cookie") or ""),
        str(task.get("account_id") or ""),
    ) or "default")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Consume Douyin private-message tasks from Redis.")
    parser.add_argument("--mode", choices=["dry_run", "generate", "prefill", "send"], default="send")
    parser.add_argument("--queue", default=DM_REDIS_AUTO_PENDING_QUEUE, help="Redis queue name to consume.")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit.")
    parser.add_argument("--block-timeout", type=int, default=5)
    parser.add_argument("--idle-sleep", type=float, default=1.0)
    parser.add_argument(
        "--max-browsers",
        type=int,
        default=DM_MAX_OPEN_BROWSERS,
        help="Max concurrent account browsers (default from AISEC_DM_MAX_OPEN_BROWSERS).",
    )
    parser.add_argument(
        "--browser-idle-seconds",
        type=float,
        default=max(5.0, DM_BROWSER_IDLE_CLOSE_MS / 1000.0),
        help="Close account browser after this idle time with no tasks.",
    )
    parser.add_argument(
        "--no-browser-pool",
        action="store_true",
        help="Disable multi-account browser pool; process tasks serially.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logic = build_logic()
    project_store = ProjectMaterialStore()
    repair = reconcile_douyin_dm_queue_state()
    if repair["processing_removed"]:
        logging.info("cleaned stale terminal DM queue references: %s", repair)

    use_pool = DM_SEND_BROWSER_POOL and not args.no_browser_pool and args.mode == "send"
    logging.info(
        "Douyin DM worker started: mode=%s once=%s pool=%s max_browsers=%s idle_s=%.0f",
        args.mode,
        args.once,
        use_pool,
        args.max_browsers,
        args.browser_idle_seconds,
    )

    if not use_pool:
        while True:
            result = process_douyin_dm_task_once(
                logic=logic,
                project_store=project_store,
                mode=args.mode,
                queue_name=args.queue,
                block_timeout=args.block_timeout,
            )
            if result:
                logging.info("processed task: %s", result)
            elif args.once:
                logging.info("no task")
            else:
                time.sleep(args.idle_sleep)
            if args.once:
                break
        return

    redis_conn = _redis_conn()

    def _process_one(task_id: str):
        return process_douyin_dm_task_once(
            logic=logic,
            project_store=project_store,
            mode=args.mode,
            task_id=task_id,
            block_timeout=0,
        )

    pool = DmAccountBrowserPool(
        max_browsers=args.max_browsers,
        idle_close_seconds=args.browser_idle_seconds,
        process_task=_process_one,
        close_browsers=_dm_reset_all_playwright_sessions,
        enable_inline_playwright=_dm_enable_inline_playwright,
    )

    processed = 0
    try:
        while True:
            popped = redis_conn.blpop(args.queue, timeout=args.block_timeout) if args.block_timeout else None
            if not popped:
                if args.once:
                    logging.info("no task")
                    break
                time.sleep(args.idle_sleep)
                continue

            _, raw_task_id = popped
            task_id = _dm_decode_scalar(raw_task_id)
            account_key = _peek_task_account_key(redis_conn, task_id)
            if not pool.try_dispatch(account_key, task_id):
                # All browser slots busy with other accounts; put task back and wait.
                redis_conn.rpush(args.queue, task_id)
                logging.info(
                    "browser slots full (%s); requeued task=%s account=%s",
                    pool.active_accounts,
                    task_id,
                    account_key,
                )
                time.sleep(max(0.2, float(args.idle_sleep)))
                if args.once:
                    break
                continue

            processed += 1
            logging.info("dispatched task=%s account=%s active=%s", task_id, account_key, pool.active_accounts)
            if args.once:
                # Give the account thread a moment to finish the single task.
                time.sleep(0.5)
                break
    finally:
        pool.shutdown(wait=args.once, timeout=30.0)


if __name__ == "__main__":
    main()
