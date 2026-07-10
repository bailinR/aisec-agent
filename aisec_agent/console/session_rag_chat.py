#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import uuid

from aisec_agent.logic.llm_presets import resolve_provider_config
from aisec_agent.logic.session_rag_chat import DEFAULT_USER_ID, SessionRAGChatLogic


EXIT_WORDS = {"exit", "quit", "q"}


def parse_topics(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local session-aware RAG chat console.")
    parser.add_argument("--provider", choices=["default", "minimax", "deepseek"], default="default",
                        help="LLM provider preset. Use minimax or deepseek for third-party APIs.")
    parser.add_argument("--topics", default="", help="Comma-separated knowledge topics.")
    parser.add_argument("--session-id", default="", help="Session id. Generated when omitted.")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="User id for memory isolation.")
    parser.add_argument("--model-name", default=None, help="Model name override.")
    parser.add_argument("--url", default=None, help="LLM endpoint override.")
    parser.add_argument("--key", default=None, help="LLM API key override.")
    parser.add_argument("--func-name", default=None, help="LLM function override, for example ollama_chat.")
    parser.add_argument("--max-len-input", type=int, default=None, help="Max prompt length override.")
    return parser


def resolve_provider_args(args) -> dict:
    config = resolve_provider_config(
        provider=args.provider if args.provider != "default" else "custom",
        url=args.url,
        key=args.key,
        func_name=args.func_name,
        model_name=args.model_name,
        max_len_input=args.max_len_input,
        use_env=True,
    )
    if args.provider == "minimax" and not config["key"]:
        raise SystemExit("MiniMax provider requires an API key. Pass --key or set MINIMAX_API_KEY.")
    if args.provider == "deepseek" and not config["key"]:
        raise SystemExit("DeepSeek provider requires an API key. Pass --key or set DEEPSEEK_API_KEY.")

    return {
        "url": config["url"],
        "key": config["key"],
        "func_name": config["func_name"],
        "model_name": config["model_name"],
        "max_len_input": config["max_len_input"],
    }


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    provider_conf = resolve_provider_args(args)
    topics = parse_topics(args.topics)
    session_id = args.session_id or uuid.uuid4().hex

    print(f"session_id: {session_id}")
    print(f"user_id: {args.user_id}")
    if args.provider != "default":
        print(f"provider: {args.provider}; model: {provider_conf['model_name']}")
    if topics:
        print(f"topics: {', '.join(topics)}")
    else:
        print("topics: none; knowledge search will be skipped.")
    print("type exit, quit, or q to stop.")

    logic = SessionRAGChatLogic()
    while True:
        try:
            user_input = input("\nuser> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if user_input.lower() in EXIT_WORDS:
            print("bye")
            break
        if not user_input:
            continue

        result = logic.chat_once(
            user_input=user_input,
            session_id=session_id,
            user_id=args.user_id,
            topics=topics,
            url=provider_conf["url"],
            key=provider_conf["key"],
            func_name=provider_conf["func_name"],
            model_name=provider_conf["model_name"],
            max_len_input=provider_conf["max_len_input"],
        )
        if result.second_pass:
            print(f"\n[second_pass triggered] missing_info: {result.missing_info}")
        if result.memory_error:
            print(f"\n[memory warning] {result.memory_error}")
        print(f"\nai> {result.answer}")


if __name__ == "__main__":
    main()
