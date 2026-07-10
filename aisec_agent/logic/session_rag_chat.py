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

        result = SessionRAGChatResult(
            answer=final_answer.get("answer", ""),
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
First-contact private-message rules:
- This is the only first private message after a public comment; do not stop at greeting and asking questions.
- If a scene template is provided, cover the template steps as a complete flow, especially:
  1. give a safe initial solution or next-step direction based on the comment and retrieved knowledge;
  2. include a concrete hook/sweetener that encourages the user to reply, such as a material pack, initial assessment, activity, trial, or reserved spot when supported by context.
- If exact business data is missing, use cautious placeholders like xx资料、xx评估、xx活动, but do not invent exact prices, quantities, medical promises, or guaranteed results.
- Do not output self-proving phrases about send method, interruption, or human identity. Prove personalization by accurately referencing the user's comment, video overview, pain point, or context.
- End with one low-friction reply question or action. Do not ask for a keyword if an entrance/contact method has already been given.
- Keep it concise and natural, preferably within 200 Chinese characters.
""".strip()
        if conversation_stage == "private_followup":
            return """
Private follow-up rules:
- Continue the existing private conversation, answer the latest concern directly, and do not repeat the first-contact self-introduction.
- If more information is needed, still provide a useful partial answer or next step based on available context.
- Use one clear next action only; do not duplicate keyword prompts and contact/entry delivery in the same reply.
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
- If the compressed memory and knowledge are not enough, set enough_info to false and explain missing_info.
- Return JSON only with these fields: answer, enough_info, missing_info, used_knowledge.
""".strip()
        else:
            output_contract = """
- Use the compressed memory, knowledge context, and raw session context if present.
- If some business details are missing, answer safely without inventing unsupported facts.
- Return only the private-message text; do not return JSON, markdown, labels, or explanations.
""".strip()
        stage_rules = SessionRAGChatLogic._stage_reply_rules(conversation_stage)
        return f"""
Task:
- Generate a reply using only the user input/session context, global prompt, scene template, and retrieved knowledge.
- Follow the scene template first when it is provided.
- Prefer facts from retrieved knowledge and session memory.
- Do not invent facts that are not supported by context.
{output_contract}
{stage_rules}

mode: {mode}
conversation_stage: {conversation_stage}
session_id: {session_id}

<compressed_session_memory>
{summary_context or "No compressed memory yet."}
</compressed_session_memory>
{project_section}

<knowledge_context>
{knowledge_context or "No knowledge snippets were found or configured."}
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
