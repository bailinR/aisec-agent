import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from aisec_agent.logic.llm_presets import (
    DEEPSEEK_CHAT_COMPLETIONS_URL,
    DEEPSEEK_DEFAULT_MODEL,
    MINIMAX_ANTHROPIC_BASE_URL,
    MINIMAX_DEFAULT_MODEL,
)
from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic


class FakeMemory:
    def __init__(self):
        self.raw_called = False
        self.store_calls = []

    def get_session_summary_context(self, user_id, session_id, max_chars):
        return f"summary for {user_id}/{session_id}"

    def get_session_raw_context(self, user_id, session_id, max_chars):
        self.raw_called = True
        return f"raw for {user_id}/{session_id}"

    def store_conversation(self, user_id, session_id, user_question, ai_response):
        self.store_calls.append((user_id, session_id, user_question, ai_response))
        return "memory-1"


class FakeKnowledge:
    def __init__(self):
        self.calls = []

    def search_knowledge(self, question, topics, size=4):
        self.calls.append((question, topics, size))
        return [
            {
                "topic": "topic_a",
                "file_name": "faq.md",
                "similarity": 0.91,
                "content": "kb content",
            }
        ]


class FakeSessionRAGChatLogic(SessionRAGChatLogic):
    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self.responses = list(responses)
        self.prompts = []

    def _resolve_model_config(self, *args, **kwargs):
        return {
            "url": "http://ollama",
            "key": "",
            "func_name": "ollama_chat",
            "model_name": "test-model",
            "max_len_input": 1000,
        }

    def _call_structured_llm(self, prompt, user_input, model_conf):
        self.prompts.append(prompt)
        return self.responses.pop(0)


class SessionRAGChatLogicTest(unittest.TestCase):
    def test_enough_info_skips_raw_context_and_stores_memory(self):
        memory = FakeMemory()
        knowledge = FakeKnowledge()
        logic = FakeSessionRAGChatLogic(
            responses=[
                {
                    "answer": "final answer",
                    "enough_info": True,
                    "missing_info": "",
                    "used_knowledge": ["topic_a/faq.md#1"],
                }
            ],
            memory_manager=memory,
            knowledge_logic=knowledge,
        )

        result = logic.chat_once("hello", "sid-1", user_id="user-1", topics=["topic_a"])

        self.assertEqual(result.answer, "final answer")
        self.assertFalse(result.second_pass)
        self.assertFalse(memory.raw_called)
        self.assertEqual(len(memory.store_calls), 1)
        self.assertIn("kb content", logic.prompts[0])

    def test_not_enough_info_reads_raw_context_and_second_passes(self):
        memory = FakeMemory()
        logic = FakeSessionRAGChatLogic(
            responses=[
                {
                    "answer": "need more",
                    "enough_info": False,
                    "missing_info": "need raw context",
                    "used_knowledge": [],
                },
                {
                    "answer": "answer after raw context",
                    "enough_info": True,
                    "missing_info": "",
                    "used_knowledge": [],
                },
            ],
            memory_manager=memory,
            knowledge_logic=FakeKnowledge(),
        )

        result = logic.chat_once("continue", "sid-2", user_id="user-2", topics=["topic_a"])

        self.assertTrue(result.second_pass)
        self.assertTrue(memory.raw_called)
        self.assertEqual(result.answer, "answer after raw context")
        self.assertIn("raw for user-2/sid-2", logic.prompts[1])
        self.assertIn("need raw context", logic.prompts[1])

    def test_skips_knowledge_search_when_topics_empty(self):
        knowledge = FakeKnowledge()
        logic = FakeSessionRAGChatLogic(
            responses=[
                {
                    "answer": "no kb answer",
                    "enough_info": True,
                    "missing_info": "",
                    "used_knowledge": [],
                }
            ],
            memory_manager=FakeMemory(),
            knowledge_logic=knowledge,
        )

        result = logic.chat_once("hello", "sid-3", topics=[])

        self.assertEqual(result.answer, "no kb answer")
        self.assertEqual(knowledge.calls, [])

    def test_generated_private_message_is_split_into_mobile_readable_lines(self):
        memory = FakeMemory()
        logic = FakeSessionRAGChatLogic(
            responses=[
                {
                    "answer": "您好，我是健康顾问助理，看到您对这个方向比较关注，这类情况一般需要先看年龄、疼痛多久和有没有检查结果，我这边可以先发您一份资料，也可以帮您做一次初步评估，您是自己了解还是帮家人问呢？",
                    "enough_info": True,
                    "missing_info": "",
                    "used_knowledge": [],
                }
            ],
            memory_manager=memory,
            knowledge_logic=FakeKnowledge(),
        )

        result = logic.chat_once("这个有用吗", "sid-readable", topics=[])

        self.assertIn("\n\n", result.answer)
        self.assertLessEqual(len([line for line in result.answer.splitlines() if line.strip()]), 4)
        self.assertEqual(memory.store_calls[0][3], result.answer)

    def test_prompt_uses_only_session_template_and_knowledge_shell(self):
        prompt = SessionRAGChatLogic._build_prompt(
            session_id="sid-hooks",
            summary_context="summary ctx",
            knowledge_context="kb ctx",
            project_context="<scene_template>{}</scene_template>\n\n<retrieved_knowledge>doc ctx</retrieved_knowledge>",
            conversation_stage="first_comment",
        )

        self.assertIn("只根据用户输入、会话上下文、全局提示词、场景模板和检索知识生成回复", prompt)
        self.assertIn("summary ctx", prompt)
        self.assertIn("kb ctx", prompt)
        self.assertIn("<scene_template>", prompt)
        self.assertIn("<retrieved_knowledge>", prompt)
        self.assertIn("给出安全的初步方案或下一步方向", prompt)
        self.assertIn("钩子", prompt)
        self.assertIn("不要只打招呼或只提问", prompt)
        self.assertIn("准确承接用户评论", prompt)
        self.assertIn("一句话明显过长时请主动换行", prompt)
        self.assertIn("行与行之间空一行", prompt)
        self.assertIn("不要在私信正文里明说“视频里讲的是/视频介绍的是/看到这个视频”", prompt)
        self.assertIn("身份称谓只能使用“运营”“助理”“顾问”“客服”“工作人员”", prompt)
        self.assertIn("如果场景模板已经定义开场，优先使用模板开场", prompt)
        self.assertIn("确需自我介绍时使用“您好，我是这边的{通用身份}”", prompt)
        self.assertIn("模板是本轮话术的高优先级约束", prompt)
        self.assertIn("不要用通用默认话术覆盖模板", prompt)
        self.assertIn("禁止说“顾问这边”“我是账号运营”“我是账号的助理”", prompt)
        self.assertIn("禁止说“健康顾问”“招聘助理”“跨境运营顾问”", prompt)
        self.assertNotIn("You are a Douyin", prompt)
        self.assertNotIn("公司", prompt)

    def test_identity_introduction_is_normalized_to_natural_word_order(self):
        advisor_context = "本轮私信对外只使用通用身份：顾问。"
        assistant_context = "本轮私信对外只使用通用身份：助理。"

        self.assertEqual(
            SessionRAGChatLogic._normalize_identity_introduction(
                "顾问这边，看到您留言问为什么不放店铺。",
                advisor_context,
            ),
            "您好，我是这边的顾问，看到您留言问为什么不放店铺。",
        )
        self.assertEqual(
            SessionRAGChatLogic._normalize_identity_introduction(
                "你好呀，我是账号的助理～看到您评论了。",
                assistant_context,
            ),
            "您好，我是这边的助理，看到您评论了。",
        )
        self.assertEqual(
            SessionRAGChatLogic._normalize_identity_introduction(
                "您好，我是健康顾问助理，看到您在评论区留言。",
                assistant_context,
            ),
            "您好，我是这边的助理，看到您在评论区留言。",
        )
        self.assertEqual(
            SessionRAGChatLogic._normalize_identity_introduction(
                "看到您留言问为什么不放店铺。",
                advisor_context,
            ),
            "看到您留言问为什么不放店铺。",
        )

    def test_private_followup_prompt_keeps_conversation_stage_metadata(self):
        prompt = SessionRAGChatLogic._build_prompt(
            session_id="sid-hooks",
            summary_context="",
            knowledge_context="",
            conversation_stage="private_followup",
        )

        self.assertIn("conversation_stage: private_followup", prompt)
        self.assertIn("一句话明显过长时请主动换行", prompt)
        self.assertIn("行与行之间空一行", prompt)
        self.assertNotIn("You are continuing a Douyin", prompt)

    def test_invalid_model_json_falls_back_to_raw_answer(self):
        parsed = SessionRAGChatLogic._parse_llm_json(
            '{"answer": "可以先发您一份资料包" "enough_info": true}'
        )

        self.assertEqual(parsed["answer"], "可以先发您一份资料包")
        self.assertTrue(parsed["enough_info"])
        self.assertIn("invalid JSON", parsed["missing_info"])


class ConsoleSmokeTest(unittest.TestCase):
    def test_console_uses_same_session_for_multiple_inputs(self):
        from aisec_agent.console import session_rag_chat as console

        calls = []

        class FakeResult:
            second_pass = False
            missing_info = ""
            memory_error = ""
            answer = "ok"

        class FakeLogic:
            def chat_once(self, **kwargs):
                calls.append(kwargs)
                return FakeResult()

        class FakeUUID:
            hex = "fixed-session"

        inputs = iter(["first", "second", "q"])
        with patch.object(console, "SessionRAGChatLogic", return_value=FakeLogic()), \
                patch.object(console.uuid, "uuid4", return_value=FakeUUID()), \
                patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            output = io.StringIO()
            with redirect_stdout(output):
                console.main([])

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["session_id"], "fixed-session")
        self.assertEqual(calls[1]["session_id"], "fixed-session")

    def test_console_minimax_provider_passes_preset_config(self):
        from aisec_agent.console import session_rag_chat as console

        calls = []

        class FakeResult:
            second_pass = False
            missing_info = ""
            memory_error = ""
            answer = "ok"

        class FakeLogic:
            def chat_once(self, **kwargs):
                calls.append(kwargs)
                return FakeResult()

        inputs = iter(["hello", "q"])
        with patch.object(console, "SessionRAGChatLogic", return_value=FakeLogic()), \
                patch.dict("os.environ", {"MINIMAX_API_KEY": "env-key"}), \
                patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            output = io.StringIO()
            with redirect_stdout(output):
                console.main(["--provider", "minimax", "--session-id", "sid-minimax"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], MINIMAX_ANTHROPIC_BASE_URL)
        self.assertEqual(calls[0]["key"], "env-key")
        self.assertEqual(calls[0]["func_name"], "minimax_anthropic_chat")
        self.assertEqual(calls[0]["model_name"], MINIMAX_DEFAULT_MODEL)

    def test_console_deepseek_provider_passes_preset_config(self):
        from aisec_agent.console import session_rag_chat as console

        calls = []

        class FakeResult:
            second_pass = False
            missing_info = ""
            memory_error = ""
            answer = "ok"

        class FakeLogic:
            def chat_once(self, **kwargs):
                calls.append(kwargs)
                return FakeResult()

        inputs = iter(["hello", "q"])
        with patch.object(console, "SessionRAGChatLogic", return_value=FakeLogic()), \
                patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-env-key"}), \
                patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            output = io.StringIO()
            with redirect_stdout(output):
                console.main(["--provider", "deepseek", "--session-id", "sid-deepseek"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], DEEPSEEK_CHAT_COMPLETIONS_URL)
        self.assertEqual(calls[0]["key"], "deepseek-env-key")
        self.assertEqual(calls[0]["func_name"], "deepseek_chat")
        self.assertEqual(calls[0]["model_name"], DEEPSEEK_DEFAULT_MODEL)


class MiniMaxChatToolsTest(unittest.TestCase):
    def test_minimax_chat_uses_openai_compatible_payload(self):
        try:
            from aisec_agent.model.llm_chat import LLMChatTools
        except ModuleNotFoundError as exc:
            if exc.name == "requests":
                self.skipTest("requests is not installed in this Python environment")
            raise

        class FakeResponse:
            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({
                                    "answer": "ok",
                                    "enough_info": True,
                                    "missing_info": "",
                                    "used_knowledge": [],
                                })
                            }
                        }
                    ]
                }

        captured = {}
        tools = LLMChatTools()

        def fake_post(url, headers, payload, stream, max_retries=3):
            captured.update({
                "url": url,
                "headers": headers,
                "payload": payload,
                "stream": stream,
                "max_retries": max_retries,
            })
            return FakeResponse()

        tools._minimax_provider._safe_post = fake_post
        result = tools.minimax_chat(
            url="https://api.minimax.io/v1/chat/completions",
            api_key="test-key",
            prompt="system prompt",
            message="hello",
            model="MiniMax-M3",
            json_format=True,
            stream=False,
        )

        self.assertIn('"answer": "ok"', result)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["payload"]["model"], "MiniMax-M3")
        self.assertFalse(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["thinking"], {"type": "disabled"})
        self.assertIn("Return JSON only.", captured["payload"]["messages"][0]["content"])

    def test_minimax_anthropic_chat_normalizes_base_url(self):
        try:
            from aisec_agent.model.llm_chat import LLMChatTools
        except ModuleNotFoundError as exc:
            if exc.name == "requests":
                self.skipTest("requests is not installed in this Python environment")
            raise

        class FakeResponse:
            def json(self):
                return {
                    "content": [
                        {"type": "text", "text": json.dumps({
                            "answer": "ok",
                            "enough_info": True,
                            "missing_info": "",
                            "used_knowledge": [],
                        })}
                    ]
                }

        captured = {}
        tools = LLMChatTools()

        def fake_post(url, headers, payload, stream, max_retries=3):
            captured.update({
                "url": url,
                "headers": headers,
                "payload": payload,
                "stream": stream,
                "max_retries": max_retries,
            })
            return FakeResponse()

        tools._minimax_provider._safe_post = fake_post
        result = tools.minimax_anthropic_chat(
            url="https://api.minimaxi.com/anthropic",
            api_key="test-key",
            prompt="system prompt",
            message="hello",
            model="MiniMax-M3",
            json_format=True,
            stream=False,
        )

        self.assertIn('"answer": "ok"', result)
        self.assertEqual(captured["url"], "https://api.minimaxi.com/anthropic/v1/messages")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["payload"]["model"], "MiniMax-M3")
        self.assertIn("system prompt", captured["payload"]["system"])
        self.assertEqual(captured["payload"]["messages"][0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
