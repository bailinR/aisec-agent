#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import logging
import time

from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic
from aisec_agent.web.session_rag_chat import (
    DM_REDIS_AUTO_PENDING_QUEUE,
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
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logic = build_logic()
    project_store = ProjectMaterialStore()
    logging.info("Douyin DM worker started: mode=%s once=%s", args.mode, args.once)

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


if __name__ == "__main__":
    main()
