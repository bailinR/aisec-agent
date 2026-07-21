#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import logging
import time

from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic
from aisec_agent.web.session_rag_chat import (
    DM_ACCOUNT_BROWSER_POOL_DEFAULT_MAX,
    DM_REDIS_AUTO_PENDING_QUEUE,
    DouyinAccountBrowserPool,
    InProcessSessionMemoryManager,
    PlaceholderKnowledgeLogic,
    SimpleLLMChatTools,
    process_douyin_dm_task_once,
)


def build_logic() -> SessionRAGChatLogic:
    return SessionRAGChatLogic(
        memory_manager=InProcessSessionMemoryManager(),
        knowledge_logic=PlaceholderKnowledgeLogic(),
        llm_tools=SimpleLLMChatTools(),
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Consume Douyin private-message tasks from Redis.")
    parser.add_argument("--mode", choices=["dry_run", "generate", "prefill", "send"], default="send")
    parser.add_argument("--queue", default=DM_REDIS_AUTO_PENDING_QUEUE, help="Redis queue name to consume.")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit.")
    parser.add_argument("--block-timeout", type=int, default=5)
    parser.add_argument("--idle-sleep", type=float, default=1.0)
    parser.add_argument("--account-browser-pool", action="store_true", help="Keep one persistent browser per account and reuse it across tasks.")
    parser.add_argument("--max-account-browsers", type=int, default=DM_ACCOUNT_BROWSER_POOL_DEFAULT_MAX)
    parser.add_argument("--account-browser", default="edge", help="Browser channel for account browser pool: edge or chrome.")
    parser.add_argument("--account-browser-headless", action="store_true", help="Run account browser pool in headless mode.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logic = build_logic()
    project_store = ProjectMaterialStore()
    account_browser_pool = None
    if args.account_browser_pool:
        account_browser_pool = DouyinAccountBrowserPool(
            max_accounts=args.max_account_browsers,
            browser_name=args.account_browser,
            headless=args.account_browser_headless,
        )
    logging.info(
        "Douyin DM worker started: mode=%s once=%s account_browser_pool=%s max_account_browsers=%s",
        args.mode,
        args.once,
        bool(account_browser_pool),
        args.max_account_browsers,
    )

    try:
        while True:
            result = process_douyin_dm_task_once(
                logic=logic,
                project_store=project_store,
                mode=args.mode,
                queue_name=args.queue,
                block_timeout=args.block_timeout,
                account_browser_pool=account_browser_pool,
            )
            if result:
                logging.info("processed task: %s", result)
            elif args.once:
                logging.info("no task")
            else:
                time.sleep(args.idle_sleep)

            if args.once:
                break
    finally:
        if account_browser_pool:
            account_browser_pool.close_all()


if __name__ == "__main__":
    main()
