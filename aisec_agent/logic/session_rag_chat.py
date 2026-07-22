#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


DEFAULT_USER_ID = "local_user"
DEFAULT_SUMMARY_CHARS = 4000
DEFAULT_RAW_CHARS = 12000
DEFAULT_KNOWLEDGE_SIZE = 4


@dataclass
class SessionRAGChatResult:
    answer: str
    enough_info: bool
    second_pass: bool
    missing_info: str = ""
    used_knowledge: List[str] = field(default_factory=list)
    knowledge: List[Dict[str, Any]] = field(default_factory=list)
    memory_id: Optional[str] = None
    memory_error: str = ""
    final_prompt: str = ""
    prompt_trace: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "enough_info": self.enough_info,
            "second_pass": self.second_pass,
            "missing_info": self.missing_info,
            "used_knowledge": self.used_knowledge,
            "knowledge": self.knowledge,
            "memory_id": self.memory_id,
            "memory_error": self.memory_error,
            "final_prompt": self.final_prompt,
            "prompt_trace": self.prompt_trace,
        }


class SessionRAGChatLogic:
    """Lightweight session-aware RAG chat flow for local console use."""

    def __init__(self, memory_manager=None, knowledge_logic=None, llm_tools=None):
        self.memory_manager = memory_manager
        self.knowledge_logic = knowledge_logic
        self.llm_tools = llm_tools
        self.logger = logging.getLogger(self.__class__.__name__)

    def chat_once(
        self,
        user_input: str,
        session_id: str,
        user_id: str = DEFAULT_USER_ID,
        topics: Optional[List[str]] = None,
        url: str = None,
        key: str = None,
        func_name: str = None,
        model_name: str = None,
        max_len_input: int = None,
        summary_max_chars: int = DEFAULT_SUMMARY_CHARS,
        raw_max_chars: int = DEFAULT_RAW_CHARS,
        knowledge_size: int = DEFAULT_KNOWLEDGE_SIZE,
        conversation_stage: str = "first_comment",
        project_context: str = "",
    ) -> SessionRAGChatResult:
        if not user_input or not user_input.strip():
            raise ValueError("user_input must not be empty")
        if not session_id or not session_id.strip():
            raise ValueError("session_id must not be empty")

        user_id = user_id or DEFAULT_USER_ID
        topics = [topic for topic in (topics or []) if topic]

        model_conf = self._resolve_model_config(url, key, func_name, model_name, max_len_input)
        summary_context = self._memory().get_session_summary_context(user_id, session_id, summary_max_chars)
        knowledge = self._search_knowledge(user_input, topics, knowledge_size)
        knowledge_context, knowledge_labels = self._format_knowledge(knowledge)

        first_prompt = self._build_prompt(
            session_id=session_id,
            summary_context=summary_context,
            knowledge_context=knowledge_context,
            project_context=project_context,
            conversation_stage=conversation_stage,
        )
        first_answer = self._call_structured_llm(first_prompt, user_input, model_conf)

        final_answer = first_answer
        final_prompt = first_prompt
        second_pass = False
        if not first_answer.get("enough_info", False):
            second_pass = True
            raw_context = self._memory().get_session_raw_context(user_id, session_id, raw_max_chars)
            second_prompt = self._build_prompt(
                session_id=session_id,
                summary_context=summary_context,
                knowledge_context=knowledge_context,
                project_context=project_context,
                raw_context=raw_context,
                missing_info=first_answer.get("missing_info", ""),
                second_pass=True,
                conversation_stage=conversation_stage,
            )
            final_prompt = second_prompt
            final_answer = self._call_structured_llm(second_prompt, user_input, model_conf)

        answer = self._format_private_message_readability(final_answer.get("answer", ""))

        result = SessionRAGChatResult(
            answer=answer,
            enough_info=bool(final_answer.get("enough_info", True)),
            second_pass=second_pass,
            missing_info=final_answer.get("missing_info") or first_answer.get("missing_info", ""),
            used_knowledge=final_answer.get("used_knowledge") or first_answer.get("used_knowledge") or knowledge_labels,
            knowledge=knowledge,
            final_prompt=final_prompt,
            prompt_trace={
                "final_prompt": final_prompt,
                "first_prompt": first_prompt,
                "second_prompt": final_prompt if second_pass else "",
                "second_pass": second_pass,
                "conversation_stage": conversation_stage,
            },
        )
        self._store_memory(result, user_id, session_id, user_input)
        return result

    def _resolve_model_config(
        self,
        url: str = None,
        key: str = None,
        func_name: str = None,
        model_name: str = None,
        max_len_input: int = None,
    ) -> Dict[str, Any]:
        if not all([url, func_name, model_name, max_len_input]):
            from aisec_agent.config import AIDED_LLM_CONF
        else:
            AIDED_LLM_CONF = {}

        return {
            "url": url or AIDED_LLM_CONF.get("url"),
            "key": key if key is not None else AIDED_LLM_CONF.get("key", ""),
            "func_name": func_name or AIDED_LLM_CONF.get("func_name", "ollama_chat"),
            "model_name": model_name or AIDED_LLM_CONF.get("model_name"),
            "max_len_input": self._to_int(max_len_input or AIDED_LLM_CONF.get("max_len_input", 16000)),
        }

    @staticmethod
    def _to_int(value: Any, default: int = 16000) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_private_message_readability(answer: Any, max_line_chars: int = 34, max_lines: int = 4) -> str:
        text = str(answer or "").strip()
        if not text:
            return ""

        existing_lines = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
        if len(existing_lines) > 1:
            return "\n\n".join(existing_lines)
        if len(text) <= max_line_chars:
            return text

        segments = SessionRAGChatLogic._split_reply_segments(text)
        lines = []
        current = ""
        for segment in segments:
            if not segment:
                continue
            if current and len(current) + len(segment) > max_line_chars:
                lines.append(current.strip())
                current = segment
            else:
                current += segment

            while len(current) > max_line_chars + 8 and not any(mark in current for mark in "，,、。！？!?；;"):
                lines.append(current[:max_line_chars].strip())
                current = current[max_line_chars:].strip()

        if current.strip():
            lines.append(current.strip())

        lines = [line for line in lines if line]
        if len(lines) <= 1:
            return text
        if len(lines) > max_lines:
            lines = lines[: max_lines - 1] + ["".join(lines[max_lines - 1:]).strip()]
        return "\n\n".join(lines)

    @staticmethod
    def _split_reply_segments(text: str) -> List[str]:
        sentence_parts = re.split(r"([。！？!?；;])", text)
        sentence_segments = []
        for index in range(0, len(sentence_parts), 2):
            body = sentence_parts[index].strip()
            mark = sentence_parts[index + 1] if index + 1 < len(sentence_parts) else ""
            if body or mark:
                sentence_segments.append(f"{body}{mark}")

        segments = []
        for sentence in sentence_segments:
            if len(sentence) <= 34:
                segments.append(sentence)
                continue
            comma_parts = re.split(r"([，,、])", sentence)
            for index in range(0, len(comma_parts), 2):
                body = comma_parts[index].strip()
                mark = comma_parts[index + 1] if index + 1 < len(comma_parts) else ""
                if body or mark:
                    segments.append(f"{body}{mark}")
        return segments

    def _memory(self):
        if self.memory_manager is None:
            from aisec_agent.logic.memory.tools import LongTermMemoryManager
            self.memory_manager = LongTermMemoryManager()
        return self.memory_manager

    def _knowledge(self):
        if self.knowledge_logic is None:
            from aisec_agent.logic.knowledge.logic import PreFileLogic
            self.knowledge_logic = PreFileLogic()
        return self.knowledge_logic

    def _llm(self):
        if self.llm_tools is None:
            from aisec_agent.model.llm_chat import LLMChatTools
            self.llm_tools = LLMChatTools()
        return self.llm_tools

    def _search_knowledge(self, user_input: str, topics: List[str], size: int) -> List[Dict[str, Any]]:
        if not topics:
            return []
        try:
            return self._knowledge().search_knowledge(user_input, topics, size=size) or []
        except Exception as e:
            self.logger.warning(f"Knowledge search failed: {e}")
            return []

    @staticmethod
    def _format_knowledge(knowledge: List[Dict[str, Any]]) -> tuple[str, List[str]]:
        blocks = []
        labels = []
        for index, item in enumerate(knowledge, 1):
            label = f"{item.get('topic', '')}/{item.get('file_name', '')}#{index}"
            labels.append(label)
            blocks.append(
                "\n".join([
                    f"[{index}] source: {label}",
                    f"similarity: {item.get('similarity', '')}",
                    f"content: {item.get('content', '')}",
                ])
            )
        return "\n\n".join(blocks), labels

    @staticmethod
    def _stage_reply_rules(conversation_stage: str) -> str:
        if conversation_stage == "first_comment":
            return """
首次私信规则：
- 这是公开评论后的第一条私信；不要只打招呼或只提问，要给出有价值的承接。
- 如果提供了场景模板，优先按照模板步骤组织成完整回复，尤其要做到：
  1. 基于用户评论和检索知识，给出安全的初步方案或下一步方向；
  2. 在上下文支持时，加入能促使用户回复的具体钩子或甜头，例如资料包、初评、活动、试用、预留名额等。
- 如果缺少精确业务数据，可以使用“资料”“评估”“活动”等谨慎占位表达，但不要编造具体价格、数量、医疗承诺或保证性结果。
- 不要输出“我是人工发的”“不是群发”“打扰了”等自证发送方式或真人身份的话术；要通过准确承接用户评论、视频概述、痛点或上下文来体现个性化。
- 视频概述只用于判断用户可能感兴趣的方向，不要在私信里明说“视频里讲的是/视频介绍的是/看到这个视频”；可以自然表达为“看到您对xx比较感兴趣”，信息不足时也可以不提视频。
- 结尾只保留一个低门槛的回复问题或行动。如果已经给出入口或联系方式，不要再要求用户回复关键词。
- 回复要简洁自然，优先控制在 200 个中文字符以内。
- 为了方便用户阅读，单句不要过长；一句话明显过长时请主动换行。
- 当语义发生明显切换、动作切换或信息层次变化时，请分段换行，不要把所有内容挤成一整段。
- 优先输出 2-4 行自然短句，行与行之间空一行，保证像手机私信里真人发送的阅读感。
""".strip()
        if conversation_stage == "private_followup":
            return """
私信跟进规则：
- 延续已有私信对话，直接回应用户最新关注点，不要重复首次私信里的自我介绍。
- 如果还需要更多信息，也要先基于已有上下文给出有用的部分回答或下一步建议。
- 只保留一个清晰的下一步行动；不要在同一条回复里同时要求回复关键词，又投递联系方式或入口。
- 为了方便用户阅读，单句不要过长；一句话明显过长时请主动换行。
- 当语义发生明显切换、动作切换或信息层次变化时，请分段换行，不要把所有内容挤成一整段。
- 优先输出 2-4 行自然短句，行与行之间空一行，保证像手机私信里真人发送的阅读感。
""".strip()
        return ""
    @staticmethod
    def _build_prompt(
        session_id: str,
        summary_context: str,
        knowledge_context: str,
        project_context: str = "",
        raw_context: str = "",
        missing_info: str = "",
        second_pass: bool = False,
        conversation_stage: str = "first_comment",
        structured: bool = True,
    ) -> str:
        mode = "second_pass" if second_pass else "first_pass"
        raw_section = f"\n<raw_session_context>\n{raw_context}\n</raw_session_context>\n" if raw_context else ""
        missing_section = f"\n<missing_info_from_first_pass>\n{missing_info}\n</missing_info_from_first_pass>\n" if missing_info else ""
        project_section = f"\n<scene_template_and_retrieved_knowledge>\n{project_context}\n</scene_template_and_retrieved_knowledge>\n" if project_context else ""
        if structured:
            output_contract = """
- 如果压缩记忆和知识内容不足以生成可靠回复，请将 enough_info 设为 false，并在 missing_info 中说明缺少什么信息。
- 只返回 JSON，不要返回 Markdown、解释或额外文本。JSON 字段固定为：answer, enough_info, missing_info, used_knowledge。
""".strip()
        else:
            output_contract = """
- 如存在压缩记忆、知识上下文或原始会话上下文，请结合使用。
- 如果缺少部分业务细节，请安全回答，不要编造没有上下文支持的事实。
- 只返回私信正文，不要返回 JSON、Markdown、标签或解释。
""".strip()
        stage_rules = SessionRAGChatLogic._stage_reply_rules(conversation_stage)
        return f"""
任务：
- 只根据用户输入、会话上下文、全局提示词、场景模板和检索知识生成回复。
- 人员身份由业务、视频概述和活动自动生成，不读取知识库里的 sender_identity 字段；如果页面/API已传入账号或产品身份，以配置身份为准。
- 视频概述只用于判断用户可能感兴趣的方向，不要在私信正文里明说“视频里讲的是/视频介绍的是/看到这个视频”；可改成“看到您对xx比较感兴趣”，信息不足时也可以不提视频。
- 如果提供了场景模板，请优先遵循场景模板。
- 优先使用检索知识和会话记忆中的事实。
- 不要编造上下文中没有支持的事实。
{output_contract}
{stage_rules}

mode: {mode}
conversation_stage: {conversation_stage}
session_id: {session_id}

<compressed_session_memory>
{summary_context or "暂无压缩会话记忆。"}
</compressed_session_memory>
{project_section}

<knowledge_context>
{knowledge_context or "未找到或未配置知识片段。"}
</knowledge_context>
{raw_section}{missing_section}
""".strip()

    def _call_structured_llm(self, prompt: str, user_input: str, model_conf: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from aisec_agent.model.llm_typing import SessionRAGAnswerModel
        except ModuleNotFoundError as e:
            if e.name != "pydantic":
                raise
            SessionRAGAnswerModel = True

        chat = getattr(self._llm(), model_conf["func_name"])
        response = chat(
            url=model_conf["url"],
            api_key=model_conf["key"],
            prompt=prompt,
            message=user_input,
            model=model_conf["model_name"],
            json_format=SessionRAGAnswerModel,
            stream=False,
            max_len_input=model_conf["max_len_input"],
        )
        data = self._parse_llm_json(response)
        return self._normalize_answer(data)

    @staticmethod
    def _parse_llm_json(response: Any) -> Dict[str, Any]:
        if isinstance(response, dict):
            return response
        text = response.decode("utf-8", errors="replace") if isinstance(response, bytes) else str(response)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            matches = re.findall(r"\{.*\}", text, flags=re.DOTALL)
            if matches:
                return json.loads(matches[0])
            raise

    @staticmethod
    def _normalize_answer(data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "answer": str(data.get("answer", "")),
            "enough_info": bool(data.get("enough_info", False)),
            "missing_info": str(data.get("missing_info", "")),
            "used_knowledge": data.get("used_knowledge") if isinstance(data.get("used_knowledge"), list) else [],
        }

    def _store_memory(self, result: SessionRAGChatResult, user_id: str, session_id: str, user_input: str) -> None:
        try:
            result.memory_id = self._memory().store_conversation(user_id, session_id, user_input, result.answer)
        except Exception as e:
            result.memory_error = str(e)
            self.logger.warning(f"Store conversation failed: {e}")
