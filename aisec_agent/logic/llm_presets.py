#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, Mapping, Optional


MINIMAX_ANTHROPIC_BASE_URL = "https://api.minimaxi.com/anthropic"
MINIMAX_CHAT_COMPLETIONS_URL = "https://api.minimax.io/v1/chat/completions"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"

PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "minimax": {
        "label": "MiniMax",
        "url": MINIMAX_ANTHROPIC_BASE_URL,
        "func_name": "minimax_anthropic_chat",
        "model_name": MINIMAX_DEFAULT_MODEL,
        "key_env": "MINIMAX_API_KEY",
        "requires_key": True,
    },
    "minimax_openai": {
        "label": "MiniMax OpenAI",
        "url": MINIMAX_CHAT_COMPLETIONS_URL,
        "func_name": "minimax_chat",
        "model_name": MINIMAX_DEFAULT_MODEL,
        "key_env": "MINIMAX_API_KEY",
        "requires_key": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "url": DEEPSEEK_CHAT_COMPLETIONS_URL,
        "func_name": "deepseek_chat",
        "model_name": DEEPSEEK_DEFAULT_MODEL,
        "key_env": "DEEPSEEK_API_KEY",
        "requires_key": True,
    },
    "openai_compatible": {
        "label": "OpenAI compatible",
        "url": "",
        "func_name": "openai_chat",
        "model_name": "",
        "key_env": "OPENAI_API_KEY",
        "requires_key": False,
    },
    "ollama": {
        "label": "Ollama",
        "url": "http://localhost:11434/api/chat",
        "func_name": "ollama_chat",
        "model_name": "",
        "key_env": "",
        "requires_key": False,
    },
    "custom": {
        "label": "Custom",
        "url": "",
        "func_name": "",
        "model_name": "",
        "key_env": "",
        "requires_key": False,
    },
}


def normalize_anthropic_messages_url(url: str) -> str:
    clean_url = (url or "").strip().rstrip("/")
    if not clean_url:
        return clean_url
    if clean_url.endswith("/v1/messages"):
        return clean_url
    if clean_url.endswith("/anthropic"):
        return f"{clean_url}/v1/messages"
    if clean_url.endswith("/anthropic/v1") or clean_url.endswith("/v1"):
        return f"{clean_url}/messages"
    return clean_url


def public_provider_presets() -> Dict[str, Dict[str, Any]]:
    return {
        name: {
            key: value
            for key, value in preset.items()
            if key not in {"key_env"}
        }
        for name, preset in PROVIDER_PRESETS.items()
    }


def resolve_provider_config(
    provider: str = "custom",
    url: Optional[str] = None,
    key: Optional[str] = None,
    func_name: Optional[str] = None,
    model_name: Optional[str] = None,
    max_len_input: Optional[int] = None,
    use_env: bool = False,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    preset = PROVIDER_PRESETS.get(provider) or {}
    env = environ if environ is not None else os.environ

    resolved_key = key
    if resolved_key is None and use_env and preset.get("key_env"):
        resolved_key = env.get(preset["key_env"])

    return {
        "provider": provider,
        "url": url or preset.get("url"),
        "key": resolved_key,
        "func_name": func_name or preset.get("func_name"),
        "model_name": model_name or preset.get("model_name"),
        "max_len_input": max_len_input,
        "requires_key": bool(preset.get("requires_key")),
    }
