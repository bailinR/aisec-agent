#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import cgi
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aisec_agent.logic.llm_presets import (
    normalize_anthropic_messages_url,
    public_provider_presets,
    resolve_provider_config,
)
from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.logic.session_rag_chat import (
    DEFAULT_USER_ID,
    SessionRAGChatLogic,
    SessionRAGChatResult,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7860
STATIC_DIR = Path(__file__).with_name("static")
HTML_FILE = STATIC_DIR / "session_rag_chat.html"
FILE_PARSER_HTML_FILE = STATIC_DIR / "file_parser.html"
PROJECT_MATERIALS_HTML_FILE = STATIC_DIR / "project_materials.html"
ADMIN_VUE_HTML_FILE = STATIC_DIR / "admin_vue.html"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class WebInputError(ValueError):
    pass


@dataclass
class LocalMemoryRecord:
    sequence_number: int
    user_question: str
    ai_response: str
    user_question_summary: str
    timestamp: str


@dataclass
class PreparedChatRequest:
    question: str
    provider: str
    config: Dict[str, Any]
    session_id: str
    user_id: str
    conversation_stage: str
    project_id: str
    scene_id: str
    template_id: str
    activity_id: str
    account_id: str
    product_id: str
    sender_identity: str
    video_overview: str
    global_prompt: str
    enable_knowledge: bool
    topics: List[str]
    summary_max_chars: int
    raw_max_chars: int
    knowledge_size: int
    source_platform: str
    conversion_target: str
    target_note: str


class InProcessSessionMemoryManager:
    """Small local memory backend for the web demo; it resets when the server stops."""

    def __init__(self):
        self._records: Dict[tuple[str, str], List[LocalMemoryRecord]] = {}

    def get_session_summary_context(self, user_id: str, session_id: str, max_chars: int = 4000) -> str:
        raw_context = self._format_raw_records(user_id, session_id)
        if raw_context and len(raw_context) <= max_chars:
            return raw_context

        lines = []
        for record in self._records.get((user_id, session_id), []):
            lines.append(f"[{record.sequence_number}] {record.user_question_summary}")
        return self._clip("\n\n".join(lines), max_chars)

    def get_session_raw_context(self, user_id: str, session_id: str, max_chars: int = 12000) -> str:
        return self._clip(self._format_raw_records(user_id, session_id), max_chars)

    def _format_raw_records(self, user_id: str, session_id: str) -> str:
        lines = []
        for record in self._records.get((user_id, session_id), []):
            lines.append(f"[{record.sequence_number}]\nUser: {record.user_question}\nAssistant: {record.ai_response}")
        return "\n\n".join(lines)

    def store_conversation(self, user_id: str, session_id: str, user_question: str, ai_response: str) -> str:
        key = (user_id, session_id)
        records = self._records.setdefault(key, [])
        sequence_number = len(records) + 1
        memory_id = uuid.uuid4().hex
        summary = f"Q: {self._clip(user_question, 400)}\nA: {self._clip(ai_response, 800)}"
        records.append(LocalMemoryRecord(
            sequence_number=sequence_number,
            user_question=user_question,
            ai_response=ai_response,
            user_question_summary=summary,
            timestamp=datetime.now().isoformat(),
        ))
        return memory_id

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        if max_chars and max_chars > 0 and len(text) > max_chars:
            return text[-max_chars:]
        return text


class PlaceholderKnowledgeLogic:
    def search_knowledge(self, question, topics, size=4):
        return []


class SimpleLLMChatTools:
    """Dependency-light model caller for the local web page."""

    @staticmethod
    def _format_message(prompt: str, message: str) -> str:
        return f"""
<system>
{prompt}

time_now: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
</system>

<user>
{message}
</user>
""".strip()

    @staticmethod
    def _schema_instruction(json_format: Any) -> str:
        if not json_format:
            return ""
        if hasattr(json_format, "model_json_schema"):
            schema = json.dumps(json_format.model_json_schema(), ensure_ascii=False)
            return f"\n\nReturn JSON only. The JSON must match this schema: {schema}"
        return "\n\nReturn JSON only with fields: answer, enough_info, missing_info, used_knowledge."

    def minimax_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                     max_len_input: int = 16000, json_format: Any = False, **kwargs) -> str:
        return self._openai_compatible_chat(
            url=url,
            api_key=api_key,
            prompt=prompt,
            message=message,
            model=model,
            stream=stream,
            max_len_input=max_len_input,
            json_format=json_format,
            extra_payload={"thinking": {"type": "disabled"}},
        )

    def minimax_anthropic_chat(self, url: str, api_key: str, prompt: str, message: str, model: str,
                               stream: bool = False, max_len_input: int = 16000,
                               json_format: Any = False, **kwargs) -> str:
        system_prompt = prompt[:max_len_input] + self._schema_instruction(json_format)
        payload = {
            "model": model,
            "max_tokens": int(kwargs.get("max_tokens") or 4096),
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": message}],
                }
            ],
            "stream": stream,
        }
        if stream:
            return self._stream_anthropic_text(normalize_anthropic_messages_url(url), payload, api_key)
        data = self._post_json(normalize_anthropic_messages_url(url), payload, api_key)
        return self._extract_anthropic_text(data)

    def openai_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                    max_len_input: int = 16000, json_format: Any = False, **kwargs) -> str:
        return self._openai_compatible_chat(
            url=url,
            api_key=api_key,
            prompt=prompt,
            message=message,
            model=model,
            stream=stream,
            max_len_input=max_len_input,
            json_format=json_format,
        )

    def deepseek_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                      max_len_input: int = 16000, json_format: Any = False, **kwargs) -> str:
        return self._openai_compatible_chat(
            url=url,
            api_key=api_key,
            prompt=prompt,
            message=message,
            model=model,
            stream=stream,
            max_len_input=max_len_input,
            json_format=json_format,
        )

    def ollama_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                    max_len_input: int = 16000, json_format: Any = False, **kwargs) -> str:
        formatted_message = self._format_message(prompt[:max_len_input], message) + self._schema_instruction(json_format)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream,
        }
        if json_format:
            payload["format"] = "json"
        if stream:
            return self._stream_ollama_text(url, payload, api_key)
        data = self._post_json(url, payload, api_key)
        return data.get("message", {}).get("content", "")

    def _openai_compatible_chat(self, url: str, api_key: str, prompt: str, message: str, model: str,
                                stream: bool, max_len_input: int, json_format: Any,
                                extra_payload: Optional[Dict[str, Any]] = None) -> str:
        formatted_message = self._format_message(prompt[:max_len_input], message) + self._schema_instruction(json_format)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream,
        }
        if extra_payload:
            payload.update(extra_payload)
        if stream:
            return self._stream_openai_text(url, payload, api_key)
        data = self._post_json(url, payload, api_key)
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    @staticmethod
    def _extract_anthropic_text(data: Dict[str, Any]) -> str:
        blocks = data.get("content", [])
        if isinstance(blocks, str):
            return blocks
        parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    def _stream_openai_text(self, url: str, payload: Dict[str, Any], api_key: str = "") -> Iterator[str]:
        for data in self._stream_json_objects(url, payload, api_key):
            delta = data.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield str(content)

    def _stream_anthropic_text(self, url: str, payload: Dict[str, Any], api_key: str = "") -> Iterator[str]:
        for data in self._stream_json_objects(url, payload, api_key):
            if data.get("type") == "content_block_delta":
                content = data.get("delta", {}).get("text")
                if content:
                    yield str(content)

    def _stream_ollama_text(self, url: str, payload: Dict[str, Any], api_key: str = "") -> Iterator[str]:
        for data in self._stream_json_objects(url, payload, api_key):
            if data.get("done"):
                break
            content = data.get("message", {}).get("content") or data.get("response")
            if content:
                yield str(content)

    def _stream_json_objects(self, url: str, payload: Dict[str, Any], api_key: str = "") -> Iterator[Dict[str, Any]]:
        for line in self._stream_json_lines(url, payload, api_key):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            yield data

    def _stream_json_lines(self, url: str, payload: Dict[str, Any], api_key: str = "") -> Iterator[str]:
        try:
            with self._open_json_response(url, payload, api_key, timeout=300) as response:
                for raw_line in response:
                    line = self._decode_stream_line(raw_line)
                    if line is None:
                        continue
                    if line == "[DONE]":
                        break
                    yield line
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"Request failed: {e.reason}") from e

    @staticmethod
    def _decode_stream_line(raw_line: bytes) -> Optional[str]:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            return None
        if line.startswith(":") or line.startswith("event:"):
            return None
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            return None
        return line

    @staticmethod
    def _post_json(url: str, payload: Dict[str, Any], api_key: str = "") -> Dict[str, Any]:
        try:
            with SimpleLLMChatTools._open_json_response(url, payload, api_key, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"Request failed: {e.reason}") from e

    @staticmethod
    def _open_json_response(url: str, payload: Dict[str, Any], api_key: str = "", timeout: int = 180):
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return urlopen(request, timeout=timeout)


def parse_topics(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clip_text(value: Any, max_chars: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _display_sender_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"^\s*(?:xx|XX)[\s._-]*", "", text).strip()
    return cleaned or text


def _validate_api_key_for_headers(config: Dict[str, Any]) -> None:
    key = str(config.get("key") or "").strip()
    if not key:
        return
    placeholder_patterns = [
        "替换成你的",
        "你的 API Key",
        "your api key",
        "api_key_here",
        "sk-x",
        "test-key",
    ]
    lowered = key.lower()
    if any(pattern.lower() in lowered for pattern in placeholder_patterns):
        raise WebInputError("api_key looks like a placeholder; please use a real provider API key")
    try:
        key.encode("latin-1")
    except UnicodeEncodeError as e:
        raise WebInputError("api_key must contain only HTTP-header-safe characters; remove Chinese placeholder text") from e


def build_config_test_response(
    payload: Dict[str, Any],
    llm_tools: Optional[Any] = None,
    use_saved_model_config: bool = False,
) -> Dict[str, Any]:
    provider = payload.get("provider") or "minimax"
    config = _resolve_request_model_config(payload, use_saved=use_saved_model_config, use_env=False)
    _validate_model_config(config)

    tools = llm_tools or SimpleLLMChatTools()
    chat = getattr(tools, config["func_name"], None)
    if not callable(chat):
        raise WebInputError(f"unknown func_name: {config['func_name']}")

    reply = chat(
        url=config["url"],
        api_key=config["key"] or "",
        prompt="你是模型配置测试助手。请只回复 OK，不要输出其它内容。",
        message="请回复 OK",
        model=config["model_name"],
        stream=False,
        json_format=False,
        max_len_input=min(_to_int(config.get("max_len_input"), 16000), 2000),
    )
    return {
        "provider": provider,
        "url": config["url"],
        "func_name": config["func_name"],
        "model_name": config["model_name"],
        "reply": _clip_text(reply, 200),
    }


def _result_to_dict(result: Any) -> Dict[str, Any]:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {
        "answer": getattr(result, "answer", ""),
        "enough_info": getattr(result, "enough_info", True),
        "second_pass": getattr(result, "second_pass", False),
        "missing_info": getattr(result, "missing_info", ""),
        "used_knowledge": getattr(result, "used_knowledge", []),
        "knowledge": getattr(result, "knowledge", []),
        "memory_id": getattr(result, "memory_id", None),
        "memory_error": getattr(result, "memory_error", ""),
        "final_prompt": getattr(result, "final_prompt", ""),
        "prompt_trace": getattr(result, "prompt_trace", {}),
    }


def prepare_chat_request(payload: Dict[str, Any], use_saved_model_config: bool = False) -> PreparedChatRequest:
    question = str(payload.get("question") or payload.get("user_input") or "").strip()
    if not question:
        raise WebInputError("question is required")

    provider = payload.get("provider") or "minimax"
    config = _resolve_request_model_config(payload, use_saved=use_saved_model_config, use_env=False)
    _validate_model_config(config)

    session_id = str(payload.get("session_id") or uuid.uuid4().hex).strip()
    user_id = str(payload.get("user_id") or DEFAULT_USER_ID).strip()
    conversation_stage = str(payload.get("conversation_stage") or "first_comment").strip()
    if conversation_stage not in {"first_comment", "private_followup"}:
        conversation_stage = "private_followup"
    project_id = str(payload.get("project_id") or "").strip()
    # The page may still display the last matched scene, but chat routing must
    # always be inferred from the current comment/session context.
    scene_id = "auto"
    template_id = str(payload.get("template_id") or "").strip()
    activity_id = str(payload.get("activity_id") or "").strip()
    account_id = str(payload.get("account_id") or "").strip()
    product_id = str(payload.get("product_id") or "").strip()
    sender_identity = _clip_text(payload.get("sender_identity") or payload.get("sender_identity_override"), 80)
    video_overview = _clip_text(
        payload.get("video_overview") or payload.get("video_summary") or payload.get("video_description"),
        2000,
    )
    global_prompt = _clip_text(payload.get("global_prompt"), 8000)
    enable_knowledge = bool(payload.get("enable_knowledge", False))
    topics = parse_topics(payload.get("topics")) if enable_knowledge else []
    source_platform = _clip_text(payload.get("source_platform"), 120)
    conversion_target = _clip_text(payload.get("conversion_target"), 200)
    target_note = _clip_text(payload.get("target_note"), 600)

    return PreparedChatRequest(
        question=question,
        provider=provider,
        config=config,
        session_id=session_id,
        user_id=user_id,
        conversation_stage=conversation_stage,
        project_id=project_id,
        scene_id=scene_id,
        template_id=template_id,
        activity_id=activity_id,
        account_id=account_id,
        product_id=product_id,
        sender_identity=sender_identity,
        video_overview=video_overview,
        global_prompt=global_prompt,
        enable_knowledge=enable_knowledge,
        topics=topics,
        summary_max_chars=_to_int(payload.get("summary_max_chars"), 4000),
        raw_max_chars=_to_int(payload.get("raw_max_chars"), 12000),
        knowledge_size=_to_int(payload.get("knowledge_size"), 4),
        source_platform=source_platform,
        conversion_target=conversion_target,
        target_note=target_note,
    )


def _chat_response_payload(
    prepared: PreparedChatRequest,
    result: Any,
    project_bundle: Any = None,
    project_documents: Optional[Dict[str, Any]] = None,
    scene_template: Optional[Dict[str, Any]] = None,
    activity_settings: Optional[Dict[str, Any]] = None,
    sender_identity: str = "",
    sender_identity_source: Optional[Dict[str, Any]] = None,
    prompt_trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    project_data = {}
    if project_bundle is not None:
        project_data = {
            "project_id": project_bundle.project_id,
            "project_name": project_bundle.project_name,
            "scene_id": project_bundle.scene_id,
            "scene_name": project_bundle.scene_name,
        }
    result_data = _result_to_dict(result)
    trace = prompt_trace or result_data.get("prompt_trace") or {}
    final_prompt = trace.get("final_prompt") or result_data.get("final_prompt") or ""
    if final_prompt:
        trace = {
            "final_prompt": final_prompt,
            **{key: value for key, value in trace.items() if key != "final_prompt"},
        }

    answer = str(result_data.get("answer") or "")
    resolved_sender_identity = _display_sender_identity(sender_identity or (sender_identity_source or {}).get("sender_identity")) or "品牌客服"
    return {
        "answer": answer,
        "result": result_data,
        "session_id": prepared.session_id,
        "user_id": prepared.user_id,
        "provider": prepared.provider,
        "project": project_data,
        "project_documents": project_documents or {"documents": [], "document_ids": [], "reason": ""},
        "sender_identity": resolved_sender_identity,
        "sender_identity_source": sender_identity_source or {},
        "scene_template": scene_template or {},
        "activity_settings": activity_settings or {},
        "config": {
            "url": prepared.config["url"],
            "func_name": prepared.config["func_name"],
            "model_name": prepared.config["model_name"],
            "max_len_input": prepared.config["max_len_input"],
            "key_source": prepared.config.get("key_source"),
            "project_id": project_data.get("project_id") or prepared.project_id,
            "scene_id": project_data.get("scene_id") or prepared.scene_id,
            "template_id": (scene_template or {}).get("template_id") or prepared.template_id,
            "activity_id": (activity_settings or {}).get("activity_id") or prepared.activity_id,
            "video_overview": prepared.video_overview,
            "global_prompt": prepared.global_prompt,
            "knowledge_enabled": prepared.enable_knowledge,
            "topics": prepared.topics,
            "conversation_stage": prepared.conversation_stage,
            "source_platform": prepared.source_platform,
            "conversion_target": prepared.conversion_target,
            "target_note": prepared.target_note,
            "sender_identity": resolved_sender_identity,
            "sender_identity_source": sender_identity_source or {},
            "account_id": prepared.account_id,
            "product_id": prepared.product_id,
        },
        "prompt_trace": trace,
    }


def _compact_private_message_response(
    *,
    question: str,
    session_id: str,
    user_id: str,
    provider: str,
    config: Dict[str, Any],
    answer: str,
    prompt_modules: List[Dict[str, Any]],
    extra_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    input_data = {
        "question": question,
        "session_id": session_id,
        "user_id": user_id,
    }
    if extra_input:
        for key, value in extra_input.items():
            if value is None:
                continue
            if isinstance(value, (str, list, dict, tuple, set)) and not value:
                continue
            input_data[key] = value
    return {
        "input": input_data,
        "reply": answer,
        "prompts": prompt_modules,
        "config": {
            "provider": provider,
            "api": config.get("url"),
            "func": config.get("func_name"),
            "model": config.get("model_name"),
            "max_len": config.get("max_len_input"),
            "key_source": config.get("key_source"),
        },
    }


def _project_store(store: Optional[ProjectMaterialStore] = None) -> ProjectMaterialStore:
    return store or ProjectMaterialStore()


def parse_uploaded_file(
    file_name: str,
    file_data: bytes,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    safe_name = Path(file_name or "").name
    if not safe_name:
        raise WebInputError("file is required")

    store = _project_store(project_store)
    content = store.parse_file_content(safe_name, file_data or b"")
    normalized = content.strip() if content else ""
    extension = Path(safe_name).suffix.lower()
    return {
        "file_name": safe_name,
        "extension": extension,
        "content": normalized,
        "content_chars": len(normalized),
        "line_count": normalized.count("\n") + 1 if normalized else 0,
    }


def _document_selection_model_conf(prepared: PreparedChatRequest) -> Dict[str, Any]:
    return {
        "url": prepared.config["url"],
        "key": prepared.config.get("key") or "",
        "func_name": prepared.config["func_name"],
        "model_name": prepared.config["model_name"],
        "max_len_input": prepared.config["max_len_input"],
    }


def _format_user_context_text(question: str, video_overview: str = "", context: str = "") -> str:
    parts = [f"用户评论：\n{question}"]
    if video_overview:
        parts.append(f"视频概述：\n{video_overview}")
    if context:
        parts.append(f"上下文：\n{context}")
    return "\n\n".join(parts)


def _project_routing_input(prepared: PreparedChatRequest, chat_logic: SessionRAGChatLogic) -> str:
    parts = [_format_user_context_text(prepared.question, prepared.video_overview)]
    if prepared.conversation_stage != "private_followup" or type(chat_logic) is not SessionRAGChatLogic:
        return parts[0]

    try:
        max_chars = min(max(prepared.summary_max_chars, 500), 2000)
        summary_context = chat_logic._memory().get_session_summary_context(
            prepared.user_id,
            prepared.session_id,
            max_chars,
        )
    except Exception:
        summary_context = ""

    if summary_context:
        parts.append(f"当前会话已有上下文：\n{summary_context}")
    return "\n\n".join(parts)


def _select_project_documents(
    store: ProjectMaterialStore,
    prepared: PreparedChatRequest,
    chat_logic: SessionRAGChatLogic,
    scene_id: str,
    routing_input: str = "",
) -> Dict[str, Any]:
    use_ai_selector = type(chat_logic) is SessionRAGChatLogic
    return store.select_relevant_documents(
        user_input=routing_input or prepared.question,
        project_id=prepared.project_id,
        scene_id=scene_id,
        llm_tools=chat_logic._llm() if use_ai_selector else None,
        model_conf=_document_selection_model_conf(prepared) if use_ai_selector else None,
    )


def _format_scene_template_context(template: Optional[Dict[str, Any]]) -> str:
    if not template:
        return ""
    return "\n".join([
        "<scene_template>",
        "这是后台配置的场景模板数据。请按模板的 purpose、applicable_scene、tags、steps 生成回复；不要把模板当成固定代码逻辑。不要主动自证消息真实性、解释发送方式或强调自己是真人，要用对评论和视频内容的准确承接证明真人感。",
        json.dumps(template, ensure_ascii=False, indent=2),
        "</scene_template>",
    ])


def _format_global_prompt_context(global_prompt: str) -> str:
    return "\n".join([
        "<global_prompt>",
        global_prompt.strip() if global_prompt and global_prompt.strip() else "没有配置全局提示词。",
        "</global_prompt>",
    ])


def _derive_sender_identity(documents: Optional[List[Dict[str, Any]]]) -> str:
    identity, _ = _infer_sender_identity_from_context(documents=documents)
    return identity


def _infer_sender_identity_from_context(
    prepared: Optional[PreparedChatRequest] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    scene_template: Optional[Dict[str, Any]] = None,
    activity_settings: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    segments: List[str] = []
    if prepared:
        segments.extend([
            prepared.question,
            prepared.video_overview,
            prepared.source_platform,
            prepared.conversion_target,
            prepared.target_note,
            prepared.conversation_stage,
        ])
    if scene_template:
        segments.extend([
            str(scene_template.get("title") or ""),
            str(scene_template.get("purpose") or ""),
            str(scene_template.get("applicable_scene") or ""),
        ])
        segments.extend(str(tag) for tag in (scene_template.get("tags") or []) if str(tag).strip())
        segments.extend(str(step) for step in (scene_template.get("steps") or []) if str(step).strip())
    if activity_settings:
        segments.extend([
            str(activity_settings.get("title") or ""),
            str(activity_settings.get("activity_type") or ""),
            str(activity_settings.get("applicable_scene") or ""),
            str(activity_settings.get("description") or ""),
            str(activity_settings.get("benefit") or ""),
            str(activity_settings.get("claim_method") or ""),
            str(activity_settings.get("deadline") or ""),
            str(activity_settings.get("quota") or ""),
            str(activity_settings.get("compliance_note") or ""),
        ])
        segments.extend(str(tag) for tag in (activity_settings.get("tags") or []) if str(tag).strip())
    text = "\n".join(_clip_text(segment, 800) for segment in segments if str(segment or "").strip())
    identity = "品牌客服"
    family = "general"
    reason = "通用业务信息"
    if re.search(r"招聘|求职|面试|岗位|简历|候选人|hr|boss|到岗|offer|入职", text, flags=re.I):
        identity, family, reason = "招聘助理", "recruitment", "招聘/求职/面试/岗位"
    elif re.search(r"跨境|电商|店铺|货源|托管|选品|海外|出海|留学|平台运营", text, flags=re.I):
        identity, family, reason = "跨境运营顾问", "cross_border", "跨境/电商/托管/选品"
    elif re.search(r"睡眠|失眠|入睡|早醒|压力|疲惫|熬夜|关节|膝|骨积液|软骨|prp|干细胞|康养|理疗|医疗|健康", text, flags=re.I):
        identity, family, reason = "健康顾问助理", "health", "大健康/睡眠/关节/康养"
    elif re.search(r"自动私信|评论采集|自动回复|文生视频|批量剪辑|自动发布|数据统计|引流|ai|agent|运营", text, flags=re.I):
        identity, family, reason = "运营顾问", "ops", "AI私信/内容生产/运营引流"
    elif re.search(r"活动|义诊|体验|名额|资料包|领取|预约|优惠|钩子|承接", text, flags=re.I):
        identity, family, reason = "运营助理", "activity", "活动/钩子/承接"

    source: Dict[str, Any] = {
        "source": "context_generated",
        "sender_identity": identity,
        "identity_family": family,
        "reason": reason,
    }
    if prepared:
        source["conversation_stage"] = prepared.conversation_stage
        source["question"] = _clip_text(prepared.question, 300)
        if prepared.video_overview:
            source["video_overview"] = _clip_text(prepared.video_overview, 500)
        if prepared.source_platform:
            source["source_platform"] = prepared.source_platform
        if prepared.conversion_target:
            source["conversion_target"] = prepared.conversion_target
        if prepared.target_note:
            source["target_note"] = prepared.target_note
    if scene_template:
        if scene_template.get("template_id"):
            source["template_id"] = scene_template.get("template_id")
        if scene_template.get("title"):
            source["template_title"] = scene_template.get("title")
        if scene_template.get("applicable_scene"):
            source["template_scene"] = scene_template.get("applicable_scene")
    if activity_settings:
        for key in ("activity_id", "title", "activity_type", "applicable_scene", "benefit", "claim_method"):
            value = activity_settings.get(key)
            if value:
                source[key] = _clip_text(value, 300)
    return identity, source

def _valid_sender_identity(value: Any) -> str:
    identity = str(value or "").strip()
    return identity if ProjectMaterialStore._is_valid_sender_identity(identity) else ""


def _identity_source_payload(
    source: str,
    identity: str,
    item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = {
        "source": source,
        "sender_identity": _display_sender_identity(identity),
    }
    if item:
        for key in ("account_id", "account_name", "product_id", "product_name", "platform", "description"):
            if item.get(key):
                data[key] = _display_sender_identity(item.get(key))
    return data


def _find_identity_item(
    items: List[Dict[str, Any]],
    id_key: str,
    target_id: str,
) -> Optional[Dict[str, Any]]:
    if not target_id:
        return None
    for item in items:
        if not item.get("enabled", True):
            continue
        if str(item.get(id_key) or "").strip() == target_id:
            return item
    return None


def _auto_match_product_identity(
    products: List[Dict[str, Any]],
    documents: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    docs = documents or []
    doc_ids = {str(doc.get("doc_id") or "").strip() for doc in docs if doc.get("doc_id")}
    if doc_ids:
        scored_products = []
        for product in products:
            if not product.get("enabled", True):
                continue
            related_doc_ids = {str(item).strip() for item in product.get("related_doc_ids") or []}
            matched = doc_ids.intersection(related_doc_ids)
            if matched:
                has_specific_mock_doc = any(doc_id.startswith("mock_") for doc_id in matched)
                is_general_product = str(product.get("product_id") or "") == "general_brand_service"
                scored_products.append((len(matched), 1 if has_specific_mock_doc else 0, 0 if is_general_product else 1, product))
        if scored_products:
            scored_products.sort(key=lambda item: (-item[0], -item[1], -item[2], str(item[3].get("product_id") or "")))
            return scored_products[0][3]

    combined = "\n".join(
        " ".join([
            str(doc.get("title") or ""),
            str(doc.get("description") or ""),
            str(doc.get("summary") or ""),
            " ".join(str(tag) for tag in doc.get("tags") or doc.get("keywords") or []),
        ])
        for doc in docs
    )
    if not combined:
        return None
    for product in products:
        if not product.get("enabled", True):
            continue
        tags = [str(tag).strip() for tag in product.get("tags") or [] if str(tag).strip()]
        if tags and any(tag and tag in combined for tag in tags):
            return product
    return None


def _resolve_sender_identity(
    store: ProjectMaterialStore,
    project_id: str,
    documents: Optional[List[Dict[str, Any]]] = None,
    prepared: Optional[PreparedChatRequest] = None,
    scene_template: Optional[Dict[str, Any]] = None,
    activity_settings: Optional[Dict[str, Any]] = None,
    account_id: str = "",
    product_id: str = "",
    sender_identity: str = "",
) -> tuple[str, Dict[str, Any]]:
    settings = _load_identity_settings(store, project_id)
    explicit_identity = _valid_sender_identity(_display_sender_identity(sender_identity or (prepared.sender_identity if prepared else "")))
    if explicit_identity:
        return explicit_identity, _identity_source_payload("api_override", explicit_identity)

    account_id = account_id or (prepared.account_id if prepared else "")
    product_id = product_id or (prepared.product_id if prepared else "")
    account = _find_identity_item(settings.get("accounts", []), "account_id", account_id)
    account_identity = _valid_sender_identity(_display_sender_identity((account or {}).get("sender_identity")))
    if account_identity:
        return account_identity, _identity_source_payload("account", account_identity, account)

    product = _find_identity_item(settings.get("products", []), "product_id", product_id)
    product_identity = _valid_sender_identity(_display_sender_identity((product or {}).get("sender_identity")))
    if product_identity:
        return product_identity, _identity_source_payload("product", product_identity, product)

    generated_identity, generated_source = _infer_sender_identity_from_context(
        prepared=prepared,
        documents=documents,
        scene_template=scene_template,
        activity_settings=activity_settings,
    )
    if generated_identity:
        auto_product = _auto_match_product_identity(settings.get("products", []), documents)
        auto_product_identity = _valid_sender_identity(_display_sender_identity((auto_product or {}).get("sender_identity")))
        if generated_source.get("identity_family") == "general" and auto_product_identity:
            return auto_product_identity, _identity_source_payload("product_auto", auto_product_identity, auto_product)
        return generated_identity, generated_source

    auto_product = _auto_match_product_identity(settings.get("products", []), documents)
    auto_product_identity = _valid_sender_identity(_display_sender_identity((auto_product or {}).get("sender_identity")))
    if auto_product_identity:
        return auto_product_identity, _identity_source_payload("product_auto", auto_product_identity, auto_product)

    default_identity = _valid_sender_identity(_display_sender_identity(settings.get("default_sender_identity"))) or "品牌客服"
    return default_identity, _identity_source_payload("default", default_identity)


def _format_sender_identity_context(sender_identity: str, source: Optional[Dict[str, Any]] = None) -> str:
    source = source or {}
    sender_identity = _display_sender_identity(sender_identity)
    source_key = str(source.get("source") or "")
    source_text = {
        "api_override": "页面/API 明确传入",
        "account": "账号身份配置",
        "product": "产品身份配置",
        "product_auto": "根据检索文档自动匹配到的产品身份配置",
        "context_generated": "根据业务、视频概述和活动自动生成",
        "knowledge": "知识库资料建议",
        "default": "项目默认身份",
    }.get(source_key, "身份配置")
    details = []
    if source_key == "context_generated":
        if source.get("reason"):
            details.append(f"依据：{source.get('reason')}")
        if source.get("question"):
            details.append(f"评论：{source.get('question')}")
        if source.get("video_overview"):
            details.append(f"视频：{source.get('video_overview')}")
        activity_label = source.get("title") or source.get("activity_type")
        if activity_label:
            details.append(f"活动：{activity_label}")
        if source.get("template_title"):
            details.append(f"模板：{source.get('template_title')}")
    else:
        if source.get("account_name"):
            details.append(f"账号：{source.get('account_name')}")
        if source.get("product_name"):
            details.append(f"产品：{source.get('product_name')}")
        if source.get("platform"):
            details.append(f"平台：{source.get('platform')}")
    return "\n".join([
        "<sender_identity>",
        f"本轮私信建议使用身份：{sender_identity or '品牌客服'}。",
        f"身份来源：{source_text}{'；' + '；'.join(details) if details else ''}。",
        "使用原则：身份只用于引流承接，不要伪装医生、专家或平台官方人员；不要读取知识库里的 sender_identity 字段。",
        "</sender_identity>",
    ])


def _format_activity_settings_context(activity: Optional[Dict[str, Any]]) -> str:
    if not activity:
        return "\n".join([
            "<activity_settings>",
            "没有匹配到当前可用活动；不要编造活动、义诊、优惠券、名额、截止时间或价格。",
            "</activity_settings>",
        ])
    return "\n".join([
        "<activity_settings>",
        json.dumps(activity, ensure_ascii=False, indent=2),
        "",
        "使用要求：如果活动适合当前评论，可自然表达为“咱们这边正好有{活动名称/类型}”；只使用上面明确配置的活动利益、领取方式、限制和合规备注，不要编造未配置的名额、时间、价格或效果。",
        "</activity_settings>",
    ])


def _format_retrieved_knowledge_context(context: str) -> str:
    return "\n".join([
        "<retrieved_knowledge>",
        context.strip() if context and context.strip() else "没有检索到相关知识库。",
        "</retrieved_knowledge>",
    ])


def _format_runtime_rag_context(
    user_context: str,
    global_prompt: str,
    sender_identity: str,
    scene_template: Optional[Dict[str, Any]],
    activity_settings: Optional[Dict[str, Any]],
    retrieved_knowledge: str,
    sender_identity_source: Optional[Dict[str, Any]] = None,
) -> str:
    return "\n\n".join(part for part in [
        "\n".join(["<user_context>", user_context, "</user_context>"]),
        _format_global_prompt_context(global_prompt),
        _format_sender_identity_context(sender_identity, sender_identity_source),
        _format_scene_template_context(scene_template),
        _format_activity_settings_context(activity_settings),
        _format_retrieved_knowledge_context(retrieved_knowledge),
    ] if part)


def _template_routing_input(prepared: PreparedChatRequest) -> str:
    stage_text = {
        "first_comment": "用户评论 -> 首次私信",
        "private_followup": "用户私信 -> 私信维护",
    }.get(prepared.conversation_stage, prepared.conversation_stage)
    parts = [
        _format_user_context_text(prepared.question, prepared.video_overview),
        f"对话阶段：{stage_text}",
    ]
    if prepared.source_platform:
        parts.append(f"平台：{prepared.source_platform}")
    if prepared.conversion_target:
        parts.append(f"转化目标：{prepared.conversion_target}")
    if prepared.target_note:
        parts.append(f"补充目标：{prepared.target_note}")
    return "\n".join(parts)


def _select_runtime_scene_template(
    store: ProjectMaterialStore,
    prepared: PreparedChatRequest,
    project_id: str,
) -> Dict[str, Any]:
    templates = _load_scene_templates(store, project_id).get("templates", [])
    return _select_scene_template(
        _template_routing_input(prepared),
        templates,
        prepared.template_id,
    )


def _select_runtime_activity(
    store: ProjectMaterialStore,
    prepared: PreparedChatRequest,
    project_id: str,
    routing_input: str = "",
) -> Dict[str, Any]:
    activities = _load_activity_settings(store, project_id).get("activities", [])
    return _select_activity(
        routing_input or _template_routing_input(prepared),
        activities,
        prepared.activity_id,
    )


def _project_root_and_meta(store: ProjectMaterialStore, project_id: str = "") -> tuple[Path, Dict[str, Any]]:
    project_dir = store._resolve_project_dir(project_id)
    store.ensure_default_knowledge_documents(project_dir)
    return project_dir, store._read_json(project_dir / "project.json")


def _safe_project_path(store: ProjectMaterialStore, project_id: str, relative_path: str) -> tuple[Path, Path, str]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not relative:
        raise WebInputError("relative_path is required")
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or any(part == ".." for part in candidate_relative.parts):
        raise WebInputError("invalid relative_path")

    candidate = (project_dir / candidate_relative).resolve()
    root = project_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise WebInputError("relative_path is outside project") from e

    if candidate.suffix.lower() not in {".md", ".txt", ".json", ".jsonl"}:
        raise WebInputError("only md/txt/json/jsonl files can be edited")
    return project_dir, candidate, candidate_relative.as_posix()


def _safe_project_folder_path(store: ProjectMaterialStore, project_id: str, relative_path: str) -> tuple[Path, Path, str]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not relative:
        raise WebInputError("folder_path is required")
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or any(part == ".." for part in candidate_relative.parts):
        raise WebInputError("invalid folder_path")

    candidate = (project_dir / candidate_relative).resolve()
    root = (project_dir / "knowledge" / "files").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise WebInputError("folder_path is outside knowledge files") from e
    return project_dir, candidate, candidate_relative.as_posix()


def _project_file_item(
    project_dir: Path,
    relative_path: str,
    title: str,
    group: str,
    file_type: str,
    editable: bool = True,
) -> Dict[str, Any]:
    path = project_dir / relative_path
    content = ""
    exists = path.exists() and path.is_file()
    if exists:
        content = path.read_text(encoding="utf-8", errors="replace")
    return {
        "file_id": relative_path.replace("\\", "/"),
        "title": title,
        "group": group,
        "type": file_type,
        "relative_path": relative_path.replace("\\", "/"),
        "editable": editable,
        "exists": exists,
        "content": content,
        "chars": len(content),
    }


def build_project_materials_response(
    project_store: Optional[ProjectMaterialStore] = None,
    project_id: str = "",
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_dir, project_meta = _project_root_and_meta(store, project_id)
    project_id = project_dir.name
    files = [
        _project_file_item(
            project_dir=project_dir,
            relative_path="global_prompt.md",
            title="全局统一提示词",
            group="项目全局",
            file_type="global_prompt",
        ),
        _project_file_item(
            project_dir=project_dir,
            relative_path="project.json",
            title="项目信息",
            group="项目全局",
            file_type="project_meta",
        ),
        _project_file_item(
            project_dir=project_dir,
            relative_path="identity_settings.json",
            title="身份配置",
            group="项目全局",
            file_type="identity_settings",
        ),
    ]

    scenes = []
    scenes_dir = project_dir / "scenes"
    for scene_dir in sorted(scenes_dir.glob("*")):
        if not scene_dir.is_dir():
            continue
        scene_meta = store._read_json(scene_dir / "scene.json")
        scene_id = scene_dir.name
        scene_name = scene_meta.get("name") or scene_id
        scenes.append({"scene_id": scene_id, "name": scene_name})
        files.extend([
            _project_file_item(
                project_dir=project_dir,
                relative_path=f"scenes/{scene_id}/prompt.md",
                title=f"{scene_name} - 场景提示词",
                group=f"场景 / {scene_name}",
                file_type="scene_prompt",
            ),
            _project_file_item(
                project_dir=project_dir,
                relative_path=f"scenes/{scene_id}/materials.md",
                title=f"{scene_name} - 场景资料",
                group=f"场景 / {scene_name}",
                file_type="scene_materials",
            ),
        ])

    documents = store.list_knowledge_documents(project_id)
    for doc in documents:
        relative_path = str(doc.get("relative_path") or "").replace("\\", "/")
        if not relative_path:
            continue
        files.append(
            _project_file_item(
                project_dir=project_dir,
                relative_path=relative_path,
                title=str(doc.get("title") or doc.get("doc_id") or relative_path),
                group=f"知识库原始资料 / {doc.get('category') or '未分类'}",
                file_type="knowledge_document",
            )
        )

    manifest_path = project_dir / "knowledge" / "manifest.json"
    if manifest_path.exists():
        files.append(
            _project_file_item(
                project_dir=project_dir,
                relative_path="knowledge/manifest.json",
                title="知识库索引清单",
                group="知识库索引",
                file_type="knowledge_manifest",
            )
        )

    chunks_path = project_dir / "knowledge" / "chunks.jsonl"
    if chunks_path.exists():
        files.append(
            _project_file_item(
                project_dir=project_dir,
                relative_path="knowledge/chunks.jsonl",
                title="文本切分块",
                group="知识库索引",
                file_type="knowledge_chunks",
            )
        )

    return {
        "projects": store.list_projects(),
        "project": {
            "project_id": project_id,
            "name": project_meta.get("name") or project_id,
            "default_scene": project_meta.get("default_scene") or "auto",
            "path": str(project_dir),
            "scenes": scenes,
        },
        "files": files,
    }


def build_project_material_save_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    relative_path = str(payload.get("relative_path") or "").strip()
    content = str(payload.get("content") if payload.get("content") is not None else "")
    project_dir, target_path, normalized_relative = _safe_project_path(store, project_id, relative_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return _project_file_item(
        project_dir=project_dir,
        relative_path=normalized_relative,
        title=Path(normalized_relative).name,
        group="已保存",
        file_type="saved_file",
    )


def build_project_material_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    relative_path = str(payload.get("relative_path") or "").strip()
    if not relative_path:
        raise WebInputError("relative_path is required")
    project_dir, target_path, normalized_relative = _safe_project_path(store, project_id, relative_path)
    if not target_path.exists() or not target_path.is_file():
        raise WebInputError("file not found")
    target_path.unlink()
    _remove_empty_parent_dirs(target_path, project_dir)
    return {
        "deleted": True,
        "relative_path": normalized_relative,
        "title": Path(normalized_relative).name,
    }


def build_project_create_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    name = _clip_text(payload.get("name"), 120)
    if not name:
        raise WebInputError("project name is required")
    project = store.create_project(name)
    materials = build_project_materials_response(store, project["project_id"])
    materials["created_project"] = project
    return materials


def _optional_document_selector_conf(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not payload.get("use_ai_selector"):
        return None

    try:
        provider = payload.get("provider") or "custom"
        config = resolve_provider_config(
            provider=provider,
            url=(payload.get("url") or "").strip() or None,
            key=payload.get("api_key") if payload.get("api_key") is not None else payload.get("key"),
            func_name=(payload.get("func_name") or "").strip() or None,
            model_name=(payload.get("model_name") or "").strip() or None,
            max_len_input=_to_int(payload.get("max_len_input"), 16000),
            use_env=False,
        )
    except Exception:
        return None

    if not config.get("url") or not config.get("func_name") or not config.get("model_name"):
        return None
    if config.get("requires_key") and not config.get("key"):
        return None
    return config


def _prompt_module(key: str, title: str, content: str) -> Dict[str, Any]:
    text = str(content or "")
    return {
        "key": key,
        "title": title,
        "content": text,
        "chars": len(text),
    }


def _admin_json_path(store: ProjectMaterialStore, project_id: str, relative_path: str) -> tuple[Path, Path]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    path = (project_dir / relative_path).resolve()
    try:
        path.relative_to(project_dir.resolve())
    except ValueError as e:
        raise WebInputError("invalid admin path") from e
    return project_dir, path


def _load_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_model_config_store_path() -> Path:
    return ENV_FILE


def _env_key(provider: str, field: str) -> str:
    clean_provider = re.sub(r"[^A-Za-z0-9]+", "_", str(provider or "custom")).strip("_").upper() or "CUSTOM"
    return f"{clean_provider}_{field}"


def _read_env_file(path: Optional[Path] = None) -> tuple[Dict[str, str], List[str]]:
    target = path or _default_model_config_store_path()
    values: Dict[str, str] = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values, []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values, lines


def _quote_env_value(value: Any) -> str:
    text = str(value if value is not None else "")
    if not text:
        return ""
    if re.search(r"[\s#='\"\r\n]", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _write_env_values(updates: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or _default_model_config_store_path()
    _, lines = _read_env_file(target)
    seen = set()
    output: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={_quote_env_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)

    if output and output[-1].strip():
        output.append("")
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={_quote_env_value(value)}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[str(key)] = str(value if value is not None else "")


def _load_model_config_store(path: Optional[Path] = None) -> Dict[str, Any]:
    env_values, _ = _read_env_file(path)
    env_values = {**env_values, **os.environ}
    providers: Dict[str, Dict[str, Any]] = {}

    for provider, preset in public_provider_presets().items():
        key = env_values.get(_env_key(provider, "API_KEY")) or ""
        url = env_values.get(_env_key(provider, "URL")) or preset.get("url") or ""
        func_name = env_values.get(_env_key(provider, "FUNC_NAME")) or preset.get("func_name") or ""
        model_name = env_values.get(_env_key(provider, "MODEL_NAME")) or preset.get("model_name") or ""
        max_len_input = _to_int(env_values.get(_env_key(provider, "MAX_LEN_INPUT")), 16000)
        if any([key, env_values.get(_env_key(provider, "URL")), env_values.get(_env_key(provider, "FUNC_NAME")), env_values.get(_env_key(provider, "MODEL_NAME"))]):
            providers[provider] = {
                "provider": provider,
                "api_key": key,
                "url": url,
                "func_name": func_name,
                "model_name": model_name,
                "max_len_input": max_len_input,
            }

    for key, value in env_values.items():
        if not key.endswith("_API_KEY"):
            continue
        prefix = key[:-8]
        provider = prefix.lower()
        if provider in providers:
            continue
        providers[provider] = {
            "provider": provider,
            "api_key": value,
            "url": env_values.get(f"{prefix}_URL", ""),
            "func_name": env_values.get(f"{prefix}_FUNC_NAME", ""),
            "model_name": env_values.get(f"{prefix}_MODEL_NAME", ""),
            "max_len_input": _to_int(env_values.get(f"{prefix}_MAX_LEN_INPUT"), 16000),
        }

    updated_at = env_values.get("SESSION_RAG_MODEL_CONFIG_UPDATED_AT", "")
    return {"version": 1, "updated_at": updated_at, "providers": providers}


def _mask_api_key(key: str) -> str:
    clean = str(key or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def _model_config_public(config: Dict[str, Any]) -> Dict[str, Any]:
    key = str(config.get("api_key") or config.get("key") or "")
    return {
        "provider": config.get("provider") or "",
        "url": config.get("url") or "",
        "func_name": config.get("func_name") or "",
        "model_name": config.get("model_name") or "",
        "max_len_input": config.get("max_len_input") or 16000,
        "has_api_key": bool(key),
        "api_key_masked": _mask_api_key(key),
        "updated_at": config.get("updated_at") or "",
    }


def _public_model_configs(path: Optional[Path] = None) -> Dict[str, Any]:
    store = _load_model_config_store(path)
    providers = {}
    for name, config in store.get("providers", {}).items():
        if isinstance(config, dict):
            providers[name] = _model_config_public({**config, "provider": config.get("provider") or name})
    return {
        "version": store.get("version") or 1,
        "updated_at": store.get("updated_at") or "",
        "providers": providers,
    }


def _saved_provider_config(provider: str, path: Optional[Path] = None) -> Dict[str, Any]:
    store = _load_model_config_store(path)
    providers = store.get("providers") or {}
    config = providers.get(provider) if isinstance(providers, dict) else None
    return dict(config) if isinstance(config, dict) else {}


def _explicit_key_from_payload(payload: Dict[str, Any]) -> Any:
    return payload.get("api_key") if payload.get("api_key") is not None else payload.get("key")


def _resolve_request_model_config(payload: Dict[str, Any], *, use_saved: bool = True, use_env: bool = True) -> Dict[str, Any]:
    provider = payload.get("provider") or "minimax"
    saved = _saved_provider_config(provider) if use_saved else {}
    explicit_key = _explicit_key_from_payload(payload)
    saved_key = saved.get("api_key") if saved.get("api_key") is not None else saved.get("key")
    key = explicit_key if explicit_key not in {None, ""} else saved_key

    def merged_text(name: str) -> Optional[str]:
        explicit = str(payload.get(name) or "").strip()
        if explicit:
            return explicit
        saved_value = str(saved.get(name) or "").strip()
        return saved_value or None

    max_len_input = payload.get("max_len_input")
    if max_len_input in {None, ""}:
        max_len_input = saved.get("max_len_input")

    config = resolve_provider_config(
        provider=provider,
        url=merged_text("url"),
        key=key,
        func_name=merged_text("func_name"),
        model_name=merged_text("model_name"),
        max_len_input=_to_int(max_len_input, 16000),
        use_env=use_env,
    )
    config["key_source"] = (
        "request"
        if explicit_key not in {None, ""}
        else "saved"
        if saved_key not in {None, ""}
        else "env"
        if config.get("key")
        else ""
    )
    return config


def _validate_model_config(config: Dict[str, Any]) -> None:
    missing = []
    if not config["url"]:
        missing.append("url")
    if not config["func_name"]:
        missing.append("func_name")
    if not config["model_name"]:
        missing.append("model_name")
    if config.get("requires_key") and not config["key"]:
        provider = config.get("provider") or "selected provider"
        missing.append(
            f"api_key ({provider} has no saved key; save model config once via the page or /api/v1/model/config/save)"
        )
    if missing:
        raise WebInputError("missing config: " + ", ".join(missing))
    _validate_api_key_for_headers(config)


def build_model_config_save_response(
    payload: Dict[str, Any],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebInputError("json body is required")

    normalized = _normalize_public_api_payload(payload)
    provider = str(normalized.get("provider") or "").strip()
    if not provider:
        raise WebInputError("provider is required")

    current = _saved_provider_config(provider, path)
    incoming_key = _explicit_key_from_payload(normalized)
    keep_existing_key = incoming_key in {None, ""} and bool(current.get("api_key") or current.get("key"))
    key = (current.get("api_key") or current.get("key")) if keep_existing_key else incoming_key

    config = resolve_provider_config(
        provider=provider,
        url=(normalized.get("url") or "").strip() or current.get("url") or None,
        key=key,
        func_name=(normalized.get("func_name") or "").strip() or current.get("func_name") or None,
        model_name=(normalized.get("model_name") or "").strip() or current.get("model_name") or None,
        max_len_input=_to_int(normalized.get("max_len_input") or current.get("max_len_input"), 16000),
        use_env=False,
    )
    _validate_api_key_for_headers(config)

    provider_config = {
        "provider": provider,
        "api_key": config.get("key") or "",
        "url": config.get("url") or "",
        "func_name": config.get("func_name") or "",
        "model_name": config.get("model_name") or "",
        "max_len_input": config.get("max_len_input") or 16000,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_env_values(
        {
            _env_key(provider, "API_KEY"): provider_config["api_key"],
            _env_key(provider, "URL"): provider_config["url"],
            _env_key(provider, "FUNC_NAME"): provider_config["func_name"],
            _env_key(provider, "MODEL_NAME"): provider_config["model_name"],
            _env_key(provider, "MAX_LEN_INPUT"): provider_config["max_len_input"],
            "SESSION_RAG_MODEL_CONFIG_UPDATED_AT": provider_config["updated_at"],
        },
        path,
    )
    return {
        "config": _model_config_public(provider_config),
        "configs": _public_model_configs(path),
    }


def build_model_config_delete_response(
    payload: Dict[str, Any],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_public_api_payload(payload)
    provider = str(normalized.get("provider") or "").strip()
    if not provider:
        raise WebInputError("provider is required")
    _write_env_values(
        {
            _env_key(provider, "API_KEY"): "",
            _env_key(provider, "URL"): "",
            _env_key(provider, "FUNC_NAME"): "",
            _env_key(provider, "MODEL_NAME"): "",
            _env_key(provider, "MAX_LEN_INPUT"): "",
            "SESSION_RAG_MODEL_CONFIG_UPDATED_AT": datetime.now().isoformat(timespec="seconds"),
        },
        path,
    )
    return _public_model_configs(path)


def _safe_segment(value: str, fallback: str = "默认") -> str:
    clean = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(value or "").strip(" ._"))
    return clean[:60] or fallback


def _doc_description_path(store: ProjectMaterialStore, project_id: str) -> tuple[Path, Path]:
    return _admin_json_path(store, project_id, "knowledge/document_descriptions.json")


DEFAULT_KNOWLEDGE_BASE_NAME = "公司私信知识库"
RECRUITMENT_KNOWLEDGE_BASE_NAME = "招聘知识库"


def _scene_templates_path(store: ProjectMaterialStore, project_id: str) -> tuple[Path, Path]:
    return _admin_json_path(store, project_id, "scene_templates.json")


def _activity_settings_path(store: ProjectMaterialStore, project_id: str) -> tuple[Path, Path]:
    return _admin_json_path(store, project_id, "activity_settings.json")


def _identity_settings_path(store: ProjectMaterialStore, project_id: str) -> tuple[Path, Path]:
    return _admin_json_path(store, project_id, "identity_settings.json")


def _empty_document_descriptions(project_id: str) -> Dict[str, Any]:
    domains: List[Dict[str, Any]] = []
    return {
        "version": 1,
        "project_id": project_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "knowledge_bases": [
            {
                "kb_id": _knowledge_base_id(DEFAULT_KNOWLEDGE_BASE_NAME),
                "name": DEFAULT_KNOWLEDGE_BASE_NAME,
                "description": "用于公司业务、视频评论、私信承接、活动钩子和客户沟通的资料。",
                "domains": domains,
            },
            {
                "kb_id": _knowledge_base_id(RECRUITMENT_KNOWLEDGE_BASE_NAME),
                "name": RECRUITMENT_KNOWLEDGE_BASE_NAME,
                "description": "用于招聘评论、候选人私信、岗位咨询、面试邀约和招聘合规沟通的资料。",
                "domains": [],
            }
        ],
        "domains": domains,
    }


def _knowledge_base_id(name: str) -> str:
    return "kb_" + uuid.uuid5(uuid.NAMESPACE_URL, str(name or DEFAULT_KNOWLEDGE_BASE_NAME)).hex[:10]


def _domain_id(name: str) -> str:
    return "domain_" + uuid.uuid5(uuid.NAMESPACE_URL, str(name or "默认领域")).hex[:10]


def _section_id(domain_name: str, section_name: str, knowledge_base_name: str = DEFAULT_KNOWLEDGE_BASE_NAME) -> str:
    return "section_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{knowledge_base_name}:{domain_name}:{section_name}").hex[:10]


def _normalize_description_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    legacy_domains = data.get("domains") if isinstance(data.get("domains"), list) else []
    knowledge_bases = data.get("knowledge_bases") if isinstance(data.get("knowledge_bases"), list) else []

    if not knowledge_bases:
        knowledge_bases = [
            {
                "kb_id": _knowledge_base_id(DEFAULT_KNOWLEDGE_BASE_NAME),
                "name": DEFAULT_KNOWLEDGE_BASE_NAME,
                "description": "用于公司业务、视频评论、私信承接、活动钩子和客户沟通的资料。",
                "domains": legacy_domains,
            }
        ]

    normalized_bases = []
    seen_base_names = set()
    for index, base in enumerate(knowledge_bases):
        if not isinstance(base, dict):
            continue
        base_name = str(base.get("name") or "").strip() or (
            DEFAULT_KNOWLEDGE_BASE_NAME if index == 0 else f"知识库{index + 1}"
        )
        if base_name in seen_base_names:
            base_name = f"{base_name}_{index + 1}"
        seen_base_names.add(base_name)
        domains = base.get("domains") if isinstance(base.get("domains"), list) else []
        normalized_bases.append({
            **base,
            "kb_id": base.get("kb_id") or _knowledge_base_id(base_name),
            "name": base_name,
            "description": str(base.get("description") or "").strip(),
            "domains": domains,
        })

    if not normalized_bases:
        normalized_bases.append({
            "kb_id": _knowledge_base_id(DEFAULT_KNOWLEDGE_BASE_NAME),
            "name": DEFAULT_KNOWLEDGE_BASE_NAME,
            "description": "用于公司业务、视频评论、私信承接、活动钩子和客户沟通的资料。",
            "domains": legacy_domains,
        })

    default_base = next(
        (base for base in normalized_bases if base.get("name") == DEFAULT_KNOWLEDGE_BASE_NAME),
        normalized_bases[0],
    )
    existing_domain_names = {str(domain.get("name") or "") for domain in default_base.get("domains", [])}
    for legacy_domain in legacy_domains:
        legacy_name = str((legacy_domain or {}).get("name") or "")
        if legacy_name and legacy_name not in existing_domain_names:
            default_base.setdefault("domains", []).append(legacy_domain)
            existing_domain_names.add(legacy_name)

    data["knowledge_bases"] = normalized_bases
    data["domains"] = default_base.setdefault("domains", [])
    return data


def _ensure_description_domain(
    data: Dict[str, Any],
    domain_name: str,
    section_name: str,
    knowledge_base_name: str = DEFAULT_KNOWLEDGE_BASE_NAME,
) -> Dict[str, Any]:
    data = _normalize_description_structure(data)
    knowledge_base_name = knowledge_base_name or DEFAULT_KNOWLEDGE_BASE_NAME
    bases = data.setdefault("knowledge_bases", [])
    base = next((item for item in bases if item.get("name") == knowledge_base_name), None)
    if not base:
        base = {
            "kb_id": _knowledge_base_id(knowledge_base_name),
            "name": knowledge_base_name,
            "description": "",
            "domains": [],
        }
        bases.append(base)
    domains = base.setdefault("domains", [])
    domain_name = domain_name or "默认领域"
    section_name = section_name or "默认板块"
    domain = next((item for item in domains if item.get("name") == domain_name), None)
    if not domain:
        domain = {"domain_id": _domain_id(domain_name), "name": domain_name, "sections": []}
        domains.append(domain)
    sections = domain.setdefault("sections", [])
    section = next((item for item in sections if item.get("name") == section_name), None)
    if not section:
        section = {
            "section_id": _section_id(domain_name, section_name, knowledge_base_name),
            "name": section_name,
            "documents": [],
        }
        sections.append(section)
    data["domains"] = (next(
        (item for item in bases if item.get("name") == DEFAULT_KNOWLEDGE_BASE_NAME),
        bases[0],
    )).setdefault("domains", [])
    return section


def _flatten_description_documents(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = _normalize_description_structure(data)
    docs = []
    for base in data.get("knowledge_bases", []):
        for domain in base.get("domains", []):
            for section in domain.get("sections", []):
                for doc in section.get("documents", []):
                    docs.append({
                        **doc,
                        "knowledge_base": base.get("name", ""),
                        "knowledge_base_id": base.get("kb_id", ""),
                        "domain": domain.get("name", ""),
                        "section": section.get("name", ""),
                        "domain_id": domain.get("domain_id", ""),
                        "section_id": section.get("section_id", ""),
                    })
    return docs


def _suggest_sender_identity_from_doc(doc: Dict[str, Any], domain: str = "", section: str = "") -> str:
    identity = str(doc.get("sender_identity") or "").strip()
    if ProjectMaterialStore._is_valid_sender_identity(identity):
        return identity
    return ProjectMaterialStore._suggest_sender_identity_by_rules(
        f"{domain}/{section}",
        str(doc.get("title") or doc.get("source_file_name") or doc.get("relative_path") or ""),
        " ".join([
            str(doc.get("description") or ""),
            str(doc.get("summary") or ""),
            " ".join(str(tag) for tag in doc.get("tags") or []),
        ]),
    )


def _description_path_key(relative_path: Any) -> str:
    return str(relative_path or "").replace("\\", "/")


def _clean_document_text_for_summary(content: str) -> str:
    lines = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("> 来源文件") or line.startswith("> 分类") or line.startswith("> 读取说明"):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*•]\s*", "", line)
        lines.append(line)
    return re.sub(r"\s+", " ", "；".join(lines)).strip()


def _summarize_document_content(content: str, max_chars: int = 260) -> str:
    clean = _clean_document_text_for_summary(content)
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean
    clipped = clean[:max_chars].rstrip("，。；、,.; ")
    return clipped + "。"


def _resolve_project_document_file(
    store: ProjectMaterialStore,
    project_id: str,
    doc: Dict[str, Any],
) -> tuple[Path, str]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    for relative in ProjectMaterialStore._knowledge_document_path_candidates(doc):
        try:
            _, path, normalized_relative = _safe_project_path(store, project_dir.name, relative)
        except Exception:
            continue
        if path.exists() and path.is_file():
            return path, normalized_relative
    fallback = str(doc.get("relative_path") or "").replace("\\", "/").strip().lstrip("/")
    if fallback:
        try:
            _, path, normalized_relative = _safe_project_path(store, project_dir.name, fallback)
            return path, normalized_relative
        except Exception:
            pass
    return project_dir / "__missing_document__", fallback


def _read_project_document_text(
    store: ProjectMaterialStore,
    project_id: str,
    relative_path: str,
    doc: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    payload = {**(doc or {}), "relative_path": relative_path or (doc or {}).get("relative_path") or ""}
    try:
        path, normalized_relative = _resolve_project_document_file(store, project_id, payload)
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            return ProjectMaterialStore._sanitize_prompt_document_text(content), normalized_relative
    except Exception:
        return "", str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    return "", str(relative_path or "").replace("\\", "/").strip().lstrip("/")


def _summary_from_project_file(
    store: ProjectMaterialStore,
    project_id: str,
    relative_path: str,
    fallback: str = "",
    doc: Optional[Dict[str, Any]] = None,
) -> tuple[str, int, str]:
    content, resolved_relative = _read_project_document_text(store, project_id, relative_path, doc=doc)
    summary = _summarize_document_content(content)
    return summary or str(fallback or ""), len(content), resolved_relative


def _prune_missing_description_documents(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> bool:
    changed = False
    _normalize_description_structure(data)
    for knowledge_base_name, domain_name, section_name, section in _iter_description_sections(data):
        kept = []
        for doc in section.get("documents", []):
            relative_path = str(doc.get("relative_path") or "").strip()
            if not relative_path:
                kept.append(doc)
                continue
            target_path, _ = _resolve_project_document_file(
                store,
                project_id,
                {
                    **doc,
                    "knowledge_base": knowledge_base_name,
                    "domain": domain_name,
                    "section": section_name,
                },
            )
            if target_path.exists() and target_path.is_file():
                kept.append(doc)
            else:
                changed = True
        section["documents"] = kept
    if changed:
        _remove_empty_sections_and_domains(data)
    return changed


def _iter_description_sections(data: Dict[str, Any]):
    data = _normalize_description_structure(data)
    for base in data.get("knowledge_bases", []):
        base_name = str(base.get("name") or DEFAULT_KNOWLEDGE_BASE_NAME)
        for domain in base.get("domains", []):
            domain_name = str(domain.get("name") or "默认领域")
            for section in domain.get("sections", []):
                section_name = str(section.get("name") or "默认板块")
                yield base_name, domain_name, section_name, section


def _refresh_document_description_summaries(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> bool:
    changed = False
    _normalize_description_structure(data)
    for knowledge_base_name, domain_name, section_name, section in _iter_description_sections(data):
        for doc in section.get("documents", []):
            description = str(doc.get("description") or "").strip()
            summary = str(doc.get("summary") or "").strip()
            relative_path = str(doc.get("relative_path") or "").strip()
            generated_summary, content_chars, resolved_relative = _summary_from_project_file(
                store,
                project_id,
                relative_path,
                fallback=description,
                doc={
                    **doc,
                    "knowledge_base": knowledge_base_name,
                    "domain": domain_name,
                    "section": section_name,
                },
            )
            if resolved_relative and resolved_relative != relative_path:
                doc["relative_path"] = resolved_relative
                changed = True
            if content_chars and doc.get("content_chars") != content_chars:
                doc["content_chars"] = content_chars
                changed = True
            if not (summary and summary != description):
                if generated_summary and generated_summary != summary:
                    doc["summary"] = generated_summary
                    changed = True
            sender_identity = _suggest_sender_identity_from_doc(
                doc,
                f"{knowledge_base_name}/{domain_name}",
                section_name,
            )
            if sender_identity and doc.get("sender_identity") != sender_identity:
                doc["sender_identity"] = sender_identity
                changed = True
    return changed


def _sync_chunks_from_document_descriptions(
    project_dir: Path,
    data: Dict[str, Any],
) -> None:
    chunks_path = project_dir / "knowledge" / "chunks.jsonl"
    if not chunks_path.exists():
        return

    docs_by_id = {
        str(doc.get("doc_id") or ""): doc
        for doc in _flatten_description_documents(data)
        if doc.get("doc_id")
    }
    changed = False
    lines = []
    for line in chunks_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)
            continue

        doc = docs_by_id.get(str(item.get("doc_id") or ""))
        if doc:
            relative_path = str(doc.get("relative_path") or "").replace("\\", "/").strip()
            if relative_path and item.get("relative_path") != relative_path:
                item["relative_path"] = relative_path
                changed = True
            source_file_name = str(doc.get("source_file_name") or "").strip()
            if source_file_name and item.get("source_file_name") != source_file_name:
                item["source_file_name"] = source_file_name
                changed = True
            sender_identity = _suggest_sender_identity_from_doc(
                doc,
                f"{doc.get('knowledge_base') or DEFAULT_KNOWLEDGE_BASE_NAME}/{doc.get('domain') or ''}",
                str(doc.get("section") or ""),
            )
            if sender_identity and item.get("sender_identity") != sender_identity:
                item["sender_identity"] = sender_identity
                changed = True
        lines.append(json.dumps(item, ensure_ascii=False))

    if changed:
        chunks_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _sync_manifest_from_document_descriptions(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> None:
    project_dir, _ = _project_root_and_meta(store, project_id)
    manifest_path = project_dir / "knowledge" / "manifest.json"
    manifest = _load_json_file(manifest_path, {"version": 1, "documents": []})
    docs_by_key: Dict[str, Dict[str, Any]] = {}
    for doc in _flatten_description_documents(data):
        knowledge_base_name = str(doc.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME)
        domain_name = str(doc.get("domain") or "默认领域")
        section_name = str(doc.get("section") or "默认板块")
        enriched = {
            **doc,
            "knowledge_base": knowledge_base_name,
            "domain": domain_name,
            "section": section_name,
            "category": doc.get("category") or domain_name,
            "keywords": doc.get("tags") or doc.get("keywords") or [],
            "sender_identity": _suggest_sender_identity_from_doc(
                doc,
                f"{knowledge_base_name}/{domain_name}",
                section_name,
            ),
        }
        if enriched.get("doc_id"):
            docs_by_key[f"id:{enriched['doc_id']}"] = enriched
        if enriched.get("relative_path"):
            docs_by_key[f"path:{_description_path_key(enriched.get('relative_path'))}"] = enriched

    synced = []
    for item in manifest.get("documents", []):
        key = f"id:{item.get('doc_id')}" if item.get("doc_id") else ""
        desc = docs_by_key.get(key)
        if not desc and item.get("relative_path"):
            desc = docs_by_key.get(f"path:{_description_path_key(item.get('relative_path'))}")
        if desc:
            merged = {
                **item,
                "title": desc.get("title") or item.get("title"),
                "category": desc.get("category") or item.get("category"),
                "knowledge_base": desc.get("knowledge_base") or item.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME,
                "domain": desc.get("domain") or item.get("domain") or desc.get("category") or item.get("category") or "",
                "section": desc.get("section") or item.get("section") or "",
                "relative_path": desc.get("relative_path") or item.get("relative_path"),
                "description": desc.get("description") or item.get("description"),
                "keywords": desc.get("keywords") or item.get("keywords") or [],
                "source_file_name": desc.get("source_file_name") or item.get("source_file_name") or "",
                "content_chars": desc.get("content_chars") or item.get("content_chars") or 0,
                "sender_identity": desc.get("sender_identity") or item.get("sender_identity") or "品牌客服",
            }
            synced.append(merged)

    existing_ids = {item.get("doc_id") for item in synced if item.get("doc_id")}
    for doc in _flatten_description_documents(data):
        doc_id = doc.get("doc_id")
        relative_path = doc.get("relative_path")
        if not doc_id or doc_id in existing_ids or not relative_path:
            continue
        if not (project_dir / str(relative_path)).is_file():
            continue
        knowledge_base_name = str(doc.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME)
        domain_name = str(doc.get("domain") or "默认领域")
        section_name = str(doc.get("section") or "默认板块")
        synced.append({
            "doc_id": doc_id,
            "title": doc.get("title") or doc.get("source_file_name") or relative_path,
            "category": doc.get("category") or domain_name,
            "knowledge_base": knowledge_base_name,
            "domain": domain_name,
            "section": section_name,
            "relative_path": relative_path,
            "description": doc.get("description") or "",
            "keywords": doc.get("tags") or [],
            "source_file_name": doc.get("source_file_name") or "",
            "imported_at": doc.get("updated_at") or "",
            "content_chars": doc.get("content_chars") or 0,
            "sender_identity": _suggest_sender_identity_from_doc(doc, f"{knowledge_base_name}/{domain_name}", section_name),
        })
        existing_ids.add(doc_id)

    manifest["version"] = manifest.get("version") or 1
    manifest["documents"] = synced
    _write_json_file(manifest_path, manifest)
    _sync_chunks_from_document_descriptions(project_dir, data)


def _manifest_missing_description_documents(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> bool:
    project_dir, _ = _project_root_and_meta(store, project_id)
    manifest_path = project_dir / "knowledge" / "manifest.json"
    manifest = _load_json_file(manifest_path, {"documents": []})
    manifest_paths = {
        _description_path_key(item.get("relative_path"))
        for item in manifest.get("documents", [])
        if isinstance(item, dict) and item.get("relative_path")
    }
    for doc in _flatten_description_documents(data):
        relative_path = _description_path_key(doc.get("relative_path"))
        if relative_path and relative_path not in manifest_paths and (project_dir / relative_path).is_file():
            return True
    return False


def _document_descriptions_from_manifest(store: ProjectMaterialStore, project_id: str) -> Dict[str, Any]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    data = _empty_document_descriptions(project_dir.name)
    for doc in store.list_knowledge_documents(project_dir.name):
        knowledge_base_name = str(doc.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME)
        domain_name = str(doc.get("domain") or doc.get("category") or "默认领域")
        section_name = str(doc.get("section") or "默认板块")
        summary, content_chars, resolved_relative = _summary_from_project_file(
            store,
            project_dir.name,
            str(doc.get("relative_path") or ""),
            fallback=doc.get("description") or "",
            doc=doc,
        )
        section = _ensure_description_domain(data, domain_name, section_name, knowledge_base_name)
        section.setdefault("documents", []).append({
            "doc_id": doc.get("doc_id") or uuid.uuid4().hex,
            "title": doc.get("title") or doc.get("source_file_name") or doc.get("relative_path"),
            "source_file_name": doc.get("source_file_name") or "",
            "relative_path": resolved_relative or doc.get("relative_path") or "",
            "description": doc.get("description") or "",
            "summary": summary,
            "tags": doc.get("keywords") or [],
            "sender_identity": doc.get("sender_identity") or _suggest_sender_identity_from_doc(doc, f"{knowledge_base_name}/{domain_name}", section_name),
            "content_chars": doc.get("content_chars") or content_chars,
            "updated_at": doc.get("imported_at") or "",
        })
    return data


def _merge_manifest_documents_into_descriptions(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> bool:
    changed = False
    data = _normalize_description_structure(data)
    existing_doc_ids = {str(doc.get("doc_id") or "") for doc in _flatten_description_documents(data)}
    existing_paths = {_description_path_key(doc.get("relative_path")) for doc in _flatten_description_documents(data)}
    for doc in store.list_knowledge_documents(project_id):
        doc_id = str(doc.get("doc_id") or "").strip()
        relative_path = _description_path_key(doc.get("relative_path"))
        if not doc_id or not relative_path:
            continue
        if doc_id in existing_doc_ids or relative_path in existing_paths:
            continue
        knowledge_base_name = str(doc.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME)
        domain_name = str(doc.get("domain") or doc.get("category") or "默认领域")
        section_name = str(doc.get("section") or "默认板块")
        summary, content_chars, resolved_relative = _summary_from_project_file(
            store,
            project_id,
            relative_path,
            fallback=doc.get("description") or "",
            doc=doc,
        )
        section = _ensure_description_domain(data, domain_name, section_name, knowledge_base_name)
        section.setdefault("documents", []).append({
            "doc_id": doc_id,
            "title": doc.get("title") or doc.get("source_file_name") or relative_path,
            "source_file_name": doc.get("source_file_name") or "",
            "relative_path": resolved_relative or relative_path,
            "description": doc.get("description") or "",
            "summary": summary,
            "tags": doc.get("keywords") or [],
            "sender_identity": doc.get("sender_identity") or _suggest_sender_identity_from_doc(
                doc,
                f"{knowledge_base_name}/{domain_name}",
                section_name,
            ),
            "content_chars": doc.get("content_chars") or content_chars,
            "updated_at": doc.get("imported_at") or "",
        })
        existing_doc_ids.add(doc_id)
        existing_paths.add(relative_path)
        changed = True
    return changed


def _doc_id_from_relative_path(relative_path: str) -> str:
    return "doc_" + uuid.uuid5(uuid.NAMESPACE_URL, relative_path).hex[:12]


def _section_from_filesystem_path(parts: List[str]) -> str:
    if len(parts) <= 4:
        return "README与路由" if parts[-1].lower() == "readme.md" else "默认板块"
    return parts[-2] or "默认板块"


def _merge_filesystem_documents_into_descriptions(
    store: ProjectMaterialStore,
    project_id: str,
    data: Dict[str, Any],
) -> bool:
    project_dir, _ = _project_root_and_meta(store, project_id)
    files_dir = project_dir / "knowledge" / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        return False

    data = _normalize_description_structure(data)
    existing_paths = {_description_path_key(doc.get("relative_path")) for doc in _flatten_description_documents(data)}
    changed = False
    allowed_suffixes = {".md", ".txt", ".json"}

    for path in sorted(files_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        try:
            relative_path = path.relative_to(project_dir).as_posix()
        except ValueError:
            continue
        relative_key = _description_path_key(relative_path)
        if relative_key in existing_paths:
            continue
        parts = Path(relative_path).parts
        if len(parts) < 4 or parts[0] != "knowledge" or parts[1] != "files":
            continue

        knowledge_base_name = parts[2] or DEFAULT_KNOWLEDGE_BASE_NAME
        domain_name = parts[3] if len(parts) >= 5 else ("README与路由" if path.name.lower() == "readme.md" else "默认领域")
        section_name = _section_from_filesystem_path(list(parts))
        content = path.read_text(encoding="utf-8", errors="replace")
        title = path.stem if path.stem.lower() != "readme" else f"{domain_name} README"
        summary = _summarize_document_content(content)
        doc = {
            "doc_id": _doc_id_from_relative_path(relative_key),
            "title": title,
            "source_file_name": path.name,
            "relative_path": relative_path,
            "description": summary,
            "summary": summary,
            "tags": [item for item in [knowledge_base_name, domain_name, section_name] if item and item != "默认板块"],
            "sender_identity": "",
            "content_chars": len(content),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }
        doc["sender_identity"] = _suggest_sender_identity_from_doc(
            doc,
            f"{knowledge_base_name}/{domain_name}",
            section_name,
        )
        section = _ensure_description_domain(data, domain_name, section_name, knowledge_base_name)
        section.setdefault("documents", []).append(doc)
        existing_paths.add(relative_key)
        changed = True

    return changed


def _load_document_descriptions(store: ProjectMaterialStore, project_id: str) -> Dict[str, Any]:
    project_dir, path = _doc_description_path(store, project_id)
    data = _load_json_file(path, {})
    if not data.get("domains") and not data.get("knowledge_bases"):
        data = _document_descriptions_from_manifest(store, project_dir.name)
        _write_json_file(path, data)
    else:
        data = _normalize_description_structure(data)
        manifest_changed = _merge_manifest_documents_into_descriptions(store, project_dir.name, data)
        filesystem_changed = _merge_filesystem_documents_into_descriptions(store, project_dir.name, data)
        pruned_missing = _prune_missing_description_documents(store, project_dir.name, data)
        if manifest_changed or filesystem_changed or pruned_missing:
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _write_json_file(path, data)
            _sync_manifest_from_document_descriptions(store, project_dir.name, data)
    if _refresh_document_description_summaries(store, project_dir.name, data):
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json_file(path, data)
        _sync_manifest_from_document_descriptions(store, project_dir.name, data)
    elif _manifest_missing_description_documents(store, project_dir.name, data):
        _sync_manifest_from_document_descriptions(store, project_dir.name, data)
    data["project_id"] = project_dir.name
    return data


def _save_document_descriptions(store: ProjectMaterialStore, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    project_dir, path = _doc_description_path(store, project_id)
    if not isinstance(data.get("domains"), list) and not isinstance(data.get("knowledge_bases"), list):
        raise WebInputError("document descriptions must include knowledge_bases or domains list")
    data = _normalize_description_structure(data)
    _refresh_document_description_summaries(store, project_dir.name, data)
    data["version"] = data.get("version") or 1
    data["project_id"] = project_dir.name
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_file(path, data)
    _sync_manifest_from_document_descriptions(store, project_dir.name, data)
    return data


def _empty_scene_templates(project_id: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "project_id": project_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "templates": [],
    }


def _load_scene_templates(store: ProjectMaterialStore, project_id: str) -> Dict[str, Any]:
    project_dir, path = _scene_templates_path(store, project_id)
    data = _load_json_file(path, _empty_scene_templates(project_dir.name))
    if not isinstance(data.get("templates"), list):
        data["templates"] = []
    data["project_id"] = project_dir.name
    return data


def _save_scene_templates(store: ProjectMaterialStore, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    project_dir, path = _scene_templates_path(store, project_id)
    data["version"] = data.get("version") or 1
    data["project_id"] = project_dir.name
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_file(path, data)
    return data


def _empty_activity_settings(project_id: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "project_id": project_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "activities": [],
    }


def _load_activity_settings(store: ProjectMaterialStore, project_id: str) -> Dict[str, Any]:
    project_dir, path = _activity_settings_path(store, project_id)
    data = _load_json_file(path, _empty_activity_settings(project_dir.name))
    if not isinstance(data.get("activities"), list):
        data["activities"] = []
    data["project_id"] = project_dir.name
    return data


def _save_activity_settings(store: ProjectMaterialStore, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    project_dir, path = _activity_settings_path(store, project_id)
    data["version"] = data.get("version") or 1
    data["project_id"] = project_dir.name
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_file(path, data)
    return data


def _empty_identity_settings(project_id: str) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "version": 1,
        "project_id": project_id,
        "updated_at": now,
        "default_sender_identity": "品牌客服",
        "accounts": [
            {
                "account_id": "douyin_health_assistant",
                "account_name": "健康抖音号",
                "platform": "抖音",
                "sender_identity": "健康顾问助理",
                "description": "用于大健康、睡眠、膝骨关节干细胞、康养相关评论的首次私信和后续私信承接。",
                "tags": ["大健康", "睡眠", "膝骨关节", "干细胞", "康养", "抖音"],
                "enabled": True,
            },
            {
                "account_id": "douyin_ai_ops",
                "account_name": "AI运营号",
                "platform": "抖音",
                "sender_identity": "运营顾问",
                "description": "用于AI私信系统、文生视频、评论采集、智能硬件和运营工具相关咨询。",
                "tags": ["AI私信", "文生视频", "评论采集", "智能硬件", "运营"],
                "enabled": True,
            },
            {
                "account_id": "douyin_cross_border_ops",
                "account_name": "跨境运营号",
                "platform": "抖音",
                "sender_identity": "跨境运营顾问",
                "description": "用于跨境电商托管、AI选品、内容托管和海外市场测试相关咨询。",
                "tags": ["跨境", "电商", "托管", "AI选品", "海外市场"],
                "enabled": True,
            },
        ],
        "products": [
            {
                "product_id": "health_overview",
                "product_name": "大健康医疗服务",
                "sender_identity": "健康顾问助理",
                "related_doc_ids": ["healthcare_medical", "mock_health_compliance"],
                "tags": ["大健康", "医疗", "康养", "干细胞", "PRP"],
                "enabled": True,
            },
            {
                "product_id": "sleep_assessment",
                "product_name": "睡眠状态初评",
                "sender_identity": "健康顾问助理",
                "related_doc_ids": ["mock_sleep_assessment", "mock_health_compliance"],
                "tags": ["睡眠", "失眠", "睡不好", "压力大", "初评"],
                "enabled": True,
            },
            {
                "product_id": "joint_assessment",
                "product_name": "大健康膝骨关节干细胞",
                "sender_identity": "健康顾问助理",
                "related_doc_ids": ["healthcare_medical", "mock_joint_assessment", "mock_health_compliance"],
                "tags": ["膝骨关节", "膝盖疼", "上下楼疼", "骨积液", "软骨磨损", "干细胞", "PRP"],
                "enabled": True,
            },
            {
                "product_id": "ai_dm_system",
                "product_name": "自动私信引流系统",
                "sender_identity": "运营顾问",
                "related_doc_ids": ["ai_technology_hardware", "mock_ai_dm_system", "mock_conversion_hooks"],
                "tags": ["AI私信", "自动回复", "评论采集", "私域引流", "模型切换"],
                "enabled": True,
            },
            {
                "product_id": "ai_content_hardware",
                "product_name": "AI内容生产与智能硬件",
                "sender_identity": "运营顾问",
                "related_doc_ids": ["ai_technology_hardware", "mock_ai_dm_system"],
                "tags": ["文生视频", "批量剪辑", "自动发布", "智能硬件", "录音转纪要"],
                "enabled": True,
            },
            {
                "product_id": "cross_border_operation",
                "product_name": "跨境电商托管",
                "sender_identity": "跨境运营顾问",
                "related_doc_ids": ["cross_border_services", "mock_cross_border_operation"],
                "tags": ["跨境", "跨境电商", "货源", "店铺托管", "AI选品"],
                "enabled": True,
            },
            {
                "product_id": "general_brand_service",
                "product_name": "综合企业服务咨询",
                "sender_identity": "品牌客服",
                "related_doc_ids": ["company_positioning", "mock_business_overview", "mock_conversion_hooks"],
                "tags": ["公司介绍", "综合服务", "资料包", "承接方式"],
                "enabled": True,
            },
        ],
    }


def _normalize_identity_settings_display(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data
    cleaned = dict(data)
    cleaned["default_sender_identity"] = _display_sender_identity(cleaned.get("default_sender_identity")) or "品牌客服"
    accounts = []
    for item in cleaned.get("accounts", []) if isinstance(cleaned.get("accounts"), list) else []:
        if not isinstance(item, dict):
            continue
        cleaned_item = dict(item)
        for key in ("account_name", "sender_identity", "platform", "description"):
            if cleaned_item.get(key):
                cleaned_item[key] = _display_sender_identity(cleaned_item.get(key))
        accounts.append(cleaned_item)
    cleaned["accounts"] = accounts
    products = []
    for item in cleaned.get("products", []) if isinstance(cleaned.get("products"), list) else []:
        if not isinstance(item, dict):
            continue
        cleaned_item = dict(item)
        for key in ("product_name", "sender_identity", "description"):
            if cleaned_item.get(key):
                cleaned_item[key] = _display_sender_identity(cleaned_item.get(key))
        products.append(cleaned_item)
    cleaned["products"] = products
    return cleaned


def _load_identity_settings(store: ProjectMaterialStore, project_id: str) -> Dict[str, Any]:
    project_dir, path = _identity_settings_path(store, project_id)
    data = _load_json_file(path, {})
    if not isinstance(data, dict) or not data:
        data = _empty_identity_settings(project_dir.name)
        _write_json_file(path, data)
    if not isinstance(data.get("accounts"), list):
        data["accounts"] = []
    if not isinstance(data.get("products"), list):
        data["products"] = []
    data["version"] = data.get("version") or 1
    data["project_id"] = project_dir.name
    data = _normalize_identity_settings_display(data)
    if not ProjectMaterialStore._is_valid_sender_identity(data.get("default_sender_identity")):
        data["default_sender_identity"] = "品牌客服"
    return data


def _save_identity_settings(store: ProjectMaterialStore, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    project_dir, path = _identity_settings_path(store, project_id)
    if not isinstance(data.get("accounts"), list):
        raise WebInputError("identity settings must include accounts list")
    if not isinstance(data.get("products"), list):
        raise WebInputError("identity settings must include products list")
    data["version"] = data.get("version") or 1
    data["project_id"] = project_dir.name
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    data = _normalize_identity_settings_display(data)
    if not ProjectMaterialStore._is_valid_sender_identity(data.get("default_sender_identity")):
        data["default_sender_identity"] = "品牌客服"
    _write_json_file(path, data)
    return data


def build_admin_state_response(
    project_store: Optional[ProjectMaterialStore] = None,
    project_id: str = "",
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_dir, project_meta = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    templates = _load_scene_templates(store, project_dir.name)
    activities = _load_activity_settings(store, project_dir.name)
    identities = _load_identity_settings(store, project_dir.name)
    return {
        "projects": store.list_projects(),
        "project": {
            "project_id": project_dir.name,
            "name": project_meta.get("name") or project_dir.name,
            "path": str(project_dir),
        },
        "document_descriptions": descriptions,
        "scene_templates": templates,
        "activity_settings": activities,
        "identity_settings": identities,
    }


def build_admin_knowledge_save_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    descriptions = payload.get("document_descriptions")
    if not isinstance(descriptions, dict):
        raise WebInputError("document_descriptions is required")
    saved = _save_document_descriptions(store, project_id, descriptions)
    return {"document_descriptions": saved}


def _remove_document_from_descriptions(
    descriptions: Dict[str, Any],
    doc_id: str,
    relative_path: str,
) -> Optional[Dict[str, Any]]:
    removed = None
    normalized_relative = str(relative_path or "").replace("\\", "/")
    descriptions = _normalize_description_structure(descriptions)
    for _, _, _, section in _iter_description_sections(descriptions):
        kept = []
        for doc in section.get("documents", []):
            same_doc_id = doc_id and doc.get("doc_id") == doc_id
            same_path = normalized_relative and str(doc.get("relative_path") or "").replace("\\", "/") == normalized_relative
            if same_doc_id or same_path:
                removed = doc
                continue
            kept.append(doc)
        section["documents"] = kept
    return removed


def _remove_empty_sections_and_domains(descriptions: Dict[str, Any]) -> bool:
    descriptions = _normalize_description_structure(descriptions)
    before = json.dumps(descriptions.get("knowledge_bases", []), ensure_ascii=False, sort_keys=True)
    kept_bases = []
    for base in descriptions.get("knowledge_bases", []):
        kept_domains = []
        for domain in base.get("domains", []):
            kept_sections = []
            for section in domain.get("sections", []):
                if section.get("documents"):
                    kept_sections.append(section)
            domain["sections"] = kept_sections
            if kept_sections:
                kept_domains.append(domain)
        base["domains"] = kept_domains
        if kept_domains:
            kept_bases.append(base)
    descriptions["knowledge_bases"] = kept_bases
    if kept_bases:
        descriptions["domains"] = (next(
            (item for item in kept_bases if item.get("name") == DEFAULT_KNOWLEDGE_BASE_NAME),
            kept_bases[0],
        )).setdefault("domains", [])
    else:
        descriptions["domains"] = []
    after = json.dumps(descriptions.get("knowledge_bases", []), ensure_ascii=False, sort_keys=True)
    return before != after


def _remove_document_from_manifest(project_dir: Path, doc_id: str, relative_path: str) -> bool:
    manifest_path = project_dir / "knowledge" / "manifest.json"
    manifest = _load_json_file(manifest_path, {"version": 1, "documents": []})
    normalized_relative = str(relative_path or "").replace("\\", "/")
    documents = []
    removed = False
    removed_doc_id = doc_id
    for doc in manifest.get("documents", []):
        same_doc_id = doc_id and doc.get("doc_id") == doc_id
        same_path = normalized_relative and str(doc.get("relative_path") or "").replace("\\", "/") == normalized_relative
        if same_doc_id or same_path:
            removed = True
            removed_doc_id = removed_doc_id or str(doc.get("doc_id") or "")
            continue
        documents.append(doc)

    manifest["version"] = manifest.get("version") or 1
    manifest["documents"] = documents
    if removed_doc_id:
        deleted_doc_ids = set(manifest.get("deleted_doc_ids") or [])
        deleted_doc_ids.add(removed_doc_id)
        manifest["deleted_doc_ids"] = sorted(deleted_doc_ids)
    _write_json_file(manifest_path, manifest)
    return removed


def _remove_document_from_chunks(project_dir: Path, doc_id: str, relative_path: str) -> int:
    chunks_path = project_dir / "knowledge" / "chunks.jsonl"
    if not chunks_path.exists():
        return 0
    normalized_relative = str(relative_path or "").replace("\\", "/")
    kept_lines = []
    removed_count = 0
    for line in chunks_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue
        same_doc_id = doc_id and item.get("doc_id") == doc_id
        same_path = normalized_relative and str(item.get("relative_path") or "").replace("\\", "/") == normalized_relative
        if same_doc_id or same_path:
            removed_count += 1
            continue
        kept_lines.append(json.dumps(item, ensure_ascii=False))
    chunks_path.write_text(("\n".join(kept_lines) + "\n") if kept_lines else "", encoding="utf-8")
    return removed_count


def _remove_empty_parent_dirs(path: Path, stop_dir: Path) -> None:
    current = path.parent
    stop_dir = stop_dir.resolve()
    while current.exists():
        try:
            current.resolve().relative_to(stop_dir)
        except ValueError:
            break
        if current == stop_dir:
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _remove_documents_from_manifest(project_dir: Path, docs: List[Dict[str, Any]]) -> int:
    removed = 0
    for doc in docs:
        if _remove_document_from_manifest(
            project_dir,
            str(doc.get("doc_id") or ""),
            str(doc.get("relative_path") or ""),
        ):
            removed += 1
    return removed


def _remove_documents_from_chunks(project_dir: Path, docs: List[Dict[str, Any]]) -> int:
    removed = 0
    for doc in docs:
        removed += _remove_document_from_chunks(
            project_dir,
            str(doc.get("doc_id") or ""),
            str(doc.get("relative_path") or ""),
        )
    return removed


def _knowledge_folder_path(project_dir: Path, *parts: str) -> Path:
    safe_parts = [_safe_segment(part, "默认") for part in parts if str(part or "").strip()]
    return (project_dir / "knowledge" / "files" / Path(*safe_parts)).resolve()


def _ensure_inside_knowledge_files(project_dir: Path, target_path: Path) -> None:
    root = (project_dir / "knowledge" / "files").resolve()
    try:
        target_path.resolve().relative_to(root)
    except ValueError as e:
        raise WebInputError("folder is outside knowledge files") from e


def _move_knowledge_folder(project_dir: Path, old_path: Path, new_path: Path) -> bool:
    _ensure_inside_knowledge_files(project_dir, old_path)
    _ensure_inside_knowledge_files(project_dir, new_path)
    if old_path == new_path:
        return False
    if not old_path.exists():
        return False
    if new_path.exists():
        raise WebInputError("target folder already exists")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    return True


def _delete_knowledge_folder(project_dir: Path, folder_path: Path) -> bool:
    _ensure_inside_knowledge_files(project_dir, folder_path)
    if not folder_path.exists():
        return False
    if not folder_path.is_dir():
        raise WebInputError("target is not a folder")
    shutil.rmtree(folder_path)
    _remove_empty_parent_dirs(folder_path, project_dir / "knowledge" / "files")
    return True


def _replace_relative_path_prefix(relative_path: str, old_prefix: str, new_prefix: str) -> str:
    normalized = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    old_prefix = str(old_prefix or "").replace("\\", "/").strip().strip("/")
    new_prefix = str(new_prefix or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return normalized
    if normalized == old_prefix:
        return new_prefix
    if normalized.startswith(old_prefix + "/"):
        return new_prefix + normalized[len(old_prefix):]
    return normalized


def build_admin_knowledge_base_update_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    old_name = str(payload.get("old_name") or payload.get("name") or "").strip()
    new_name = str(payload.get("new_name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not old_name:
        raise WebInputError("old_name is required")
    if not new_name:
        raise WebInputError("new_name is required")

    project_dir, _ = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    bases = descriptions.get("knowledge_bases") or []
    base = next((item for item in bases if item.get("name") == old_name), None)
    if not base:
        raise WebInputError("knowledge base not found")
    if new_name != old_name and any(item.get("name") == new_name for item in bases):
        raise WebInputError("knowledge base name already exists")

    old_safe = _safe_segment(old_name, DEFAULT_KNOWLEDGE_BASE_NAME)
    new_safe = _safe_segment(new_name, DEFAULT_KNOWLEDGE_BASE_NAME)
    old_prefix = f"knowledge/files/{old_safe}"
    new_prefix = f"knowledge/files/{new_safe}"
    moved = False
    if new_name != old_name or new_safe != old_safe:
        old_path = _knowledge_folder_path(project_dir, old_name)
        new_path = _knowledge_folder_path(project_dir, new_name)
        moved = _move_knowledge_folder(project_dir, old_path, new_path)

    base["name"] = new_name
    base["kb_id"] = base.get("kb_id") or _knowledge_base_id(new_name)
    base["description"] = description
    for domain in base.get("domains", []):
        for section in domain.get("sections", []):
            for doc in section.get("documents", []):
                doc["relative_path"] = _replace_relative_path_prefix(
                    str(doc.get("relative_path") or ""),
                    old_prefix,
                    new_prefix,
                )
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)
    return {
        "document_descriptions": descriptions,
        "updated": {
            "type": "knowledge_base",
            "old_name": old_name,
            "new_name": new_name,
            "folder_moved": moved,
        },
    }


def build_admin_knowledge_domain_update_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    knowledge_base_name = str(payload.get("knowledge_base") or "").strip()
    old_name = str(payload.get("old_name") or payload.get("name") or "").strip()
    new_name = str(payload.get("new_name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not knowledge_base_name:
        raise WebInputError("knowledge_base is required")
    if not old_name:
        raise WebInputError("old_name is required")
    if not new_name:
        raise WebInputError("new_name is required")

    project_dir, _ = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    base = next((item for item in descriptions.get("knowledge_bases", []) if item.get("name") == knowledge_base_name), None)
    if not base:
        raise WebInputError("knowledge base not found")
    domains = base.get("domains") or []
    domain = next((item for item in domains if item.get("name") == old_name), None)
    if not domain:
        raise WebInputError("domain not found")
    if new_name != old_name and any(item.get("name") == new_name for item in domains):
        raise WebInputError("domain name already exists")

    base_safe = _safe_segment(knowledge_base_name, DEFAULT_KNOWLEDGE_BASE_NAME)
    old_safe = _safe_segment(old_name, "默认领域")
    new_safe = _safe_segment(new_name, "默认领域")
    old_prefix = f"knowledge/files/{base_safe}/{old_safe}"
    new_prefix = f"knowledge/files/{base_safe}/{new_safe}"
    moved = False
    if new_name != old_name or new_safe != old_safe:
        old_path = _knowledge_folder_path(project_dir, knowledge_base_name, old_name)
        new_path = _knowledge_folder_path(project_dir, knowledge_base_name, new_name)
        moved = _move_knowledge_folder(project_dir, old_path, new_path)

    domain["name"] = new_name
    domain["domain_id"] = domain.get("domain_id") or _domain_id(new_name)
    domain["description"] = description
    for section in domain.get("sections", []):
        for doc in section.get("documents", []):
            doc["relative_path"] = _replace_relative_path_prefix(
                str(doc.get("relative_path") or ""),
                old_prefix,
                new_prefix,
            )
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)
    return {
        "document_descriptions": descriptions,
        "updated": {
            "type": "domain",
            "knowledge_base": knowledge_base_name,
            "old_name": old_name,
            "new_name": new_name,
            "folder_moved": moved,
        },
    }


def build_admin_knowledge_base_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise WebInputError("name is required")

    project_dir, _ = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    bases = descriptions.get("knowledge_bases") or []
    target = next((item for item in bases if item.get("name") == name), None)
    if not target:
        raise WebInputError("knowledge base not found")
    docs = []
    for domain in target.get("domains", []):
        for section in domain.get("sections", []):
            docs.extend(section.get("documents", []))
    descriptions["knowledge_bases"] = [item for item in bases if item.get("name") != name]
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)
    manifest_removed = _remove_documents_from_manifest(project_dir, docs)
    chunks_removed = _remove_documents_from_chunks(project_dir, docs)
    folder_deleted = _delete_knowledge_folder(project_dir, _knowledge_folder_path(project_dir, name))
    return {
        "document_descriptions": descriptions,
        "deleted": {
            "type": "knowledge_base",
            "name": name,
            "documents": len(docs),
            "manifest_removed": manifest_removed,
            "chunks_removed": chunks_removed,
            "folder_deleted": folder_deleted,
        },
    }


def build_admin_knowledge_domain_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    knowledge_base_name = str(payload.get("knowledge_base") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not knowledge_base_name:
        raise WebInputError("knowledge_base is required")
    if not name:
        raise WebInputError("name is required")

    project_dir, _ = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    base = next((item for item in descriptions.get("knowledge_bases", []) if item.get("name") == knowledge_base_name), None)
    if not base:
        raise WebInputError("knowledge base not found")
    target = next((item for item in base.get("domains", []) if item.get("name") == name), None)
    if not target:
        raise WebInputError("domain not found")
    docs = []
    for section in target.get("sections", []):
        docs.extend(section.get("documents", []))
    base["domains"] = [item for item in base.get("domains", []) if item.get("name") != name]
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)
    manifest_removed = _remove_documents_from_manifest(project_dir, docs)
    chunks_removed = _remove_documents_from_chunks(project_dir, docs)
    folder_deleted = _delete_knowledge_folder(project_dir, _knowledge_folder_path(project_dir, knowledge_base_name, name))
    return {
        "document_descriptions": descriptions,
        "deleted": {
            "type": "domain",
            "knowledge_base": knowledge_base_name,
            "name": name,
            "documents": len(docs),
            "manifest_removed": manifest_removed,
            "chunks_removed": chunks_removed,
            "folder_deleted": folder_deleted,
        },
    }


def build_admin_knowledge_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    doc_id = str(payload.get("doc_id") or "").strip()
    relative_path = str(payload.get("relative_path") or "").strip()
    if not doc_id and not relative_path:
        raise WebInputError("doc_id or relative_path is required")

    project_dir, _ = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    all_docs = _flatten_description_documents(descriptions)
    target_doc = None
    if not relative_path and doc_id:
        for doc in all_docs:
            if doc.get("doc_id") == doc_id:
                target_doc = doc
                relative_path = str(doc.get("relative_path") or "").strip()
                break
    if not target_doc and doc_id:
        target_doc = next((doc for doc in all_docs if doc.get("doc_id") == doc_id), None)
    if target_doc:
        target_path, normalized_relative = _resolve_project_document_file(
            store,
            project_dir.name,
            {**target_doc, "relative_path": relative_path or target_doc.get("relative_path") or ""},
        )
    else:
        project_dir, target_path, normalized_relative = _safe_project_path(store, project_id, relative_path)
    removed_doc = _remove_document_from_descriptions(descriptions, doc_id, normalized_relative)
    if not removed_doc and relative_path and relative_path != normalized_relative:
        removed_doc = _remove_document_from_descriptions(descriptions, doc_id, relative_path)
    if removed_doc and not doc_id:
        doc_id = str(removed_doc.get("doc_id") or "")
    _remove_empty_sections_and_domains(descriptions)
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)

    manifest_removed = _remove_document_from_manifest(project_dir, doc_id, normalized_relative)
    removed_chunks = _remove_document_from_chunks(project_dir, doc_id, normalized_relative)
    file_deleted = False
    if target_path.exists() and target_path.is_file():
        target_path.unlink()
        file_deleted = True
        _remove_empty_parent_dirs(target_path, project_dir / "knowledge" / "files")

    if not removed_doc and not manifest_removed and not file_deleted and removed_chunks == 0:
        raise WebInputError("document not found")

    return {
        "document_descriptions": descriptions,
        "deleted": {
            "doc_id": doc_id,
            "relative_path": normalized_relative,
            "file_deleted": file_deleted,
            "manifest_removed": manifest_removed,
            "chunks_removed": removed_chunks,
        },
    }


def _open_location_in_file_manager(target_path: Path) -> None:
    if os.name == "nt":
        if target_path.is_file():
            subprocess.Popen(["explorer.exe", f"/select,{target_path}"])
        else:
            os.startfile(str(target_path))  # type: ignore[attr-defined]
        return

    if sys.platform == "darwin":
        if target_path.is_file():
            subprocess.Popen(["open", "-R", str(target_path)])
        else:
            subprocess.Popen(["open", str(target_path)])
        return

    folder = target_path.parent if target_path.is_file() else target_path
    subprocess.Popen(["xdg-open", str(folder)])


def build_admin_open_file_location_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
    opener: Optional[Any] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    relative_path = str(payload.get("relative_path") or "").strip()
    open_type = str(payload.get("type") or "file").strip()
    if open_type == "folder":
        _, target_path, normalized_relative = _safe_project_folder_path(store, project_id, relative_path)
    else:
        _, target_path, normalized_relative = _safe_project_path(store, project_id, relative_path)
    if not target_path.exists():
        raise WebInputError("path does not exist")

    (opener or _open_location_in_file_manager)(target_path)
    return {
        "relative_path": normalized_relative,
        "absolute_path": str(target_path),
        "opened": True,
    }


def build_admin_knowledge_upload_response(
    file_name: str,
    file_data: bytes,
    project_id: str = "",
    knowledge_base: str = "",
    domain: str = "",
    section: str = "",
    llm_tools: Any = None,
    model_conf: Optional[Dict[str, Any]] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_dir, _ = _project_root_and_meta(store, project_id)
    if not file_name or not file_name.strip():
        raise WebInputError("file is required")
    if not file_data:
        raise WebInputError("file data is empty")

    content = store.parse_file_content(file_name, file_data).strip()
    if not content:
        raise WebInputError("file content is empty after parsing")

    meta = store._suggest_imported_document_meta(file_name, content, llm_tools, model_conf)
    knowledge_base_name = _safe_segment(
        knowledge_base or meta.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE_NAME,
        DEFAULT_KNOWLEDGE_BASE_NAME,
    )
    domain_name = _safe_segment(domain or meta.get("domain") or meta.get("category") or "导入资料", "导入资料")
    section_name = _safe_segment(section or meta.get("section") or "默认板块", "默认板块")
    title = store._safe_title(meta.get("title") or Path(file_name).stem or "导入资料")
    doc_id = "admin_" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{project_dir.name}:{knowledge_base_name}:{domain_name}:{section_name}:{file_name}:{content[:500]}:{len(content)}",
    ).hex[:12]
    relative_path = f"knowledge/files/{knowledge_base_name}/{domain_name}/{section_name}/{title}.md"
    doc_path = project_dir / relative_path
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    description = str(meta.get("description") or f"从 {file_name} 解析入库的资料").strip()
    summary = str(meta.get("summary") or "").strip() or _summarize_document_content(content)
    tags = store._normalize_keywords(meta.get("keywords") or [])
    sender_identity = str(
        meta.get("sender_identity")
        if store._is_valid_sender_identity(meta.get("sender_identity"))
        else store._suggest_sender_identity_by_rules(f"{knowledge_base_name}/{domain_name}", file_name, content)
    ).strip()
    doc_path.write_text(
        store._format_imported_markdown(
            title=title,
            source_file_name=file_name,
            category=domain_name,
            description=description,
            sender_identity=sender_identity,
            content=content,
        ),
        encoding="utf-8",
    )

    chunks = store._split_text(content)
    manifest_doc = {
        "doc_id": doc_id,
        "title": title,
        "category": domain_name,
        "knowledge_base": knowledge_base_name,
        "domain": domain_name,
        "section": section_name,
        "sender_identity": sender_identity,
        "relative_path": relative_path,
        "description": description,
        "keywords": tags,
        "source_file_name": file_name,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "chunk_count": len(chunks),
    }
    store._upsert_manifest_document(project_dir, manifest_doc)
    store._upsert_chunks(project_dir, doc_id, file_name, relative_path, chunks, sender_identity=sender_identity)

    descriptions = _load_document_descriptions(store, project_dir.name)
    target_section = _ensure_description_domain(descriptions, domain_name, section_name, knowledge_base_name)
    documents = [
        item for item in target_section.setdefault("documents", [])
        if item.get("doc_id") != doc_id
    ]
    document_meta = {
        "doc_id": doc_id,
        "title": title,
        "knowledge_base": knowledge_base_name,
        "domain": domain_name,
        "section": section_name,
        "source_file_name": file_name,
        "relative_path": relative_path,
        "description": description,
        "summary": summary or description,
        "tags": tags,
        "sender_identity": sender_identity,
        "content_chars": len(content),
        "chunk_count": len(chunks),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    documents.append(document_meta)
    target_section["documents"] = documents
    descriptions = _save_document_descriptions(store, project_dir.name, descriptions)

    return {
        "document": document_meta,
        "document_descriptions": descriptions,
        "manifest_document": manifest_doc,
        "absolute_path": str(doc_path),
    }


def _normalize_scene_template(raw: Dict[str, Any]) -> Dict[str, Any]:
    template_id = str(raw.get("template_id") or "").strip() or "tpl_" + uuid.uuid4().hex[:10]
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    return {
        "template_id": template_id,
        "title": _clip_text(raw.get("title"), 80) or "未命名场景",
        "purpose": _clip_text(raw.get("purpose"), 600),
        "applicable_scene": _clip_text(raw.get("applicable_scene"), 800),
        "tags": [str(item).strip() for item in tags if str(item).strip()][:12],
        "steps": [str(item).strip() for item in steps if str(item).strip()][:12],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _normalize_activity(raw: Dict[str, Any]) -> Dict[str, Any]:
    activity_id = str(raw.get("activity_id") or "").strip() or "act_" + uuid.uuid4().hex[:10]
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return {
        "activity_id": activity_id,
        "title": _clip_text(raw.get("title"), 80) or "未命名活动",
        "activity_type": _clip_text(raw.get("activity_type") or raw.get("type"), 40) or "活动",
        "applicable_scene": _clip_text(raw.get("applicable_scene"), 800),
        "description": _clip_text(raw.get("description"), 1200),
        "benefit": _clip_text(raw.get("benefit") or raw.get("offer"), 800),
        "claim_method": _clip_text(raw.get("claim_method"), 600),
        "deadline": _clip_text(raw.get("deadline"), 120),
        "quota": _clip_text(raw.get("quota"), 160),
        "compliance_note": _clip_text(raw.get("compliance_note"), 800),
        "tags": [str(item).strip() for item in tags if str(item).strip()][:12],
        "enabled": bool(raw.get("enabled", True)),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_admin_activity_save_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    activity = _normalize_activity(payload.get("activity") or {})
    data = _load_activity_settings(store, project_id)
    activities = [
        item for item in data.get("activities", [])
        if item.get("activity_id") != activity["activity_id"]
    ]
    activities.append(activity)
    data["activities"] = activities
    saved = _save_activity_settings(store, project_id, data)
    return {"activity_settings": saved, "activity": activity}


def build_admin_activity_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    activity_id = str(payload.get("activity_id") or "").strip()
    data = _load_activity_settings(store, project_id)
    data["activities"] = [
        item for item in data.get("activities", [])
        if item.get("activity_id") != activity_id
    ]
    saved = _save_activity_settings(store, project_id, data)
    return {"activity_settings": saved}


def _extract_scene_key_info(purpose: str) -> Dict[str, Any]:
    text = str(purpose or "").strip()
    lower = text.lower()
    contacts = []
    contact_accounts = set()
    contact_patterns = [
        r"(微信号|微信|vx|wx|企微|企业微信)\s*[：:=为是到加]*\s*([A-Za-z0-9_\-]{4,})",
        r"([A-Za-z][A-Za-z0-9_\-]{4,})",
    ]
    for pattern in contact_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if len(match.groups()) >= 2:
                label = match.group(1)
                account = match.group(2)
                contact = f"{label}{account}"
                contact_accounts.add(account.lower())
            else:
                account = match.group(1)
                if account.lower() in {"ai", "api", "json", "http", "https"}:
                    continue
                if account.lower() in contact_accounts:
                    continue
                contact = account
                contact_accounts.add(account.lower())
            if contact not in contacts:
                contacts.append(contact)

    platforms = []
    platform_rules = [
        ("抖音", ["抖音", "douyin"]),
        ("小红书", ["小红书", "xiaohongshu"]),
        ("视频号", ["视频号"]),
        ("快手", ["快手"]),
    ]
    for platform, keywords in platform_rules:
        if any(keyword in lower or keyword in text for keyword in keywords):
            platforms.append(platform)

    source = "用户评论/留言" if re.search(r"评论|留言", text) else "用户输入"
    channel = "私信" if re.search(r"私信|私域|dm", text, flags=re.IGNORECASE) else "私信承接"
    target = "、".join(contacts) if contacts else _clip_text(text, 80)
    return {
        "source": source,
        "channel": channel,
        "platforms": platforms,
        "contacts": contacts,
        "target": target,
    }


def build_admin_scene_template_save_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    template = _normalize_scene_template(payload.get("template") or {})
    data = _load_scene_templates(store, project_id)
    templates = [
        item for item in data.get("templates", [])
        if item.get("template_id") != template["template_id"]
    ]
    templates.append(template)
    data["templates"] = templates
    saved = _save_scene_templates(store, project_id, data)
    return {"scene_templates": saved, "template": template}


def build_admin_scene_template_delete_response(
    payload: Dict[str, Any],
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    store = _project_store(project_store)
    project_id = str(payload.get("project_id") or "").strip()
    template_id = str(payload.get("template_id") or "").strip()
    data = _load_scene_templates(store, project_id)
    data["templates"] = [
        item for item in data.get("templates", [])
        if item.get("template_id") != template_id
    ]
    saved = _save_scene_templates(store, project_id, data)
    return {"scene_templates": saved}


def _fallback_scene_template_from_purpose(purpose: str) -> Dict[str, Any]:
    purpose = purpose.strip()
    key_info = _extract_scene_key_info(purpose)
    tags = []
    if re.search(r"膝|关节|骨|疼|痛|康养|医疗", purpose, flags=re.IGNORECASE):
        tags.extend(["大健康", "关节", "售前"])
    if re.search(r"AI|剪辑|发布|数据", purpose, flags=re.IGNORECASE):
        tags.extend(["AI", "自动化", "私信"])
    if re.search(r"评论|私信", purpose, flags=re.IGNORECASE):
        tags.extend(["评论承接", "私信"])
    if re.search(r"跨境|电商|选品|海外", purpose, flags=re.IGNORECASE):
        tags.extend(["跨境", "电商", "资源对接"])
    if "抖音" in key_info["platforms"]:
        tags.extend(["抖音", "评论承接"])
    if key_info["contacts"]:
        tags.extend(["微信引流", "联系方式"])
    tags = tags or ["通用", "私信", "转化"]
    tags = list(dict.fromkeys(tags))
    platform_text = "、".join(key_info["platforms"]) or "当前平台"
    contact_text = "、".join(key_info["contacts"]) or "目标联系方式/私域入口"
    title_core = ""
    if "评论" in key_info["source"] and key_info["contacts"]:
        title_core = f"{platform_text}评论私信引导至{contact_text}"
    elif key_info["contacts"]:
        title_core = f"私信引导至{contact_text}"
    else:
        title_core = purpose[:28] or "自定义场景"
    return {
        "template_id": "tpl_" + uuid.uuid4().hex[:10],
        "title": _clip_text(title_core, 36) + "模板",
        "purpose": purpose,
        "applicable_scene": (
            f"适用于{platform_text}上用户通过评论/留言表达兴趣、疑问、痛点或转化意向后，"
            f"需要用{key_info['channel']}自然承接，并最终引导到{contact_text}继续沟通的场景。"
        ),
        "tags": tags[:8],
        "steps": [
            f"读取{key_info['source']}原文，先判断用户是在咨询、吐槽、求资料、问价格，还是只是围观。",
            "匹配对应项目资料和知识库，提取能回应这条评论的具体信息，不要只生成通用客服话术。",
            f"生成{platform_text}{key_info['channel']}首句时，先承接用户评论里的具体词和痛点，再说明来意，降低陌生触达感。",
            f"在回复中自然推进最终目标：引导用户添加或前往 {contact_text}，不能把这个账号泛化成“入口”“下一步动作”而丢失。",
            "如果平台表达联系方式较敏感，就用更自然的真人口吻分步表达，但模板中必须保留该目标账号用于后续提示词组装。",
            f"用户回复后进入私信维护，继续围绕 {contact_text} 的添加、资料发送、预约或进一步沟通推进。",
        ],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _enforce_scene_template_key_info(template: Dict[str, Any], purpose: str) -> Dict[str, Any]:
    key_info = _extract_scene_key_info(purpose)
    contacts = key_info.get("contacts") or []
    if not contacts:
        return template

    contact_text = "、".join(contacts)
    combined = "\n".join([
        str(template.get("title") or ""),
        str(template.get("purpose") or ""),
        str(template.get("applicable_scene") or ""),
        *[str(step) for step in template.get("steps") or []],
    ])
    if contact_text not in combined and not any(contact in combined for contact in contacts):
        steps = list(template.get("steps") or [])
        key_step = f"最终转化目标必须保留为：引导用户添加或前往 {contact_text}，不能改成泛化入口。"
        if len(steps) >= 8:
            steps[-1] = key_step
        else:
            steps.append(key_step)
        template["steps"] = steps
    if contact_text not in str(template.get("purpose") or "") and not any(contact in str(template.get("purpose") or "") for contact in contacts):
        template["purpose"] = f"{template.get('purpose') or purpose}；最终引导到 {contact_text}"
    return template


def build_admin_scene_template_generate_response(
    payload: Dict[str, Any],
    llm_tools: Optional[Any] = None,
) -> Dict[str, Any]:
    purpose = str(payload.get("purpose") or "").strip()
    if not purpose:
        raise WebInputError("purpose is required")
    model_conf = _optional_document_selector_conf({**payload, "use_ai_selector": True})
    template = _fallback_scene_template_from_purpose(purpose)
    tools = llm_tools
    if model_conf and tools:
        try:
            prompt = """
你是私信智能体场景模板设计助手。请根据用户输入的目的生成一个可执行场景模板。
重要规则：
- 用户目的里的平台、动作、账号、微信号、vx、wx、企微号、链接、品牌名等关键信息必须原样保留，不允许改写成“入口”“下一步动作”“私域”等泛化词。
- 如果目的里写了“最终引到微信号 x”，steps 中必须至少有一步明确写出“引导到/添加/前往 微信号 x”。
- 处理步骤必须贴合用户填写的目的链路，例如“评论留言 -> 私信 -> 微信号/目标渠道”，不要只输出通用的识别痛点、匹配资料。
- 生成内容是场景模板，不是直接回复用户的话术；但步骤要能指导后续话术生成。
字段要求：
- title: 简短标题
- purpose: 保留并优化目的
- applicable_scene: 说明适用的用户评论/私信场景
- tags: 5到10个标签
- steps: 4到8个处理步骤
只返回 JSON，字段固定为 title, purpose, applicable_scene, tags, steps。
""".strip()
            key_info = _extract_scene_key_info(purpose)
            message = "\n".join([
                f"用户填写的目的：{purpose}",
                f"系统提取到的关键信息：{json.dumps(key_info, ensure_ascii=False)}",
                "请重点围绕这些关键信息生成，不要忽略账号、平台和最终转化目标。",
            ])
            chat = getattr(tools, model_conf["func_name"])
            response = chat(
                url=model_conf["url"],
                api_key=model_conf.get("key", ""),
                prompt=prompt,
                message=message,
                model=model_conf["model_name"],
                json_format=True,
                stream=False,
                max_len_input=model_conf.get("max_len_input", 16000),
            )
            generated = ProjectMaterialStore._parse_json_response(response)
            template = _normalize_scene_template(_enforce_scene_template_key_info(
                {**generated, "purpose": generated.get("purpose") or purpose},
                purpose,
            ))
        except Exception:
            template = _fallback_scene_template_from_purpose(purpose)
    return {"template": template}


def _score_text_match(question: str, values: Iterable[Any]) -> int:
    text = question.lower()
    score = 0
    for value in values:
        item = str(value or "").strip()
        if item and item.lower() in text:
            score += 2
        for token in re.split(r"[\s,，、/；;|。.!！?？：:\-]+", item):
            token = token.strip()
            if len(token) >= 2 and token.lower() in text:
                score += 1
    return score


def _document_priority_bonus(doc_id: str, text: str) -> int:
    recruitment_intent = re.search(
        r"招聘|岗位|候选人|简历|面试|薪资|福利|hr|boss|求职|入职|到岗|工程师|项目经理",
        text,
        flags=re.I,
    )
    if doc_id.startswith("recruitment_") and not recruitment_intent:
        return -30
    if doc_id.startswith("recruitment_") and re.search(r"招聘|岗位|候选人|简历|面试|薪资|福利|hr|boss|求职|入职|到岗|工程师|项目经理", text, flags=re.I):
        return 18
    if doc_id == "recruitment_ai_project_manager" and re.search(r"ai项目经理|项目经理|产品|需求|交付|推进", text, flags=re.I):
        return 22
    if doc_id == "recruitment_engineer_role" and re.search(r"工程师|后端|前端|开发|爬虫|接口|agent|技术", text, flags=re.I):
        return 22
    if doc_id == "recruitment_interview_process" and re.search(r"面试|简历|投递|到岗|流程|方便沟通", text, flags=re.I):
        return 20
    if doc_id == "recruitment_compensation_compliance" and re.search(r"薪资|工资|待遇|福利|社保|真假|录用|承诺", text, flags=re.I):
        return 20
    if doc_id in {"mock_video_direction_samples", "mock_comment_intent_samples"}:
        return -6
    if doc_id == "mock_joint_assessment" and re.search(r"膝骨关节|膝盖|上下楼|骨积液|软骨|干细胞|prp|注射|门诊评估", text, flags=re.I):
        return 24
    if doc_id == "mock_health_compliance" and re.search(r"医疗|膝骨关节|膝盖|骨积液|软骨|干细胞|prp|注射|疗效|治|诊断", text, flags=re.I):
        return 12
    business_keywords = {
        "mock_sleep_assessment": ["睡眠", "睡不", "失眠", "入睡", "早醒", "压力", "熬夜"],
        "mock_joint_assessment": ["膝", "膝骨关节", "关节", "上下楼", "骨积液", "软骨", "软骨磨损", "老人", "疼", "肿", "干细胞", "prp", "注射", "门诊", "评估"],
        "mock_ai_dm_system": ["自动回复", "私信", "评论采集", "抖音评论", "视频摘要", "知识库", "模型", "引流系统"],
        "mock_cross_border_operation": ["跨境", "电商", "货源", "开店", "店铺", "托管", "选品", "海外"],
    }
    return 8 if any(keyword.lower() in text for keyword in business_keywords.get(doc_id, [])) else 0


def _boost_mock_business_doc_score(text: str, doc_id: str) -> int:
    groups = {
        "mock_sleep_assessment": ["睡眠", "睡不", "失眠", "入睡", "早醒", "压力", "熬夜", "精神不好"],
        "mock_joint_assessment": ["膝", "膝骨关节", "关节", "上下楼", "骨积液", "软骨", "软骨磨损", "老人", "疼", "肿", "干细胞", "prp", "注射", "门诊", "评估"],
        "mock_ai_dm_system": ["自动回复", "私信", "评论采集", "抖音评论", "视频摘要", "知识库", "模型", "引流系统"],
        "mock_cross_border_operation": ["跨境", "电商", "货源", "开店", "店铺", "托管", "选品", "海外"],
        "mock_conversion_hooks": ["钩子", "甜头", "资料包", "评估", "体验", "名额", "草料码", "微信", "入口", "领取"],
        "mock_health_compliance": ["医疗", "健康", "疗效", "治", "诊断", "睡眠", "膝", "关节", "活动", "合规"],
        "mock_comment_intent_samples": ["评论", "意向", "怎么回", "回复方向", "真的假的", "有用吗", "多少钱"],
        "mock_video_direction_samples": ["视频", "概述", "总结", "转写", "音频", "方向"],
    }
    return sum(4 for keyword in groups.get(doc_id, []) if keyword.lower() in text)


def _select_admin_documents_by_rules(question: str, docs: List[Dict[str, Any]], max_documents: int = 4) -> List[str]:
    scores = {}
    text = (question or "").lower()
    for doc in docs:
        values = [
            doc.get("title"),
            doc.get("knowledge_base"),
            doc.get("description"),
            doc.get("summary"),
            doc.get("domain"),
            doc.get("section"),
            *(doc.get("tags") or []),
        ]
        score = _score_text_match(question, values)
        score += _boost_mock_business_doc_score(text, str(doc.get("doc_id") or ""))
        score += _document_priority_bonus(str(doc.get("doc_id") or ""), text)
        if score:
            scores[doc.get("doc_id")] = score
    if not scores:
        for doc in docs[:max_documents]:
            if doc.get("doc_id"):
                scores[doc["doc_id"]] = 1
    return [
        doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if doc_id
    ][:max_documents]


def _select_scene_template(question: str, templates: List[Dict[str, Any]], template_id: str = "") -> Dict[str, Any]:
    if template_id:
        found = next((item for item in templates if item.get("template_id") == template_id), None)
        if found:
            return found
    if not templates:
        return {}
    scored = []
    for template in templates:
        values = [
            template.get("title"),
            template.get("purpose"),
            template.get("applicable_scene"),
            *(template.get("tags") or []),
        ]
        scored.append((_score_text_match(question, values), template))
    scored.sort(key=lambda item: -item[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return {}


def _select_activity(question: str, activities: List[Dict[str, Any]], activity_id: str = "") -> Dict[str, Any]:
    enabled = [item for item in activities if item.get("enabled", True)]
    if activity_id:
        found = next((item for item in enabled if item.get("activity_id") == activity_id), None)
        if found:
            return found
    if not enabled:
        return {}
    scored = []
    for activity in enabled:
        values = [
            activity.get("title"),
            activity.get("activity_type"),
            activity.get("applicable_scene"),
            activity.get("description"),
            activity.get("benefit"),
            activity.get("claim_method"),
            *(activity.get("tags") or []),
        ]
        scored.append((_score_text_match(question, values), activity))
    scored.sort(key=lambda item: -item[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return {}


def _read_admin_selected_documents(
    store: ProjectMaterialStore,
    project_id: str,
    docs: List[Dict[str, Any]],
    selected_ids: List[str],
) -> List[Dict[str, Any]]:
    docs_by_id = {doc.get("doc_id"): doc for doc in docs}
    selected = []
    for doc_id in selected_ids:
        doc = docs_by_id.get(doc_id)
        if not doc:
            continue
        relative_path = str(doc.get("relative_path") or "").strip()
        content, normalized_relative = _read_project_document_text(
            store,
            project_id,
            relative_path,
            doc=doc,
        )
        selected.append({**doc, "relative_path": normalized_relative, "content": content})
    return selected


def build_admin_prompt_restore_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    question = str(payload.get("question") or payload.get("user_input") or "").strip()
    if not question:
        raise WebInputError("question is required")
    video_overview = _clip_text(
        payload.get("video_overview") or payload.get("video_summary") or payload.get("video_description"),
        2000,
    )
    user_context_extra = _clip_text(payload.get("context") or payload.get("user_context"), 6000)
    store = _project_store(project_store)
    chat_logic = logic or SessionRAGChatLogic()
    project_id = str(payload.get("project_id") or "").strip()
    project_dir, project_meta = _project_root_and_meta(store, project_id)
    descriptions = _load_document_descriptions(store, project_dir.name)
    all_docs = _flatten_description_documents(descriptions)
    max_documents = _to_int(payload.get("max_documents"), 4)
    model_conf = _optional_document_selector_conf(payload)

    description_json = json.dumps(descriptions, ensure_ascii=False, indent=2)
    selector_prompt = f"""
你是知识库文档路由器。请先阅读总文档描述 JSON，再根据用户评论选择需要读取的具体文档。
规则：
- 最多选择 {max_documents} 个文档。
- 只选择 document_descriptions 中存在的 doc_id。
- 优先选择与用户痛点、业务领域、标签、适用描述最匹配的文档。
- 只返回 JSON，字段固定为 document_ids, reason。
""".strip()
    selector_question = _format_user_context_text(question, video_overview, user_context_extra)
    selector_message = f"{selector_question}\n\n总文档描述 JSON：\n{description_json}"
    selected_ids = []
    selector_reason = "rule_fallback"
    if model_conf:
        try:
            chat = getattr(chat_logic._llm(), model_conf["func_name"])
            response = chat(
                url=model_conf["url"],
                api_key=model_conf.get("key", ""),
                prompt=selector_prompt,
                message=selector_message,
                model=model_conf["model_name"],
                json_format=True,
                stream=False,
                max_len_input=model_conf.get("max_len_input", 16000),
            )
            data = ProjectMaterialStore._parse_json_response(response)
            allowed = {doc.get("doc_id") for doc in all_docs}
            selected_ids = [doc_id for doc_id in data.get("document_ids", []) if doc_id in allowed]
            selector_reason = data.get("reason") or "ai_selector"
        except Exception:
            selected_ids = []
            selector_reason = "ai_selector_failed_rule_fallback"
    if not selected_ids:
        selected_ids = _select_admin_documents_by_rules(selector_question, all_docs, max_documents)

    selected_docs = _read_admin_selected_documents(store, project_dir.name, all_docs, selected_ids)
    templates = _load_scene_templates(store, project_dir.name).get("templates", [])
    selected_template = _select_scene_template(question, templates, str(payload.get("template_id") or ""))
    selected_activity = _select_activity(
        selector_question,
        _load_activity_settings(store, project_dir.name).get("activities", []),
        str(payload.get("activity_id") or ""),
    )
    prepared = PreparedChatRequest(
        question=question,
        provider="",
        config={},
        session_id=str(payload.get("session_id") or "").strip(),
        user_id=str(payload.get("user_id") or DEFAULT_USER_ID).strip(),
        conversation_stage=str(payload.get("conversation_stage") or "first_comment"),
        project_id=project_dir.name,
        scene_id=selected_template.get("scene_id") if isinstance(selected_template, dict) else "",
        template_id=str(payload.get("template_id") or "").strip(),
        activity_id=str(payload.get("activity_id") or "").strip(),
        account_id=str(payload.get("account_id") or "").strip(),
        product_id=str(payload.get("product_id") or "").strip(),
        sender_identity=_clip_text(payload.get("sender_identity") or payload.get("sender_identity_override"), 80),
        video_overview=video_overview,
        global_prompt=_clip_text(payload.get("global_prompt"), 8000),
        enable_knowledge=False,
        topics=[],
        summary_max_chars=_to_int(payload.get("summary_max_chars"), 4000),
        raw_max_chars=_to_int(payload.get("raw_max_chars"), 12000),
        knowledge_size=_to_int(payload.get("knowledge_size"), 4),
        source_platform=_clip_text(payload.get("source_platform"), 120),
        conversion_target=_clip_text(payload.get("conversion_target"), 200),
        target_note=_clip_text(payload.get("target_note"), 600),
    )
    selected_docs_context = "\n\n".join(
        "\n".join([
            f"[{index}] {doc.get('title')} ({doc.get('relative_path')})",
            f"knowledge_base: {doc.get('knowledge_base') or ''}",
            f"domain: {doc.get('domain') or ''}",
            f"section: {doc.get('section') or ''}",
            str(doc.get("content") or ""),
        ])
        for index, doc in enumerate(selected_docs, 1)
    )
    sender_identity, sender_identity_source = _resolve_sender_identity(
        store,
        project_dir.name,
        selected_docs,
        scene_template=selected_template,
        activity_settings=selected_activity,
        account_id=str(payload.get("account_id") or "").strip(),
        product_id=str(payload.get("product_id") or "").strip(),
        sender_identity=_clip_text(payload.get("sender_identity") or payload.get("sender_identity_override"), 80),
        prepared=prepared,
    )
    global_prompt_path = project_dir / "global_prompt.md"
    default_global_prompt = global_prompt_path.read_text(encoding="utf-8", errors="replace") if global_prompt_path.exists() else ""
    global_prompt_context = _clip_text(payload.get("global_prompt"), 8000) or default_global_prompt
    scene_template_context = json.dumps(selected_template, ensure_ascii=False, indent=2) if selected_template else "未选择自定义场景模板。"
    activity_settings_context = _format_activity_settings_context(selected_activity)
    final_prompt = f"""
<user_context>
{selector_question}
</user_context>

<global_prompt>
{global_prompt_context}
</global_prompt>

<sender_identity>
本轮私信建议使用身份：{sender_identity}。
身份来源：{sender_identity_source.get("source", "")}。
</sender_identity>

<scene_template>
{scene_template_context}
</scene_template>

{activity_settings_context}

<retrieved_knowledge>
{selected_docs_context or "没有读取到具体文档。"}
</retrieved_knowledge>

<task>
只根据用户评论/上下文、全局提示词、人员身份、场景模板、活动设置、检索到的相关知识库生成回复。
人员身份由业务、视频概述和活动自动生成，不要读取知识库里的 sender_identity 字段；如果页面/API已传入账号或产品身份，以配置身份为准。
不要编造未在知识库或场景模板中出现的事实、价格、名额、医疗承诺或平台规则。
如活动设置为空，不要主动编造“活动/义诊/优惠券”；如活动设置存在且适合当前评论，可自然表达为“咱们这边正好有活动/义诊/优惠券”。
不要主动自证消息真实性、解释发送方式或强调自己是真人，要用对评论和视频内容的准确承接证明真人感。
</task>
""".strip()
    modules = [
        _prompt_module("user_context", "用户评论以及上下文", selector_question),
        _prompt_module("global_prompt", "全局提示词", global_prompt_context),
        _prompt_module("sender_identity", "人员身份", _format_sender_identity_context(sender_identity, sender_identity_source)),
        _prompt_module("scene_template", "场景模板", scene_template_context),
        _prompt_module("activity_settings", "活动设置", activity_settings_context),
        _prompt_module("retrieved_knowledge", "检索相关知识库", selected_docs_context or "没有读取到具体文档。"),
        _prompt_module("final_prompt", "最终完整 Prompt", final_prompt),
    ]
    return {
        "question": question,
        "selector": {
            "document_ids": selected_ids,
            "reason": selector_reason,
            "used_ai_selector": bool(model_conf),
        },
        "selected_documents": selected_docs,
        "sender_identity": sender_identity,
        "sender_identity_source": sender_identity_source,
        "scene_template": selected_template,
        "activity_settings": selected_activity,
        "prompt_modules": modules,
        "final_prompt": final_prompt,
    }

def _build_debug_prompt_modules(
    prepared: PreparedChatRequest,
    bundle: Any,
    routing_input: str,
    global_prompt_context: str,
    sender_identity_context: str,
    scene_template_context: str,
    activity_settings_context: str,
    retrieved_knowledge_context: str,
    final_prompt: str,
) -> List[Dict[str, Any]]:
    input_context = "\n\n".join([
        f"用户评论：\n{prepared.question}",
        f"上下文：\n{routing_input}" if routing_input and routing_input != prepared.question else "",
    ]).strip()
    if not input_context:
        input_context = f"用户评论：\n{prepared.question}"
    return [
        _prompt_module("user_context", "用户评论以及上下文", input_context),
        _prompt_module("global_prompt", "全局提示词", global_prompt_context or "没有配置全局提示词。"),
        _prompt_module("sender_identity", "人员身份", sender_identity_context or _format_sender_identity_context("")),
        _prompt_module("scene_template", "场景模板", scene_template_context or "未匹配到后台场景模板。"),
        _prompt_module("activity_settings", "活动设置", activity_settings_context or _format_activity_settings_context({})),
        _prompt_module("retrieved_knowledge", "检索相关知识库", retrieved_knowledge_context or "没有检索到相关知识库。"),
        _prompt_module("final_prompt", "最终完整 Prompt", final_prompt),
    ]


def build_project_route_debug_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    question = str(payload.get("question") or payload.get("user_input") or "").strip()
    if not question:
        raise WebInputError("question is required")

    chat_logic = logic or SessionRAGChatLogic(
        memory_manager=InProcessSessionMemoryManager(),
        knowledge_logic=PlaceholderKnowledgeLogic(),
        llm_tools=SimpleLLMChatTools(),
    )
    store = _project_store(project_store)
    conversation_stage = str(payload.get("conversation_stage") or "first_comment").strip()
    if conversation_stage not in {"first_comment", "private_followup"}:
        conversation_stage = "first_comment"
    prepared = PreparedChatRequest(
        question=question,
        provider=str(payload.get("provider") or ""),
        config={
            "url": str(payload.get("url") or ""),
            "key": str(payload.get("api_key") if payload.get("api_key") is not None else payload.get("key") or ""),
            "func_name": str(payload.get("func_name") or ""),
            "model_name": str(payload.get("model_name") or ""),
            "max_len_input": _to_int(payload.get("max_len_input"), 16000),
        },
        session_id=str(payload.get("session_id") or "debug-session").strip(),
        user_id=str(payload.get("user_id") or DEFAULT_USER_ID).strip(),
        conversation_stage=conversation_stage,
        project_id=str(payload.get("project_id") or "").strip(),
        scene_id="auto",
        template_id=str(payload.get("template_id") or "").strip(),
        activity_id=str(payload.get("activity_id") or "").strip(),
        account_id=str(payload.get("account_id") or "").strip(),
        product_id=str(payload.get("product_id") or "").strip(),
        sender_identity=_clip_text(payload.get("sender_identity") or payload.get("sender_identity_override"), 80),
        video_overview=_clip_text(
            payload.get("video_overview") or payload.get("video_summary") or payload.get("video_description"),
            2000,
        ),
        global_prompt=_clip_text(payload.get("global_prompt"), 8000),
        enable_knowledge=False,
        topics=[],
        summary_max_chars=_to_int(payload.get("summary_max_chars"), 4000),
        raw_max_chars=_to_int(payload.get("raw_max_chars"), 12000),
        knowledge_size=_to_int(payload.get("knowledge_size"), 4),
        source_platform=_clip_text(payload.get("source_platform"), 120),
        conversion_target=_clip_text(payload.get("conversion_target"), 200),
        target_note=_clip_text(payload.get("target_note"), 600),
    )
    routing_input = _project_routing_input(prepared, chat_logic)
    initial_bundle = store.get_bundle(prepared.project_id, "auto", routing_input)

    model_conf = _optional_document_selector_conf(payload)
    project_documents = store.select_relevant_documents(
        user_input=routing_input,
        project_id=prepared.project_id,
        scene_id=initial_bundle.scene_id,
        llm_tools=chat_logic._llm() if model_conf else None,
        model_conf=model_conf,
    )
    bundle = store.get_bundle(
        prepared.project_id,
        initial_bundle.scene_id,
        routing_input,
        document_context=project_documents.get("context", ""),
    )
    scene_template = _select_runtime_scene_template(store, prepared, bundle.project_id)
    activity_settings = _select_runtime_activity(store, prepared, bundle.project_id, routing_input)
    scene_template_context = _format_scene_template_context(scene_template)
    activity_settings_context = _format_activity_settings_context(activity_settings)
    retrieved_knowledge_context = _format_retrieved_knowledge_context(project_documents.get("context", ""))
    global_prompt_context = prepared.global_prompt or bundle.global_prompt
    sender_identity, sender_identity_source = _resolve_sender_identity(
        store,
        bundle.project_id,
        project_documents.get("documents", []),
        scene_template=scene_template,
        activity_settings=activity_settings,
        prepared=prepared,
    )
    project_context = _format_runtime_rag_context(
        _format_user_context_text(prepared.question, prepared.video_overview),
        global_prompt_context,
        sender_identity,
        scene_template,
        activity_settings,
        project_documents.get("context", ""),
        sender_identity_source,
    )
    prompt = chat_logic._build_prompt(
        session_id=prepared.session_id,
        summary_context="",
        knowledge_context="",
        project_context=project_context,
        conversation_stage=prepared.conversation_stage,
        structured=True,
    )
    prompt_modules = _build_debug_prompt_modules(
        prepared=prepared,
        bundle=bundle,
        routing_input=routing_input,
        global_prompt_context=_format_global_prompt_context(global_prompt_context),
        sender_identity_context=_format_sender_identity_context(sender_identity, sender_identity_source),
        scene_template_context=scene_template_context,
        activity_settings_context=activity_settings_context,
        retrieved_knowledge_context=retrieved_knowledge_context,
        final_prompt=prompt,
    )

    return {
        "question": question,
        "routing_input": routing_input,
        "conversation_stage": prepared.conversation_stage,
        "project": {
            "project_id": bundle.project_id,
            "project_name": bundle.project_name,
            "scene_id": bundle.scene_id,
            "scene_name": bundle.scene_name,
            "scene_reason": "auto_match_from_latest_input_and_session_summary",
        },
        "project_documents": project_documents,
        "sender_identity": sender_identity,
        "sender_identity_source": sender_identity_source,
        "scene_template": scene_template,
        "activity_settings": activity_settings,
        "project_context": project_context,
        "prompt": prompt,
        "prompt_modules": prompt_modules,
        "selector": {
            "used_ai_selector": bool(model_conf),
            "reason": project_documents.get("reason") or "",
        },
    }


def build_chat_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
    use_saved_model_config: bool = False,
) -> Dict[str, Any]:
    prepared = prepare_chat_request(payload, use_saved_model_config=use_saved_model_config)
    chat_logic = logic or SessionRAGChatLogic()
    store = _project_store(project_store)
    routing_input = _project_routing_input(prepared, chat_logic)
    initial_bundle = store.get_bundle(prepared.project_id, "auto", routing_input)
    project_documents = _select_project_documents(store, prepared, chat_logic, initial_bundle.scene_id, routing_input)
    bundle = store.get_bundle(
        prepared.project_id,
        initial_bundle.scene_id,
        routing_input,
        document_context=project_documents.get("context", ""),
    )
    scene_template = _select_runtime_scene_template(store, prepared, bundle.project_id)
    activity_settings = _select_runtime_activity(store, prepared, bundle.project_id, routing_input)
    global_prompt_context = prepared.global_prompt or bundle.global_prompt
    sender_identity, sender_identity_source = _resolve_sender_identity(
        store,
        bundle.project_id,
        project_documents.get("documents", []),
        scene_template=scene_template,
        activity_settings=activity_settings,
        prepared=prepared,
    )
    project_context = _format_runtime_rag_context(
        _format_user_context_text(prepared.question, prepared.video_overview),
        global_prompt_context,
        sender_identity,
        scene_template,
        activity_settings,
        project_documents.get("context", ""),
        sender_identity_source,
    )
    result = chat_logic.chat_once(
        user_input=prepared.question,
        session_id=prepared.session_id,
        user_id=prepared.user_id,
        topics=prepared.topics,
        url=prepared.config["url"],
        key=prepared.config["key"] or "",
        func_name=prepared.config["func_name"],
        model_name=prepared.config["model_name"],
        max_len_input=prepared.config["max_len_input"],
        summary_max_chars=prepared.summary_max_chars,
        raw_max_chars=prepared.raw_max_chars,
        knowledge_size=prepared.knowledge_size,
        conversation_stage=prepared.conversation_stage,
        project_context=project_context,
    )
    result_data = _result_to_dict(result)
    final_prompt = (
        (result_data.get("prompt_trace") or {}).get("final_prompt")
        or result_data.get("final_prompt")
        or ""
    )
    prompt_modules = _build_debug_prompt_modules(
        prepared=prepared,
        bundle=bundle,
        routing_input=routing_input,
        global_prompt_context=global_prompt_context,
        sender_identity_context=_format_sender_identity_context(sender_identity, sender_identity_source),
        scene_template_context=_format_scene_template_context(scene_template),
        activity_settings_context=_format_activity_settings_context(activity_settings),
        retrieved_knowledge_context=_format_retrieved_knowledge_context(project_documents.get("context", "")),
        final_prompt=final_prompt,
    )
    prompt_trace = dict(result_data.get("prompt_trace") or {})
    if final_prompt:
        prompt_trace["final_prompt"] = final_prompt
    prompt_trace["prompt_modules"] = prompt_modules

    return _chat_response_payload(
        prepared,
        result,
        bundle,
        project_documents,
        scene_template,
        activity_settings,
        sender_identity,
        sender_identity_source,
        prompt_trace=prompt_trace,
    )


def _normalize_public_api_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebInputError("json body is required")

    normalized = dict(payload)
    question = (
        normalized.get("question")
        or normalized.get("comment")
        or normalized.get("user_comment")
        or normalized.get("message")
        or normalized.get("user_input")
    )
    if question is not None:
        normalized["question"] = question

    model = normalized.get("model")
    if isinstance(model, dict):
        for source_key, target_key in [
            ("provider", "provider"),
            ("api_key", "api_key"),
            ("key", "key"),
            ("url", "url"),
            ("func_name", "func_name"),
            ("model_name", "model_name"),
            ("max_len_input", "max_len_input"),
        ]:
            if source_key in model and target_key not in normalized:
                normalized[target_key] = model.get(source_key)

    target = normalized.get("target")
    if isinstance(target, dict):
        for source_key, target_key in [
            ("source_platform", "source_platform"),
            ("platform", "source_platform"),
            ("conversion_target", "conversion_target"),
            ("target", "conversion_target"),
            ("target_note", "target_note"),
            ("note", "target_note"),
        ]:
            if source_key in target and target_key not in normalized:
                normalized[target_key] = target.get(source_key)

    stage = str(normalized.get("stage") or normalized.get("conversation_stage") or "").strip()
    stage_map = {
        "comment": "first_comment",
        "first": "first_comment",
        "first_comment": "first_comment",
        "first_dm": "first_comment",
        "首次私信": "first_comment",
        "private": "private_followup",
        "followup": "private_followup",
        "private_followup": "private_followup",
        "私信维护": "private_followup",
        "私信回复": "private_followup",
    }
    if stage:
        normalized["conversation_stage"] = stage_map.get(stage, stage)

    if "use_ai_selector" not in normalized:
        normalized["use_ai_selector"] = False
    return normalized


def build_public_private_message_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
    use_saved_model_config: bool = False,
) -> Dict[str, Any]:
    normalized = _normalize_public_api_payload(payload)
    data = build_chat_response(
        normalized,
        logic=logic,
        project_store=project_store,
        use_saved_model_config=use_saved_model_config,
    )
    config = dict(data.get("config") or {})
    config.setdefault("provider", data.get("provider") or normalized.get("provider") or "minimax")
    trace = data.get("prompt_trace") or {}
    prompts = trace.get("prompt_modules") or []
    if not prompts and trace.get("final_prompt"):
        prompts = [_prompt_module("final_prompt", "最终完整 Prompt", trace.get("final_prompt") or "")]
    return _compact_private_message_response(
        question=str(normalized.get("question") or ""),
        session_id=str(data.get("session_id") or ""),
        user_id=str(data.get("user_id") or ""),
        provider=str(data.get("provider") or normalized.get("provider") or "minimax"),
        config=config,
        answer=str(data.get("answer") or ""),
        prompt_modules=prompts,
        extra_input={
            "conversation_stage": normalized.get("conversation_stage"),
            "project_id": normalized.get("project_id"),
            "template_id": normalized.get("template_id"),
            "activity_id": normalized.get("activity_id"),
            "source_platform": normalized.get("source_platform"),
            "conversion_target": normalized.get("conversion_target"),
            "target_note": normalized.get("target_note"),
            "video_overview": normalized.get("video_overview"),
        },
    )


def build_public_prompt_preview_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    return build_admin_prompt_restore_response(
        _normalize_public_api_payload(payload),
        logic=logic,
        project_store=project_store,
    )


def build_public_route_debug_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
) -> Dict[str, Any]:
    return build_project_route_debug_response(
        _normalize_public_api_payload(payload),
        logic=logic,
        project_store=project_store,
    )


def build_public_model_test_response(
    payload: Dict[str, Any],
    llm_tools: Optional[Any] = None,
    use_saved_model_config: bool = False,
) -> Dict[str, Any]:
    return build_config_test_response(
        _normalize_public_api_payload(payload),
        llm_tools=llm_tools,
        use_saved_model_config=use_saved_model_config,
    )


def _business_text(payload: Dict[str, Any], *keys: str, max_chars: int = 500) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return _clip_text(value, max_chars)
    return ""


def _identity_field_text(value: Any, max_chars: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return _clip_text(value, max_chars)


def _format_business_identity(identity_fields: Dict[str, str]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in identity_fields.items() if value)


def _normalize_business_identity(payload: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    raw_identity = payload.get("user_identity")
    if not isinstance(raw_identity, dict) or not raw_identity:
        raise WebInputError('user_identity must be a non-empty object, for example: {"name": "求职者", "job": "销售岗"}')

    identity_fields: Dict[str, str] = {}

    field_order = [
        "name",
        "role",
        "job",
        "job_title",
        "position",
        "platform",
        "source",
        "company",
        "city",
        "experience",
        "resume_summary",
        "note",
    ]

    for key in field_order:
        value = _identity_field_text(raw_identity.get(key), 800 if key in {"resume_summary", "note"} else 300)
        if value:
            identity_fields[key] = value

    for key, value in raw_identity.items():
        key_text = str(key).strip()
        if not key_text or key_text in identity_fields:
            continue
        text = _identity_field_text(value, 800 if key_text in {"resume_summary", "note"} else 300)
        if text:
            identity_fields[key_text] = text

    if not identity_fields:
        raise WebInputError("user_identity must contain at least one scalar field")

    formatted = _format_business_identity(identity_fields)
    return formatted, identity_fields


def _business_route_type(caller: str, business_scene: str, message: str, user_identity: str) -> str:
    combined = "\n".join([caller, business_scene, message, user_identity]).lower()
    if re.search(r"boss|直聘|招聘|求职|候选人|面试|岗位|hr|机器人面试", combined, flags=re.I):
        return "recruitment_interview"
    return "generic_private_message"


_RECRUITMENT_UNKNOWN_IDENTITY_VALUES = {
    "",
    "未知",
    "未提供",
    "不清楚",
    "待确认",
    "unknown",
    "none",
    "null",
    "求职者",
    "候选人",
    "用户",
    "应聘者",
}

_RECRUITMENT_NON_JOB_HEADINGS = {
    "业务用途",
    "目录结构",
    "Prompt 文件概括",
    "prompt 文件概括",
    "相关文档概括",
    "岗位路由",
    "运行时读取流程",
    "维护方式",
}

_RECRUITMENT_NAME_NOISE_VALUES = {
    "你好",
    "您好",
    "好的",
    "可以",
    "嗯",
    "嗯嗯",
    "对",
    "是的",
    "不是",
    "未知",
    "求职者",
}


def _extract_recruitment_opening_question(prompt_content: str) -> str:
    text = str(prompt_content or "")
    if not text.strip():
        return ""
    lines = [line.strip() for line in text.splitlines()]

    def is_candidate_question(value: str) -> bool:
        candidate = str(value or "").strip("` ")
        if not candidate:
            return False
        if candidate.startswith("#") or candidate.startswith("-"):
            return False
        if "称呼" not in candidate or "岗位" not in candidate:
            return False
        if "？" not in candidate and "?" not in candidate:
            return False
        return len(candidate) <= 80

    for index, line in enumerate(lines):
        if "优先确认" not in line and "首次进线" not in line:
            continue
        for candidate in lines[index + 1:index + 8]:
            if is_candidate_question(candidate):
                return candidate.strip("` ")
    for line in lines:
        if is_candidate_question(line):
            return line.strip("` ")
    return ""


def _infer_recruitment_name_from_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[，,。.!！?？\s]+|[，,。.!！?？\s]+$", "", text)
    explicit_match = re.search(r"(?:我叫|我是|叫我|姓名是|名字是|本人叫)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·\s]{1,10})", text)
    if explicit_match:
        candidate = explicit_match.group(1)
        candidate = re.split(r"[，,。.!！?？\s]|应聘|面试|岗位|职位", candidate, 1)[0].strip()
        if candidate and candidate not in _RECRUITMENT_NAME_NOISE_VALUES:
            return candidate

    if text in _RECRUITMENT_NAME_NOISE_VALUES:
        return ""
    if re.search(r"岗位|职位|应聘|面试|销售|运营|客服|经理|工程师|专员|主管|总监|hr|HR", text):
        return ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text):
        return text
    if re.fullmatch(r"[A-Za-z][A-Za-z\s]{1,30}", text):
        return text.strip()
    return ""


_RECRUITMENT_JOB_KEYWORD_RE = re.compile(
    r"销售|运营|客服|工程师|项目经理|经理|专员|主管|总监|商务|产品|技术|开发|设计|财务|人事|行政|主播|顾问|助理|HR|hr|岗|岗位|职位"
)


def _clean_recruitment_job_candidate(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[，,。.!！?？\s]+|[，,。.!！?？\s]+$", "", text)
    text = re.sub(
        r"^(?:我想了解|想了解|了解|我想做|想做|投递|应聘|面试|岗位|职位|做|看|对|是|的|这个|那个)\s*",
        "",
        text,
    ).strip()
    text = re.sub(r"(?:岗位|职位)$", "岗", text).strip()
    text = text.strip("，,。.!！?？；;：: ")
    if not text or text in _RECRUITMENT_NAME_NOISE_VALUES:
        return ""
    if len(text) > 30:
        return ""
    if not _RECRUITMENT_JOB_KEYWORD_RE.search(text):
        return ""
    if _infer_recruitment_name_from_message(text):
        return ""
    return text


def _infer_recruitment_job_from_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[，,。.!！?？\s]+|[，,。.!！?？\s]+$", "", text)

    explicit_match = re.search(
        r"(?:应聘|面试|投递|找|想做|岗位|职位)(?:的|是)?\s*([\u4e00-\u9fffA-Za-z0-9+/#·\s]{1,30})",
        text,
    )
    if explicit_match:
        candidate = re.split(r"[，,。.!！?？；;\n\r]", explicit_match.group(1), 1)[0]
        cleaned = _clean_recruitment_job_candidate(candidate)
        if cleaned:
            return cleaned

    for part in reversed([item.strip() for item in re.split(r"[，,。.!！?？；;\n\r]", text) if item.strip()]):
        cleaned = _clean_recruitment_job_candidate(part)
        if cleaned:
            return cleaned
    return _clean_recruitment_job_candidate(text)


def _recruitment_assistant_asks_name(text: str) -> bool:
    return bool(re.search(r"称呼|姓名|名字|怎么叫|怎么称呼|贵姓", str(text or "")))


def _recruitment_assistant_asks_job(text: str) -> bool:
    return bool(re.search(r"应聘.*岗位|岗位|职位|哪个岗|什么岗|投递", str(text or "")))


def _merge_recruitment_identity_from_answer(
    identity_fields: Dict[str, str],
    assistant_question: str,
    user_answer: str,
) -> None:
    if not str(user_answer or "").strip():
        return
    if _recruitment_assistant_asks_name(assistant_question) and _is_recruitment_identity_missing(
        identity_fields.get("name"),
        field="name",
    ):
        name = _infer_recruitment_name_from_message(user_answer)
        if name:
            identity_fields["name"] = name
    if _recruitment_assistant_asks_job(assistant_question):
        job_value = identity_fields.get("job") or identity_fields.get("job_title") or identity_fields.get("position")
        if _is_recruitment_identity_missing(job_value, field="job"):
            job = _infer_recruitment_job_from_message(user_answer)
            if job:
                identity_fields["job"] = job


def _merge_recruitment_identity_from_dialogues(
    identity_fields: Dict[str, str],
    dialogue_list: List[Dict[str, Any]],
    current_message: str = "",
) -> str:
    last_assistant = ""
    for item in sorted(dialogue_list or [], key=lambda value: int(value.get("sequence_number") or 0)):
        user_text = str(item.get("user") or "").strip()
        if user_text:
            _merge_recruitment_identity_from_answer(identity_fields, last_assistant, user_text)
        assistant_text = str(item.get("assistant") or "").strip()
        if assistant_text:
            last_assistant = assistant_text
    if str(current_message or "").strip():
        _merge_recruitment_identity_from_answer(identity_fields, last_assistant, current_message)
    return last_assistant


def _is_recruitment_identity_missing(value: Any, *, field: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if re.fullmatch(r"[?？�]+", text):
        return True
    if text.lower() in _RECRUITMENT_UNKNOWN_IDENTITY_VALUES or text in _RECRUITMENT_UNKNOWN_IDENTITY_VALUES:
        return True
    if field == "job" and text in _RECRUITMENT_NON_JOB_HEADINGS:
        return True
    if field == "name" and re.fullmatch(r"(求职者|候选人|用户|应聘者|客户|意向客户)[\d号]*", text):
        return True
    if field == "job" and re.fullmatch(r"(岗位|职位|应聘岗位|招聘岗位|未知岗位|这个岗位)", text):
        return True
    return False


def _recruitment_context_has_formal_interview(summary_context: str) -> bool:
    for item in _extract_recruitment_dialogue_messages(summary_context):
        if item.get("role") != "assistant":
            continue
        if _detect_recruitment_question_group_ids_from_question(item.get("content") or ""):
            return True
    return False


def _clean_recruitment_job_hint(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _RECRUITMENT_NON_JOB_HEADINGS:
        return ""
    if _is_recruitment_identity_missing(text, field="job"):
        return ""
    return text


def _recruitment_identity_opening_question(
    identity_fields: Dict[str, str],
    selected_job_title: str = "",
    summary_context: str = "",
    opening_template: str = "",
) -> str:
    name_missing = _is_recruitment_identity_missing(identity_fields.get("name"), field="name")
    job_value = identity_fields.get("job") or identity_fields.get("job_title") or identity_fields.get("position") or ""
    job_missing = _is_recruitment_identity_missing(job_value, field="job")
    if not name_missing and not job_missing:
        return ""

    default_opening = "请问怎么称呼？应聘哪个岗位？"
    opening = str(opening_template or "").strip() or default_opening
    if name_missing and job_missing:
        selected_hint = _clean_recruitment_job_hint(selected_job_title)
        if selected_hint:
            return f"请问怎么称呼？应聘的是{selected_hint}对吗？"
        return opening
    job_hint = _clean_recruitment_job_hint(job_value) or _clean_recruitment_job_hint(selected_job_title)
    if name_missing and job_hint:
        return f"请问怎么称呼？应聘的是{job_hint}对吗？"
    if name_missing:
        return opening
    if job_missing and _recruitment_context_has_formal_interview(summary_context):
        return ""
    return "请问应聘哪个岗位？"


def _parse_session_dialogue_list(raw_context: str) -> List[Dict[str, Any]]:
    text = str(raw_context or "").strip()
    if not text:
        return []
    header_matches = list(re.finditer(r"(?m)^\[(\d+)\]\s*$", text))
    dialogues: List[Dict[str, Any]] = []
    for index, match in enumerate(header_matches):
        start = match.end()
        end = header_matches[index + 1].start() if index + 1 < len(header_matches) else len(text)
        block = text[start:end].strip()
        user_match = re.search(r"(?m)^(?:User|用户|求职者)[:：]\s*", block)
        assistant_match = re.search(r"(?m)^(?:Assistant|AI|机器人|HR)[:：]\s*", block)
        if not user_match and not assistant_match:
            continue
        user_text = ""
        assistant_text = ""
        if user_match and assistant_match and user_match.end() <= assistant_match.start():
            user_text = block[user_match.end():assistant_match.start()].strip()
            assistant_text = block[assistant_match.end():].strip()
        elif user_match:
            user_text = block[user_match.end():].strip()
        elif assistant_match:
            assistant_text = block[assistant_match.end():].strip()
        dialogues.append({
            "sequence_number": int(match.group(1)),
            "user": user_text,
            "assistant": assistant_text,
        })
    return dialogues


def _format_recruitment_conversation_context(summary_context: str, raw_context: str) -> str:
    summary = str(summary_context or "").strip()
    raw = str(raw_context or "").strip()
    if summary and raw and summary != raw:
        return f"压缩摘要：\n{summary}\n\n原始对话：\n{raw}"
    return raw or summary


def _read_business_project_file(
    store: ProjectMaterialStore,
    project_id: str,
    relative_path: str,
) -> Dict[str, Any]:
    try:
        project_dir, path, normalized_relative = _safe_project_path(store, project_id, relative_path)
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() and path.is_file() else ""
    except Exception:
        project_dir, _ = _project_root_and_meta(store, project_id)
        normalized_relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
        content = ""
    return {
        "title": Path(normalized_relative).stem,
        "relative_path": normalized_relative,
        "absolute_path": str(project_dir / normalized_relative) if normalized_relative else "",
        "content": content,
        "content_chars": len(content),
    }


def _read_business_project_markdown_dir(
    store: ProjectMaterialStore,
    project_id: str,
    relative_path: str,
    title: str,
) -> Dict[str, Any]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    normalized_relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    contents: List[str] = []
    source_files: List[str] = []
    try:
        candidate_relative = Path(normalized_relative)
        if candidate_relative.is_absolute() or any(part == ".." for part in candidate_relative.parts):
            raise WebInputError("invalid relative_path")
        directory = (project_dir / candidate_relative).resolve()
        directory.relative_to(project_dir.resolve())
        if directory.exists() and directory.is_dir():
            for path in sorted(directory.glob("*.md"), key=lambda item: item.name):
                rel = path.relative_to(project_dir).as_posix()
                source_files.append(rel)
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    contents.append(f"<!-- source: {rel} -->\n{content}")
    except Exception:
        contents = []
        source_files = []
    content = "\n\n".join(contents).strip()
    return {
        "title": title,
        "relative_path": normalized_relative,
        "absolute_path": str(project_dir / normalized_relative) if normalized_relative else "",
        "content": content,
        "content_chars": len(content),
        "source_files": source_files,
    }


def _select_recruitment_business_dir(
    store: ProjectMaterialStore,
    project_id: str,
    kb_base: str,
    caller: str,
    business_scene: str,
    message: str,
) -> Dict[str, str]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    kb_relative = Path(kb_base)
    kb_dir = (project_dir / kb_relative).resolve()
    business_dirs = []
    if kb_dir.exists() and kb_dir.is_dir():
        for path in sorted(kb_dir.iterdir(), key=lambda item: item.name):
            if path.is_dir():
                business_dirs.append(path)

    structured_business_dirs = [
        path for path in business_dirs
        if (path / "README.md").is_file()
        and ((path / "prompts").is_dir() or (path / "相关文档").is_dir())
    ]
    if structured_business_dirs:
        business_dirs = structured_business_dirs
    else:
        legacy_flow_dir = kb_dir / "招聘流程"
        if legacy_flow_dir.exists() and legacy_flow_dir.is_dir():
            return {
                "name": "招聘流程",
                "relative_path": f"{kb_base}/招聘流程",
                "reason": "legacy_recruitment_flow",
            }

    if not business_dirs:
        return {
            "name": "招聘流程",
            "relative_path": f"{kb_base}/招聘流程",
            "reason": "legacy_recruitment_flow",
        }

    combined = "\n".join([caller or "", business_scene or "", message or ""]).lower()
    scored = []
    for index, path in enumerate(business_dirs):
        name = path.name
        score = 0
        if business_scene and (name in business_scene or business_scene in name):
            score += 30
        if caller and (name in caller or caller in name):
            score += 12
        if name.lower() in combined:
            score += 20
        if name == "机器人面试" and re.search(r"机器人|面试|初面|候选人|求职", combined, flags=re.I):
            score += 16
        if name == "boss沟通" and re.search(r"boss|直聘|打招呼|沟通|招聘", combined, flags=re.I):
            score += 8
        has_readme = (path / "README.md").exists()
        has_prompts = (path / "prompts").is_dir()
        has_docs = (path / "相关文档").is_dir()
        if has_readme:
            score += 3
        if has_prompts:
            score += 2
        if has_docs:
            score += 2
        scored.append((score, -index, path))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = scored[0][2]
    reason = "matched_business_scene" if scored[0][0] > 0 else "fallback_first_business_dir"
    return {
        "name": selected.name,
        "relative_path": f"{kb_base}/{selected.name}",
        "reason": reason,
    }


def _parse_recruitment_job_overview(overview: str) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def append_current() -> None:
        if not current:
            return
        if current.get("aliases") or current.get("document_name"):
            jobs.append(current)

    for raw_line in str(overview or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^#{2,6}\s*(.+?)\s*$", line)
        if heading:
            append_current()
            current = {
                "title": heading.group(1).strip(),
                "aliases": [],
                "document_name": "",
                "description": "",
            }
            continue
        if current is None:
            continue
        alias_match = re.search(r"岗位别名[：:]\s*(.+)$", line)
        if alias_match:
            aliases = [
                item.strip(" ，,、;；")
                for item in re.split(r"[，,、;；]", alias_match.group(1))
                if item.strip(" ，,、;；")
            ]
            current["aliases"] = aliases
            continue
        doc_match = re.search(r"对应岗位文档[：:]\s*(.+?)(?:\s|$)", line)
        if doc_match:
            current["document_name"] = doc_match.group(1).strip(" `。；;")
            continue
        if "流程用途" in line:
            current["description"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
    append_current()
    return jobs


def _select_recruitment_job_document(
    overview: str,
    message: str,
    user_identity: str,
    business_scene: str,
) -> Dict[str, Any]:
    jobs = _parse_recruitment_job_overview(overview)
    if not jobs:
        return {"reason": "overview_has_no_job_mapping", "job": {}, "document_name": ""}

    text = "\n".join([message, user_identity, business_scene]).lower()
    scored = []
    for index, job in enumerate(jobs):
        score = 0
        values = [job.get("title"), job.get("document_name"), *(job.get("aliases") or [])]
        for value in values:
            clean = str(value or "").strip()
            if clean and clean.lower() in text:
                score += 10 if clean in (job.get("aliases") or []) else 6
        scored.append((score, -index, job))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored and scored[0][0] > 0:
        selected = scored[0][2]
        return {"reason": "matched_job_alias_from_overview", "job": selected, "document_name": selected.get("document_name") or ""}
    return {"reason": "no_job_signal_in_message_or_identity", "job": {}, "document_name": ""}


def _format_business_documents_context(documents: List[Dict[str, Any]]) -> str:
    blocks = []
    for index, doc in enumerate(documents, 1):
        blocks.append(
            "\n".join([
                f"[{index}] {doc.get('title') or ''}",
                f"path: {doc.get('relative_path') or ''}",
                str(doc.get("content") or ""),
            ])
        )
    return "\n\n".join(blocks)


_RECRUITMENT_RESISTANCE_RE = re.compile(
    r"(^\s*[？?]+\s*$|说了|已经.*说|刚.*说|不是.*说|问过|还问|重复|要.*这么.*详细|这么.*详细|问题.*太多|问.*干嘛|问.*做什么|有必要|没必要|不想.*(说|聊|答|回)|不方便|太麻烦|烦|算了|不聊|隐私)",
    flags=re.I,
)

_RECRUITMENT_QUESTION_GROUPS = [
    {
        "id": "basic_hometown",
        "title": "基本情况/老家籍贯",
        "keywords": ["老家", "哪里人", "籍贯"],
    },
    {
        "id": "basic_location",
        "title": "基本情况/住址通勤",
        "keywords": ["住在", "住哪", "住哪里", "居住", "地铁", "通勤", "目前住"],
    },
    {
        "id": "family",
        "title": "家庭/婚育/配偶",
        "keywords": ["家庭", "已婚", "结婚", "婚育", "小孩", "孩子", "老公", "配偶"],
    },
    {
        "id": "industry_product",
        "title": "过往行业/公司产品",
        "keywords": ["行业", "主营", "产品", "业务", "软件服务", "公司具体"],
    },
    {
        "id": "career_stability",
        "title": "职业稳定性/离职原因/加班",
        "keywords": [
            "几份工作",
            "每份",
            "做了多久",
            "工作多久",
            "工作时长",
            "任职",
            "离职",
            "个人原因",
            "选择新公司",
            "比较看重",
            "看重什么",
            "加班",
            "跳槽",
            "稳定",
        ],
    },
    {
        "id": "customer_composition",
        "title": "客户构成/客户占比",
        "keywords": ["客户构成", "占比", "大C", "小C", "大B", "小B", "大G", "小G"],
    },
    {
        "id": "customer_level",
        "title": "客户层级/决策人",
        "keywords": ["客户层级", "对接客户", "采购", "部门负责人", "决策", "老板", "主管领导"],
    },
    {
        "id": "acquisition",
        "title": "客户资源/自拓渠道",
        "keywords": ["客户资源", "自拓", "拓客", "渠道", "转介绍", "陌拜", "线上推广", "拜访", "成交几单"],
    },
    {
        "id": "bidding",
        "title": "招投标/项目规模",
        "keywords": ["招投标", "投标", "项目规模", "操盘"],
    },
    {
        "id": "performance_cycle",
        "title": "业绩数据/客单价回款任务",
        "keywords": ["客单价", "回款", "月平均任务", "月均任务", "业绩任务", "完成多少", "独立成交", "团队协作"],
    },
    {
        "id": "annual_sales",
        "title": "年度销售业绩",
        "keywords": ["年度", "销售业绩", "业绩总额", "分年度"],
    },
    {
        "id": "key_customers",
        "title": "重点客户",
        "keywords": ["重点客户", "服务过", "前五"],
    },
    {
        "id": "team_management",
        "title": "带团队经历",
        "keywords": ["带过团队", "带团队", "团队人均", "人员管理"],
    },
    {
        "id": "salary_structure",
        "title": "薪资结构/考核标准",
        "keywords": ["薪资结构", "考核标准", "底薪"],
    },
    {
        "id": "commission",
        "title": "提成规则",
        "keywords": ["提成", "多少点", "阶梯"],
    },
    {
        "id": "expected_salary",
        "title": "期望薪资",
        "keywords": ["期望薪资", "期望的薪资", "薪资是多少"],
    },
]

_RECRUITMENT_GROUP_ORDER = [str(group["id"]) for group in _RECRUITMENT_QUESTION_GROUPS]

_RECRUITMENT_DEFAULT_NEXT_QUESTIONS = {
    "basic_hometown": "先问下，您老家是哪里人？",
    "basic_location": "您目前住在哪里，近哪个地铁站？",
    "family": "您目前婚育情况这块简单说下就行。",
    "industry_product": "您之前主要在哪些行业做过，公司产品或业务大概是什么方向？",
    "career_stability": "您之前几份工作大概每份做了多久，离职主要是什么原因？",
    "customer_composition": "那我们看下一块，您过往客户主要是哪类为主，比如企业客户、个人客户还是政府客户？",
    "customer_level": "您日常对接客户一般是哪类层级，比如采购、部门负责人，还是老板或决策人？",
    "acquisition": "您之前客户资源主要是自己开发还是公司分配？",
    "bidding": "过往业务里有涉及招投标或项目制推进吗？",
    "performance_cycle": "您之前产品客单价和回款周期大概是什么水平？",
    "annual_sales": "近两家公司的年度销售业绩大概分别是多少？",
    "key_customers": "方便列举几个您服务过的重点客户吗？",
    "team_management": "您之前有带过团队吗，大概带过多少人？",
    "salary_structure": "最近一份工作的薪资结构和考核标准大概是怎样的？",
    "commission": "之前提成一般是怎么计算的？",
    "expected_salary": "您目前期望薪资大概是多少？",
}


def _normalize_recruitment_flow_question(text: str) -> str:
    question = re.sub(r"\s+", "", str(text or "").strip())
    question = question.strip("；;，,。")
    if question and "？" not in question and "?" not in question:
        question = f"{question}？"
    return question


def _split_recruitment_flow_questions(text: str) -> List[str]:
    block = re.sub(r"\s+", "", str(text or "").strip())
    if not block:
        return []
    marker_matches = list(re.finditer(r"(?<!\d)(\d+)[.、,，]\s*", block))
    pieces: List[str] = []
    if marker_matches:
        for index, match in enumerate(marker_matches):
            start = match.end()
            end = marker_matches[index + 1].start() if index + 1 < len(marker_matches) else len(block)
            pieces.append(block[start:end])
    else:
        pieces = re.split(r"[\n\r]+", block)
    return [
        normalized
        for normalized in (_normalize_recruitment_flow_question(piece) for piece in pieces)
        if normalized
    ]


def _extract_recruitment_flow_questions(specific_flow: str) -> Dict[str, str]:
    text = str(specific_flow or "")
    if not text.strip():
        return {}
    question_blocks = re.findall(
        r"题目[:：]\s*(.*?)(?=\n\s*(?:分值[:：]|评分标准[:：]|评分维度[:：]|结果判定[:：]|##|###|题目[:：])|\Z)",
        text,
        flags=re.S,
    )
    questions_by_group: Dict[str, str] = {}
    for block in question_blocks:
        for question in _split_recruitment_flow_questions(block):
            for group_id in _detect_recruitment_question_group_ids(question):
                if group_id not in questions_by_group:
                    questions_by_group[group_id] = question
    return questions_by_group


def _detect_recruitment_question_group_ids(text: str) -> List[str]:
    lower_text = str(text or "").lower()
    matched = []
    for group in _RECRUITMENT_QUESTION_GROUPS:
        if any(str(keyword).lower() in lower_text for keyword in group["keywords"]):
            matched.append(group["id"])
    return matched


def _recruitment_question_windows(text: str, window_chars: int = 90) -> List[str]:
    windows: List[str] = []
    for match in re.finditer(r"[?？]", str(text or "")):
        start = max(0, match.start() - window_chars)
        window = str(text or "")[start:match.end()]
        pieces = [piece.strip() for piece in re.split(r"[。；;！!\n\r]", window) if piece.strip()]
        windows.append(pieces[-1] if pieces else window.strip())
    return windows


def _detect_recruitment_question_group_ids_from_question(text: str) -> List[str]:
    matched: List[str] = []
    windows = _recruitment_question_windows(text)
    if not windows and str(text or "").strip():
        windows = [str(text or "")]
    for window in windows:
        for group_id in _detect_recruitment_question_group_ids(window):
            if group_id not in matched:
                matched.append(group_id)
    return matched


_RECRUITMENT_UNUSABLE_ANSWER_RE = re.compile(
    r"^\s*(?:嗯+|哦+|好+|好的|可以|行|对|是|不是|不知道|不清楚|不太清楚|不确定|没想好|随便|看情况|还好|一般)\s*[。.!！?？]*\s*$",
    flags=re.I,
)


def _is_recruitment_usable_answer_for_group(group_id: str, answer: str) -> bool:
    text = re.sub(r"\s+", "", str(answer or "").strip())
    text = text.strip("，,。.!！?？；;：:")
    if not text:
        return False
    if _RECRUITMENT_RESISTANCE_RE.search(text):
        return False
    if _RECRUITMENT_UNUSABLE_ANSWER_RE.fullmatch(text):
        return False
    if re.search(r"哪里|哪儿|什么|怎么|多少|吗|呢|嘛|为啥|为什么", text):
        return False
    if group_id == "basic_hometown":
        return bool(re.search(r"[\u4e00-\u9fff]{2,12}", text))
    return len(text) >= 2


def _recruitment_question_group_title(group_id: str) -> str:
    for group in _RECRUITMENT_QUESTION_GROUPS:
        if group["id"] == group_id:
            return str(group["title"])
    return group_id


def _next_recruitment_group_id(
    blocked_group_ids: List[str],
    answered_group_ids: List[str],
    after_group_ids: Optional[List[str]] = None,
) -> str:
    blocked = set(blocked_group_ids or [])
    answered = set(answered_group_ids or [])
    start_index = 0
    if after_group_ids:
        group_indexes = [
            _RECRUITMENT_GROUP_ORDER.index(group_id)
            for group_id in after_group_ids
            if group_id in _RECRUITMENT_GROUP_ORDER
        ]
        if group_indexes:
            start_index = max(group_indexes) + 1

    ordered_after_anchor = _RECRUITMENT_GROUP_ORDER[start_index:]
    for group_id in ordered_after_anchor:
        if group_id not in blocked and group_id not in answered:
            return group_id
    for group_id in ordered_after_anchor:
        if group_id not in blocked:
            return group_id
    for group_id in _RECRUITMENT_GROUP_ORDER:
        if group_id not in blocked and group_id not in answered:
            return group_id
    for group_id in _RECRUITMENT_GROUP_ORDER:
        if group_id not in blocked:
            return group_id
    return ""


def _extract_recruitment_dialogue_messages(context: str) -> List[Dict[str, str]]:
    role_re = re.compile(r"(User|Assistant|Q|A|用户|求职者|HR|机器人)[:：]\s*", flags=re.I)
    matches = list(role_re.finditer(str(context or "")))
    messages: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        role_text = match.group(1).lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(str(context or ""))
        content = str(context or "")[start:end].strip()
        content = re.sub(r"\n\s*\[\d+\]\s*$", "", content).strip()
        if not content:
            continue
        role = "user" if role_text in {"user", "q", "用户", "求职者"} else "assistant"
        messages.append({"role": role, "content": content})
    return messages


def _analyze_recruitment_interview_control(
    summary_context: str,
    current_message: str = "",
    flow_questions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    flow_questions = flow_questions or {}
    messages = _extract_recruitment_dialogue_messages(summary_context)
    if str(current_message or "").strip():
        messages.append({"role": "user", "content": str(current_message).strip()})

    asked_counts: Dict[str, int] = {str(group["id"]): 0 for group in _RECRUITMENT_QUESTION_GROUPS}
    answer_counts: Dict[str, int] = {str(group["id"]): 0 for group in _RECRUITMENT_QUESTION_GROUPS}
    last_asked_group_ids: List[str] = []
    current_answered_group_ids: List[str] = []
    current_resisted_group_ids: List[str] = []
    resistance_count = 0
    current_message_resistance = False

    for index, item in enumerate(messages):
        role = item.get("role") or ""
        content = item.get("content") or ""
        if role == "assistant":
            group_ids = _detect_recruitment_question_group_ids_from_question(content)
            if group_ids and ("?" in content or "？" in content):
                for group_id in group_ids:
                    asked_counts[group_id] += 1
                last_asked_group_ids = group_ids
            continue

        is_resistance = bool(_RECRUITMENT_RESISTANCE_RE.search(content))
        if is_resistance:
            resistance_count += 1
            if index == len(messages) - 1:
                current_message_resistance = True
                current_resisted_group_ids = list(last_asked_group_ids)
            continue

        usable_answer_group_ids: List[str] = []
        if last_asked_group_ids and content.strip():
            for group_id in last_asked_group_ids:
                if _is_recruitment_usable_answer_for_group(group_id, content):
                    answer_counts[group_id] += 1
                    usable_answer_group_ids.append(group_id)
        for group_id in _detect_recruitment_question_group_ids(content):
            if (
                group_id not in usable_answer_group_ids
                and _is_recruitment_usable_answer_for_group(group_id, content)
            ):
                answer_counts[group_id] += 1
                usable_answer_group_ids.append(group_id)
        if index == len(messages) - 1:
            current_answered_group_ids = usable_answer_group_ids

    repeated_group_ids = [group_id for group_id, count in asked_counts.items() if count >= 2]
    answered_group_ids = [group_id for group_id, count in answer_counts.items() if count > 0]
    blocked_group_ids = list(dict.fromkeys([
        *repeated_group_ids,
        *current_answered_group_ids,
        *current_resisted_group_ids,
    ]))
    move_after_group_ids: List[str] = []
    if current_resisted_group_ids:
        move_after_group_ids = current_resisted_group_ids
    elif current_answered_group_ids:
        move_after_group_ids = current_answered_group_ids
    elif repeated_group_ids:
        move_after_group_ids = repeated_group_ids

    next_group_id = _next_recruitment_group_id(blocked_group_ids, answered_group_ids, move_after_group_ids)
    last_group_titles = [_recruitment_question_group_title(group_id) for group_id in last_asked_group_ids]
    repeated_titles = [_recruitment_question_group_title(group_id) for group_id in repeated_group_ids]
    answered_titles = [_recruitment_question_group_title(group_id) for group_id in answered_group_ids]
    blocked_titles = [_recruitment_question_group_title(group_id) for group_id in blocked_group_ids]
    next_group_title = _recruitment_question_group_title(next_group_id) if next_group_id else ""
    next_question_source = "specific_flow" if next_group_id in flow_questions else "default"
    next_question = flow_questions.get(next_group_id) or _RECRUITMENT_DEFAULT_NEXT_QUESTIONS.get(next_group_id, "")
    formal_question_count = sum(asked_counts.values())
    next_question_exact = bool(next_group_id and next_question_source == "specific_flow" and asked_counts.get(next_group_id, 0) == 0)
    should_direct_initial_question = bool(next_question_exact and formal_question_count == 0)

    if resistance_count >= 2:
        action = "候选人已出现两次及以上不耐烦或不愿细答，本轮必须跳过当前问题组，不要继续安抚或解释过多，直接进入岗位流程里的下一主问题。"
    elif current_message_resistance:
        action = "候选人表现不耐烦或不愿细答，本轮不要继续围绕当前问题组追问，接受已有近似答案并进入下一主问题。"
    elif repeated_group_ids:
        action = "存在已提问两次及以上的问题组，这些问题组和它们的衍生问题都不得继续追问；如果已有近似答案，接受近似答案并进入下一主问题。"
    elif current_answered_group_ids:
        action = "候选人刚才已经给出上一问题组的可用回答，本轮不要继续追问该问题组，直接承接并进入下一主问题。"
    elif last_asked_group_ids:
        action = "已经进入正式面试，本轮必须调用 AI 根据候选人刚才的回答判断：信息够用则进入下一主问题；信息明显不可用才围绕上一问题组自然追问或安抚。"
    else:
        action = "尚未问过正式面试题时，可以直接使用具体岗位文档的第一道原题作为正式初问；进入正式面试后不要机械直出下一题。"

    lines = [
        "同一问题组最多询问两次，包含改写、拆分、补数字、问比例、问周期、问任务、问完成度等衍生问题。",
        "候选人给出大概数、区间、模糊但可用的信息时，视为当前问题组已有可用回答，除非完全答非所问，否则不要继续追问细节。",
        f"候选人阻抗信号次数：{resistance_count}",
        f"当前消息是否为阻抗信号：{'是' if current_message_resistance else '否'}",
        f"上一轮问题组：{('、'.join(last_group_titles) if last_group_titles else '未识别')}",
        f"已提问达到两次上限的问题组：{('、'.join(repeated_titles) if repeated_titles else '无')}",
        f"已有可用回答的问题组：{('、'.join(answered_titles) if answered_titles else '无')}",
        f"本轮禁止继续追问的问题组：{('、'.join(blocked_titles) if blocked_titles else '无')}",
        f"本轮推进锚点问题组：{('、'.join(_recruitment_question_group_title(group_id) for group_id in move_after_group_ids) if move_after_group_ids else '无')}",
        f"建议进入的下一问题组：{next_group_title or '未识别'}",
        f"建议下一句话：{next_question or '按岗位流程进入下一主问题'}",
        f"建议话术来源：{'具体岗位文档原题' if next_question_source == 'specific_flow' else '系统兜底题'}",
        f"本问题组初次提问是否必须使用原题：{'是' if next_question_exact else '否'}",
        f"已问正式问题数量：{formal_question_count}",
        f"是否允许后端直出正式首题：{'是' if should_direct_initial_question else '否'}",
        "进入正式面试后必须让 AI 结合上下文判断回答质量；追问、安抚、解释、跳过都不要求使用原题原文。",
        f"本轮行动建议：{action}",
    ]
    return {
        "resistance_count": resistance_count,
        "current_message_resistance": current_message_resistance,
        "last_question_groups": last_group_titles,
        "last_question_group_ids": last_asked_group_ids,
        "current_resisted_group_ids": current_resisted_group_ids,
        "current_resisted_question_groups": [
            _recruitment_question_group_title(group_id) for group_id in current_resisted_group_ids
        ],
        "current_answered_group_ids": current_answered_group_ids,
        "current_answered_question_groups": [
            _recruitment_question_group_title(group_id) for group_id in current_answered_group_ids
        ],
        "repeated_question_groups": repeated_titles,
        "answered_question_groups": answered_titles,
        "blocked_question_group_ids": blocked_group_ids,
        "blocked_question_groups": blocked_titles,
        "next_question_group_id": next_group_id,
        "next_question_group": next_group_title,
        "next_question": next_question,
        "next_question_source": next_question_source,
        "next_question_exact": next_question_exact,
        "formal_question_count": formal_question_count,
        "should_direct_initial_question": should_direct_initial_question,
        "flow_questions": flow_questions,
        "prompt": "\n".join(lines),
    }


def _build_recruitment_business_prompt(
    *,
    caller: str,
    business_scene: str,
    business_name: str,
    business_path: str,
    user_identity: str,
    session_id: str,
    current_message: str,
    summary_context: str,
    interview_control_context: str,
    documents: List[Dict[str, Any]],
    selected_job: Dict[str, Any],
) -> tuple[str, List[Dict[str, Any]]]:
    global_prompt = next((doc.get("content", "") for doc in documents if doc.get("role") == "global_prompt"), "")
    overview = next((doc.get("content", "") for doc in documents if doc.get("role") == "job_overview"), "")
    specific = next((doc.get("content", "") for doc in documents if doc.get("role") == "specific_flow"), "")
    documents_context = _format_business_documents_context(documents)
    route_context = "\n".join([
        f"调用方：{caller or '未提供'}",
        f"业务场景：{business_scene or '未提供'}",
        "知识库：招聘知识库",
        f"资料业务：{business_name or '招聘流程'}",
        f"资料目录：{business_path or '未提供'}",
        f"求职者身份/岗位线索：{user_identity or '未提供'}",
        f"匹配岗位：{selected_job.get('title') or '未明确'}",
        f"session_id：{session_id}",
    ])
    final_prompt = f"""
<business_route>
{route_context}
</business_route>

<conversation_context>
{summary_context or "当前会话暂无历史上下文。"}
</conversation_context>

<current_message>
{current_message}
</current_message>

<interview_control_state>
{interview_control_context}
</interview_control_state>

<global_prompt>
{global_prompt or "你是招聘初面助手，需自然、礼貌、逐步提问。"}
</global_prompt>

<job_overview>
{overview or "没有读取到业务 README/岗位概述。"}
</job_overview>

<specific_recruitment_flow>
{specific or "没有读取到具体业务资料文档。"}
</specific_recruitment_flow>

<retrieved_documents>
{documents_context}
</retrieved_documents>

<task>
你正在代表招聘方和求职者进行线上机器人初面。
请根据调用方、业务场景、求职者身份、会话上下文、业务目录 README、提示词文件和具体业务资料文档，生成下一条应该发送给求职者的回复。
要求：
1. 严格按照全局提示词和具体岗位流程推进，不要暴露评分、分值、内部规则、字段名或系统判断。
2. 一次只问一个核心问题；如果当前信息不足以判断进度，优先确认称呼和应聘岗位。
3. 必须先结合完整会话上下文判断候选人刚才的回答是否足够：足够就进入下一主问题，不足可以自然追问或安抚后追问。
4. 首次正式提问尽量使用岗位文档原题；追问、安抚、解释、跳过、承接上下文时不需要使用原题原文。
5. 必须遵守 <interview_control_state>：同一问题组及衍生问题最多问两次；达到上限、已有近似答案或候选人明显抗拒时，跳过该问题组，进入下一主问题。
6. 如果上下文已经包含某个问题的有效回答，不要重复问同一个问题，继续下一个问题。
7. 回复只输出要发送给求职者的文本，不要输出 JSON、Markdown、标签或解释。
</task>
""".strip()
    modules = [
        _prompt_module("business_route", "业务路由", route_context),
        _prompt_module("conversation_context", "会话上下文", summary_context),
        _prompt_module("current_message", "当前用户输入", current_message),
        _prompt_module("interview_control_state", "面试问题控制状态", interview_control_context),
        _prompt_module("global_prompt", "招聘全局提示词", global_prompt),
        _prompt_module("job_overview", "业务 README/岗位概述", overview),
        _prompt_module("specific_recruitment_flow", "具体业务资料", specific),
        _prompt_module("final_prompt", "最终完整 Prompt", final_prompt),
    ]
    return final_prompt, modules


def _build_recruitment_business_context(
    store: ProjectMaterialStore,
    project_id: str,
    caller: str,
    business_scene: str,
    message: str,
    user_identity: str,
) -> Dict[str, Any]:
    project_dir, _ = _project_root_and_meta(store, project_id)
    project_id = project_dir.name
    kb_base = "knowledge/files/招聘知识库"
    business = _select_recruitment_business_dir(store, project_id, kb_base, caller, business_scene, message)
    business_base = business["relative_path"]
    flow_base = f"{kb_base}/招聘流程"
    global_doc = _read_business_project_markdown_dir(store, project_id, f"{business_base}/prompts", f"{business['name']}提示词")
    if not global_doc.get("content"):
        global_doc = _read_business_project_markdown_dir(store, project_id, f"{kb_base}/prompts", "招聘提示词")
    if not global_doc.get("content"):
        global_doc = _read_business_project_file(store, project_id, f"{flow_base}/全局提示词.md")
    overview_doc = _read_business_project_file(store, project_id, f"{business_base}/README.md")
    if not overview_doc.get("content"):
        overview_doc = _read_business_project_file(store, project_id, f"{kb_base}/总文件概述.md")
    if not overview_doc.get("content"):
        overview_doc = _read_business_project_file(store, project_id, f"{flow_base}/总文件概述.md")
    selected = _select_recruitment_job_document(
        overview_doc.get("content", ""),
        message,
        user_identity,
        business_scene,
    )
    document_name = selected.get("document_name") or "销售招聘流程.md"
    flow_doc = _read_business_project_file(store, project_id, f"{business_base}/相关文档/{document_name}")
    if not flow_doc.get("content"):
        flow_doc = _read_business_project_file(store, project_id, f"{flow_base}/{document_name}")
    if not flow_doc.get("content"):
        flow_doc = _read_business_project_file(store, project_id, f"{flow_base}/具体资料/{document_name}")
    documents = [
        {**global_doc, "role": "global_prompt", "knowledge_base": "招聘知识库", "domain": business["name"]},
        {**overview_doc, "role": "job_overview", "knowledge_base": "招聘知识库", "domain": business["name"]},
        {**flow_doc, "role": "specific_flow", "knowledge_base": "招聘知识库", "domain": business["name"]},
    ]
    return {
        "project_id": project_id,
        "route": {
            "type": "recruitment_interview",
            "caller": caller,
            "business_scene": business_scene,
            "knowledge_base": "招聘知识库",
            "business": business["name"],
            "business_path": business_base,
            "business_reason": business["reason"],
            "domain": business["name"],
            "section": "相关文档",
            "selected_job": selected.get("job") or {},
            "selected_document_name": document_name,
            "reason": selected.get("reason") or "",
        },
        "documents": documents,
    }


def _call_business_text_llm(
    logic: SessionRAGChatLogic,
    prompt: str,
    message: str,
    model_conf: Dict[str, Any],
) -> str:
    chat = getattr(logic._llm(), model_conf["func_name"])
    response = chat(
        url=model_conf["url"],
        api_key=model_conf.get("key") or "",
        prompt=prompt,
        message=message,
        model=model_conf["model_name"],
        json_format=False,
        stream=False,
        max_len_input=model_conf.get("max_len_input", 16000),
    )
    return str(response or "").strip()


def _guard_recruitment_answer(
    answer: str,
    interview_control: Dict[str, Any],
    opening_question: str = "",
) -> tuple[str, Dict[str, Any]]:
    if opening_question:
        return opening_question, {
            "rewritten": str(answer or "").strip() != opening_question,
            "reason": "missing_name_or_job_opening_required",
            "original_answer": answer,
            "detected_question_group_ids": _detect_recruitment_question_group_ids_from_question(answer),
            "violated_question_group_ids": [],
            "replacement": opening_question,
        }

    blocked_ids = [str(group_id) for group_id in (interview_control.get("blocked_question_group_ids") or []) if group_id]
    current_answered_ids = [
        str(group_id) for group_id in (interview_control.get("current_answered_group_ids") or []) if group_id
    ]
    detected_ids = _detect_recruitment_question_group_ids_from_question(answer)
    violated_ids = [group_id for group_id in detected_ids if group_id in blocked_ids]
    replacement = str(interview_control.get("next_question") or "").strip()
    if violated_ids and replacement:
        if any(group_id in current_answered_ids for group_id in violated_ids):
            natural_replacement = replacement
            if not natural_replacement.startswith(("好", "好的", "那", "您", "方便")):
                natural_replacement = f"好的，{natural_replacement}"
            return natural_replacement, {
                "rewritten": True,
                "reason": "answered_question_group_completed",
                "original_answer": answer,
                "detected_question_group_ids": detected_ids,
                "violated_question_group_ids": violated_ids,
                "replacement": natural_replacement,
            }
        if interview_control.get("current_message_resistance"):
            resistance_count = int(interview_control.get("resistance_count") or 0)
            prefix = "好的，这块先跳过。" if resistance_count >= 2 else "理解，这块主要是做基础匹配参考，先不展开。"
            replacement = f"{prefix}{replacement}"
            return replacement, {
                "rewritten": True,
                "reason": "resistance_skip_question_group",
                "original_answer": answer,
                "detected_question_group_ids": detected_ids,
                "violated_question_group_ids": violated_ids,
                "replacement": replacement,
            }
        if not replacement.startswith(("这个", "那", "您", "方便")):
            replacement = f"这个先不追问了。{replacement}"
        elif "先不追问" not in replacement:
            replacement = f"这个先不追问了。{replacement}"
        return replacement, {
            "rewritten": True,
            "reason": "blocked_question_group_repeated",
            "original_answer": answer,
            "detected_question_group_ids": detected_ids,
            "violated_question_group_ids": violated_ids,
            "replacement": replacement,
        }
    next_group_id = str(interview_control.get("next_question_group_id") or "").strip()
    if next_group_id and replacement and detected_ids:
        allowed_group_ids = {next_group_id}
        if int(interview_control.get("formal_question_count") or 0) > 0:
            allowed_group_ids.update(str(group_id) for group_id in (interview_control.get("last_question_group_ids") or []) if group_id)
        out_of_order_ids = [group_id for group_id in detected_ids if group_id not in allowed_group_ids]
        if out_of_order_ids or detected_ids[0] not in allowed_group_ids:
            return replacement, {
                "rewritten": True,
            "reason": "out_of_order_question_group",
            "original_answer": answer,
            "detected_question_group_ids": detected_ids,
            "violated_question_group_ids": out_of_order_ids or detected_ids,
            "replacement": replacement,
            "expected_question_group_id": next_group_id,
            "expected_question_group": interview_control.get("next_question_group") or "",
        }
    if current_answered_ids and replacement:
        answer_has_question = bool(re.search(r"[?？]", str(answer or "")))
        moved_to_next_group = bool(next_group_id and next_group_id in detected_ids)
        if answer_has_question and not moved_to_next_group:
            natural_replacement = replacement
            if not natural_replacement.startswith(("好", "好的", "那", "您", "方便")):
                natural_replacement = f"好的，{natural_replacement}"
            return natural_replacement, {
                "rewritten": True,
                "reason": "answered_question_group_should_move_next",
                "original_answer": answer,
                "detected_question_group_ids": detected_ids,
                "violated_question_group_ids": current_answered_ids,
                "replacement": natural_replacement,
                "expected_question_group_id": next_group_id,
                "expected_question_group": interview_control.get("next_question_group") or "",
            }
    if interview_control.get("should_direct_initial_question") and replacement and str(answer or "").strip() != replacement:
        return replacement, {
            "rewritten": True,
            "reason": "specific_flow_initial_question_required",
            "original_answer": answer,
            "detected_question_group_ids": detected_ids,
            "violated_question_group_ids": [],
            "replacement": replacement,
            "expected_question_group_id": next_group_id,
            "expected_question_group": interview_control.get("next_question_group") or "",
        }
    return answer, {
        "rewritten": False,
        "reason": "",
        "original_answer": answer,
        "detected_question_group_ids": detected_ids,
        "violated_question_group_ids": [],
        "replacement": "",
    }


def build_business_reply_response(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
    use_saved_model_config: bool = True,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WebInputError("json body is required")

    normalized = _normalize_public_api_payload(payload)
    message = _business_text(normalized, "message", "content", "text", "question", "user_input", max_chars=8000)
    if not message:
        raise WebInputError("message is required")
    session_id = str(normalized.get("session_id") or "").strip()
    if not session_id:
        raise WebInputError("session_id is required")

    chat_logic = logic or SessionRAGChatLogic()
    store = _project_store(project_store)
    project_id = str(normalized.get("project_id") or "").strip()
    user_id = str(normalized.get("user_id") or DEFAULT_USER_ID).strip()
    user_identity, user_identity_structured = _normalize_business_identity(normalized)
    caller = _business_text(normalized, "caller", "source", "source_platform", "platform", max_chars=120)
    business_scene = _business_text(normalized, "business_scene", "scene", "scenario", max_chars=120)
    route_type = _business_route_type(caller, business_scene, message, user_identity)

    if route_type != "recruitment_interview":
        return build_public_private_message_response(
            {
                **normalized,
                "question": message,
                "session_id": session_id,
                "user_id": user_id,
                "source_platform": caller,
            },
            logic=chat_logic,
            project_store=store,
            use_saved_model_config=use_saved_model_config,
        )

    config = _resolve_request_model_config(normalized, use_saved=use_saved_model_config, use_env=False)
    summary_max_chars = _to_int(normalized.get("summary_max_chars"), 4000)
    raw_max_chars = _to_int(normalized.get("raw_max_chars"), 12000)
    memory_manager = chat_logic._memory()
    try:
        summary_context = memory_manager.get_session_summary_context(user_id, session_id, summary_max_chars)
    except Exception:
        summary_context = ""
    try:
        raw_context = memory_manager.get_session_raw_context(user_id, session_id, raw_max_chars)
    except Exception:
        raw_context = ""
    dialogue_list = _parse_session_dialogue_list(raw_context)
    _merge_recruitment_identity_from_dialogues(user_identity_structured, dialogue_list, message)
    if _is_recruitment_identity_missing(user_identity_structured.get("name"), field="name"):
        inferred_name = _infer_recruitment_name_from_message(message)
        if inferred_name:
            user_identity_structured["name"] = inferred_name
    job_value = user_identity_structured.get("job") or user_identity_structured.get("job_title") or user_identity_structured.get("position")
    if _is_recruitment_identity_missing(job_value, field="job"):
        inferred_job = _infer_recruitment_job_from_message(message)
        if inferred_job:
            user_identity_structured["job"] = inferred_job
    user_identity = _format_business_identity(user_identity_structured)

    context = _build_recruitment_business_context(
        store,
        project_id,
        caller,
        business_scene,
        message,
        user_identity,
    )
    selected_job = context["route"].get("selected_job") or {}
    selected_job_title = str(selected_job.get("title") or "").strip()
    current_job = str(user_identity_structured.get("job") or "").strip().lower()
    selected_job_aliases = [str(item or "").strip().lower() for item in (selected_job.get("aliases") or [])]
    if selected_job_title and current_job in {"", "未知", "unknown", "未提供", "不清楚", "待确认"}:
        user_identity_structured["job"] = selected_job_title
        user_identity = _format_business_identity(user_identity_structured)
    elif selected_job_title and current_job in selected_job_aliases:
        user_identity_structured["job"] = selected_job_title
        user_identity = _format_business_identity(user_identity_structured)
    original_identity_structured = dict(user_identity_structured)
    conversation_context = _format_recruitment_conversation_context(summary_context, raw_context)
    specific_flow_content = next(
        (str(doc.get("content") or "") for doc in context["documents"] if doc.get("role") == "specific_flow"),
        "",
    )
    flow_questions = _extract_recruitment_flow_questions(specific_flow_content)
    interview_control = _analyze_recruitment_interview_control(
        conversation_context,
        message,
        flow_questions=flow_questions,
    )
    recruitment_prompt_content = next(
        (str(doc.get("content") or "") for doc in context["documents"] if doc.get("role") == "global_prompt"),
        "",
    )
    opening_template = _extract_recruitment_opening_question(recruitment_prompt_content)
    opening_question = _recruitment_identity_opening_question(
        original_identity_structured,
        selected_job_title,
        conversation_context,
        opening_template=opening_template,
    )
    opening_control_context = ""
    if opening_question:
        opening_control_context = "\n".join([
            "开场信息缺失控制：",
            "求职者真实姓名或应聘岗位尚未确认，本轮必须先同时确认姓名和岗位。",
            f"本轮固定回复：{opening_question}",
            "禁止在姓名和岗位确认前询问居住地、老家、离职原因、客户构成、薪资等后续问题。",
        ])
    interview_control_context = "\n\n".join(
        part for part in [opening_control_context, interview_control["prompt"]] if part
    )

    final_prompt, prompt_modules = _build_recruitment_business_prompt(
        caller=caller,
        business_scene=business_scene,
        business_name=str(context["route"].get("business") or ""),
        business_path=str(context["route"].get("business_path") or ""),
        user_identity=user_identity,
        session_id=session_id,
        current_message=message,
        summary_context=conversation_context,
        interview_control_context=interview_control_context,
        documents=context["documents"],
        selected_job=selected_job,
    )
    exact_flow_question = ""
    if not opening_question and interview_control.get("should_direct_initial_question"):
        exact_flow_question = str(interview_control.get("next_question") or "").strip()

    if opening_question:
        raw_answer = opening_question
    elif exact_flow_question:
        raw_answer = exact_flow_question
    else:
        _validate_model_config(config)
        raw_answer = _call_business_text_llm(chat_logic, final_prompt, message, config)
    answer, answer_guard = _guard_recruitment_answer(raw_answer, interview_control, opening_question)
    memory_id = None
    memory_error = ""
    try:
        memory_id = memory_manager.store_conversation(user_id, session_id, message, answer)
    except Exception as e:
        memory_error = str(e)
    next_sequence_number = max([int(item.get("sequence_number") or 0) for item in dialogue_list] or [0]) + 1
    dialogue_list = [
        *dialogue_list,
        {
            "sequence_number": next_sequence_number,
            "user": message,
            "assistant": answer,
            "current": True,
        },
    ]

    return _compact_private_message_response(
        question=message,
        session_id=session_id,
        user_id=user_id,
        provider=str(config.get("provider") or normalized.get("provider") or "minimax"),
        config=config,
        answer=answer,
        prompt_modules=prompt_modules,
        extra_input={
            "caller": caller,
            "business_scene": business_scene,
            "user_identity": user_identity_structured,
        },
    )


def build_chat_stream_events(
    payload: Dict[str, Any],
    logic: Optional[SessionRAGChatLogic] = None,
    project_store: Optional[ProjectMaterialStore] = None,
    use_saved_model_config: bool = False,
) -> Iterator[Dict[str, Any]]:
    prepared = prepare_chat_request(payload, use_saved_model_config=use_saved_model_config)
    chat_logic = logic or SessionRAGChatLogic()
    store = _project_store(project_store)
    yield {"type": "status", "message": "selecting_project_documents"}
    routing_input = _project_routing_input(prepared, chat_logic)
    initial_bundle = store.get_bundle(prepared.project_id, "auto", routing_input)
    project_documents = _select_project_documents(store, prepared, chat_logic, initial_bundle.scene_id, routing_input)
    bundle = store.get_bundle(
        prepared.project_id,
        initial_bundle.scene_id,
        routing_input,
        document_context=project_documents.get("context", ""),
    )
    scene_template = _select_runtime_scene_template(store, prepared, bundle.project_id)
    activity_settings = _select_runtime_activity(store, prepared, bundle.project_id, routing_input)
    global_prompt_context = prepared.global_prompt or bundle.global_prompt
    sender_identity, sender_identity_source = _resolve_sender_identity(
        store,
        bundle.project_id,
        project_documents.get("documents", []),
        scene_template=scene_template,
        activity_settings=activity_settings,
        prepared=prepared,
    )
    project_context = _format_runtime_rag_context(
        _format_user_context_text(prepared.question, prepared.video_overview),
        global_prompt_context,
        sender_identity,
        scene_template,
        activity_settings,
        project_documents.get("context", ""),
        sender_identity_source,
    )

    yield {"type": "status", "message": "reading_memory"}
    model_conf = chat_logic._resolve_model_config(
        prepared.config["url"],
        prepared.config["key"] or "",
        prepared.config["func_name"],
        prepared.config["model_name"],
        prepared.config["max_len_input"],
    )
    summary_context = chat_logic._memory().get_session_summary_context(
        prepared.user_id,
        prepared.session_id,
        prepared.summary_max_chars,
    )

    yield {"type": "status", "message": "searching_knowledge"}
    knowledge = chat_logic._search_knowledge(prepared.question, prepared.topics, prepared.knowledge_size)
    knowledge_context, knowledge_labels = chat_logic._format_knowledge(knowledge)

    yield {"type": "status", "message": "checking_context"}
    first_prompt = chat_logic._build_prompt(
        session_id=prepared.session_id,
        summary_context=summary_context,
        knowledge_context=knowledge_context,
        project_context=project_context,
        conversation_stage=prepared.conversation_stage,
        structured=True,
    )
    first_answer = chat_logic._call_structured_llm(first_prompt, prepared.question, model_conf)

    second_pass = False
    raw_context = ""
    missing_info = first_answer.get("missing_info", "")
    if not first_answer.get("enough_info", False):
        second_pass = True
        yield {"type": "status", "message": "reading_raw_context"}
        raw_context = chat_logic._memory().get_session_raw_context(
            prepared.user_id,
            prepared.session_id,
            prepared.raw_max_chars,
        )

    yield {"type": "status", "message": "streaming_answer"}
    final_prompt = chat_logic._build_prompt(
        session_id=prepared.session_id,
        summary_context=summary_context,
        knowledge_context=knowledge_context,
        project_context=project_context,
        raw_context=raw_context,
        missing_info=missing_info,
        second_pass=second_pass,
        conversation_stage=prepared.conversation_stage,
        structured=False,
    )
    chat = getattr(chat_logic._llm(), model_conf["func_name"])
    stream_result = chat(
        url=model_conf["url"],
        api_key=model_conf["key"],
        prompt=final_prompt,
        message=prepared.question,
        model=model_conf["model_name"],
        json_format=False,
        stream=True,
        max_len_input=model_conf["max_len_input"],
    )

    answer_parts: List[str] = []
    chunks = [stream_result] if isinstance(stream_result, str) else stream_result
    for chunk in chunks:
        text = str(chunk)
        if not text:
            continue
        answer_parts.append(text)
        yield {"type": "delta", "text": text}

    answer = "".join(answer_parts).strip()
    result = SessionRAGChatResult(
        answer=answer,
        enough_info=bool(answer),
        second_pass=second_pass,
        missing_info=missing_info,
        used_knowledge=first_answer.get("used_knowledge") or knowledge_labels,
        knowledge=knowledge,
        final_prompt=final_prompt,
        prompt_trace={
            "final_prompt": final_prompt,
            "first_prompt": first_prompt,
            "second_pass": second_pass,
            "conversation_stage": prepared.conversation_stage,
            "missing_info": missing_info,
        },
    )
    chat_logic._store_memory(result, prepared.user_id, prepared.session_id, prepared.question)
    yield {
        "type": "done",
        "data": _chat_response_payload(
            prepared,
            result,
            bundle,
            project_documents,
            scene_template,
            activity_settings,
            sender_identity,
            sender_identity_source,
            prompt_trace=result.prompt_trace,
        ),
    }


class SessionRAGHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_cls, logic: Optional[SessionRAGChatLogic] = None):
        super().__init__(server_address, handler_cls)
        self.project_store = ProjectMaterialStore()
        self.logic = logic or SessionRAGChatLogic(
            memory_manager=InProcessSessionMemoryManager(),
            knowledge_logic=PlaceholderKnowledgeLogic(),
            llm_tools=SimpleLLMChatTools(),
        )


class SessionRAGRequestHandler(BaseHTTPRequestHandler):
    server_version = "SessionRAGHTTP/0.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path in {"/", "/session-rag-chat"}:
            self._send_html()
        elif path == "/file-parser":
            self._send_html(FILE_PARSER_HTML_FILE)
        elif path == "/project-materials":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/admin-vue")
            self.end_headers()
        elif path in {"/admin", "/admin-vue"}:
            self._send_html(ADMIN_VUE_HTML_FILE)
        elif path.startswith("/static/"):
            self._send_static(path.removeprefix("/static/"))
        elif path == "/api/presets":
            projects = self.server.project_store.list_projects()
            default_project = projects[0] if projects else {}
            self._send_json({
                "providers": public_provider_presets(),
                "defaults": {
                    "provider": "minimax",
                    "session_id": uuid.uuid4().hex,
                    "user_id": DEFAULT_USER_ID,
                    "project_id": default_project.get("project_id", ""),
                    "scene_id": default_project.get("default_scene", "auto"),
                    "max_len_input": 16000,
                    "summary_max_chars": 4000,
                    "raw_max_chars": 12000,
                    "knowledge_size": 4,
                },
            })
        elif path == "/api/projects":
            self._send_json({
                "projects": self.server.project_store.list_projects(),
                "default_project_id": self.server.project_store.default_project_id,
            })
        elif path == "/api/v1/health":
            self._send_json({"ok": True, "version": "v1"})
        elif path == "/api/v1/projects":
            self._send_json({
                "ok": True,
                "data": {
                    "projects": self.server.project_store.list_projects(),
                    "default_project_id": self.server.project_store.default_project_id,
                },
            })
        elif path == "/api/v1/presets":
            projects = self.server.project_store.list_projects()
            default_project = projects[0] if projects else {}
            self._send_json({
                "ok": True,
                "data": {
                    "providers": public_provider_presets(),
                    "defaults": {
                        "provider": "minimax",
                        "session_id": uuid.uuid4().hex,
                        "user_id": DEFAULT_USER_ID,
                        "project_id": default_project.get("project_id", ""),
                        "conversation_stage": "first_comment",
                        "max_len_input": 16000,
                        "summary_max_chars": 4000,
                        "raw_max_chars": 12000,
                        "knowledge_size": 4,
                    },
                },
            })
        elif path == "/api/v1/model/configs":
            self._send_json({"ok": True, "data": _public_model_configs()})
        elif path == "/api/project-materials":
            self._handle_project_materials(parsed_url)
        elif path == "/api/admin/state":
            self._handle_admin_state(parsed_url)
        elif path == "/api/health":
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/v1/business/reply":
            self._handle_business_reply()
            return

        if path == "/api/v1/private-message/generate":
            self._handle_public_private_message_generate()
            return

        if path == "/api/v1/private-message/prompt-preview":
            self._handle_public_prompt_preview()
            return

        if path == "/api/v1/private-message/route-debug":
            self._handle_public_route_debug()
            return

        if path == "/api/v1/model/test":
            self._handle_public_model_test()
            return

        if path == "/api/v1/model/config/save":
            self._handle_model_config_save()
            return

        if path == "/api/v1/model/config/delete":
            self._handle_model_config_delete()
            return

        if path == "/api/files/parse":
            self._handle_file_parse()
            return

        if path == "/api/project-documents/import":
            self._handle_project_document_import()
            return

        if path == "/api/chat/stream":
            self._handle_chat_stream()
            return

        if path == "/api/config/test":
            self._handle_config_test()
            return

        if path == "/api/project-materials/save":
            self._handle_project_material_save()
            return

        if path == "/api/project-materials/delete":
            self._handle_project_material_delete()
            return

        if path == "/api/projects/create":
            self._handle_project_create()
            return

        if path == "/api/project-route/debug":
            self._handle_project_route_debug()
            return

        if path == "/api/admin/knowledge/upload":
            self._handle_admin_knowledge_upload()
            return

        if path == "/api/admin/knowledge/save":
            self._handle_admin_knowledge_save()
            return

        if path == "/api/admin/knowledge/delete":
            self._handle_admin_knowledge_delete()
            return

        if path == "/api/admin/knowledge/base/update":
            self._handle_admin_knowledge_base_update()
            return

        if path == "/api/admin/knowledge/base/delete":
            self._handle_admin_knowledge_base_delete()
            return

        if path == "/api/admin/knowledge/domain/update":
            self._handle_admin_knowledge_domain_update()
            return

        if path == "/api/admin/knowledge/domain/delete":
            self._handle_admin_knowledge_domain_delete()
            return

        if path == "/api/admin/open-file-location":
            self._handle_admin_open_file_location()
            return

        if path == "/api/admin/scene-template/save":
            self._handle_admin_scene_template_save()
            return

        if path == "/api/admin/scene-template/delete":
            self._handle_admin_scene_template_delete()
            return

        if path == "/api/admin/scene-template/generate":
            self._handle_admin_scene_template_generate()
            return

        if path == "/api/admin/activity/save":
            self._handle_admin_activity_save()
            return

        if path == "/api/admin/activity/delete":
            self._handle_admin_activity_delete()
            return

        if path == "/api/admin/prompt/restore":
            self._handle_admin_prompt_restore()
            return

        if path != "/api/chat":
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            data = build_public_private_message_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
                use_saved_model_config=True,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_public_private_message_generate(self):
        try:
            payload = self._read_json()
            data = build_public_private_message_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
                use_saved_model_config=True,
            )
            self._send_json({"answer": data.get("reply", ""), "ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_business_reply(self):
        try:
            payload = self._read_json()
            data = build_business_reply_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
                use_saved_model_config=True,
            )
            self._send_json({"answer": data.get("reply", ""), "ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_public_prompt_preview(self):
        try:
            payload = self._read_json()
            data = build_public_prompt_preview_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_public_route_debug(self):
        try:
            payload = self._read_json()
            data = build_public_route_debug_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_public_model_test(self):
        try:
            payload = self._read_json()
            data = build_public_model_test_response(
                payload,
                llm_tools=self.server.logic._llm(),
                use_saved_model_config=True,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_model_config_save(self):
        try:
            payload = self._read_json()
            data = build_model_config_save_response(payload)
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_model_config_delete(self):
        try:
            payload = self._read_json()
            data = build_model_config_delete_response(payload)
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_materials(self, parsed_url):
        try:
            params = parse_qs(parsed_url.query)
            project_id = (params.get("project_id") or [""])[0]
            data = build_project_materials_response(
                project_store=self.server.project_store,
                project_id=project_id,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_state(self, parsed_url):
        try:
            params = parse_qs(parsed_url.query)
            project_id = (params.get("project_id") or [""])[0]
            data = build_admin_state_response(
                project_store=self.server.project_store,
                project_id=project_id,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_material_save(self):
        try:
            payload = self._read_json()
            data = build_project_material_save_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_material_delete(self):
        try:
            payload = self._read_json()
            data = build_project_material_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_create(self):
        try:
            payload = self._read_json()
            data = build_project_create_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_route_debug(self):
        try:
            payload = self._read_json()
            data = build_project_route_debug_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_upload(self):
        try:
            form = self._read_multipart()
            file_field = self._form_field(form, "file")
            if isinstance(file_field, list):
                file_field = file_field[0] if file_field else None
            if file_field is None or not getattr(file_field, "filename", ""):
                raise WebInputError("file is required")
            model_conf = self._import_model_conf(form)
            data = build_admin_knowledge_upload_response(
                file_name=Path(file_field.filename).name,
                file_data=file_field.file.read(),
                project_id=self._form_value(form, "project_id"),
                knowledge_base=self._form_value(form, "knowledge_base"),
                domain=self._form_value(form, "domain"),
                section=self._form_value(form, "section"),
                llm_tools=self.server.logic._llm() if model_conf else None,
                model_conf=model_conf,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_save(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_save_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_delete(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_base_update(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_base_update_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_base_delete(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_base_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_domain_update(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_domain_update_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_knowledge_domain_delete(self):
        try:
            payload = self._read_json()
            data = build_admin_knowledge_domain_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_open_file_location(self):
        try:
            payload = self._read_json()
            data = build_admin_open_file_location_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_scene_template_save(self):
        try:
            payload = self._read_json()
            data = build_admin_scene_template_save_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_scene_template_delete(self):
        try:
            payload = self._read_json()
            data = build_admin_scene_template_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_scene_template_generate(self):
        try:
            payload = self._read_json()
            data = build_admin_scene_template_generate_response(
                payload,
                llm_tools=self.server.logic._llm(),
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_activity_save(self):
        try:
            payload = self._read_json()
            data = build_admin_activity_save_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_activity_delete(self):
        try:
            payload = self._read_json()
            data = build_admin_activity_delete_response(
                payload,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_admin_prompt_restore(self):
        try:
            payload = self._read_json()
            data = build_admin_prompt_restore_response(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_config_test(self):
        try:
            payload = self._read_json()
            data = build_config_test_response(
                payload,
                llm_tools=self.server.logic._llm(),
                use_saved_model_config=True,
            )
            self._send_json({"ok": True, "data": data})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_project_document_import(self):
        try:
            form = self._read_multipart()
            file_field = self._form_field(form, "file")
            if isinstance(file_field, list):
                file_field = file_field[0] if file_field else None
            if file_field is None or not getattr(file_field, "filename", ""):
                raise WebInputError("file is required")

            file_data = file_field.file.read()
            project_id = self._form_value(form, "project_id")
            model_conf = self._import_model_conf(form)
            result = self.server.project_store.import_file_to_knowledge(
                file_name=Path(file_field.filename).name,
                file_data=file_data,
                project_id=project_id,
                llm_tools=self.server.logic._llm() if model_conf else None,
                model_conf=model_conf,
            )
            self._send_json({"ok": True, "data": result})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_file_parse(self):
        try:
            form = self._read_multipart()
            file_field = self._form_field(form, "file")
            if isinstance(file_field, list):
                file_field = file_field[0] if file_field else None
            if file_field is None or not getattr(file_field, "filename", ""):
                raise WebInputError("file is required")

            result = parse_uploaded_file(
                file_name=file_field.filename,
                file_data=file_field.file.read(),
                project_store=self.server.project_store,
            )
            self._send_json({"ok": True, "data": result})
        except WebInputError as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_chat_stream(self):
        try:
            payload = self._read_json()
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        try:
            for event in build_chat_stream_events(
                payload,
                logic=self.server.logic,
                project_store=self.server.project_store,
                use_saved_model_config=True,
            ):
                self._write_sse(event)
        except BrokenPipeError:
            return
        except Exception as e:
            try:
                self._write_sse({"type": "error", "error": str(e)})
            except BrokenPipeError:
                return

    def _read_multipart(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise WebInputError("multipart/form-data is required")
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

    @staticmethod
    def _form_field(form, name: str):
        if name not in form:
            return None
        return form[name]

    @staticmethod
    def _form_value(form, name: str, default: str = "") -> str:
        field = SessionRAGRequestHandler._form_field(form, name)
        if field is None:
            return default
        if isinstance(field, list):
            field = field[0] if field else None
        if field is None:
            return default
        return str(getattr(field, "value", default) or default).strip()

    def _import_model_conf(self, form) -> Optional[Dict[str, Any]]:
        provider = self._form_value(form, "provider", "custom") or "custom"
        config = _resolve_request_model_config(
            {
                "provider": provider,
                "url": self._form_value(form, "url"),
                "api_key": self._form_value(form, "api_key"),
                "func_name": self._form_value(form, "func_name"),
                "model_name": self._form_value(form, "model_name"),
                "max_len_input": self._form_value(form, "max_len_input"),
            },
            use_saved=True,
            use_env=False,
        )
        if not config.get("url") or not config.get("func_name") or not config.get("model_name"):
            return None
        if config.get("requires_key") and not config.get("key"):
            return None
        return config

    def _write_sse(self, event: Dict[str, Any]):
        content = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        self.wfile.write(content.encode("utf-8"))
        self.wfile.flush()

    def _send_html(self, html_file: Path = HTML_FILE):
        try:
            content = html_file.read_bytes()
        except FileNotFoundError:
            self._send_json({"ok": False, "error": "html file not found"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_static(self, relative_path: str):
        try:
            static_root = STATIC_DIR.resolve()
            target = (static_root / relative_path).resolve()
            target.relative_to(static_root)
        except ValueError:
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        if not target.is_file():
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        content = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> Dict[str, Any]:
        length = _to_int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, data: Dict[str, Any], status: int = HTTPStatus.OK):
        content = json.dumps(self._response_envelope(data, status), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _response_envelope(data: Dict[str, Any], status: int = HTTPStatus.OK) -> Dict[str, Any]:
        payload = dict(data or {})
        if "code" in payload:
            payload.setdefault("msg", "success" if int(payload.get("code") or 0) == 0 else "")
            payload.setdefault("data", None)
            payload.pop("ok", None)
            payload.pop("error", None)
            return payload

        if "ok" in payload:
            ok = bool(payload.pop("ok"))
            message = str(payload.pop("msg", "") or payload.pop("error", "") or ("success" if ok else "failed"))
            body = payload.pop("data", None)
            if body is None and payload:
                payload.pop("answer", None)
                body = payload or None
            return {
                "code": 0 if ok else int(status),
                "msg": message,
                "data": body,
            }

        message = str(payload.pop("msg", "") or ("success" if int(status) < 400 else "failed"))
        body = payload.pop("data", None)
        if body is None and payload:
            body = payload
        return {
            "code": 0 if int(status) < 400 else int(status),
            "msg": message,
            "data": body,
        }


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def choose_port(host: str, preferred_port: int) -> int:
    if preferred_port == 0 or _is_port_available(host, preferred_port):
        return preferred_port
    for port in range(preferred_port + 1, preferred_port + 30):
        if _is_port_available(host, port):
            return port
    raise OSError(f"No available port found near {preferred_port}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start local Session RAG chat web page.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    port = choose_port(args.host, args.port)
    server = SessionRAGHTTPServer((args.host, port), SessionRAGRequestHandler)
    port = server.server_port
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", ""} else args.host
    url = f"http://{display_host}:{port}"

    print(f"Session RAG chat page: {url}")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()





