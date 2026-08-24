import json
import unittest
import tempfile
import time
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from aisec_agent.logic.session_rag_chat import SessionRAGChatLogic
from aisec_agent.logic.llm_presets import DEEPSEEK_CHAT_COMPLETIONS_URL, DEEPSEEK_DEFAULT_MODEL
from aisec_agent.logic.project_materials import ProjectMaterialStore
from aisec_agent.web.session_rag_chat import (
    WebInputError,
    build_admin_activity_delete_response,
    build_admin_activity_save_response,
    build_admin_account_settings_save_response,
    build_admin_knowledge_delete_response,
    build_admin_knowledge_base_create_response,
    build_admin_knowledge_domain_create_response,
    build_admin_knowledge_route_update_response,
    build_admin_knowledge_save_response,
    build_admin_knowledge_upload_response,
    build_admin_prompt_restore_response,
    build_admin_open_file_location_response,
    build_admin_scene_template_generate_response,
    build_admin_scene_template_save_response,
    build_admin_state_response,
    build_business_reply_response,
    build_chat_response,
    build_config_test_response,
    build_chat_stream_events,
    build_public_private_message_response,
    build_public_prompt_preview_response,
    build_public_route_debug_response,
    build_open_url_response,
    build_http_audit_log_clear_response,
    build_http_audit_log_list_response,
    HTTP_AUDIT_REDIS_KEY,
    build_douyin_dm_task_status_response,
    build_douyin_dm_task_list_response,
    build_douyin_dm_task_submit_response,
    build_douyin_dm_task_clear_response,
    build_douyin_dm_account_list_response,
    build_douyin_dm_account_register_response,
    DM_DEBUG_ARTIFACT_DIR,
    _dm_click_editor_send_button,
    DM_REDIS_PENDING_QUEUE,
    DM_REDIS_AUTO_PENDING_QUEUE,
    DM_REDIS_FAILED_QUEUE,
    DM_REDIS_RETRY_ZSET,
    DM_REDIS_MANUAL_QUEUE,
    DM_REDIS_DEAD_LETTER_QUEUE,
    DM_REDIS_PROCESSING_ZSET,
    DM_REDIS_ACCOUNT_PREFIX,
    build_douyin_account_cookie_apply_response,
    build_douyin_private_message_demo_response,
    _douyin_account_profile,
    _DM_PLAYWRIGHT_SESSIONS,
    _dm_cleanup_closed_playwright_sessions,
    _dm_apply_page_geometry,
    _dm_browser_launch_args,
    _dm_normalize_editor_text,
    _dm_fill_message_editor,
    _dm_close_douyin_global_message_drawer,
    _dm_click_profile_private_message,
    _dm_get_playwright_context,
    _dm_mark_private_message_editor,
    _dm_message_page,
    _dm_persistent_context_alive,
    _dm_collect_message_bubble_matches,
    _dm_detect_send_failure_notice,
    _dm_failure_metadata,
    _dm_infer_failure_code,
    _dm_message_editor_text,
    _dm_should_retry_browser_closed,
    _dm_should_retry_playwright_runtime,
    _dm_should_keep_browser_open_on_failure,
    _dm_reset_all_playwright_sessions,
    _dm_stop_playwright_session,
    build_model_config_save_response,
    build_project_create_response,
    build_project_material_save_response,
    build_project_materials_response,
    build_project_route_debug_response,
    build_workspace_state_response,
    _public_model_configs,
    _dm_pending_queue_for_account,
    _dm_promote_due_retry_tasks,
    _dm_redis_hash_set,
    _dm_send_and_confirm_current_message,
    _dm_send_current_message,
    _dm_send_failure_notice_code,
    _dm_wait_message_sent,
    _douyin_detect_login_requirement,
    _douyin_detect_private_account_restriction,
    _douyin_private_message_playwright_executor,
    _format_sender_identity_context,
    process_douyin_dm_task_once,
    reconcile_douyin_dm_queue_state,
    parse_topics,
    parse_uploaded_file,
)


class FakeResult:
    def __init__(self):
        self.answer = "ok"
        self.enough_info = True
        self.second_pass = False
        self.missing_info = ""
        self.used_knowledge = []
        self.knowledge = []
        self.memory_id = "memory-1"
        self.memory_error = ""

    def to_dict(self):
        return {
            "answer": self.answer,
            "enough_info": self.enough_info,
            "second_pass": self.second_pass,
            "missing_info": self.missing_info,
            "used_knowledge": self.used_knowledge,
            "knowledge": self.knowledge,
            "memory_id": self.memory_id,
            "memory_error": self.memory_error,
        }


class FakeLogic:
    def __init__(self):
        self.calls = []

    def chat_once(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResult()


class FakeConfigTestLLM:
    def __init__(self):
        self.calls = []

    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "OK"


def fake_douyin_demo_executor(profile_url, message, browser, auto_send, options):
    return {
        "success": True,
        "opened": True,
        "prefilled": True,
        "sent": bool(auto_send),
        "resolved_browser": browser,
        "engine": "playwright",
        "steps": [
            {"name": "open_profile", "ok": True},
            {"name": "paste_message", "ok": True},
        ],
        "seen": {
            "profile_url": profile_url,
            "message": message,
            "browser": browser,
            "auto_send": auto_send,
            "options": options,
        },
    }


def fake_douyin_account_cookie_executor(raw_cookies, browser, options):
    return {
        "success": True,
        "opened": True,
        "resolved_browser": browser,
        "engine": "playwright",
        "account_cookie_loaded": True,
        "account_cookie_count": 1,
        "seen": {
            "browser": browser,
            "options": options,
            "account_cookies": raw_cookies,
        },
        "steps": [
            {"name": "connect_browser", "ok": True},
            {"name": "apply_account_cookies", "ok": True, "detail": "1 cookies"},
        ],
    }


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.lists = {}
        self.zsets = {}
        self.sets = {}

    def hset(self, key, field=None, value=None, mapping=None, **kwargs):
        self.hashes.setdefault(key, {})
        if mapping:
            self.hashes[key].update(mapping)
            return len(mapping)
        if field is not None:
            self.hashes[key][field] = value
            return 1
        return 0

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, seconds):
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpush(self, key, *values):
        self.lists.setdefault(key, [])
        for value in values:
            self.lists[key].insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        items = list(self.lists.get(key, []))
        if end == -1:
            end = len(items) - 1
        self.lists[key] = items[start:end + 1]
        return True

    def blpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if not items:
            return None
        return key, items.pop(0)

    def lrange(self, key, start, end):
        items = list(self.lists.get(key, []))
        if end == -1:
            end = len(items) - 1
        return items[start:end + 1]

    def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        original_len = len(items)
        self.lists[key] = [item for item in items if item != value]
        return original_len - len(self.lists[key])

    def llen(self, key):
        return len(self.lists.get(key, []))

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrem(self, key, member):
        self.zsets.setdefault(key, {}).pop(member, None)
        return 1

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    def zrange(self, key, start, end):
        items = list(self.zsets.get(key, {}).keys())
        if end == -1:
            end = len(items) - 1
        return items[start:end + 1]

    def zrangebyscore(self, key, min_score, max_score, start=0, num=None):
        items = [
            member
            for member, score in sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
            if float(min_score) <= float(score) <= float(max_score)
        ]
        if num is None:
            return items[start:]
        return items[start:start + num]

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def sadd(self, key, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(values)
        return len(target) - before

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            existed = key in self.hashes or key in self.lists or key in self.zsets or key in self.sets
            self.hashes.pop(key, None)
            self.lists.pop(key, None)
            self.zsets.pop(key, None)
            self.sets.pop(key, None)
            deleted += int(existed)
        return deleted

    def scan_iter(self, pattern="*", match=None, **_kwargs):
        pattern = match if match is not None else pattern
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        for key in list(self.hashes) + list(self.lists) + list(self.zsets) + list(self.sets):
            if pattern == "*" or key.startswith(prefix):
                yield key


class LegacyHsetRedis(FakeRedis):
    def hset(self, key, field=None, value=None, mapping=None, **kwargs):
        if mapping is not None:
            raise Exception("wrong number of arguments for 'hset' command")
        return super().hset(key, field, value, **kwargs)


class FakeStreamMemory:
    def __init__(self):
        self.raw_called = False
        self.store_calls = []

    def get_session_summary_context(self, user_id, session_id, max_chars):
        return "summary context"

    def get_session_raw_context(self, user_id, session_id, max_chars):
        self.raw_called = True
        return "raw context"

    def store_conversation(self, user_id, session_id, user_question, ai_response):
        self.store_calls.append((user_id, session_id, user_question, ai_response))
        return "stream-memory-1"


class FakeStreamKnowledge:
    def search_knowledge(self, question, topics, size=4):
        return [
            {
                "topic": "topic-a",
                "file_name": "faq.md",
                "similarity": 0.9,
                "content": "knowledge content",
            }
        ]


class FakeStreamLLM:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def minimax_anthropic_chat(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield chunk


class FakeStreamLogic(SessionRAGChatLogic):
    def __init__(self, first_answer, memory, llm):
        super().__init__(
            memory_manager=memory,
            knowledge_logic=FakeStreamKnowledge(),
            llm_tools=llm,
        )
        self.first_answer = first_answer

    def _resolve_model_config(self, *args, **kwargs):
        return {
            "url": "http://fake",
            "key": "fake-key",
            "func_name": "minimax_anthropic_chat",
            "model_name": "fake-model",
            "max_len_input": 1000,
        }

    def _call_structured_llm(self, prompt, user_input, model_conf):
        return self.first_answer


class FakeBusinessMemory:
    def __init__(self):
        self.store_calls = []

    def get_session_summary_context(self, user_id, session_id, max_chars):
        return "历史上下文：候选人说自己想应聘销售。"

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return ""

    def store_conversation(self, user_id, session_id, user_question, ai_response):
        self.store_calls.append((user_id, session_id, user_question, ai_response))
        return "business-memory-1"


class FakeBusinessConversationMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 你好，我想了解销售岗
Assistant: 请问怎么称呼？目前应聘的是销售岗对吗？
""".strip()

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 你好，我想了解销售岗
Assistant: 请问怎么称呼？目前应聘的是销售岗对吗？
""".strip()


class FakeBusinessOpeningOnlyMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 你好
Assistant: 请问怎么称呼？应聘哪个岗位？
""".strip()

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 你好
Assistant: 请问怎么称呼？应聘哪个岗位？
""".strip()


class FakeBusinessNameThenJobMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return self.get_session_raw_context(user_id, session_id, max_chars)

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 你好
Assistant: 请问怎么称呼？应聘哪个岗位？

[2]
User: 赵四
Assistant: 请问应聘哪个岗位？
""".strip()


class FakeBusinessFailureMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return """
[14]
User: 要这么详细吗
Assistant: 拓客渠道这块还是想多了解一下，您自拓一般通过哪些方式？比如转介绍、陌拜、线上推广这些，哪种为主？

[15]
User: 主要是线上推广
Assistant: 那您目前手上的客户资源是积累在您自己这边还是公司那边呢？月均大概拜访多少客户，能成交几单？

[16]
User: 你问题太多了吧
Assistant: 不好意思哈，咱们初面问题确实会比较多一点，也是想更全面地了解您的情况，大概还剩几个问题，麻烦您再耐心一下~

那您之前客单价大概是多少？回款周期一般是多久呢？

[17]
User: 六七百吧，大概一个月
Assistant: 那您之前客单价大概是多少？回款周期一般是多久呢？

[18]
User: 六七百吧，大概一个月
Assistant: 那您之前客单价大概多少呢？每个月的业绩任务是定的多少？平时是自己独立成交还是跟团队一起配合完成的？

[19]
User: 都是自己独立完成
Assistant: 那您之前客单价六七百左右，回款一个月左右，月均任务大概是多少？一般能完成多少？
""".strip()


class FakeBusinessCareerStabilityMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return """
[4]
Assistant: 先问下，您老家是哪里人？现在住哪个位置，靠近哪个地铁站？
[5]
User: 白云区
Assistant: 您目前婚育情况怎么样？
[6]
User: 未婚
Assistant: 之前主要在什么行业？公司主营什么业务或产品？
[7]
User: 软件服务
Assistant: 之前几份工作大概做了多久？主要是因为什么原因离职的？
[8]
User: 一两年吧，个人原因
Assistant: 现在选择新公司比较看重什么？以前的工作加班多吗？
[9]
User: 比较看重发展，加班是不多的
Assistant: 之前几份工作做了多久？因为什么原因离职的可以说说吗？
[10]
User: 说了个人原因
Assistant: 好的，那之前几份工作大概做了多久呢？
""".strip()


class FakeBusinessHometownAnsweredMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return self.get_session_raw_context(user_id, session_id, max_chars)

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return """
[1]
User: 我叫张三，应聘销售岗
Assistant: 老家哪里人？
""".strip()


class FakeBusinessFamilyResistanceMemory(FakeBusinessMemory):
    def get_session_summary_context(self, user_id, session_id, max_chars):
        return self.get_session_raw_context(user_id, session_id, max_chars)

    def get_session_raw_context(self, user_id, session_id, max_chars):
        return """
[5]
User: 白云
Assistant: 请问家庭情况怎么样？是否已结婚，有没有小孩？
""".strip()


class FakeBusinessLLM:
    def __init__(self):
        self.calls = []

    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "请问怎么称呼？目前应聘的是销售岗对吗？"


class FakeBusinessRepeatingLLM(FakeBusinessLLM):
    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "您好，我是负责本次初面的招聘助手~ 麻烦您配合回答几个问题哈\n\n之前几份工作大概每份做了多久呢？"


class FakeBusinessOutOfOrderLLM(FakeBusinessLLM):
    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "您目前住在哪里，近哪个地铁站？"


class FakeBusinessHometownFollowupLLM(FakeBusinessLLM):
    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "广州哪个区的呢？"


class FakeBusinessFamilyRepeatLLM(FakeBusinessLLM):
    def deepseek_chat(self, **kwargs):
        self.calls.append(kwargs)
        return "这个先不追问了。家庭情况怎么样？是否已结婚，如果是，有没有小孩，如果有，小孩目前多大？谁带？老公在哪？做什么行业工作的？"


class FakeBusinessLogic(SessionRAGChatLogic):
    def __init__(self, memory=None, llm=None):
        super().__init__(
            memory_manager=memory or FakeBusinessMemory(),
            knowledge_logic=None,
            llm_tools=llm or FakeBusinessLLM(),
        )


class SessionRAGWebTest(unittest.TestCase):
    def test_douyin_dm_redis_task_submit_status_and_dry_run_process(self):
        redis = FakeRedis()

        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_test_001",
                "video_info": "video about knee pain",
                "account_cookie": "sessionid=SECRET_COOKIE",
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        self.assertEqual(submit["accepted"], 1)
        self.assertEqual(submit["task_ids"], ["dm_test_001"])
        self.assertEqual(submit["tasks"][0]["account_cookie"], "[redacted]")

        status = build_douyin_dm_task_status_response("dm_test_001", redis_client=redis)
        self.assertEqual(status["task"]["status"], "pending")
        self.assertEqual(status["task"]["account_cookie"], "[redacted]")

        processed = process_douyin_dm_task_once(redis_client=redis, mode="dry_run", block_timeout=1)

        self.assertEqual(processed["task_id"], "dm_test_001")
        self.assertEqual(processed["status"], "generated")
        self.assertFalse(processed["sent"])
        self.assertIn("my mom has knee pain", processed["reply"])

        completed = build_douyin_dm_task_status_response("dm_test_001", redis_client=redis)
        self.assertEqual(completed["task"]["status"], "generated")
        self.assertEqual(completed["task"]["sent"], "false")
        self.assertEqual(completed["task"]["error"], "")

        snapshot = build_douyin_dm_task_list_response(redis_client=redis)
        self.assertEqual(snapshot["counts"]["pending"], 0)
        self.assertEqual(snapshot["counts"]["auto_pending"], 0)
        self.assertEqual(snapshot["counts"]["processing"], 0)
        self.assertEqual(snapshot["counts"]["done"], 1)
        self.assertEqual(snapshot["tasks"]["done"][0]["task_id"], "dm_test_001")

    def test_douyin_dm_worker_claim_clears_all_pending_references(self):
        redis = FakeRedis()
        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_auto_claim_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        account_key = submit["account_queues"][0]

        processed = process_douyin_dm_task_once(
            redis_client=redis,
            mode="dry_run",
            queue_name=DM_REDIS_AUTO_PENDING_QUEUE,
            block_timeout=1,
        )

        self.assertEqual(processed["queue_status"], "done")
        self.assertEqual(redis.lrange(DM_REDIS_PENDING_QUEUE, 0, -1), [])
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), [])
        self.assertEqual(redis.lrange(_dm_pending_queue_for_account(account_key), 0, -1), [])
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [])

    def test_douyin_dm_worker_runs_sync_playwright_on_stable_thread(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_threaded_playwright_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        demo_result = {
            "success": True,
            "opened": True,
            "prefilled": True,
            "sent": True,
            "steps": [],
        }

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat._run_web_playwright_call",
            return_value=demo_result,
        ) as threaded_call:
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertTrue(processed["sent"])
        self.assertEqual(threaded_call.call_count, 1)
        self.assertIs(threaded_call.call_args.args[0], _douyin_private_message_playwright_executor)

    def test_douyin_dm_reconcile_removes_terminal_processing_residue_only(self):
        redis = FakeRedis()
        terminal_id = "dm_terminal_residue_001"
        running_id = "dm_running_001"
        stale_id = "dm_stale_running_001"
        terminal_account = "account_terminal"
        for queue in (DM_REDIS_PENDING_QUEUE, DM_REDIS_AUTO_PENDING_QUEUE):
            redis.rpush(queue, terminal_id)
            redis.rpush(queue, running_id)
            redis.rpush(queue, stale_id)
        redis.rpush(_dm_pending_queue_for_account(terminal_account), terminal_id)
        _dm_redis_hash_set(redis, f"dm:task:{terminal_id}", {
            "task_id": terminal_id,
            "account_key": terminal_account,
            "status": "success",
            "queue_status": "done",
            "sent": "true",
        })
        _dm_redis_hash_set(redis, f"dm:task:{running_id}", {
            "task_id": running_id,
            "status": "running",
            "queue_status": "processing",
        })
        _dm_redis_hash_set(redis, f"dm:task:{stale_id}", {
            "task_id": stale_id,
            "status": "running",
            "queue_status": "processing",
            "sent": "false",
        })
        redis.zadd(DM_REDIS_PROCESSING_ZSET, {terminal_id: 1, running_id: 10**12, stale_id: 2})

        result = reconcile_douyin_dm_queue_state(redis_client=redis)

        self.assertEqual(result["processing_checked"], 3)
        self.assertEqual(result["processing_removed"], 2)
        self.assertEqual(result["stale_moved_to_manual"], 1)
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [running_id])
        self.assertEqual(redis.lrange(DM_REDIS_PENDING_QUEUE, 0, -1), [running_id])
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), [running_id])
        self.assertEqual(redis.lrange(_dm_pending_queue_for_account(terminal_account), 0, -1), [])
        self.assertEqual(redis.lrange(DM_REDIS_MANUAL_QUEUE, 0, -1), [stale_id])
        stale_task = redis.hgetall(f"dm:task:{stale_id}")
        self.assertEqual(stale_task["status"], "manual_required")
        self.assertEqual(stale_task["failure_code"], "stale_processing_unconfirmed")

    def test_douyin_dm_task_submit_supports_legacy_hset(self):
        redis = LegacyHsetRedis()

        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_legacy_hset_001",
                "video_info": "video about knee pain",
                "account_cookie": "sessionid=SECRET_COOKIE",
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        self.assertEqual(submit["accepted"], 1)
        status = build_douyin_dm_task_status_response("dm_legacy_hset_001", redis_client=redis)
        self.assertEqual(status["task"]["status"], "pending")

    def test_douyin_dm_task_submit_accepts_cookie_object(self):
        redis = FakeRedis()
        cookie_object = {
            "cookies": [
                {
                    "name": "sessionid",
                    "value": "SECRET_COOKIE",
                    "domain": ".douyin.com",
                    "path": "/",
                }
            ]
        }

        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_cookie_object_001",
                "video_info": "video about knee pain",
                "account_cookie": cookie_object,
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        self.assertEqual(submit["accepted"], 1)
        stored = redis.hgetall("dm:task:dm_cookie_object_001")
        self.assertEqual(stored["account_cookie"], json.dumps(cookie_object, ensure_ascii=False))
        self.assertTrue(stored["account_cookie"].startswith("{"))

    def test_douyin_dm_resubmit_clears_stale_queue_state(self):
        redis = FakeRedis()
        task_id = "dm_resubmit_001"
        redis.rpush(DM_REDIS_FAILED_QUEUE, task_id)
        redis.rpush(DM_REDIS_DEAD_LETTER_QUEUE, task_id)
        redis.zadd(DM_REDIS_PROCESSING_ZSET, {task_id: 1})

        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": task_id,
                "video_info": "video about knee pain",
                "account_cookie": "sessionid=SECRET_COOKIE",
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        self.assertEqual(submit["accepted"], 1)
        self.assertEqual(redis.lrange(DM_REDIS_FAILED_QUEUE, 0, -1), [])
        self.assertEqual(redis.lrange(DM_REDIS_DEAD_LETTER_QUEUE, 0, -1), [])
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [])
        self.assertEqual(redis.lrange(DM_REDIS_PENDING_QUEUE, 0, -1), [task_id])
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), [task_id])

    def test_douyin_dm_resubmit_clears_previous_account_queue(self):
        redis = FakeRedis()
        task_id = "dm_resubmit_account_001"
        first = build_douyin_dm_task_submit_response(
            {
                "task_id": task_id,
                "video_info": "video about knee pain",
                "account_cookie": "sessionid=OLD_COOKIE",
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        old_account_key = first["account_queues"][0]

        second = build_douyin_dm_task_submit_response(
            {
                "task_id": task_id,
                "video_info": "video about knee pain",
                "account_cookie": "sessionid=NEW_COOKIE",
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        new_account_key = second["account_queues"][0]

        self.assertNotEqual(old_account_key, new_account_key)
        self.assertEqual(redis.lrange(_dm_pending_queue_for_account(old_account_key), 0, -1), [])
        self.assertEqual(redis.lrange(_dm_pending_queue_for_account(new_account_key), 0, -1), [task_id])

    def test_douyin_dm_redis_task_requires_five_fields(self):
        with self.assertRaises(WebInputError):
            build_douyin_dm_task_submit_response(
                {
                    "video_info": "video",
                    "account_cookie": "cookie",
                    "comment_info": "comment",
                    "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                },
                redis_client=FakeRedis(),
            )

    def test_douyin_dm_submit_forces_send_and_auto_queue_for_upstream(self):
        redis = FakeRedis()
        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_send_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
                "run_mode": "generate",
                "auto_send": False,
            },
            redis_client=redis,
        )

        self.assertEqual(submit["tasks"][0]["run_mode"], "send")
        self.assertEqual(submit["tasks"][0]["headless"], "false")
        self.assertEqual(submit["tasks"][0]["use_cdp"], "true")
        self.assertEqual(submit["tasks"][0]["keep_browser_open"], "true")
        self.assertEqual(submit["tasks"][0]["persistent_context"], "true")
        self.assertEqual(submit["tasks"][0]["auto_send"], "true")
        self.assertEqual(submit["tasks"][0]["auto_process"], "true")
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), ["dm_send_001"])

    def test_douyin_private_message_demo_passes_visible_persistent_browser_options(self):
        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "reply": "hello",
                "browser_name": "edge",
                "auto_send": True,
                "headless": False,
                "user_data_dir": "D:\\tmp\\douyin-profile",
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertFalse(response["seen"]["options"]["headless"])
        self.assertTrue(response["seen"]["options"]["use_cdp"])
        self.assertTrue(response["seen"]["options"]["keep_browser_open"])
        self.assertTrue(response["seen"]["options"]["persistent_context"])
        self.assertEqual(response["seen"]["options"]["user_data_dir"], "D:\\tmp\\douyin-profile")

    def test_douyin_dm_submit_debug_mode_does_not_auto_send_or_auto_process(self):
        redis = FakeRedis()
        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_debug_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
                "run_mode": "prefill",
                "debug_mode": True,
            },
            redis_client=redis,
        )

        self.assertEqual(submit["tasks"][0]["run_mode"], "prefill")
        self.assertEqual(submit["tasks"][0]["auto_send"], "false")
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), [])

    def test_douyin_dm_process_backfills_api_defaults_for_redis_seeded_task(self):
        redis = FakeRedis()
        task_id = "dm_seeded_001"
        _dm_redis_hash_set(redis, f"dm:task:{task_id}", {
            "task_id": task_id,
            "video_info": "video",
            "account_cookie": "cookie",
            "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
            "project_name": "test project",
            "status": "pending",
            "queue_status": "queued",
        })
        redis.rpush(DM_REDIS_PENDING_QUEUE, task_id)

        def fake_demo_response(payload, executor=None):
            self.assertEqual(payload["browser"], "edge")
            self.assertFalse(payload["headless"])
            self.assertTrue(payload["use_cdp"])
            self.assertTrue(payload["keep_browser_open"])
            self.assertTrue(payload["persistent_context"])
            self.assertTrue(payload["auto_send"])
            return {
                "success": True,
                "opened": True,
                "prefilled": True,
                "sent": True,
                "resolved_browser": "edge",
                "engine": "playwright",
                "steps": [],
            }

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=fake_demo_response,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "success")
        stored = redis.hgetall(f"dm:task:{task_id}")
        self.assertEqual(stored["run_mode"], "send")
        self.assertEqual(stored["debug_mode"], "false")
        self.assertEqual(stored["headless"], "false")
        self.assertEqual(stored["use_cdp"], "true")
        self.assertEqual(stored["keep_browser_open"], "true")
        self.assertEqual(stored["persistent_context"], "true")
        self.assertEqual(stored["auto_send"], "true")
        self.assertEqual(stored["auto_process"], "true")

    def test_douyin_dm_task_uses_provided_message_without_model_generation(self):
        redis = FakeRedis()
        task_id = "dm_provided_message_001"
        provided_message = "您好，这是一条调用方指定的私信内容。"
        submit = build_douyin_dm_task_submit_response(
            {
                "task_id": task_id,
                "account_cookie": "sessionid=test",
                "message": provided_message,
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
            },
            redis_client=redis,
        )

        demo_calls = []

        def fake_demo_response(payload, executor=None):
            demo_calls.append(payload)
            return {"success": True, "opened": True, "prefilled": True, "sent": True, "steps": []}

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            side_effect=AssertionError("model generation must be skipped"),
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=fake_demo_response,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(submit["tasks"][0]["message"], provided_message)
        self.assertEqual(len(demo_calls), 1)
        self.assertEqual(demo_calls[0]["message"], provided_message)
        self.assertEqual(processed["reply"], provided_message)
        self.assertEqual(processed["message_source"], "provided")
        self.assertTrue(processed["sent"])
        stored = redis.hgetall(f"dm:task:{task_id}")
        self.assertEqual(stored["message"], provided_message)
        self.assertEqual(stored["message_source"], "provided")

    def test_douyin_dm_process_sends_followup_in_same_browser_execution(self):
        redis = FakeRedis()
        task_id = "dm_same_page_followup_001"
        followup_message = "https://v.douyin.com/group/623049833691"
        build_douyin_dm_task_submit_response(
            {
                "task_id": task_id,
                "video_info": "video",
                "account_cookie": "sessionid=test",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
                "followup_message": followup_message,
            },
            redis_client=redis,
        )
        demo_calls = []

        def fake_demo_response(payload, executor=None):
            demo_calls.append(payload)
            return {
                "success": True,
                "opened": True,
                "prefilled": True,
                "sent": True,
                "followup_private_message": True,
                "followup_private_message_status": "sent",
                "followup_private_message_detail": "followup private message sent",
            }

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=fake_demo_response,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(len(demo_calls), 1)
        self.assertEqual(demo_calls[0]["message"], "hello")
        self.assertEqual(demo_calls[0]["followup_message"], followup_message)
        self.assertTrue(processed["sent"])
        self.assertTrue(processed["followup_private_message"])
        self.assertEqual(processed["followup_private_message_status"], "sent")
        stored = redis.hgetall(f"dm:task:{task_id}")
        self.assertEqual(stored["followup_private_message"], "true")
        self.assertEqual(stored["followup_private_message_status"], "sent")

    def test_douyin_dm_success_result_does_not_report_unknown_failure(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_success_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        _dm_redis_hash_set(redis, "dm:task:dm_success_001", {
            "status": "success",
            "queue_status": "done",
            "sent": "true",
            "error": "first private message recorded",
            "first_private_message_detail": "first private message recorded",
            "result_ready": "true",
        })

        status = build_douyin_dm_task_status_response("dm_success_001", redis_client=redis)

        self.assertTrue(status["result"]["success"])
        self.assertTrue(status["result"]["sent"])
        self.assertEqual(status["result"]["failure_code"], "")
        self.assertEqual(status["result"]["failure_summary"], "")

    def test_douyin_dm_retryable_failure_moves_to_retry_wait(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_retry_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
                "max_retries": 2,
            },
            redis_client=redis,
        )

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            side_effect=TimeoutError("model timeout"),
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "retry_wait")
        self.assertEqual(processed["error_code"], "model_timeout")
        self.assertEqual(processed["failure_type"], "retryable")
        self.assertEqual(redis.zrange(DM_REDIS_RETRY_ZSET, 0, -1), ["dm_retry_001"])
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [])

        status = build_douyin_dm_task_status_response("dm_retry_001", redis_client=redis)
        self.assertEqual(status["result"]["queue_status"], "retry_wait")
        self.assertFalse(status["result"]["result_ready"])
        self.assertEqual(status["result"]["retry_count"], 1)
        self.assertEqual(status["result"]["max_retries"], 2)
        self.assertTrue(status["result"]["next_retry_at"])

    def test_douyin_dm_final_failure_moves_to_dead_letter(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_dead_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "not-a-valid-url",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "failed")
        self.assertEqual(processed["queue_status"], "dead_letter")
        self.assertEqual(processed["error_code"], "invalid_profile_url")
        self.assertTrue(processed["dead_letter"])
        self.assertEqual(redis.lrange(DM_REDIS_DEAD_LETTER_QUEUE, 0, -1), ["dm_dead_001"])
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [])

        status = build_douyin_dm_task_status_response("dm_dead_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_type"], "final")
        self.assertTrue(status["result"]["dead_letter"])

    def test_douyin_dm_manual_failure_moves_to_manual_queue(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_manual_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        demo_result = {
            "success": False,
            "opened": True,
            "prefilled": False,
            "sent": False,
            "requires_login": True,
            "steps": [
                {"name": "playwright_error", "ok": False, "detail": "抖音登录或二次验证弹层挡住了页面"}
            ],
        }
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            return_value=demo_result,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "manual_required")
        self.assertEqual(processed["queue_status"], "manual_required")
        self.assertEqual(processed["error_code"], "verification_required")
        self.assertTrue(processed["manual_required"])
        self.assertEqual(redis.lrange(DM_REDIS_MANUAL_QUEUE, 0, -1), ["dm_manual_001"])
        self.assertEqual(redis.zrange(DM_REDIS_PROCESSING_ZSET, 0, -1), [])

        status = build_douyin_dm_task_status_response("dm_manual_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_type"], "manual")
        self.assertTrue(status["result"]["manual_required"])
        self.assertTrue(status["result"]["manual_takeover"]["available"])
        self.assertEqual(status["result"]["manual_takeover"]["url"], "https://www.douyin.com/user/test-sec-uid")
        self.assertEqual(status["result"]["manual_takeover"]["action"], "open_url")

        task_list = build_douyin_dm_task_list_response(redis_client=redis, limit=20)
        manual_task = task_list["tasks"]["manual_required"][0]
        self.assertTrue(manual_task["manual_takeover"]["available"])
        self.assertEqual(manual_task["manual_takeover"]["browser"], "edge")

    def test_douyin_dm_login_modal_fails_with_need_relogin_reason(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_login_modal_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        demo_result = {
            "success": False,
            "opened": True,
            "prefilled": False,
            "sent": False,
            "requires_login": True,
            "error": "login_required: 需要重新登录",
            "steps": [
                {"name": "open_profile", "ok": True, "detail": "opened"},
                {"name": "login_required", "ok": False, "detail": "after_open_profile: 需要重新登录"},
            ],
        }
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            return_value=demo_result,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "failed")
        self.assertEqual(processed["queue_status"], "failed")
        self.assertEqual(processed["error_code"], "login_required")
        self.assertEqual(processed["failure_code"], "login_required")
        self.assertEqual(processed["failure_reason"], "需要重新登录")
        self.assertIn("需要重新登录", processed["failure_summary"])
        self.assertTrue(processed["manual_required"])
        self.assertFalse(processed["dead_letter"])
        self.assertEqual(redis.lrange(DM_REDIS_FAILED_QUEUE, 0, -1), ["dm_login_modal_001"])
        self.assertEqual(redis.lrange(DM_REDIS_MANUAL_QUEUE, 0, -1), [])

        status = build_douyin_dm_task_status_response("dm_login_modal_001", redis_client=redis)
        self.assertEqual(status["result"]["status"], "failed")
        self.assertEqual(status["result"]["queue_status"], "failed")
        self.assertEqual(status["result"]["failure_code"], "login_required")
        self.assertEqual(status["result"]["failure_reason"], "需要重新登录")
        self.assertTrue(status["result"]["manual_required"])
        self.assertTrue(status["result"]["manual_takeover"]["available"])
        account_key = status["result"]["account_key"]
        account_state = redis.hgetall(f"{DM_REDIS_ACCOUNT_PREFIX}{account_key}")
        self.assertEqual(account_state["status"], "login_invalid")
        self.assertEqual(account_state["dy_private_message_note"], "账号登录失效，需要重新登录")

    def test_douyin_detect_login_requirement_matches_free_hd_login_modal(self):
        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return {
                    "requires_login": True,
                    "requires_verification": False,
                    "body_text": "登录后免费畅享高清视频 扫码登录 验证码登录",
                }

        state = _douyin_detect_login_requirement(DummyPage())
        self.assertTrue(state["requires_login"])
        self.assertFalse(state["requires_verification"])

    def test_dm_infer_failure_code_prefers_login_required_for_relogin_text(self):
        self.assertEqual(
            _dm_infer_failure_code("", "login_required: 需要重新登录", {
                "requires_login": True,
                "steps": [{"name": "login_required", "ok": False, "detail": "after_open_profile: 需要重新登录"}],
            }),
            "login_required",
        )
        meta = _dm_failure_metadata("login_required", "manual", "login_required: 需要重新登录")
        self.assertEqual(meta["failure_reason"], "需要重新登录")

    def test_open_url_response_uses_injected_opener(self):
        calls = []

        def fake_opener(url, browser):
            calls.append((url, browser))
            return True

        response = build_open_url_response(
            {
                "url": "https://www.douyin.com/user/test-sec-uid",
                "browser": "edge",
                "new_window": True,
            },
            opener=fake_opener,
        )

        self.assertTrue(response["opened"])
        self.assertEqual(response["resolved_browser"], "edge")
        self.assertEqual(calls, [("https://www.douyin.com/user/test-sec-uid", "edge")])

    def test_http_audit_log_list_and_clear(self):
        redis = FakeRedis()
        redis.lpush(
            HTTP_AUDIT_REDIS_KEY,
            '{"request_id":"1","timestamp":"2026-07-15 10:00:00","method":"POST","path":"/api/open-url","status":200,"remote_ip":"127.0.0.1","body":{"keys":["url"]},"response":{"opened":true}}',
        )

        listed = build_http_audit_log_list_response(redis_client=redis, limit=10, offset=0)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["entries"][0]["path"], "/api/open-url")
        self.assertEqual(listed["entries"][0]["response"]["opened"], True)

        cleared = build_http_audit_log_clear_response(redis_client=redis)
        self.assertTrue(cleared["cleared"])
        self.assertEqual(redis.lrange(HTTP_AUDIT_REDIS_KEY, 0, -1), [])

    def test_douyin_dm_failure_reason_exposes_missing_api_key(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_api_key_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        missing_key_error = RuntimeError(
            "missing config: api_key (minimax has no saved key; save model config once via the page or /api/v1/model/config/save)"
        )
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            side_effect=missing_key_error,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["failure_code"], "missing_model_api_key")
        self.assertIn("api_key", processed["failure_summary"])

        status = build_douyin_dm_task_status_response("dm_api_key_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_code"], "missing_model_api_key")
        self.assertEqual(status["result"]["failure_stage"], "model_config")
        self.assertIn("MiniMax", status["result"]["failure_hint"])
        self.assertIn("/api/v1/model/config/save", status["result"]["failure_summary"])

    def test_douyin_dm_task_clear_response_removes_dm_keys(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_clear_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        response = build_douyin_dm_task_clear_response(redis_client=redis)

        self.assertTrue(response["cleared"])
        self.assertGreaterEqual(response["deleted_keys"], 1)
        self.assertEqual(redis.hashes, {})
        self.assertEqual(redis.lists, {})
        self.assertEqual(redis.zsets, {})
        self.assertEqual(redis.sets, {})

    def test_douyin_dm_task_clear_response_handles_byte_keys(self):
        class ByteScanRedis(FakeRedis):
            def scan_iter(self, pattern="*", match=None, **kwargs):
                for key in super().scan_iter(pattern=pattern, match=match, **kwargs):
                    yield key.encode("utf-8") if isinstance(key, str) else key

        redis = ByteScanRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_clear_bytes_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        response = build_douyin_dm_task_clear_response(redis_client=redis)

        self.assertTrue(response["cleared"])
        self.assertGreaterEqual(response["deleted_keys"], 1)
        self.assertEqual(redis.hashes, {})
        self.assertEqual(redis.lists, {})
        self.assertEqual(redis.zsets, {})
        self.assertEqual(redis.sets, {})

    def test_douyin_dm_failure_reason_exposes_message_send_unconfirmed(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_send_fail_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        send_error = RuntimeError("message send was not confirmed")
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=send_error,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["failure_code"], "message_send_unconfirmed")
        self.assertIn("message send was not confirmed", processed["failure_summary"])

        status = build_douyin_dm_task_status_response("dm_send_fail_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_code"], "message_send_unconfirmed")
        self.assertEqual(status["result"]["failure_stage"], "send_confirm")
        self.assertIn("发送", status["result"]["failure_reason"])
        self.assertIn("message send was not confirmed", status["result"]["failure_step_detail"])

    def test_douyin_dm_recipient_privacy_failure_is_final_without_retry_or_manual_queue(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_recipient_privacy_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        failing_demo_result = {
            "success": False,
            "opened": True,
            "prefilled": True,
            "sent": False,
            "resolved_browser": "edge",
            "engine": "playwright",
            "failure_screenshot_path": str(DM_DEBUG_ARTIFACT_DIR / "dm_recipient_privacy_001.png"),
            "steps": [
                {"name": "open_profile", "ok": True, "detail": "opened"},
                {"name": "playwright_error", "ok": False, "detail": "recipient_privacy_restriction: 对方设置仅允许互关的人发消息"},
            ],
            "error": "recipient_privacy_restriction: 对方设置仅允许互关的人发消息",
        }
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            return_value=failing_demo_result,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "success")
        self.assertEqual(processed["queue_status"], "done")
        self.assertTrue(processed["success"])
        self.assertFalse(processed["sent"])
        self.assertEqual(processed["send_status"], "recipient_privacy_restriction")
        self.assertTrue(processed["result_ready"])
        self.assertEqual(processed["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(processed["failure_stage"], "send_confirm")
        self.assertEqual(processed["failure_category"], "recipient")
        self.assertFalse(processed["manual_required"])
        self.assertFalse(processed["dead_letter"])
        self.assertEqual(processed["next_retry_at"], "")
        self.assertEqual(redis.zcard(DM_REDIS_RETRY_ZSET), 0)
        self.assertNotIn("dm_recipient_privacy_001", redis.lists.get(DM_REDIS_MANUAL_QUEUE, []))
        self.assertNotIn("dm_recipient_privacy_001", redis.lists.get(DM_REDIS_DEAD_LETTER_QUEUE, []))
        self.assertIn("dm_recipient_privacy_001", redis.lists.get("dm:queue:done", []))

        status = build_douyin_dm_task_status_response("dm_recipient_privacy_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(status["result"]["failure_stage"], "send_confirm")
        self.assertEqual(status["result"]["failure_category"], "recipient")
        self.assertNotIn("D:\\", status["result"]["failure_screenshot_url"])

    def test_douyin_dm_stranger_daily_limit_waits_until_next_day(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_stranger_daily_limit_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=RuntimeError("给陌生人发送消息已达到今日上限，请明天再发送陌生人消息"),
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "retry_wait")
        self.assertEqual(processed["failure_code"], "stranger_daily_limit")
        self.assertEqual(processed["failure_category"], "account_limit")
        self.assertTrue(processed["next_retry_at"])
        self.assertEqual(redis.zcard(DM_REDIS_RETRY_ZSET), 1)
        task_status = build_douyin_dm_task_status_response("dm_stranger_daily_limit_001", redis_client=redis)
        account_key = task_status["result"]["account_key"]
        account_state = redis.hgetall(f"{DM_REDIS_ACCOUNT_PREFIX}{account_key}")
        self.assertEqual(account_state["status"], "daily_limited")
        self.assertEqual(account_state["dy_private_message_note"], "已达今日私信上限")
        self.assertTrue(account_state["next_available_at"])

    def test_douyin_dm_rate_limit_switches_to_available_account(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response({
            "task_id": "dm_switch_001", "video_info": "video", "account_id": "one",
            "account_cookie": "cookie-one", "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid", "project_name": "test",
        }, redis_client=redis)
        build_douyin_dm_account_register_response({
            "account_id": "two", "account_name": "two", "account_cookie": "cookie-two",
        }, redis_client=redis)
        with patch("aisec_agent.web.session_rag_chat.build_public_private_message_response", return_value={"reply": "hello"}), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=RuntimeError("私信功能使用频繁，请稍后再试"),
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)
        self.assertEqual(processed["status"], "retry_wait")
        self.assertEqual(processed["send_status"], "account_switched")
        self.assertTrue(processed["account_switched"])
        self.assertEqual(processed["account_id"], "two")
        task = redis.hgetall("dm:task:dm_switch_001")
        self.assertEqual(task["account_id"], "two")
        self.assertEqual(redis.lrange("dm:queue:auto_pending", 0, -1), ["dm_switch_001"])

    def test_douyin_dm_login_invalid_does_not_switch_account(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response({
            "task_id": "dm_login_invalid_001", "video_info": "video", "account_id": "login-one",
            "account_cookie": "cookie-login-one", "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid", "project_name": "test",
        }, redis_client=redis)
        task = redis.hgetall("dm:task:dm_login_invalid_001")
        redis.hset(f"{DM_REDIS_ACCOUNT_PREFIX}{task['account_key']}", mapping={
            "status": "login_invalid", "dy_private_message_note": "账号登录失效，需要重新登录",
        })
        build_douyin_dm_account_register_response({
            "account_id": "login-two", "account_cookie": "cookie-login-two",
        }, redis_client=redis)
        processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)
        self.assertEqual(processed["status"], "failed")
        self.assertEqual(processed["failure_code"], "login_required")
        self.assertEqual(processed["failure_reason"], "需要重新登录")
        self.assertFalse(processed["account_switched"])
        self.assertEqual(redis.hgetall("dm:task:dm_login_invalid_001")["account_id"], "login-one")

    def test_douyin_dm_hourly_limit_returns_structured_failure(self):
        redis = FakeRedis()
        build_douyin_dm_account_register_response({
            "account_id": "hourly", "account_name": "hourly", "account_cookie": "cookie-hourly", "hourly_limit": 1,
        }, redis_client=redis)
        account_key = next(iter(redis.sets["dm:accounts"]))
        redis.hset(f"{DM_REDIS_ACCOUNT_PREFIX}{account_key}", mapping={
            "hourly_window_start": str(time.time()), "hourly_sent_count": "1", "hourly_limit": "1",
        })
        build_douyin_dm_task_submit_response({
            "task_id": "dm_hourly_001", "video_info": "video", "account_id": "hourly",
            "account_cookie": "cookie-hourly", "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid", "project_name": "test",
        }, redis_client=redis)
        processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)
        self.assertEqual(processed["status"], "failed")
        self.assertEqual(processed["failure_code"], "hourly_limit_reached")
        self.assertEqual(processed["failure_reason"], "账号已达到每小时私信上限")
        self.assertEqual(processed["send_status"], "failed")

    def test_douyin_dm_platform_busy_marks_cooldown_and_switches_account(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response({
            "task_id": "dm_busy_001", "video_info": "video", "account_id": "busy-one",
            "account_cookie": "cookie-busy-one", "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid", "project_name": "test",
        }, redis_client=redis)
        build_douyin_dm_account_register_response({
            "account_id": "busy-two", "account_cookie": "cookie-busy-two",
        }, redis_client=redis)
        with patch("aisec_agent.web.session_rag_chat.build_public_private_message_response", return_value={"reply": "hello"}), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=RuntimeError("系统繁忙，请稍后再试"),
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)
        self.assertEqual(processed["failure_code"], "platform_busy")
        self.assertTrue(processed["account_switched"])
        old_key = processed["switched_from_account_key"]
        state = redis.hgetall(f"{DM_REDIS_ACCOUNT_PREFIX}{old_key}")
        self.assertEqual(state["status"], "cooldown")
        self.assertEqual(state["dy_private_message_note"], "抖音私信系统繁忙，账号暂时冷却")
        self.assertTrue(state["next_available_at"])

    def test_douyin_dm_promotes_due_retry_task(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response({
            "task_id": "dm_due_001", "video_info": "video", "account_id": "due",
            "account_cookie": "cookie-due", "comment_info": "comment",
            "target_profile_url": "https://www.douyin.com/user/test-sec-uid", "project_name": "test",
        }, redis_client=redis)
        task = redis.hgetall("dm:task:dm_due_001")
        redis.lists[DM_REDIS_PENDING_QUEUE] = []
        redis.lists[DM_REDIS_AUTO_PENDING_QUEUE] = []
        redis.lists[_dm_pending_queue_for_account(task["account_key"])] = []
        redis.hset("dm:task:dm_due_001", mapping={"status": "retry_wait", "queue_status": "retry_wait", "retry_queue": DM_REDIS_AUTO_PENDING_QUEUE})
        redis.zadd(DM_REDIS_RETRY_ZSET, {"dm_due_001": time.time() - 1})
        self.assertEqual(_dm_promote_due_retry_tasks(redis), 1)
        self.assertEqual(redis.lrange(DM_REDIS_AUTO_PENDING_QUEUE, 0, -1), ["dm_due_001"])
        self.assertEqual(redis.hgetall("dm:task:dm_due_001")["queue_status"], "queued")

    def test_douyin_dm_account_state_api_redacts_cookie(self):
        redis = FakeRedis()
        build_douyin_dm_account_register_response({
            "account_id": "api-account", "account_cookie": "secret-cookie", "hourly_limit": 12,
        }, redis_client=redis)
        listed = build_douyin_dm_account_list_response(redis_client=redis)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["accounts"][0]["account_id"], "api-account")
        self.assertEqual(listed["accounts"][0]["hourly_limit"], 12)
        self.assertEqual(listed["accounts"][0]["account_cookie"], "[redacted]")

    def test_douyin_dm_send_process_accepts_cookie_object(self):
        redis = FakeRedis()
        cookie_object = {
            "cookies": [
                {
                    "name": "sessionid",
                    "value": "SECRET_COOKIE",
                    "domain": ".douyin.com",
                    "path": "/",
                }
            ]
        }
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_cookie_send_001",
                "video_info": "video about knee pain",
                "account_cookie": cookie_object,
                "comment_info": "my mom has knee pain",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        def fake_demo_response(payload, executor=None):
            self.assertIsInstance(payload["account_cookies"], str)
            self.assertTrue(payload["account_cookies"].startswith("{"))
            return {
                "success": True,
                "opened": True,
                "prefilled": True,
                "sent": True,
                "resolved_browser": "edge",
                "engine": "playwright",
                "steps": [],
            }

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            side_effect=fake_demo_response,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["task_id"], "dm_cookie_send_001")
        self.assertEqual(processed["status"], "success")
        self.assertTrue(processed["sent"])

    def test_douyin_dm_send_current_message_prefers_dom_button_click(self):
        class DummyPage:
            pass

        page = DummyPage()

        with patch(
            "aisec_agent.web.session_rag_chat._dm_visible_message_editor",
            return_value=object(),
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_click_editor_send_button",
            return_value="1200,740:send",
        ) as dom_click, patch(
            "aisec_agent.web.session_rag_chat._dm_click_first",
        ) as generic_click, patch(
            "aisec_agent.web.session_rag_chat._dm_editor_send_click_point",
        ) as coordinate_click:
            result = _dm_send_current_message(page, timeout_ms=1)

        self.assertEqual(result["name"], "click_send")
        self.assertIn("editor send button", result["detail"])
        dom_click.assert_called_once()
        generic_click.assert_not_called()
        coordinate_click.assert_not_called()

    def test_douyin_dm_fill_message_uses_only_marked_conversation_editor(self):
        class DummyKeyboard:
            def press(self, *_args, **_kwargs):
                raise AssertionError("keyboard fallback should not be used")

            def insert_text(self, *_args, **_kwargs):
                raise AssertionError("keyboard fallback should not be used")

        class DummyTarget:
            def __init__(self):
                self.value = ""

            def wait_for(self, **_kwargs):
                return None

            def click(self, **_kwargs):
                return None

            def fill(self, message, **_kwargs):
                self.value = message

            def evaluate(self, *_args, **_kwargs):
                return self.value

        class DummyLocator:
            def __init__(self, target):
                self.first = target

        class DummyPage:
            def __init__(self):
                self.keyboard = DummyKeyboard()
                self.target = DummyTarget()
                self.locator_calls = []

            def evaluate(self, *_args, **_kwargs):
                return {"score": 180, "x": 980, "y": 720, "width": 360, "height": 80}

            def locator(self, selector):
                self.locator_calls.append(selector)
                return DummyLocator(self.target)

            def get_by_role(self, *_args, **_kwargs):
                raise AssertionError("global textbox lookup must not be used")

        page = DummyPage()
        result = _dm_fill_message_editor(page, "hello", timeout_ms=1)

        self.assertTrue(result["ok"])
        self.assertEqual(page.target.value, "hello")
        self.assertEqual(page.locator_calls, ['[data-aisec-dm-editor="true"]'])

    def test_douyin_dm_fill_message_rejects_page_without_trusted_editor(self):
        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return {}

            def locator(self, *_args, **_kwargs):
                raise AssertionError("untrusted page must not be queried for a fallback textbox")

        self.assertEqual(_dm_mark_private_message_editor(DummyPage()), {})
        with self.assertRaisesRegex(RuntimeError, "no trusted editor"):
            _dm_fill_message_editor(DummyPage(), "hello", timeout_ms=1)

    def test_douyin_dm_profile_private_message_uses_scoped_action(self):
        class DummyKeyboard:
            def __init__(self):
                self.presses = []

            def press(self, key):
                self.presses.append(key)

        class DummyTarget:
            def wait_for(self, **_kwargs):
                return None

            def click(self, **_kwargs):
                return None

        class DummyLocator:
            def __init__(self, target):
                self.first = target

        class DummyPage:
            def __init__(self):
                self.keyboard = DummyKeyboard()
                self.target = DummyTarget()
                self.locator_calls = []

            def evaluate(self, _script):
                return {"score": 160, "label": "私信", "x": 900, "y": 120}

            def locator(self, selector):
                self.locator_calls.append(selector)
                return DummyLocator(self.target)

        page = DummyPage()
        result = _dm_click_profile_private_message(page, timeout_ms=1)

        self.assertTrue(result["ok"])
        self.assertEqual(page.keyboard.presses, ["Escape", "Escape"])
        self.assertEqual(page.locator_calls, ['[data-aisec-dm-profile-action="true"]'])

    def test_douyin_dm_closes_stale_global_message_drawer(self):
        class DummyPage:
            def __init__(self):
                self.waits = []

            def evaluate(self, _script):
                return {"closed": True, "label": "关闭"}

            def wait_for_timeout(self, timeout_ms):
                self.waits.append(timeout_ms)

        page = DummyPage()
        result = _dm_close_douyin_global_message_drawer(page, timeout_ms=2500)

        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"], "closed 关闭")
        self.assertEqual(page.waits, [500])

    def test_douyin_dm_message_editor_treats_zero_width_residue_as_empty(self):
        class DummyPage:
            def evaluate(self, _script):
                return "\u200b\u200c\u200d\u2060\ufeff"

        self.assertEqual(_dm_message_editor_text(DummyPage()), "")

    def test_douyin_dm_send_current_message_does_not_blind_click_coordinates(self):
        class DummyPage:
            def get_by_role(self, *_args, **_kwargs):
                return object()

            def locator(self, *_args, **_kwargs):
                return object()

        page = DummyPage()
        with patch(
            "aisec_agent.web.session_rag_chat._dm_visible_message_editor",
            return_value=object(),
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_click_editor_send_button",
            return_value=None,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_click_first",
            side_effect=RuntimeError("explicit send button not found"),
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_click_editor_send_icon",
            return_value=None,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_editor_send_click_point",
        ) as coordinate_click:
            with self.assertRaisesRegex(RuntimeError, "trusted editor send control not found"):
                _dm_send_current_message(page, timeout_ms=1)

        coordinate_click.assert_not_called()

    def test_douyin_dm_wait_message_sent_requires_new_bubble(self):
        class DummyPage:
            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        page = DummyPage()
        old_match = {
            "signature": "old|100|200|300|120",
            "text": "old",
            "x": 100,
            "y": 200,
            "width": 300,
            "height": 120,
            "score": 10,
        }
        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[old_match],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="draft still here",
        ), patch(
            "aisec_agent.web.session_rag_chat.time.time",
            side_effect=[0.0, 0.2, 1.2],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _dm_wait_message_sent(page, "hello", timeout_ms=500, baseline=[old_match["signature"]])

        self.assertIn("message send was not confirmed", str(ctx.exception))

    def test_douyin_dm_wait_message_sent_accepts_new_bubble(self):
        class DummyPage:
            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        page = DummyPage()
        old_match = {
            "signature": "old|100|200|300|120",
            "text": "old",
            "x": 100,
            "y": 200,
            "width": 300,
            "height": 120,
            "score": 10,
        }
        new_match = {
            "signature": "new|500|220|320|128",
            "text": "new outgoing message",
            "x": 500,
            "y": 220,
            "width": 320,
            "height": 128,
            "score": 80,
        }
        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[old_match, new_match],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="",
        ):
            result = _dm_wait_message_sent(page, "new outgoing message", timeout_ms=500, baseline=[old_match["signature"]])

        self.assertEqual(result["name"], "send_message")
        self.assertIn("outgoing bubble confirmed", result["detail"])

    def test_douyin_dm_wait_message_sent_rejects_new_bubble_until_editor_clears(self):
        class DummyPage:
            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        page = DummyPage()
        new_match = {
            "signature": "new|500|220|320|128",
            "text": "new outgoing message",
            "x": 500,
            "y": 220,
            "width": 320,
            "height": 128,
            "score": 80,
        }
        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[new_match],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="draft still here",
        ), patch(
            "aisec_agent.web.session_rag_chat.time.time",
            side_effect=[0.0, 0.2, 1.2],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _dm_wait_message_sent(page, "new outgoing message", timeout_ms=500, baseline=[])

        self.assertIn("message send was not confirmed", str(ctx.exception))

    def test_douyin_dm_failure_notice_codes_cover_inline_platform_messages(self):
        self.assertEqual(
            _dm_send_failure_notice_code("给陌生人发送消息已达到今日上限，请明天再发送陌生人消息"),
            "stranger_daily_limit",
        )
        self.assertEqual(
            _dm_send_failure_notice_code(
                "对方设置了仅和他互关的人可发消息，暂无法给对方发送消息"
            ),
            "recipient_privacy_restriction",
        )
        self.assertEqual(_dm_send_failure_notice_code("发送失败"), "message_send_rejected")
        self.assertEqual(_dm_send_failure_notice_code("重新发送"), "message_send_rejected")
        self.assertEqual(_dm_send_failure_notice_code("系统繁忙，重新登录后可以正常使用私信功能"), "login_required")
        self.assertEqual(_dm_send_failure_notice_code("系统繁忙，请稍后再试"), "platform_busy")
        self.assertEqual(_dm_send_failure_notice_code("私信功能使用频繁，请稍后再试"), "rate_limited")
        self.assertEqual(_dm_send_failure_notice_code("请完成下列验证后继续"), "verification_required")

    def test_douyin_dm_detect_prefers_specific_inline_failure_over_resend_label(self):
        notices = [
            {
                "signature": "发送失败|900|560|80|24",
                "text": "发送失败",
                "x": 900,
                "y": 560,
                "width": 80,
                "height": 24,
                "score": 150,
            },
            {
                "signature": "给陌生人发送消息已达到今日上限，请明天再发送陌生人消息|880|590|360|40",
                "text": "给陌生人发送消息已达到今日上限，请明天再发送陌生人消息",
                "x": 880,
                "y": 590,
                "width": 360,
                "height": 40,
                "score": 150,
            },
        ]

        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return notices

        detected = _dm_detect_send_failure_notice(DummyPage())
        self.assertEqual(detected["failure_code"], "stranger_daily_limit")
        self.assertIn("今日上限", detected["message"])

    def test_douyin_dm_wait_message_sent_prefers_inline_failure_over_new_bubble(self):
        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return []

            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        page = DummyPage()
        new_match = {
            "signature": "new|900|500|320|128",
            "text": "new outgoing message",
            "x": 900,
            "y": 500,
            "width": 320,
            "height": 128,
            "score": 80,
        }
        failure_notice = {
            "failure_code": "stranger_daily_limit",
            "message": "给陌生人发送消息已达到今日上限，请明天再发送陌生人消息",
        }
        with patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="",
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_detect_send_failure_notice",
            return_value=failure_notice,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_collect_send_failure_notice_candidates",
            return_value=[],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[new_match],
        ):
            with self.assertRaisesRegex(RuntimeError, "stranger_daily_limit"):
                _dm_wait_message_sent(page, "new outgoing message", timeout_ms=500, notice_baseline=[])

    def test_douyin_dm_detects_recipient_privacy_notice(self):
        notice = {
            "signature": "对方设置仅允许互关的人发消息|500|120|300|48",
            "text": "对方设置仅允许互关的人发消息",
            "x": 500,
            "y": 120,
            "width": 300,
            "height": 48,
            "score": 80,
        }

        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return [notice]

        page = DummyPage()
        detected = _dm_detect_send_failure_notice(page)

        self.assertEqual(detected["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(detected["message"], notice["text"])
        self.assertIsNone(
            _dm_detect_send_failure_notice(
                page,
                baseline=[notice["signature"]],
                previous=[notice["signature"]],
            )
        )
        self.assertEqual(
            _dm_detect_send_failure_notice(
                page,
                baseline=[notice["signature"]],
                previous=[],
            )["failure_code"],
            "recipient_privacy_restriction",
        )

    def test_douyin_dm_wait_message_sent_raises_recipient_privacy_notice(self):
        notice = {
            "signature": "notice|500|120|300|48",
            "text": "由于对方的隐私设置，你无法向对方发送消息",
            "x": 500,
            "y": 120,
            "width": 300,
            "height": 48,
            "score": 80,
        }

        class DummyPage:
            def evaluate(self, *_args, **_kwargs):
                return [notice]

            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        page = DummyPage()
        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="",
        ):
            with self.assertRaisesRegex(RuntimeError, "recipient_privacy_restriction"):
                _dm_wait_message_sent(page, "hello", timeout_ms=500, notice_baseline=[])

    def test_douyin_dm_recipient_privacy_failure_metadata_and_inference(self):
        self.assertEqual(
            _dm_infer_failure_code("", "当前用户无法给对方发送消息"),
            "recipient_privacy_restriction",
        )
        metadata = _dm_failure_metadata(
            "recipient_privacy_restriction",
            "final",
            "recipient_privacy_restriction: 当前用户无法给对方发送消息",
        )
        self.assertEqual(metadata["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(metadata["failure_stage"], "send_confirm")
        self.assertEqual(metadata["failure_category"], "recipient")
        self.assertTrue(metadata["failure_summary"].startswith("对方隐私设置不允许接收私信"))

    def test_douyin_dm_private_account_restriction_not_automation_changed(self):
        private_copy = "私密账号 发起关注请求，通过后即可查看该账号内容"
        self.assertEqual(
            _dm_send_failure_notice_code(private_copy),
            "recipient_privacy_restriction",
        )
        self.assertEqual(
            _dm_infer_failure_code(
                "",
                "private chat editor not found: no trusted editor in the conversation panel",
                {
                    "steps": [
                        {"name": "open_profile", "ok": True, "detail": "opened"},
                        {
                            "name": "playwright_error",
                            "ok": False,
                            "detail": f"private chat editor not found; page shows {private_copy}",
                        },
                    ],
                    "error": f"private chat editor not found; {private_copy}",
                },
            ),
            "recipient_privacy_restriction",
        )
        self.assertEqual(
            _dm_infer_failure_code(
                "",
                "private chat editor not found: no trusted editor in the conversation panel",
            ),
            "automation_changed",
        )
        metadata = _dm_failure_metadata(
            "automation_changed",
            "manual",
            f"private chat editor not found: no trusted editor; {private_copy}",
            detail={
                "steps": [
                    {"name": "recipient_privacy_restriction", "ok": False, "detail": private_copy},
                ],
                "error": f"recipient_privacy_restriction: 对方为私密账号，无法发送私信；{private_copy}",
            },
        )
        self.assertEqual(metadata["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(metadata["failure_category"], "recipient")
        self.assertEqual(metadata["failure_reason"], "对方为私密账号，无法发送私信")
        self.assertEqual(metadata["failure_stage"], "open_conversation")
        self.assertIn("无需重试", metadata["failure_summary"])

        state = _douyin_detect_private_account_restriction(
            type(
                "DummyPage",
                (),
                {
                    "evaluate": lambda self, *_args, **_kwargs: {
                        "is_private_account": True,
                        "body_text": private_copy,
                    }
                },
            )()
        )
        self.assertTrue(state["is_private_account"])

        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_private_account_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/private-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )
        failing_demo_result = {
            "success": False,
            "opened": True,
            "prefilled": False,
            "sent": False,
            "resolved_browser": "edge",
            "engine": "playwright",
            "steps": [
                {"name": "open_profile", "ok": True, "detail": "opened"},
                {
                    "name": "recipient_privacy_restriction",
                    "ok": False,
                    "detail": "after_open_profile: 对方为私密账号，无法发送私信",
                },
            ],
            "error": "recipient_privacy_restriction: after_open_profile: 对方为私密账号，无法发送私信",
        }
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            return_value=failing_demo_result,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["status"], "success")
        self.assertFalse(processed["sent"])
        self.assertEqual(processed["send_status"], "recipient_privacy_restriction")
        self.assertEqual(processed["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(processed["failure_category"], "recipient")
        self.assertEqual(processed["failure_reason"], "对方为私密账号，无法发送私信")
        self.assertFalse(processed["manual_required"])
        self.assertFalse(processed["dead_letter"])
        self.assertEqual(processed["next_retry_at"], "")
        self.assertEqual(redis.zcard(DM_REDIS_RETRY_ZSET), 0)
        account_hash = redis.hashes.get(f"{DM_REDIS_ACCOUNT_PREFIX}cookie_cookie", {})
        self.assertNotEqual(str(account_hash.get("status") or ""), "login_invalid")
        self.assertNotEqual(str(account_hash.get("status") or ""), "cooldown")

    def test_douyin_dm_send_and_confirm_uses_enter_before_click_fallback(self):
        class DummyKeyboard:
            def __init__(self):
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

        class DummyPage:
            def __init__(self):
                self.keyboard = DummyKeyboard()

        page = DummyPage()
        wait_calls = []

        def fake_wait(_page, _message, timeout_ms=8000, baseline=None, notice_baseline=None):
            wait_calls.append(list(baseline or []))
            if len(wait_calls) == 1:
                raise RuntimeError("message send was not confirmed; editor still contains: hello")
            return {"name": "send_message", "ok": True, "detail": "confirmed"}

        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_wait_message_sent",
            side_effect=fake_wait,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="hello",
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_send_current_message",
            return_value={"name": "click_send", "ok": True, "detail": "clicked"},
        ) as click_send:
            steps = _dm_send_and_confirm_current_message(page, "hello", timeout_ms=500)

        self.assertEqual(page.keyboard.pressed, ["Enter"])
        click_send.assert_called_once()
        self.assertEqual([step["name"] for step in steps], [
            "press_enter_send",
            "press_enter_unconfirmed",
            "click_send",
            "send_message",
        ])

    def test_douyin_dm_send_and_confirm_retries_enter_for_link_preview(self):
        class DummyKeyboard:
            def __init__(self):
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

        class DummyPage:
            def __init__(self):
                self.keyboard = DummyKeyboard()

        page = DummyPage()
        wait_calls = []

        def fake_wait(_page, _message, timeout_ms=8000, baseline=None, notice_baseline=None):
            wait_calls.append(list(baseline or []))
            if len(wait_calls) == 1:
                raise RuntimeError("link preview opened; send was not confirmed")
            return {"name": "send_message", "ok": True, "detail": "confirmed"}

        with patch(
            "aisec_agent.web.session_rag_chat._dm_collect_message_bubble_matches",
            return_value=[],
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_wait_message_sent",
            side_effect=fake_wait,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_message_editor_text",
            return_value="",
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_send_current_message",
        ) as click_send:
            steps = _dm_send_and_confirm_current_message(
                page,
                "https://v.douyin.com/group/623049833691",
                timeout_ms=500,
                retry_enter_before_click=True,
            )

        self.assertEqual(page.keyboard.pressed, ["Enter", "Enter"])
        click_send.assert_not_called()
        self.assertEqual([step["name"] for step in steps], [
            "press_enter_send",
            "press_enter_unconfirmed",
            "press_enter_retry_send",
            "send_message",
        ])

    def test_douyin_dm_failure_reason_keeps_demo_step_trace(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_send_fail_trace_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        failing_demo_result = {
            "success": False,
            "opened": True,
            "prefilled": True,
            "sent": False,
            "resolved_browser": "edge",
            "engine": "playwright",
            "failure_screenshot_path": str(DM_DEBUG_ARTIFACT_DIR / "dm_send_fail_trace_001.png"),
            "steps": [
                {"name": "open_profile", "ok": True, "detail": "opened"},
                {"name": "send_message", "ok": False, "detail": "message send was not confirmed"},
            ],
            "error": "message send was not confirmed",
        }
        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            return_value={"reply": "hello"},
        ), patch(
            "aisec_agent.web.session_rag_chat.build_douyin_private_message_demo_response",
            return_value=failing_demo_result,
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["failure_code"], "message_send_unconfirmed")
        self.assertEqual(processed["failure_step"], "send_message")
        self.assertIn("send_message", processed["failure_trace"])
        self.assertEqual(processed["failure_screenshot_name"], "dm_send_fail_trace_001.png")
        self.assertEqual(
            processed["failure_screenshot_url"],
            "/api/v1/douyin/private-message/artifacts/dm_send_fail_trace_001.png",
        )

        status = build_douyin_dm_task_status_response("dm_send_fail_trace_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_code"], "message_send_unconfirmed")
        self.assertEqual(status["result"]["failure_step"], "send_message")
        self.assertIn("send_message", status["result"]["failure_trace"])
        self.assertEqual(
            status["result"]["failure_screenshot_path"],
            str(DM_DEBUG_ARTIFACT_DIR / "dm_send_fail_trace_001.png"),
        )

    def test_douyin_dm_failure_reason_exposes_playwright_missing(self):
        redis = FakeRedis()
        build_douyin_dm_task_submit_response(
            {
                "task_id": "dm_playwright_001",
                "video_info": "video",
                "account_cookie": "cookie",
                "comment_info": "comment",
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "project_name": "test project",
            },
            redis_client=redis,
        )

        with patch(
            "aisec_agent.web.session_rag_chat.build_public_private_message_response",
            side_effect=ModuleNotFoundError("No module named 'playwright'"),
        ):
            processed = process_douyin_dm_task_once(redis_client=redis, mode="send", block_timeout=1)

        self.assertEqual(processed["failure_code"], "playwright_missing")
        self.assertEqual(processed["failure_stage"], "browser_runtime")

        status = build_douyin_dm_task_status_response("dm_playwright_001", redis_client=redis)
        self.assertEqual(status["result"]["failure_code"], "playwright_missing")
        self.assertIn("playwright", status["result"]["failure_hint"].lower())

    def test_douyin_private_message_demo_exposes_failure_metadata(self):
        def failing_executor(profile_url, message, browser, auto_send, options):
            return {
                "success": False,
                "opened": True,
                "prefilled": True,
                "sent": False,
                "resolved_browser": browser,
                "engine": "playwright",
                "failure_screenshot_path": str(DM_DEBUG_ARTIFACT_DIR / "dm_send_fail_001.png"),
                "steps": [
                    {"name": "open_profile", "ok": True},
                    {"name": "send_message", "ok": False, "detail": "message send was not confirmed"},
                ],
                "error": "message send was not confirmed",
            }

        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid?from_tab_name=main",
                "reply": "hello",
                "browser_name": "edge",
                "auto_send": True,
            },
            executor=failing_executor,
        )

        self.assertFalse(response["success"])
        self.assertEqual(response["failure_code"], "message_send_unconfirmed")
        self.assertEqual(response["failure_stage"], "send_confirm")
        self.assertEqual(response["failure_step"], "send_message")
        self.assertIn("message send was not confirmed", response["failure_summary"])
        self.assertEqual(response["failure_screenshot_path"], str(DM_DEBUG_ARTIFACT_DIR / "dm_send_fail_001.png"))
        self.assertEqual(response["failure_screenshot_name"], "dm_send_fail_001.png")
        self.assertEqual(
            response["failure_screenshot_url"],
            "/api/v1/douyin/private-message/artifacts/dm_send_fail_001.png",
        )

    def test_douyin_private_message_demo_exposes_recipient_privacy_failure(self):
        def failing_executor(profile_url, message, browser, auto_send, options):
            return {
                "success": False,
                "opened": True,
                "prefilled": True,
                "sent": False,
                "resolved_browser": browser,
                "engine": "playwright",
                "steps": [
                    {"name": "open_profile", "ok": True, "detail": "opened"},
                    {
                        "name": "playwright_error",
                        "ok": False,
                        "detail": "recipient_privacy_restriction: 对方设置仅允许互关的人发消息",
                    },
                ],
                "error": "recipient_privacy_restriction: 对方设置仅允许互关的人发消息",
            }

        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "reply": "hello",
                "browser_name": "edge",
                "auto_send": True,
            },
            executor=failing_executor,
        )

        self.assertFalse(response["success"])
        self.assertEqual(response["failure_code"], "recipient_privacy_restriction")
        self.assertEqual(response["failure_stage"], "send_confirm")
        self.assertEqual(response["failure_category"], "recipient")
        self.assertIn("对方隐私设置不允许接收私信", response["failure_summary"])

    def test_douyin_private_message_demo_prefills_by_default(self):
        response = build_douyin_private_message_demo_response(
            {
                "profile_url": "https://www.douyin.com/user/MS4wLjABAAAA0VPGcVLBTV9KuvOPi18HdpZGEDnltASrLJOsMDqs5cY",
                "message": "hello",
                "browser": "edge",
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertTrue(response["success"])
        self.assertTrue(response["prefilled"])
        self.assertFalse(response["sent"])
        self.assertFalse(response["auto_send"])
        self.assertEqual(response["browser"], "edge")
        self.assertEqual(response["message_chars"], 5)
        self.assertEqual(response["engine"], "playwright")

    def test_douyin_private_message_demo_can_auto_send(self):
        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid?from_tab_name=main",
                "reply": "hello",
                "browser_name": "chrome",
                "auto_send": True,
                "input_ratio_x": 0.5,
                "headless": True,
                "use_cdp": True,
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertTrue(response["sent"])
        self.assertTrue(response["seen"]["auto_send"])
        self.assertEqual(response["seen"]["options"]["input_ratio_x"], 0.5)
        self.assertTrue(response["seen"]["options"]["headless"])
        self.assertFalse(response["seen"]["options"]["use_cdp"])
        self.assertEqual(response["seen"]["options"]["cdp_url"], "")
        self.assertEqual(response["seen"]["options"]["viewport_width"], 1440)
        self.assertEqual(response["seen"]["options"]["viewport_height"], 900)
        self.assertEqual(response["seen"]["options"]["page_zoom_percent"], 100)
        self.assertTrue(response["seen"]["options"]["screenshot_on_failure"])
        self.assertIn("douyin_dm_artifacts", response["seen"]["options"]["screenshot_dir"])

    def test_douyin_private_message_demo_can_disable_persistent_context_for_web_send(self):
        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid",
                "reply": "hello",
                "auto_send": True,
                "headless": False,
                "keep_browser_open": False,
                "persistent_context": False,
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertTrue(response["sent"])
        self.assertFalse(response["seen"]["options"]["keep_browser_open"])
        self.assertFalse(response["seen"]["options"]["persistent_context"])

    def test_douyin_private_message_demo_sends_followup_in_same_execution(self):
        calls = []

        def executor(profile_url, message, browser, auto_send, options):
            calls.append({
                "profile_url": profile_url,
                "message": message,
                "browser": browser,
                "auto_send": auto_send,
                "options": options,
            })
            return {
                "success": True,
                "opened": True,
                "prefilled": True,
                "sent": True,
                "followup_private_message": True,
                "followup_private_message_status": "sent",
                "followup_private_message_detail": "followup private message sent",
            }

        response = build_douyin_private_message_demo_response(
            {
                "profile_url": "https://www.douyin.com/user/test-sec-uid",
                "message": "hello",
                "followup_message": "https://v.douyin.com/group/623049833691",
                "browser": "edge",
                "auto_send": True,
            },
            executor=executor,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["options"]["_followup_message"], "https://v.douyin.com/group/623049833691")
        self.assertTrue(response["sent"])
        self.assertTrue(response["followup_private_message"])
        self.assertEqual(response["followup_private_message_status"], "sent")
        self.assertEqual(response["followup_message_chars"], 39)

    def test_douyin_private_message_demo_keeps_primary_success_when_followup_fails(self):
        def executor(profile_url, message, browser, auto_send, options):
            return {
                "success": True,
                "opened": True,
                "prefilled": True,
                "sent": True,
                "followup_private_message": False,
                "followup_private_message_status": "failed_ignored",
                "followup_private_message_detail": "send button not found",
            }

        response = build_douyin_private_message_demo_response(
            {
                "profile_url": "https://www.douyin.com/user/test-sec-uid",
                "message": "hello",
                "followup_message": "https://v.douyin.com/group/623049833691",
                "auto_send": True,
            },
            executor=executor,
        )

        self.assertTrue(response["success"])
        self.assertTrue(response["sent"])
        self.assertFalse(response["followup_private_message"])
        self.assertEqual(response["followup_private_message_status"], "failed_ignored")
        self.assertEqual(response["followup_private_message_detail"], "send button not found")

    def test_douyin_account_cookie_apply_exposes_failure_metadata(self):
        def failing_executor(raw_cookies, browser, options):
            return {
                "success": False,
                "opened": True,
                "resolved_browser": browser,
                "engine": "playwright",
                "account_cookie_loaded": True,
                "account_cookie_count": 1,
                "steps": [
                    {"name": "connect_browser", "ok": True},
                    {"name": "apply_account_cookies", "ok": False, "detail": "Authentication required."},
                ],
                "error": "Authentication required.",
            }

        response = build_douyin_account_cookie_apply_response(
            {
                "browser": "chrome",
                "account_cookies": "sessionid=SECRET_COOKIE",
                "headless": True,
            },
            executor=failing_executor,
        )

        self.assertFalse(response["success"])
        self.assertEqual(response["failure_code"], "cookie_invalid")
        self.assertEqual(response["failure_stage"], "session_prepare")
        self.assertEqual(response["failure_step"], "apply_account_cookies")
        self.assertIn("Authentication required", response["failure_summary"])

    def test_douyin_private_message_demo_accepts_and_redacts_account_cookies(self):
        cookie_text = '{"cookies":[{"name":"sessionid","value":"SECRET_COOKIE","domain":".douyin.com","path":"/"}]}'

        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid?from_tab_name=main",
                "reply": "hello",
                "browser_name": "edge",
                "account_cookies": cookie_text,
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertTrue(response["account_cookie_loaded"])
        self.assertEqual(response["account_cookie_count"], 1)
        self.assertEqual(response["seen"]["options"]["account_cookies"], "[redacted]")
        self.assertNotIn("SECRET_COOKIE", str(response))

    def test_douyin_private_message_demo_accepts_cookie_object(self):
        cookie_object = {
            "cookies": [
                {
                    "name": "sessionid",
                    "value": "SECRET_COOKIE",
                    "domain": ".douyin.com",
                    "path": "/",
                }
            ]
        }

        response = build_douyin_private_message_demo_response(
            {
                "target_profile_url": "https://www.douyin.com/user/test-sec-uid?from_tab_name=main",
                "reply": "hello",
                "browser_name": "edge",
                "account_cookies": cookie_object,
            },
            executor=fake_douyin_demo_executor,
        )

        self.assertTrue(response["account_cookie_loaded"])
        self.assertEqual(response["account_cookie_count"], 1)
        self.assertEqual(response["seen"]["options"]["account_cookies"], "[redacted]")
        self.assertNotIn("SECRET_COOKIE", str(response))

    def test_douyin_account_cookie_apply_accepts_and_redacts_account_cookies(self):
        cookie_text = "sessionid=SECRET_COOKIE; sid_guard=ANOTHER_SECRET"

        response = build_douyin_account_cookie_apply_response(
            {
                "browser": "chrome",
                "account_cookies": cookie_text,
                "headless": True,
                "use_cdp": True,
            },
            executor=fake_douyin_account_cookie_executor,
        )

        self.assertTrue(response["account_cookie_loaded"])
        self.assertEqual(response["account_cookie_count"], 2)
        self.assertEqual(response["browser"], "chrome")
        self.assertEqual(response["seen"]["account_cookies"], "[redacted]")
        self.assertTrue(response["seen"]["options"]["headless"])
        self.assertFalse(response["seen"]["options"]["use_cdp"])
        self.assertEqual(response["seen"]["options"]["cdp_url"], "")
        self.assertEqual(response["seen"]["options"]["viewport_width"], 1440)
        self.assertEqual(response["seen"]["options"]["viewport_height"], 900)
        self.assertEqual(response["seen"]["options"]["page_zoom_percent"], 100)
        self.assertTrue(response["seen"]["options"]["screenshot_on_failure"])
        self.assertNotIn("SECRET_COOKIE", str(response))
        self.assertNotIn("ANOTHER_SECRET", str(response))

    def test_douyin_account_cookie_apply_forwards_browser_open_options(self):
        response = build_douyin_account_cookie_apply_response(
            {
                "browser": "edge",
                "account_cookies": "sessionid=SECRET_COOKIE",
                "open_url": "https://www.douyin.com/user/test-sec-uid",
                "keep_browser_open": True,
                "persistent_context": True,
                "user_data_dir": "content/playwright_profiles/accounts/edge/test-account",
            },
            executor=fake_douyin_account_cookie_executor,
        )

        options = response["seen"]["options"]
        self.assertEqual(options["open_url"], "https://www.douyin.com/user/test-sec-uid")
        self.assertTrue(options["keep_browser_open"])
        self.assertTrue(options["persistent_context"])
        self.assertEqual(
            options["user_data_dir"],
            "content/playwright_profiles/accounts/edge/test-account",
        )

    def test_douyin_account_profile_marks_blue_v(self):
        profile = _douyin_account_profile({
            "user": {
                "nickname": "OpenAI官方账号",
                "unique_id": "openai_cn",
                "enterprise_verify_reason": "OpenAI 官方企业认证",
            }
        })

        self.assertEqual(profile["account_type"], "blue_v")
        self.assertEqual(profile["account_type_label"], "蓝V账号")
        self.assertTrue(profile["is_blue_v"])
        self.assertEqual(profile["unique_id"], "openai_cn")

    def test_douyin_account_profile_marks_personal_when_no_enterprise_verify(self):
        profile = _douyin_account_profile({
            "user": {
                "nickname": "普通创作者",
                "unique_id": "creator_01",
                "custom_verify": "摄影师",
            }
        })

        self.assertEqual(profile["account_type"], "personal")
        self.assertEqual(profile["account_type_label"], "普通账号")
        self.assertFalse(profile["is_blue_v"])

    def test_douyin_account_cookie_apply_exposes_account_type_fields(self):
        def executor(raw_cookies, browser, options):
            return {
                "success": True,
                "opened": True,
                "resolved_browser": browser,
                "engine": "playwright",
                "account_cookie_loaded": True,
                "account_cookie_count": 1,
                "steps": [
                    {"name": "apply_account_cookies", "ok": True, "detail": "1 cookies"},
                    {"name": "detect_account_type", "ok": True, "detail": "蓝V账号"},
                ],
                "account_profile": {
                    "nickname": "企业蓝V",
                    "unique_id": "brand_01",
                    "enterprise_verify_reason": "品牌官方账号",
                },
            }

        response = build_douyin_account_cookie_apply_response(
            {
                "browser": "chrome",
                "account_cookies": "sessionid=SECRET_COOKIE",
            },
            executor=executor,
        )

        self.assertEqual(response["account_type"], "blue_v")
        self.assertEqual(response["account_type_label"], "蓝V账号")
        self.assertTrue(response["is_blue_v"])
        self.assertEqual(response["account_profile"]["unique_id"], "brand_01")

    def test_dm_persistent_context_alive_checks_browser_connection(self):
        class FakeBrowser:
            def is_connected(self):
                return False

        class FakeContext:
            browser = FakeBrowser()

            @property
            def pages(self):
                return []

        self.assertFalse(_dm_persistent_context_alive(FakeContext()))

    def test_dm_persistent_context_alive_requires_an_open_page(self):
        class FakeBrowser:
            def is_connected(self):
                return True

        class FakeContext:
            browser = FakeBrowser()
            pages = []

        self.assertFalse(_dm_persistent_context_alive(FakeContext()))

    def test_dm_should_retry_playwright_runtime_for_asyncio_conflict(self):
        detail = "It looks like you are using Playwright Sync API inside the asyncio loop."
        self.assertTrue(_dm_should_retry_playwright_runtime(detail))

    def test_dm_stop_playwright_session_tears_down_manager(self):
        _DM_PLAYWRIGHT_SESSIONS.clear()
        calls = {"exit": 0}

        class FakeManager:
            def __exit__(self, exc_type, exc, tb):
                calls["exit"] += 1

        _DM_PLAYWRIGHT_SESSIONS["edge|demo"] = {
            "manager": FakeManager(),
            "playwright": None,
            "context": None,
        }
        _dm_reset_all_playwright_sessions()
        self.assertEqual(calls["exit"], 1)
        self.assertEqual(_DM_PLAYWRIGHT_SESSIONS, {})

    def test_douyin_private_message_playwright_executor_retries_runtime_error(self):
        calls = {"count": 0}

        def fake_get_context(factory, browser_name, options):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("It looks like you are using Playwright Sync API inside the asyncio loop.")
            raise RuntimeError("stop after retry")

        with patch(
            "aisec_agent.web.session_rag_chat._dm_get_playwright_context",
            side_effect=fake_get_context,
        ), patch(
            "aisec_agent.web.session_rag_chat._dm_reset_all_playwright_sessions",
        ) as reset_mock:
            result = _douyin_private_message_playwright_executor(
                "https://www.douyin.com/user/test-sec-uid",
                "hello",
                "edge",
                False,
                {"headless": False, "persistent_context": True, "keep_browser_open": True},
            )

        reset_mock.assert_called_once()
        self.assertEqual(calls["count"], 2)
        self.assertFalse(result["success"])
        self.assertIn("stop after retry", result["error"])

    def test_dm_message_page_reuses_initial_blank_page(self):
        class FakePage:
            url = "about:blank"

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]
                self.new_page_calls = 0

            def new_page(self):
                self.new_page_calls += 1
                return FakePage()

        context = FakeContext()
        page = _dm_message_page(context, "https://www.douyin.com/user/test-sec-uid")

        self.assertIs(page, context.pages[0])
        self.assertEqual(context.new_page_calls, 0)

    def test_dm_message_page_reuses_latest_page_for_a_new_target(self):
        class FakePage:
            def __init__(self, url):
                self.url = url
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        class FakeContext:
            def __init__(self):
                self.pages = [
                    FakePage("https://www.douyin.com/user/old-target-one"),
                    FakePage("https://www.douyin.com/user/old-target-two"),
                ]
                self.new_page_calls = 0

            def new_page(self):
                self.new_page_calls += 1
                return FakePage("about:blank")

        context = FakeContext()
        page = _dm_message_page(context, "https://www.douyin.com/user/new-target")

        self.assertIs(page, context.pages[1])
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(context.pages[0].close_calls, 1)
        self.assertEqual(context.pages[1].close_calls, 0)

    def test_dm_message_page_prefers_matching_target_and_closes_other_pages(self):
        class FakePage:
            def __init__(self, url):
                self.url = url
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        matching_page = FakePage("https://www.douyin.com/user/target?from_tab_name=main")
        stale_page = FakePage("https://www.douyin.com/user/stale-target")

        class FakeContext:
            pages = [matching_page, stale_page]

            def new_page(self):
                raise AssertionError("matching page should be reused")

        page = _dm_message_page(FakeContext(), "https://www.douyin.com/user/target")

        self.assertIs(page, matching_page)
        self.assertEqual(matching_page.close_calls, 0)
        self.assertEqual(stale_page.close_calls, 1)

    def test_dm_message_page_opens_a_page_when_context_has_no_open_pages(self):
        class FakePage:
            url = "about:blank"

        class FakeContext:
            pages = []

            def __init__(self):
                self.new_page_calls = 0
                self.created_page = FakePage()

            def new_page(self):
                self.new_page_calls += 1
                return self.created_page

        context = FakeContext()
        page = _dm_message_page(context, "https://www.douyin.com/user/new-target")

        self.assertIs(page, context.created_page)
        self.assertEqual(context.new_page_calls, 1)

    def test_dm_browser_geometry_defaults_are_fixed(self):
        args = _dm_browser_launch_args({})

        self.assertIn("--window-size=1440,900", args)
        self.assertIn("--window-position=0,0", args)
        self.assertIn("--force-device-scale-factor=1", args)
        self.assertIn("--start-maximized", args)

    def test_dm_browser_geometry_keeps_headless_sessions_without_window_maximize(self):
        args = _dm_browser_launch_args({"headless": True})

        self.assertNotIn("--start-maximized", args)

    def test_dm_browser_geometry_is_limited_to_small_windows_screen(self):
        with patch(
            "aisec_agent.web.session_rag_chat._dm_windows_screen_size",
            return_value={"width": 1024, "height": 768},
        ):
            args = _dm_browser_launch_args({})

        self.assertIn("--window-size=1024,720", args)
        self.assertIn("--start-maximized", args)

    def test_dm_apply_page_geometry_sets_viewport_and_zoom(self):
        class DummyCdpSession:
            def __init__(self):
                self.calls = []
                self.detached = False

            def send(self, method, payload):
                self.calls.append((method, payload))

            def detach(self):
                self.detached = True

        class DummyContext:
            def __init__(self):
                self.session = DummyCdpSession()

            def new_cdp_session(self, _page):
                return self.session

        class DummyPage:
            def __init__(self):
                self.context = DummyContext()
                self.viewport = None
                self.zoom = None

            def set_viewport_size(self, value):
                self.viewport = value

            def evaluate(self, _script, zoom):
                self.zoom = zoom

        page = DummyPage()
        result = _dm_apply_page_geometry(page, {})

        self.assertEqual(page.viewport, {"width": 1440, "height": 900})
        self.assertEqual(page.zoom, "100%")
        self.assertEqual(result["page_zoom_percent"], 100)
        self.assertEqual(
            page.context.session.calls,
            [("Emulation.setPageScaleFactor", {"pageScaleFactor": 1.0})],
        )
        self.assertTrue(page.context.session.detached)

    def test_dm_apply_page_geometry_is_limited_to_small_windows_screen(self):
        class DummyPage:
            context = None

            def __init__(self):
                self.viewport = None
                self.zoom = None

            def set_viewport_size(self, value):
                self.viewport = value

            def evaluate(self, _script, zoom):
                self.zoom = zoom

        page = DummyPage()
        with patch(
            "aisec_agent.web.session_rag_chat._dm_windows_screen_size",
            return_value={"width": 1024, "height": 768},
        ):
            result = _dm_apply_page_geometry(page, {})

        self.assertEqual(page.viewport, {"width": 1024, "height": 720})
        self.assertEqual(result["screen_width"], 1024)
        self.assertEqual(result["screen_height"], 768)
        self.assertTrue(result["screen_limited"])

    def test_dm_normalize_editor_text_ignores_layout_whitespace(self):
        self.assertEqual(
            _dm_normalize_editor_text("  第一行\n\n第二行\u200b "),
            "第一行 第二行",
        )

    def test_dm_cleanup_closed_playwright_sessions_stops_stale_runtime(self):
        class FakeContext:
            browser = None
            pages = []

            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        class FakePlaywright:
            def __init__(self):
                self.stop_calls = 0

            def stop(self):
                self.stop_calls += 1

        context = FakeContext()
        playwright = FakePlaywright()
        with patch.dict(_DM_PLAYWRIGHT_SESSIONS, {
            "edge|closed": {
                "context": context,
                "playwright": playwright,
            },
        }, clear=True):
            cleaned = _dm_cleanup_closed_playwright_sessions()
            self.assertEqual(_DM_PLAYWRIGHT_SESSIONS, {})

        self.assertEqual(cleaned, 1)
        self.assertEqual(context.close_calls, 1)
        self.assertEqual(playwright.stop_calls, 1)

    def test_dm_persistent_launch_failure_stops_playwright_runtime(self):
        class FakePlaywright:
            def __init__(self):
                self.stop_calls = 0

            def stop(self):
                self.stop_calls += 1

        class FakeManager:
            def __init__(self, playwright):
                self.playwright = playwright

            def start(self):
                return self.playwright

        playwright = FakePlaywright()
        with patch.dict(_DM_PLAYWRIGHT_SESSIONS, {}, clear=True), patch(
            "aisec_agent.web.session_rag_chat._dm_launch_persistent_playwright_context",
            side_effect=RuntimeError("browser launch failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "browser launch failed"):
                _dm_get_playwright_context(
                    lambda: FakeManager(playwright),
                    "edge",
                    {
                        "persistent_context": True,
                        "keep_browser_open": True,
                        "user_data_dir": "D:\\tmp\\closed-browser-test",
                    },
                )

        self.assertEqual(playwright.stop_calls, 1)

    def test_dm_should_retry_browser_closed_only_before_send_stage(self):
        self.assertTrue(_dm_should_retry_browser_closed(
            "BrowserContext.add_cookies: Target page, context or browser has been closed",
            [
                {"name": "apply_account_cookies", "ok": True},
                {"name": "open_profile", "ok": True},
            ],
        ))
        self.assertFalse(_dm_should_retry_browser_closed(
            "BrowserContext.add_cookies: Target page, context or browser has been closed",
            [
                {"name": "open_profile", "ok": True},
                {"name": "send_message", "ok": False},
            ],
        ))

    def test_dm_should_keep_browser_open_on_manual_failure(self):
        self.assertTrue(_dm_should_keep_browser_open_on_failure("verification required", True))
        self.assertTrue(_dm_should_keep_browser_open_on_failure("login required", True))
        self.assertTrue(_dm_should_keep_browser_open_on_failure("message send was not confirmed", True))
        self.assertTrue(_dm_should_keep_browser_open_on_failure("page load timed out", True))
        self.assertFalse(_dm_should_keep_browser_open_on_failure("browser has been closed", True))
        self.assertFalse(_dm_should_keep_browser_open_on_failure("verification required", False))

    def test_douyin_private_message_demo_rejects_non_douyin_url(self):
        with self.assertRaises(WebInputError):
            build_douyin_private_message_demo_response(
                {
                    "profile_url": "https://example.com/user/test",
                    "message": "hello",
                },
                executor=fake_douyin_demo_executor,
            )

    def test_parse_topics_accepts_string_and_list(self):
        self.assertEqual(parse_topics("faq, scripts, "), ["faq", "scripts"])
        self.assertEqual(parse_topics(["faq", "", " scripts "]), ["faq", "scripts"])

    def test_page_config_is_passed_to_chat_logic(self):
        logic = FakeLogic()

        response = build_chat_response(
            {
                "question": "hello",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-web",
                "user_id": "user-web",
                "topics": "topic-a,topic-b",
                "enable_knowledge": False,
            },
            logic=logic,
        )

        self.assertEqual(response["session_id"], "sid-web")
        self.assertEqual(response["result"]["answer"], "ok")
        self.assertEqual(len(logic.calls), 1)
        self.assertEqual(logic.calls[0]["key"], "page-key")
        self.assertEqual(logic.calls[0]["func_name"], "minimax_anthropic_chat")
        self.assertEqual(logic.calls[0]["model_name"], "MiniMax-M3")
        self.assertEqual(logic.calls[0]["topics"], [])
        self.assertEqual(logic.calls[0]["conversation_stage"], "first_comment")
        self.assertIn("prompt_trace", response)

    def test_deepseek_provider_uses_preset_config_and_page_key(self):
        logic = FakeLogic()

        build_chat_response(
            {
                "question": "hello",
                "provider": "deepseek",
                "api_key": "deepseek-page-key",
                "session_id": "sid-web",
            },
            logic=logic,
        )

        self.assertEqual(logic.calls[0]["url"], DEEPSEEK_CHAT_COMPLETIONS_URL)
        self.assertEqual(logic.calls[0]["key"], "deepseek-page-key")
        self.assertEqual(logic.calls[0]["func_name"], "deepseek_chat")
        self.assertEqual(logic.calls[0]["model_name"], DEEPSEEK_DEFAULT_MODEL)

    def test_saved_provider_config_is_masked_in_public_listing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / ".env"
            response = build_model_config_save_response(
                {
                    "provider": "deepseek",
                    "api_key": "sk-deepseek-secret-1234",
                },
                path=config_path,
            )
            public_configs = _public_model_configs(config_path)

        self.assertTrue(response["config"]["has_api_key"])
        self.assertEqual(response["config"]["api_key_masked"], "sk-d...1234")
        self.assertNotIn("api_key", response["config"])
        self.assertNotIn("sk-deepseek-secret-1234", str(public_configs))

    def test_model_config_save_writes_env_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / ".env"
            build_model_config_save_response(
                {
                    "provider": "minimax",
                    "api_key": "mini-secret-key",
                    "url": "https://api.minimaxi.com/anthropic",
                    "func_name": "minimax_anthropic_chat",
                    "model_name": "MiniMax-M3",
                    "max_len_input": 12000,
                },
                path=config_path,
            )
            env_text = config_path.read_text(encoding="utf-8")

        self.assertIn("MINIMAX_API_KEY=mini-secret-key", env_text)
        self.assertIn("MINIMAX_URL=https://api.minimaxi.com/anthropic", env_text)
        self.assertIn("MINIMAX_FUNC_NAME=minimax_anthropic_chat", env_text)
        self.assertIn("MINIMAX_MODEL_NAME=MiniMax-M3", env_text)
        self.assertIn("MINIMAX_MAX_LEN_INPUT=12000", env_text)

    def test_public_private_message_api_accepts_postman_payload_shape(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            response = build_public_private_message_response(
                {
                    "comment": "我妈膝盖上下楼疼，干细胞这个适合吗",
                    "video_overview": "视频讲膝骨关节、骨积液和干细胞评估方向",
                    "project_id": "",
                    "product_id": "joint_assessment",
                    "session_id": "postman-session",
                    "stage": "comment",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "postman-key",
                    },
                },
                logic=logic,
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(response["session_id"], "postman-session")
        self.assertEqual(response["sender_identity"], "健康顾问助理")
        self.assertEqual(response["sender_identity_source"]["source"], "product")
        self.assertEqual(logic.calls[0]["key"], "postman-key")
        self.assertEqual(logic.calls[0]["func_name"], "deepseek_chat")
        self.assertEqual(logic.calls[0]["conversation_stage"], "first_comment")

    def test_public_private_message_api_can_use_saved_provider_key(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / ".env"
            build_model_config_save_response(
                {
                    "provider": "deepseek",
                    "api_key": "saved-deepseek-key",
                },
                path=config_path,
            )
            import aisec_agent.web.session_rag_chat as web_module
            old_path = web_module.ENV_FILE
            web_module.ENV_FILE = config_path
            try:
                response = build_public_private_message_response(
                    {
                        "comment": "hello",
                        "session_id": "postman-session",
                        "model": {"provider": "deepseek"},
                    },
                    logic=logic,
                    project_store=ProjectMaterialStore(Path(temp_dir) / "projects"),
                    use_saved_model_config=True,
                )
            finally:
                web_module.ENV_FILE = old_path

        self.assertEqual(response["session_id"], "postman-session")
        self.assertEqual(logic.calls[0]["key"], "saved-deepseek-key")

    def test_business_reply_routes_boss_interview_to_recruitment_flow_docs(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n每次只问一个核心问题，不要暴露评分。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况。",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "你好，我想了解销售岗",
                    "session_id": "boss-session-1",
                    "user_identity": {
                        "name": "求职者",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(response["route"]["type"], "recruitment_interview")
        self.assertEqual(response["route"]["knowledge_base"], "招聘知识库")
        self.assertEqual(response["route"]["domain"], "招聘流程")
        self.assertEqual(response["route"]["selected_document_name"], "销售招聘流程.md")
        self.assertEqual(response["answer"], "请问怎么称呼？应聘的是销售岗对吗？")
        self.assertTrue(any(doc["relative_path"].endswith("销售招聘流程.md") for doc in response["selected_documents"]))
        self.assertEqual(response["user_identity_structured"], {"name": "求职者", "job": "销售岗"})
        self.assertIn("job: 销售岗", response["prompt_trace"]["final_prompt"])
        self.assertIn("企业微信招聘初面全局提示词", response["prompt_trace"]["final_prompt"])
        self.assertIn("总文件概述", response["prompt_trace"]["final_prompt"])
        self.assertIn("销售招聘流程", response["prompt_trace"]["final_prompt"])
        self.assertIn("历史上下文", response["prompt_trace"]["final_prompt"])
        self.assertEqual(llm.calls, [])
        self.assertEqual(memory.store_calls[0][1], "boss-session-1")
        self.assertEqual(response["prompt_trace"]["opening_control"]["question"], "请问怎么称呼？应聘的是销售岗对吗？")
        self.assertEqual(response["prompt_trace"]["answer_guard"]["reason"], "missing_name_or_job_opening_required")
        self.assertEqual(response["dialogue_list"][-1]["user"], "你好，我想了解销售岗")
        self.assertEqual(response["dialogue_list"][-1]["assistant"], response["answer"])

    def test_business_reply_returns_dialogue_list_from_session_history(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessConversationMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n每次只问一个核心问题。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况。",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "我叫张三，对，销售岗",
                    "session_id": "boss-session-history",
                    "user_identity": {
                        "name": "张三",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(len(response["dialogue_list"]), 2)
        self.assertEqual(response["dialogue_list"][0]["sequence_number"], 1)
        self.assertEqual(response["dialogue_list"][0]["user"], "你好，我想了解销售岗")
        self.assertIn("怎么称呼", response["dialogue_list"][0]["assistant"])
        self.assertEqual(response["dialogue_list"][1]["sequence_number"], 2)
        self.assertTrue(response["dialogue_list"][1]["current"])
        self.assertEqual(response["conversation_history"], response["dialogue_list"])

    def test_business_reply_uses_specific_flow_initial_question_before_llm(self):
        llm = FakeBusinessOutOfOrderLLM()
        memory = FakeBusinessMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n按岗位流程顺序提问。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "我叫张三，应聘销售岗",
                    "session_id": "boss-session-order",
                    "user_identity": {
                        "name": "张三",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(response["answer"], "老家哪里人？")
        self.assertFalse(response["prompt_trace"]["answer_guard"]["rewritten"])
        self.assertEqual(response["prompt_trace"]["answer_guard"]["reason"], "")
        self.assertEqual(response["prompt_trace"]["interview_control"]["next_question_source"], "specific_flow")
        self.assertTrue(response["prompt_trace"]["interview_control"]["next_question_exact"])
        self.assertEqual(response["prompt_trace"]["interview_control"]["next_question_group_id"], "basic_hometown")
        self.assertEqual(llm.calls, [])

    def test_business_reply_name_only_then_asks_missing_job(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessOpeningOnlyMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            business_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "机器人面试"
            prompt_dir = business_dir / "prompts"
            docs_dir = business_dir / "相关文档"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)
            (business_dir / "README.md").write_text(
                "# 机器人面试业务说明\n\n"
                "## 岗位路由\n\n"
                "### 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (prompt_dir / "开场与岗位读取规则.md").write_text(
                "# 开场与岗位读取规则\n\n"
                "## 首次进线消息\n\n"
                "如果会话里还没有求职者称呼或应聘岗位，优先确认：\n\n"
                "请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )
            (prompt_dir / "全局提示词.md").write_text(
                "# 招聘初面全局提示词\n\n自然、礼貌，按岗位流程推进。",
                encoding="utf-8",
            )
            (docs_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "赵四",
                    "session_id": "boss_user_008",
                    "user_identity": {
                        "name": "求职者",
                        "job": "未知",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(response["answer"], "请问应聘哪个岗位？")
        self.assertEqual(response["user_identity_structured"]["name"], "赵四")
        self.assertEqual(response["user_identity_structured"]["job"], "未知")
        self.assertTrue(response["prompt_trace"]["opening_control"]["required"])
        self.assertEqual(response["prompt_trace"]["opening_control"]["question"], "请问应聘哪个岗位？")
        self.assertEqual(response["prompt_trace"]["answer_guard"]["reason"], "missing_name_or_job_opening_required")
        self.assertNotIn("称呼", response["answer"])
        self.assertEqual(llm.calls, [])

    def test_business_reply_uses_session_identity_when_job_answer_follows_name(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessNameThenJobMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            business_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "机器人面试"
            prompt_dir = business_dir / "prompts"
            docs_dir = business_dir / "相关文档"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)
            (business_dir / "README.md").write_text(
                "# 机器人面试业务说明\n\n"
                "## 岗位路由\n\n"
                "### 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (prompt_dir / "开场与岗位读取规则.md").write_text(
                "# 开场与岗位读取规则\n\n"
                "## 首次进线消息\n\n"
                "如果会话里还没有求职者称呼或应聘岗位，优先确认：\n\n"
                "请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )
            (prompt_dir / "全局提示词.md").write_text(
                "# 招聘初面全局提示词\n\n自然、礼貌，按岗位流程推进。",
                encoding="utf-8",
            )
            (docs_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "销售",
                    "session_id": "boss_user_009",
                    "user_identity": {
                        "name": "求职者",
                        "job": "未知",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(response["answer"], "老家哪里人？")
        self.assertEqual(response["user_identity_structured"]["name"], "赵四")
        self.assertEqual(response["user_identity_structured"]["job"], "销售岗")
        self.assertFalse(response["prompt_trace"]["opening_control"]["required"])
        self.assertNotIn("称呼", response["answer"])
        self.assertEqual(response["prompt_trace"]["interview_control"]["next_question_source"], "specific_flow")
        self.assertTrue(response["prompt_trace"]["interview_control"]["next_question_exact"])
        self.assertIn("User: 赵四", response["prompt_trace"]["final_prompt"])
        self.assertIn("Assistant: 请问应聘哪个岗位？", response["prompt_trace"]["final_prompt"])
        self.assertEqual(llm.calls, [])

    def test_business_reply_does_not_treat_readme_sections_as_jobs(self):
        llm = FakeBusinessLLM()
        logic = FakeBusinessLogic(memory=FakeBusinessMemory(), llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            business_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "机器人面试"
            prompt_dir = business_dir / "prompts"
            docs_dir = business_dir / "相关文档"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)
            (business_dir / "README.md").write_text(
                "# 机器人面试业务说明\n\n"
                "## 业务用途\n\n"
                "本目录用于机器人面试业务。\n\n"
                "## Prompt 文件概括\n\n"
                "| 文件 | 作用 |\n| --- | --- |\n| 开场与岗位读取规则.md | 定义首次进线 |\n\n"
                "## 岗位路由\n\n"
                "### 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (prompt_dir / "开场与岗位读取规则.md").write_text(
                "# 开场与岗位读取规则\n\n"
                "## 首次进线消息\n\n"
                "如果会话里还没有求职者称呼或应聘岗位，优先确认：\n\n"
                "请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )
            (prompt_dir / "全局提示词.md").write_text(
                "# 招聘初面全局提示词\n\n自然、礼貌。",
                encoding="utf-8",
            )
            (docs_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "你好",
                    "session_id": "boss_user_007",
                    "user_identity": {
                        "name": "求职者",
                        "job": "未知",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        self.assertEqual(response["answer"], "请问怎么称呼？应聘哪个岗位？")
        self.assertEqual(response["route"]["selected_job"], {})
        self.assertEqual(response["route"]["selected_document_name"], "销售招聘流程.md")
        self.assertEqual(llm.calls, [])
        self.assertNotIn("业务用途", response["answer"])
        self.assertNotIn("如果会话里还没有", response["answer"])
        self.assertIn("开场与岗位读取规则", response["prompt_trace"]["final_prompt"])
        self.assertEqual(response["prompt_trace"]["opening_control"]["question"], "请问怎么称呼？应聘哪个岗位？")

    def test_business_reply_merges_split_recruitment_prompt_files(self):
        llm = FakeBusinessLLM()
        logic = FakeBusinessLogic(llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            kb_dir = project_dir / "knowledge" / "files" / "招聘知识库"
            prompt_dir = kb_dir / "prompts"
            flow_dir = kb_dir / "招聘流程"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            flow_dir.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "全局提示词.md").write_text(
                "# 招聘初面全局提示词\n\n语气自然，像真人 HR。",
                encoding="utf-8",
            )
            (prompt_dir / "问题控制规则.md").write_text(
                "# 问题控制规则\n\n同一问题组最多询问两次。",
                encoding="utf-8",
            )
            (kb_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况。",
                encoding="utf-8",
            )
            (flow_dir / "销售招聘流程.md").write_text(
                "# 销售招聘流程\n\n问题组 ID：career_stability\n建议提问：离职原因是什么？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "你好，我想了解销售岗",
                    "session_id": "boss-session-split-prompts",
                    "user_identity": {
                        "name": "求职者",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        final_prompt = response["prompt_trace"]["final_prompt"]
        prompt_doc = next(doc for doc in response["selected_documents"] if doc["role"] == "global_prompt")

        self.assertIn("招聘初面全局提示词", final_prompt)
        self.assertIn("问题控制规则", final_prompt)
        self.assertIn("同一问题组最多询问两次", final_prompt)
        self.assertEqual(prompt_doc["relative_path"], "knowledge/files/招聘知识库/prompts")
        self.assertEqual(len(prompt_doc["source_files"]), 2)

    def test_business_reply_accepts_structured_user_identity(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n每次只问一个核心问题。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况。",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "题目：请问怎么称呼？应聘哪个岗位？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "你好，我想了解这个岗位",
                    "session_id": "boss-session-structured",
                    "user_identity": {
                        "name": "求职者",
                        "job": "销售岗",
                        "platform": "boss直聘",
                        "experience": "3年SaaS销售经验",
                        "note": "正在找销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        final_prompt = response["prompt_trace"]["final_prompt"]

        self.assertEqual(response["route"]["type"], "recruitment_interview")
        self.assertEqual(response["route"]["selected_document_name"], "销售招聘流程.md")
        self.assertEqual(response["user_identity_structured"]["name"], "求职者")
        self.assertEqual(response["user_identity_structured"]["job"], "销售岗")
        self.assertEqual(response["user_identity_structured"]["platform"], "boss直聘")
        self.assertIn("name: 求职者", response["user_identity"])
        self.assertIn("job: 销售岗", response["user_identity"])
        self.assertIn("name: 求职者", final_prompt)
        self.assertIn("job: 销售岗", final_prompt)
        self.assertIn("platform: boss直聘", final_prompt)
        self.assertIn("experience: 3年SaaS销售经验", final_prompt)
        self.assertEqual(memory.store_calls[0][1], "boss-session-structured")

    def test_business_reply_rejects_string_user_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(WebInputError):
                build_business_reply_response(
                    {
                        "message": "你好，我想了解销售岗",
                        "session_id": "boss-session-invalid",
                        "user_identity": "求职者，应聘销售",
                        "caller": "boss直聘",
                        "business_scene": "机器人面试",
                        "model": {
                            "provider": "deepseek",
                            "api_key": "business-key",
                        },
                    },
                    logic=FakeBusinessLogic(),
                    project_store=ProjectMaterialStore(Path(temp_dir)),
                    use_saved_model_config=False,
                )

    def test_business_reply_prompt_skips_repeated_interview_question_group(self):
        llm = FakeBusinessLLM()
        memory = FakeBusinessFailureMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n同一问题组最多询问两次。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况。",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：业绩情况\n"
                "题目：过往的产品客单价是多少？平均回款周期是多久，月平均任务多少？完成多少？业绩是单人独立成交还是团队协作的呢？\n"
                "题目：近两家公司的年度整体销售业绩总额，分年度说明数据",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "都说了是自己独立完成",
                    "session_id": "boss-session-repeat",
                    "user_identity": {
                        "name": "求职者",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        control = response["prompt_trace"]["interview_control"]
        final_prompt = response["prompt_trace"]["final_prompt"]
        module_keys = [module["key"] for module in response["prompt_trace"]["prompt_modules"]]

        self.assertGreaterEqual(control["resistance_count"], 2)
        self.assertIn("业绩数据/客单价回款任务", control["repeated_question_groups"])
        self.assertIn("本轮必须跳过当前问题组", control["prompt"])
        self.assertIn("<interview_control_state>", final_prompt)
        self.assertIn("同一问题组及衍生问题最多问两次", final_prompt)
        self.assertIn("业绩数据/客单价回款任务", final_prompt)
        self.assertIn("interview_control_state", module_keys)
        self.assertEqual(memory.store_calls[0][1], "boss-session-repeat")

    def test_business_reply_accepts_hometown_answer_and_moves_next(self):
        llm = FakeBusinessHometownFollowupLLM()
        memory = FakeBusinessHometownAnsweredMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n候选人已回答的问题不要继续追问。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n"
                "题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？3. 家庭情况怎么样？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "广州",
                    "session_id": "boss-session-hometown-answer",
                    "user_identity": {
                        "name": "张三",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        control = response["prompt_trace"]["interview_control"]
        answer_guard = response["prompt_trace"]["answer_guard"]

        self.assertEqual(len(llm.calls), 1)
        self.assertIn("基本情况/老家籍贯", control["current_answered_question_groups"])
        self.assertIn("基本情况/老家籍贯", control["blocked_question_groups"])
        self.assertEqual(control["next_question_group"], "基本情况/住址通勤")
        self.assertEqual(answer_guard["reason"], "answered_question_group_should_move_next")
        self.assertIn("目前住在哪里", response["answer"])
        self.assertNotIn("广州哪个区", response["answer"])

    def test_business_reply_skips_family_question_when_candidate_resists(self):
        llm = FakeBusinessFamilyRepeatLLM()
        memory = FakeBusinessFamilyResistanceMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n候选人抵触当前问题时，先跳过当前问题组，继续下一主问题。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：基本情况匹配度\n"
                "题目：1. 老家哪里人？2. 目前住在哪里，近哪个地铁站？3. 家庭情况怎么样？是否已结婚，有没有小孩？\n\n"
                "评分维度：行业匹配度\n"
                "题目：之前主要呆过什么行业？公司主营的业务/产品是什么？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "问这个干嘛",
                    "session_id": "boss-session-family-resistance",
                    "user_identity": {
                        "name": "张三",
                        "job": "销售岗",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        control = response["prompt_trace"]["interview_control"]
        answer_guard = response["prompt_trace"]["answer_guard"]

        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(control["current_message_resistance"])
        self.assertIn("家庭/婚育/配偶", control["current_resisted_question_groups"])
        self.assertIn("家庭/婚育/配偶", control["blocked_question_groups"])
        self.assertEqual(control["next_question_group"], "过往行业/公司产品")
        self.assertEqual(answer_guard["reason"], "resistance_skip_question_group")
        self.assertIn("之前主要呆过什么行业", response["answer"])
        self.assertNotIn("家庭情况", response["answer"])
        self.assertNotIn("已结婚", response["answer"])
        self.assertNotIn("小孩", response["answer"])
        self.assertNotIn("老公", response["answer"])

    def test_business_reply_guard_rewrites_repeated_career_question(self):
        llm = FakeBusinessRepeatingLLM()
        memory = FakeBusinessCareerStabilityMemory()
        logic = FakeBusinessLogic(memory=memory, llm=llm)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            project_dir = Path(temp_dir) / store.default_project_id
            flow_dir = project_dir / "knowledge" / "files" / "招聘知识库" / "招聘流程"
            specific_dir = flow_dir / "具体资料"
            specific_dir.mkdir(parents=True, exist_ok=True)
            (flow_dir / "全局提示词.md").write_text(
                "# 企业微信招聘初面全局提示词\n\n同一问题组最多询问两次，候选人不耐烦时跳到下一题。",
                encoding="utf-8",
            )
            (flow_dir / "总文件概述.md").write_text(
                "# 当前招聘岗位概述\n\n"
                "## 销售岗\n\n"
                "- 岗位别名：销售、销售岗、商务、客户经理、sales\n"
                "- 对应岗位文档：销售招聘流程.md\n"
                "- 流程用途：按照销售岗题目了解基本情况、职业稳定性和客户构成。",
                encoding="utf-8",
            )
            (specific_dir / "销售招聘流程.md").write_text(
                "评分维度：职业稳定性\n"
                "题目：之前几份工作大概每份做了多久？离职原因是什么？\n\n"
                "评分维度：职业匹配度\n"
                "题目：过往客户主要是哪类为主，比如企业客户、个人客户还是政府客户？",
                encoding="utf-8",
            )

            response = build_business_reply_response(
                {
                    "message": "？",
                    "session_id": "boss-session-career-repeat",
                    "user_identity": {
                        "name": "李四",
                        "job": "未知",
                    },
                    "caller": "boss直聘",
                    "business_scene": "机器人面试",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "business-key",
                    },
                },
                logic=logic,
                project_store=store,
                use_saved_model_config=False,
            )

        control = response["prompt_trace"]["interview_control"]
        answer_guard = response["prompt_trace"]["answer_guard"]
        final_prompt = response["prompt_trace"]["final_prompt"]

        self.assertTrue(control["current_message_resistance"])
        self.assertIn("职业稳定性/离职原因/加班", control["blocked_question_groups"])
        self.assertEqual(control["next_question_group"], "客户构成/客户占比")
        self.assertTrue(answer_guard["rewritten"])
        self.assertEqual(answer_guard["reason"], "resistance_skip_question_group")
        self.assertIn("客户主要是哪类", response["answer"])
        self.assertNotIn("几份工作", response["answer"])
        self.assertIn("<current_message>", final_prompt)
        self.assertIn("？", final_prompt)
        self.assertEqual(response["user_identity_structured"]["job"], "未知")
        self.assertEqual(memory.store_calls[0][3], response["answer"])

    def test_public_route_and_prompt_preview_accept_comment_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            route = build_public_route_debug_response(
                {
                    "comment": "这个能自动回复抖音评论吗",
                    "video_overview": "视频演示评论采集、知识库匹配和私信生成",
                    "stage": "comment",
                },
                project_store=store,
            )
            preview = build_public_prompt_preview_response(
                {
                    "comment": "我妈膝盖上下楼疼，干细胞这个适合吗",
                    "video_overview": "视频讲膝骨关节、骨积液和干细胞评估方向",
                },
                project_store=store,
            )

        self.assertIn("ai_technology_hardware", route["project_documents"]["document_ids"])
        self.assertTrue(preview["selected_documents"])
        self.assertIn("用户评论", preview["final_prompt"])

    def test_config_test_calls_selected_provider_function(self):
        llm = FakeConfigTestLLM()

        response = build_config_test_response(
            {
                "provider": "deepseek",
                "api_key": "page-key",
            },
            llm_tools=llm,
        )

        self.assertEqual(response["reply"], "OK")
        self.assertEqual(llm.calls[0]["url"], DEEPSEEK_CHAT_COMPLETIONS_URL)
        self.assertEqual(llm.calls[0]["api_key"], "page-key")
        self.assertEqual(llm.calls[0]["model"], DEEPSEEK_DEFAULT_MODEL)

    def test_config_test_can_use_saved_provider_key(self):
        llm = FakeConfigTestLLM()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / ".env"
            build_model_config_save_response(
                {
                    "provider": "deepseek",
                    "api_key": "saved-config-key",
                },
                path=config_path,
            )
            import aisec_agent.web.session_rag_chat as web_module
            old_path = web_module.ENV_FILE
            web_module.ENV_FILE = config_path
            try:
                response = build_config_test_response(
                    {"provider": "deepseek"},
                    llm_tools=llm,
                    use_saved_model_config=True,
                )
            finally:
                web_module.ENV_FILE = old_path

        self.assertEqual(response["reply"], "OK")
        self.assertEqual(llm.calls[0]["api_key"], "saved-config-key")

    def test_config_test_requires_provider_key(self):
        with self.assertRaises(WebInputError):
            build_config_test_response({"provider": "deepseek"}, llm_tools=FakeConfigTestLLM())

    def test_api_key_placeholder_is_rejected_before_http_header_encoding(self):
        logic = FakeLogic()

        with self.assertRaises(WebInputError) as cm:
            build_public_private_message_response(
                {
                    "comment": "我妈膝盖上下楼疼，干细胞这个适合吗",
                    "model": {
                        "provider": "deepseek",
                        "api_key": "替换成你的 API Key",
                    },
                },
                logic=logic,
            )

        self.assertIn("api_key", str(cm.exception))

    def test_knowledge_topics_are_only_used_when_enabled(self):
        logic = FakeLogic()

        build_chat_response(
            {
                "question": "hello",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-web",
                "topics": "topic-a,topic-b",
                "enable_knowledge": True,
                "knowledge_size": 8,
            },
            logic=logic,
        )

        self.assertEqual(logic.calls[0]["topics"], ["topic-a", "topic-b"])
        self.assertEqual(logic.calls[0]["knowledge_size"], 8)

    def test_runtime_context_contains_only_template_and_retrieved_knowledge(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            response = build_chat_response(
                {
                    "question": "膝盖积液疼痛怎么办",
                    "video_overview": "视频讲的是膝盖积液和上下楼疼的日常养护",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-web",
                    "scene_id": "auto",
                },
                logic=logic,
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(response["project"]["scene_id"], "health_presales")
        project_context = logic.calls[0]["project_context"]
        self.assertIn("视频概述", project_context)
        self.assertIn("上下楼疼", project_context)
        self.assertIn("<sender_identity>", project_context)
        self.assertIn("本轮私信对外只使用通用身份：助理。", project_context)
        self.assertIn("<retrieved_knowledge>", project_context)
        self.assertIn("大健康医疗板块", project_context)
        self.assertEqual(response["sender_identity"], "健康顾问助理")
        self.assertEqual(response["sender_identity_source"]["source"], "context_generated")
        self.assertNotIn("<global_operator_prompt>", project_context)
        self.assertNotIn("<project_scene_prompt>", project_context)
        self.assertNotIn("<conversion_goal>", project_context)

    def test_chat_always_auto_matches_scene_from_comment(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            response = build_chat_response(
                {
                    "question": "想做AI批量剪辑和自动发布，能采集评论吗",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-web",
                    "scene_id": "health_presales",
                },
                logic=logic,
                project_store=ProjectMaterialStore(Path(temp_dir)),
        )

        self.assertEqual(response["project"]["scene_id"], "ai_hardware_presales")
        self.assertIn("<retrieved_knowledge>", logic.calls[0]["project_context"])
        self.assertIn("<sender_identity>", logic.calls[0]["project_context"])
        self.assertIn("本轮私信对外只使用通用身份：顾问。", logic.calls[0]["project_context"])
        self.assertIn("AI技术与智能硬件板块", logic.calls[0]["project_context"])
        self.assertNotIn("<project_materials>", logic.calls[0]["project_context"])
        self.assertEqual(response["sender_identity_source"]["source"], "context_generated")
        self.assertIn("ai_technology_hardware", response["project_documents"]["document_ids"])
        self.assertEqual(response["sender_identity"], "运营顾问")

    def test_sender_identity_can_be_resolved_from_account_product_or_override(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            account_response = build_chat_response(
                {
                    "question": "我有货源但是不会做跨境",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-account",
                    "account_id": "douyin_ai_ops",
                },
                logic=logic,
                project_store=store,
            )
            product_response = build_chat_response(
                {
                    "question": "这个能自动回复抖音评论吗",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-product",
                    "product_id": "cross_border_operation",
                },
                logic=logic,
                project_store=store,
            )
            override_response = build_chat_response(
                {
                    "question": "最近压力大睡不好",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-override",
                    "account_id": "douyin_ai_ops",
                    "sender_identity": "人工运营助理",
                },
                logic=logic,
                project_store=store,
            )

        self.assertEqual(account_response["sender_identity"], "运营顾问")
        self.assertEqual(account_response["sender_identity_source"]["source"], "account")
        self.assertEqual(product_response["sender_identity"], "跨境运营顾问")
        self.assertEqual(product_response["sender_identity_source"]["source"], "product")
        self.assertEqual(override_response["sender_identity"], "人工运营助理")
        self.assertEqual(override_response["sender_identity_source"]["source"], "api_override")

    def test_sender_identity_prompt_uses_only_industry_neutral_title(self):
        health_context = _format_sender_identity_context(
            "健康顾问助理",
            {"source": "account", "account_name": "健康抖音号"},
        )
        cross_border_context = _format_sender_identity_context(
            "跨境运营顾问",
            {"source": "product", "product_name": "跨境托管"},
        )

        self.assertIn("本轮私信对外只使用通用身份：助理。", health_context)
        self.assertIn("人员身份只用于内部业务路由和口吻约束", health_context)
        self.assertIn("首次私信可从简单问候、感谢关注、感谢留言或承接用户评论开始", health_context)
        self.assertNotIn("本轮私信对外只使用通用身份：健康顾问助理", health_context)
        self.assertIn("本轮私信对外只使用通用身份：顾问。", cross_border_context)
        self.assertNotIn("确需自我介绍", cross_border_context)
        self.assertNotIn("本轮私信对外只使用通用身份：跨境运营顾问", cross_border_context)

    def test_project_materials_can_be_read_and_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            response = build_project_materials_response(project_store=store)
            global_file = next(
                item for item in response["files"]
                if item["relative_path"] == "global_prompt.md"
            )

            saved = build_project_material_save_response(
                {
                    "project_id": response["project"]["project_id"],
                    "relative_path": global_file["relative_path"],
                    "content": "新的全局提示词",
                },
                project_store=store,
            )
            refreshed = build_project_materials_response(
                project_store=store,
                project_id=response["project"]["project_id"],
            )

        self.assertIn("新的全局提示词", saved["content"])
        self.assertTrue(any(
            item["relative_path"] == "global_prompt.md" and item["content"] == "新的全局提示词"
            for item in refreshed["files"]
        ))

    def test_project_can_be_created_from_page_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            response = build_project_create_response(
                {"name": "测试新项目"},
                project_store=store,
            )

        self.assertEqual(response["project"]["name"], "测试新项目")
        self.assertTrue(any(project["name"] == "测试新项目" for project in response["projects"]))
        self.assertTrue(any(file["relative_path"] == "global_prompt.md" for file in response["files"]))

    def test_project_route_debug_shows_scene_documents_and_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            response = build_project_route_debug_response(
                {
                    "question": "想做AI批量剪辑和自动发布，能采集评论吗",
                    "project_id": "",
                    "conversation_stage": "first_comment",
                },
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(response["project"]["scene_id"], "ai_hardware_presales")
        self.assertIn("ai_technology_hardware", response["project_documents"]["document_ids"])
        self.assertIn("AI技术与智能硬件板块", response["prompt"])
        self.assertIn("本轮私信对外只使用通用身份：顾问。", response["prompt"])
        self.assertIn("只返回 JSON", response["prompt"])
        self.assertEqual(response["sender_identity"], "运营顾问")
        module_keys = [module["key"] for module in response["prompt_modules"]]
        self.assertIn("user_context", module_keys)
        self.assertIn("sender_identity", module_keys)
        self.assertIn("scene_template", module_keys)
        self.assertIn("retrieved_knowledge", module_keys)
        self.assertIn("final_prompt", module_keys)

    def test_admin_state_and_knowledge_description_can_be_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            descriptions = state["document_descriptions"]
            first_doc = descriptions["domains"][0]["sections"][0]["documents"][0]
            self.assertNotEqual(first_doc["description"], first_doc["summary"])
            descriptions["domains"].append({
                "domain_id": "domain_test",
                "name": "测试领域",
                "sections": [{"section_id": "section_test", "name": "测试板块", "documents": []}],
            })
            saved = build_admin_knowledge_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "document_descriptions": descriptions,
                },
                project_store=store,
            )
            refreshed = build_admin_state_response(
                project_store=store,
                project_id=state["project"]["project_id"],
            )

        self.assertEqual(saved["document_descriptions"]["domains"][-1]["name"], "测试领域")
        self.assertTrue(any(domain["name"] == "测试领域" for domain in refreshed["document_descriptions"]["domains"]))

    def test_admin_account_settings_can_be_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            self.assertEqual(state["account_settings"]["accounts"], [])

            saved = build_admin_account_settings_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "account_settings": {
                        "accounts": [
                            {
                                "name": "运营号",
                                "platform": "抖音",
                                "homepage_url": "https://www.douyin.com/user/test",
                                "cookie": "sid_guard=abc; uid_tt=def",
                                "browser_name": "edge",
                            }
                        ]
                    },
                },
                project_store=store,
            )
            refreshed = build_admin_state_response(
                project_store=store,
                project_id=state["project"]["project_id"],
            )

        account = saved["account_settings"]["accounts"][0]
        self.assertTrue(account["account_id"].startswith("account_"))
        self.assertEqual(account["name"], "运营号")
        self.assertEqual(account["platform"], "抖音")
        self.assertEqual(account["homepage_url"], "https://www.douyin.com/user/test")
        self.assertEqual(account["cookie"], "sid_guard=abc; uid_tt=def")
        self.assertEqual(refreshed["account_settings"]["accounts"][0]["cookie"], "sid_guard=abc; uid_tt=def")

    def test_admin_scene_template_generate_save_and_prompt_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            generated = build_admin_scene_template_generate_response({
                "purpose": "把咨询AI自动剪辑的用户引导到体验入口"
            })
            saved = build_admin_scene_template_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "template": generated["template"],
                },
                project_store=store,
            )
            restored = build_admin_prompt_restore_response(
                {
                    "project_id": state["project"]["project_id"],
                    "question": "能自动剪辑视频和采集评论吗",
                    "video_overview": "视频展示了AI批量剪辑、自动发布和评论采集流程",
                    "context": "上一轮用户说想减少人工剪辑时间",
                    "template_id": saved["template"]["template_id"],
                    "conversation_stage": "first_comment",
                },
                project_store=store,
            )

        self.assertTrue(saved["scene_templates"]["templates"])
        module_keys = [module["key"] for module in restored["prompt_modules"]]
        self.assertEqual(
            module_keys,
            ["user_context", "global_prompt", "sender_identity", "scene_template", "activity_settings", "retrieved_knowledge", "final_prompt"],
        )
        self.assertTrue(restored["selector"]["document_ids"])
        self.assertEqual(restored["sender_identity"], "运营顾问")
        self.assertIn("用户评论", restored["final_prompt"])
        self.assertIn("视频概述", restored["final_prompt"])
        self.assertIn("自动发布和评论采集流程", restored["final_prompt"])
        self.assertIn("上一轮用户说想减少人工剪辑时间", restored["final_prompt"])
        self.assertEqual(restored["sender_identity_source"]["source"], "context_generated")
        self.assertIn("<user_context>", restored["final_prompt"])
        self.assertIn("<global_prompt>", restored["final_prompt"])
        self.assertIn("<sender_identity>", restored["final_prompt"])
        self.assertIn("本轮私信对外只使用通用身份：顾问。", restored["final_prompt"])
        self.assertIn("<scene_template>", restored["final_prompt"])
        self.assertIn("<activity_settings>", restored["final_prompt"])
        self.assertIn("<retrieved_knowledge>", restored["final_prompt"])
        self.assertIn("一句话明显过长时请主动换行", restored["final_prompt"])
        self.assertIn("行与行之间空一行", restored["final_prompt"])
        self.assertNotIn("<conversion_goal>", restored["final_prompt"])
        self.assertNotIn("You are a Douyin", restored["final_prompt"])

    def test_admin_activity_save_delete_and_prompt_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            saved = build_admin_activity_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "activity": {
                        "title": "睡眠状态免费初评活动",
                        "activity_type": "义诊",
                        "business_board_name": "大健康业务板块",
                        "business_module_name": "睡眠初评服务",
                        "applicable_scene": "用户评论睡不好、压力大、熬夜、入睡困难时使用。",
                        "description": "提供一次轻量睡眠状态初评。",
                        "benefit": "免费初评和睡眠自测表。",
                        "claim_method": "私信里先问是本人还是家人，再发送自测入口。",
                        "quota": "这批名额不多",
                        "deadline": "本周内",
                        "compliance_note": "不要承诺治疗失眠或保证效果。",
                        "tags": ["睡眠", "压力大", "初评", "义诊"],
                        "enabled": True,
                    },
                },
                project_store=store,
            )
            restored = build_admin_prompt_restore_response(
                {
                    "project_id": state["project"]["project_id"],
                    "question": "最近压力大，总是睡不好",
                    "video_overview": "视频讲的是压力和熬夜影响睡眠。",
                    "activity_id": saved["activity"]["activity_id"],
                },
                project_store=store,
            )
            deleted = build_admin_activity_delete_response(
                {
                    "project_id": state["project"]["project_id"],
                    "activity_id": saved["activity"]["activity_id"],
                },
                project_store=store,
            )

        self.assertEqual(saved["activity"]["title"], "睡眠状态免费初评活动")
        self.assertEqual(saved["activity"]["business_board_name"], "大健康业务板块")
        self.assertEqual(saved["activity"]["business_module_name"], "睡眠初评服务")
        self.assertEqual(restored["activity_settings"]["activity_type"], "义诊")
        self.assertEqual(restored["activity_settings"]["business_board_name"], "大健康业务板块")
        self.assertIn("咱们这边正好有", restored["final_prompt"])
        self.assertIn("睡眠状态免费初评活动", restored["final_prompt"])
        self.assertIn("免费初评和睡眠自测表", restored["final_prompt"])
        self.assertEqual(deleted["activity_settings"]["activities"], [])

    def test_admin_scene_template_generation_keeps_contact_target(self):
        generated = build_admin_scene_template_generate_response({
            "purpose": "根据用户评论留言，去抖音进行私信，最终引到微信号gokkeshoo"
        })
        template = generated["template"]
        combined = "\n".join([
            template["title"],
            template["purpose"],
            template["applicable_scene"],
            *template["steps"],
        ])

        self.assertIn("gokkeshoo", combined)
        self.assertNotIn("gokkeshoo、gokkeshoo", combined)
        self.assertTrue(any("gokkeshoo" in step for step in template["steps"]))
        self.assertIn("抖音", combined)
        self.assertIn("评论", combined)

    def test_admin_scene_template_generation_is_generic_not_first_dm_hardcoded(self):
        generated = build_admin_scene_template_generate_response({
            "purpose": "首次私信用户，期望回复"
        })
        template = generated["template"]
        steps = "\n".join(template["steps"])

        self.assertNotEqual(template["title"], "首次私信六步引导回复模板")
        self.assertNotIn("六步法", template["tags"])
        self.assertNotIn("附上草料码/名片", steps)
        self.assertIn("首次私信用户，期望回复", template["purpose"])

    def test_chat_uses_saved_scene_template_in_project_context(self):
        logic = FakeLogic()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            saved = build_admin_scene_template_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "template": {
                        "template_id": "tpl_first_dm_reply",
                        "title": "首次私信六步引导回复模板",
                        "purpose": "首次私信用户，期望回复",
                        "applicable_scene": "用户评论后第一次主动私信触达。",
                        "tags": ["首次私信", "评论承接", "六步法"],
                        "steps": [
                            "说明来意",
                            "打消顾虑",
                            "询问问题",
                            "解决方案",
                            "给甜头/钩子",
                            "附上草料码/名片",
                        ],
                    },
                },
                project_store=store,
            )
            response = build_chat_response(
                {
                    "question": "膝盖疼想了解一下",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-web",
                    "project_id": state["project"]["project_id"],
                    "conversation_stage": "first_comment",
                },
                logic=logic,
                project_store=store,
            )

        project_context = logic.calls[0]["project_context"]
        self.assertEqual(saved["template"]["template_id"], "tpl_first_dm_reply")
        self.assertEqual(response["scene_template"]["template_id"], "tpl_first_dm_reply")
        self.assertIn("<scene_template>", project_context)
        self.assertIn("首次私信六步引导回复模板", project_context)
        self.assertIn("附上草料码/名片", project_context)
        self.assertIn("高优先级约束", project_context)
        self.assertIn("不要用通用默认话术覆盖模板", project_context)

    def test_admin_open_file_location_uses_project_relative_file(self):
        opened = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            doc = state["document_descriptions"]["domains"][0]["sections"][0]["documents"][0]
            response = build_admin_open_file_location_response(
                {
                    "project_id": state["project"]["project_id"],
                    "relative_path": doc["relative_path"],
                },
                project_store=store,
                opener=opened.append,
            )

        self.assertTrue(response["opened"])
        self.assertEqual(response["relative_path"], doc["relative_path"])
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].name.endswith(".md"))

    def test_admin_open_file_location_can_open_knowledge_root_folder(self):
        opened = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            response = build_admin_open_file_location_response(
                {
                    "project_id": state["project"]["project_id"],
                    "relative_path": "knowledge/files",
                    "type": "folder",
                },
                project_store=store,
                opener=opened.append,
            )
            opened_path_is_dir = opened[0].is_dir()

        self.assertTrue(response["opened"])
        self.assertEqual(response["relative_path"], "knowledge/files")
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened_path_is_dir)

    def test_workspace_refresh_removes_manually_deleted_knowledge_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            uploaded = build_admin_knowledge_upload_response(
                file_name="manual_delete.txt",
                file_data="手动删除后不应继续显示".encode("utf-8"),
                project_id=state["project"]["project_id"],
                domain="测试领域",
                section="测试板块",
                project_store=store,
            )
            doc = uploaded["document"]
            Path(uploaded["absolute_path"]).unlink()

            workspace = build_workspace_state_response(project_store=store)
            chunks_path = Path(temp_dir) / state["project"]["project_id"] / "knowledge" / "chunks.jsonl"
            remaining_chunk_doc_ids = {
                json.loads(line).get("doc_id")
                for line in chunks_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }

        visible_doc_ids = [
            item.get("doc_id")
            for business in workspace["businesses"]
            for item in business.get("documents", [])
        ]
        self.assertNotIn(doc["doc_id"], visible_doc_ids)
        self.assertNotIn(doc["doc_id"], remaining_chunk_doc_ids)

    def test_workspace_state_exposes_editable_scene_templates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            saved = build_admin_scene_template_save_response(
                {
                    "project_id": project_id,
                    "template": {
                        "template_id": "tpl_first_dm_reply",
                        "title": "首次私信可编辑模板",
                        "purpose": "承接评论并引导回复",
                        "applicable_scene": "公开评论后的首次私信",
                        "tags": ["首次私信", "评论承接"],
                        "steps": ["承接评论", "给出下一步方向", "引导回复"],
                    },
                },
                project_store=store,
            )

            workspace = build_workspace_state_response(project_store=store)

        templates = workspace["scene_templates"]["templates"]
        self.assertEqual(workspace["project"]["project_id"], project_id)
        self.assertEqual(templates, saved["scene_templates"]["templates"])
        self.assertEqual(templates[0]["title"], "首次私信可编辑模板")

    def test_workspace_repairs_document_attached_to_wrong_knowledge_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            descriptions = state["document_descriptions"]
            source_base, source_domain, source_section, source_doc = next(
                (base, domain, section, section["documents"][0])
                for base in descriptions["knowledge_bases"]
                for domain in base.get("domains", [])
                for section in domain.get("sections", [])
                if section.get("documents")
            )
            descriptions["knowledge_bases"].append({
                "kb_id": "kb_wrong",
                "name": "错误知识库",
                "description": "错误挂载",
                "domains": [{
                    "domain_id": source_domain["domain_id"],
                    "name": source_domain["name"],
                    "sections": [{
                        "section_id": source_section["section_id"],
                        "name": source_section["name"],
                        "documents": [json.loads(json.dumps(source_doc, ensure_ascii=False))],
                    }],
                }],
            })
            description_path = Path(temp_dir) / project_id / "knowledge" / "document_descriptions.json"
            description_path.write_text(json.dumps(descriptions, ensure_ascii=False, indent=2), encoding="utf-8")

            workspace = build_workspace_state_response(project_store=store)
            saved = json.loads(description_path.read_text(encoding="utf-8"))

        visible_docs = [
            item
            for business in workspace["businesses"]
            for item in business.get("documents", [])
            if item.get("doc_id") == source_doc["doc_id"]
        ]
        self.assertEqual(len(visible_docs), 1)
        self.assertNotIn("错误知识库", [item["name"] for item in saved["knowledge_bases"]])

    def test_reuploading_same_file_moves_single_document_instead_of_copying(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            file_data = "同一个来源文件只能归属一个知识库目录。".encode("utf-8")

            first = build_admin_knowledge_upload_response(
                file_name="single-source.txt",
                file_data=file_data,
                project_id=project_id,
                knowledge_base="知识库甲",
                domain="模块甲",
                section="资料",
                project_store=store,
            )
            second = build_admin_knowledge_upload_response(
                file_name="single-source.txt",
                file_data=file_data,
                project_id=project_id,
                knowledge_base="知识库乙",
                domain="模块乙",
                section="资料",
                project_store=store,
            )
            workspace = build_workspace_state_response(project_store=store)
            first_path_exists = Path(first["absolute_path"]).exists()
            second_path_exists = Path(second["absolute_path"]).exists()

        matching_docs = [
            item
            for business in workspace["businesses"]
            for item in business.get("documents", [])
            if item.get("source_file_name") == "single-source.txt"
        ]
        self.assertEqual(first["document"]["doc_id"], second["document"]["doc_id"])
        self.assertFalse(first_path_exists)
        self.assertTrue(second_path_exists)
        self.assertEqual(len(matching_docs), 1)
        self.assertEqual(matching_docs[0]["knowledge_base"], "知识库乙")

    def test_admin_delete_knowledge_document_removes_file_and_indexes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            uploaded = build_admin_knowledge_upload_response(
                file_name="delete_me.txt",
                file_data="第一行资料\n第二行资料\n第三行资料".encode("utf-8"),
                project_id=state["project"]["project_id"],
                domain="测试领域",
                section="测试板块",
                project_store=store,
            )
            doc = uploaded["document"]
            target_path = Path(uploaded["absolute_path"])
            project_dir = Path(temp_dir) / state["project"]["project_id"]

            deleted = build_admin_knowledge_delete_response(
                {
                    "project_id": state["project"]["project_id"],
                    "doc_id": doc["doc_id"],
                    "relative_path": doc["relative_path"],
                },
                project_store=store,
            )
            refreshed = build_admin_state_response(
                project_store=store,
                project_id=state["project"]["project_id"],
            )
            manifest = store._read_json(project_dir / "knowledge" / "manifest.json")
            chunks_text = (project_dir / "knowledge" / "chunks.jsonl").read_text(encoding="utf-8")
            target_exists_after_delete = target_path.exists()

        self.assertTrue(deleted["deleted"]["file_deleted"])
        self.assertFalse(target_exists_after_delete)
        docs = [
            item
            for domain in refreshed["document_descriptions"]["domains"]
            for section in domain["sections"]
            for item in section["documents"]
        ]
        self.assertNotIn(doc["doc_id"], [item.get("doc_id") for item in docs])
        self.assertNotIn(doc["doc_id"], [item.get("doc_id") for item in manifest.get("documents", [])])
        self.assertNotIn(doc["doc_id"], chunks_text)

    def test_admin_upload_can_target_recruitment_knowledge_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)

            uploaded = build_admin_knowledge_upload_response(
                file_name="AI项目经理招聘说明.txt",
                file_data="岗位职责包括需求梳理、项目推进、AI工具落地，面试会了解过往项目经验。".encode("utf-8"),
                project_id=state["project"]["project_id"],
                knowledge_base="招聘知识库",
                domain="岗位资料",
                section="AI项目经理",
                project_store=store,
            )

            doc = uploaded["document"]
            refreshed = build_admin_state_response(
                project_store=store,
                project_id=state["project"]["project_id"],
            )
            flat_docs = [
                item
                for base in refreshed["document_descriptions"]["knowledge_bases"]
                for domain in base["domains"]
                for section in domain["sections"]
                for item in section["documents"]
            ]

        self.assertEqual(doc["knowledge_base"], "招聘知识库")
        self.assertTrue(doc["relative_path"].startswith("knowledge/files/招聘知识库/岗位资料/AI项目经理/"))
        self.assertTrue(any(item["doc_id"] == doc["doc_id"] for item in flat_docs))

    def test_workspace_keeps_new_empty_board_and_module_visible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]

            board_result = build_admin_knowledge_base_create_response(
                {
                    "project_id": project_id,
                    "name": "企业培训业务板块",
                    "description": "企业培训资料",
                },
                project_store=store,
            )
            module_result = build_admin_knowledge_domain_create_response(
                {
                    "project_id": project_id,
                    "knowledge_base": "企业培训业务板块",
                    "name": "客户交付业务模块",
                    "description": "客户交付资料",
                },
                project_store=store,
            )
            workspace = build_workspace_state_response(project_store=store)
            project_dir = Path(temp_dir) / project_id
            board_dir = project_dir / "knowledge" / "files" / "企业培训业务板块"
            module_dir = project_dir / "knowledge" / "files" / "企业培训业务板块" / "客户交付业务模块"
            board_dir_exists = board_dir.is_dir()
            module_dir_exists = module_dir.is_dir()

            with self.assertRaisesRegex(WebInputError, "domain name already exists"):
                build_admin_knowledge_domain_create_response(
                    {
                        "project_id": project_id,
                        "knowledge_base": "企业培训业务板块",
                        "name": "客户交付业务模块",
                    },
                    project_store=store,
                )

        board = next(
            item for item in workspace["business_boards"]
            if item["business_board_name"] == "企业培训业务板块"
        )
        business = next(
            item for item in workspace["businesses"]
            if item["business_module_name"] == "客户交付业务模块"
        )
        self.assertEqual(board_result["created"]["type"], "knowledge_base")
        self.assertEqual(module_result["created"]["type"], "domain")
        self.assertEqual(board["business_module_count"], 1)
        self.assertEqual(board["document_count"], 0)
        self.assertEqual(business["document_count"], 0)
        self.assertEqual(business["documents"], [])
        self.assertTrue(business["allow_empty_placeholder"])
        self.assertTrue(board_dir_exists)
        self.assertTrue(module_dir_exists)

    def test_workspace_maps_empty_filesystem_folders_back_to_board_and_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            section_dir = (
                Path(temp_dir)
                / project_id
                / "knowledge"
                / "files"
                / "空白公司资料板块"
                / "空白公司介绍模块"
                / "基础资料"
            )
            section_dir.mkdir(parents=True, exist_ok=True)

            workspace = build_workspace_state_response(project_store=store)
            descriptions = workspace["document_descriptions"]

        board = next(
            item for item in workspace["business_boards"]
            if item["business_board_name"] == "空白公司资料板块"
        )
        business = next(
            item for item in workspace["businesses"]
            if item["business_board_name"] == "空白公司资料板块"
            and item["business_module_name"] == "空白公司介绍模块"
        )
        base = next(item for item in descriptions["knowledge_bases"] if item["name"] == "空白公司资料板块")
        domain = next(item for item in base["domains"] if item["name"] == "空白公司介绍模块")
        section = next(item for item in domain["sections"] if item["name"] == "基础资料")
        relations = workspace["company_settings"]["company_knowledge_bases"]
        self.assertEqual(board["document_count"], 0)
        self.assertEqual(business["document_count"], 0)
        self.assertTrue(base["allow_empty_placeholder"])
        self.assertTrue(domain["allow_empty_placeholder"])
        self.assertTrue(section["allow_empty_placeholder"])
        self.assertTrue(any(item["kb_id"] == base["kb_id"] and item["enabled"] for item in relations))

    def test_deleting_last_document_preserves_physical_board_and_module_folders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            uploaded = build_admin_knowledge_upload_response(
                file_name="company-profile.txt",
                file_data="公司基础资料。".encode("utf-8"),
                project_id=project_id,
                knowledge_base="待清空资料板块",
                domain="待清空资料模块",
                section="基础资料",
                project_store=store,
            )
            document = uploaded["document"]
            build_admin_knowledge_delete_response(
                {
                    "project_id": project_id,
                    "doc_id": document["doc_id"],
                    "relative_path": document["relative_path"],
                },
                project_store=store,
            )
            workspace = build_workspace_state_response(project_store=store)
            module_dir = (
                Path(temp_dir)
                / project_id
                / "knowledge"
                / "files"
                / "待清空资料板块"
                / "待清空资料模块"
            )
            module_dir_exists = module_dir.is_dir()

        self.assertTrue(module_dir_exists)
        self.assertTrue(any(
            item["business_board_name"] == "待清空资料板块"
            and item["business_module_name"] == "待清空资料模块"
            and item["document_count"] == 0
            for item in workspace["businesses"]
        ))

    def test_workspace_removes_empty_module_mapping_when_physical_folder_is_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            build_admin_knowledge_base_create_response(
                {"project_id": project_id, "name": "目录映射板块"},
                project_store=store,
            )
            build_admin_knowledge_domain_create_response(
                {
                    "project_id": project_id,
                    "knowledge_base": "目录映射板块",
                    "name": "待删除目录模块",
                },
                project_store=store,
            )
            module_dir = (
                Path(temp_dir)
                / project_id
                / "knowledge"
                / "files"
                / "目录映射板块"
                / "待删除目录模块"
            )
            module_dir.rmdir()

            workspace = build_workspace_state_response(project_store=store)

        self.assertTrue(any(
            item["business_board_name"] == "目录映射板块"
            for item in workspace["business_boards"]
        ))
        self.assertFalse(any(
            item["business_module_name"] == "待删除目录模块"
            for item in workspace["businesses"]
        ))

    def test_admin_upload_auto_classifies_into_matching_empty_business_board(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            build_admin_knowledge_base_create_response(
                {
                    "project_id": project_id,
                    "name": "空气能热泵烘干机",
                    "description": "",
                },
                project_store=store,
            )

            uploaded = build_admin_knowledge_upload_response(
                file_name="空气热泵烘干设备产品介绍文档.txt",
                file_data=(
                    "空气热泵烘干设备采用闭环热风循环，适用于农产品和中药材烘干。"
                    "产品支持温湿度智能控制，并提供不同型号和规格参数。"
                ).encode("utf-8"),
                project_id=project_id,
                project_store=store,
            )
            workspace = build_workspace_state_response(project_store=store)

        document = uploaded["document"]
        board = next(
            item for item in workspace["business_boards"]
            if item["business_board_name"] == "空气能热泵烘干机"
        )
        business = next(
            item for item in workspace["businesses"]
            if item["business_board_name"] == "空气能热泵烘干机"
        )
        self.assertEqual(document["knowledge_base"], "空气能热泵烘干机")
        self.assertEqual(document["domain"], "产品资料")
        self.assertTrue(document["relative_path"].startswith("knowledge/files/空气能热泵烘干机/产品资料/"))
        self.assertEqual(uploaded["auto_classification"]["knowledge_base"], "空气能热泵烘干机")
        self.assertTrue(uploaded["auto_classification"]["created_default_module"])
        self.assertEqual(board["business_module_count"], 1)
        self.assertEqual(board["document_count"], 1)
        self.assertEqual(business["business_module_name"], "产品资料")
        self.assertEqual(business["documents"][0]["doc_id"], document["doc_id"])

    def test_admin_knowledge_route_update_moves_file_and_indexes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            project_id = state["project"]["project_id"]
            uploaded = build_admin_knowledge_upload_response(
                file_name="热泵产品说明.txt",
                file_data="热泵烘干机产品参数与适用场景。".encode("utf-8"),
                project_id=project_id,
                knowledge_base="公司基础资料板块",
                domain="AI技术与智能硬件",
                section="默认板块",
                project_store=store,
            )
            build_admin_knowledge_base_create_response(
                {"project_id": project_id, "name": "空气能热泵烘干机"},
                project_store=store,
            )
            old_path = Path(uploaded["absolute_path"])

            routed = build_admin_knowledge_route_update_response(
                {
                    "project_id": project_id,
                    "doc_id": uploaded["document"]["doc_id"],
                    "relative_path": uploaded["document"]["relative_path"],
                    "knowledge_base": "空气能热泵烘干机",
                    "domain": "产品资料",
                    "section": "默认板块",
                },
                project_store=store,
            )
            new_path = Path(temp_dir) / project_id / routed["document"]["relative_path"]
            new_path_exists = new_path.exists()
            manifest = store._read_json(Path(temp_dir) / project_id / "knowledge" / "manifest.json")
            chunks = (Path(temp_dir) / project_id / "knowledge" / "chunks.jsonl").read_text(encoding="utf-8")

        manifest_doc = next(
            item for item in manifest["documents"]
            if item["doc_id"] == routed["document"]["doc_id"]
        )
        self.assertTrue(routed["moved"])
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path_exists)
        self.assertEqual(routed["document"]["knowledge_base"], "空气能热泵烘干机")
        self.assertEqual(routed["document"]["domain"], "产品资料")
        self.assertEqual(manifest_doc["relative_path"], routed["document"]["relative_path"])
        self.assertIn(routed["document"]["relative_path"], chunks)

    def test_admin_prompt_restore_routes_recruitment_comment_to_recruitment_docs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)

            restored = build_admin_prompt_restore_response(
                {
                    "project_id": state["project"]["project_id"],
                    "question": "这个AI项目经理岗位薪资和面试流程怎么沟通",
                    "video_overview": "视频在介绍公司招聘AI项目经理岗位。",
                },
                project_store=store,
            )

        self.assertTrue(any(doc_id.startswith("recruitment_") for doc_id in restored["selector"]["document_ids"]))
        self.assertIn("招聘知识库", restored["final_prompt"])
        self.assertIn("本轮私信对外只使用通用身份：助理。", restored["final_prompt"])

    def test_private_followup_stage_is_passed_to_chat_logic(self):
        logic = FakeLogic()

        build_chat_response(
            {
                "question": "多少钱",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-web",
                "conversation_stage": "private_followup",
            },
            logic=logic,
        )

        self.assertEqual(logic.calls[0]["conversation_stage"], "private_followup")

    def test_conversion_goal_is_not_added_to_runtime_project_context(self):
        logic = FakeLogic()

        with tempfile.TemporaryDirectory() as temp_dir:
            build_chat_response(
                {
                    "question": "想了解一下",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-web",
                    "source_platform": "抖音",
                    "conversion_target": "微信号 wx_test_001",
                    "target_note": "先让用户回复，再自然发送资料入口",
                },
                logic=logic,
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        project_context = logic.calls[0]["project_context"]
        self.assertNotIn("<conversion_goal>", project_context)
        self.assertNotIn("当前触达平台: 抖音", project_context)
        self.assertNotIn("希望引导去向: 微信号 wx_test_001", project_context)
        self.assertIn("<retrieved_knowledge>", project_context)

    def test_chat_runtime_context_includes_selected_activity(self):
        logic = FakeLogic()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectMaterialStore(Path(temp_dir))
            state = build_admin_state_response(project_store=store)
            activity = build_admin_activity_save_response(
                {
                    "project_id": state["project"]["project_id"],
                    "activity": {
                        "title": "AI系统试跑体验名额",
                        "activity_type": "体验名额",
                        "applicable_scene": "用户咨询自动回复、评论采集、私信系统时使用。",
                        "benefit": "免费试跑一个视频评论批次。",
                        "claim_method": "先确认账号数量和评论量，再安排样例试跑。",
                        "tags": ["AI私信", "自动回复", "评论采集", "试跑"],
                        "enabled": True,
                    },
                },
                project_store=store,
            )["activity"]
            response = build_chat_response(
                {
                    "question": "这个能自动回复抖音评论吗",
                    "provider": "minimax",
                    "api_key": "page-key",
                    "session_id": "sid-web",
                    "project_id": state["project"]["project_id"],
                    "activity_id": activity["activity_id"],
                },
                logic=logic,
                project_store=store,
            )

        project_context = logic.calls[0]["project_context"]
        self.assertIn("<activity_settings>", project_context)
        self.assertIn("AI系统试跑体验名额", project_context)
        self.assertIn("免费试跑一个视频评论批次", project_context)
        self.assertEqual(response["activity_settings"]["activity_id"], activity["activity_id"])

    def test_minimax_requires_page_api_key(self):
        with self.assertRaises(WebInputError):
            build_chat_response(
                {
                    "question": "hello",
                    "provider": "minimax",
                    "session_id": "sid-web",
                },
                logic=FakeLogic(),
            )

    def test_deepseek_requires_page_api_key(self):
        with self.assertRaises(WebInputError):
            build_chat_response(
                {
                    "question": "hello",
                    "provider": "deepseek",
                    "session_id": "sid-web",
                },
                logic=FakeLogic(),
            )

    def test_stream_response_skips_raw_context_when_first_pass_has_enough_info(self):
        memory = FakeStreamMemory()
        llm = FakeStreamLLM(["hello", " world"])
        logic = FakeStreamLogic(
            {
                "answer": "draft",
                "enough_info": True,
                "missing_info": "",
                "used_knowledge": [],
            },
            memory,
            llm,
        )

        events = list(build_chat_stream_events(
            {
                "question": "hello",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-stream",
                "enable_knowledge": True,
                "topics": "topic-a",
            },
            logic=logic,
        ))

        self.assertFalse(memory.raw_called)
        self.assertEqual([event["text"] for event in events if event["type"] == "delta"], ["hello", " world"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["data"]["result"]["answer"], "hello world")
        self.assertIn("final_prompt", events[-1]["data"]["prompt_trace"])
        self.assertIn("knowledge content", events[-1]["data"]["prompt_trace"]["final_prompt"])
        self.assertEqual(memory.store_calls[0][3], "hello world")
        self.assertTrue(llm.calls[0]["stream"])
        self.assertIn("knowledge content", llm.calls[0]["prompt"])

    def test_stream_response_removes_chunked_first_message_identity_introduction(self):
        memory = FakeStreamMemory()
        llm = FakeStreamLLM(["您好，", "我是这边的", "助理，", "感谢您关注我们的账号。"])
        logic = FakeStreamLogic(
            {
                "answer": "draft",
                "enough_info": True,
                "missing_info": "",
                "used_knowledge": [],
            },
            memory,
            llm,
        )

        events = list(build_chat_stream_events(
            {
                "question": "想了解一下",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-stream-no-identity",
                "conversation_stage": "first_comment",
            },
            logic=logic,
        ))

        deltas = [event["text"] for event in events if event["type"] == "delta"]
        self.assertEqual(deltas, ["感谢您关注我们的账号。"])
        self.assertEqual(events[-1]["data"]["result"]["answer"], "感谢您关注我们的账号。")
        self.assertEqual(memory.store_calls[0][3], "感谢您关注我们的账号。")

    def test_stream_response_reads_raw_context_when_first_pass_lacks_info(self):
        memory = FakeStreamMemory()
        llm = FakeStreamLLM(["after raw"])
        logic = FakeStreamLogic(
            {
                "answer": "",
                "enough_info": False,
                "missing_info": "need prior details",
                "used_knowledge": [],
            },
            memory,
            llm,
        )

        events = list(build_chat_stream_events(
            {
                "question": "continue",
                "provider": "minimax",
                "api_key": "page-key",
                "session_id": "sid-stream",
            },
            logic=logic,
        ))

        self.assertTrue(memory.raw_called)
        self.assertEqual(events[-1]["data"]["result"]["answer"], "after raw")
        self.assertTrue(events[-1]["data"]["result"]["second_pass"])
        self.assertIn("raw context", llm.calls[0]["prompt"])
        self.assertIn("need prior details", llm.calls[0]["prompt"])

    def test_parse_uploaded_file_returns_text_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = parse_uploaded_file(
                "demo.txt",
                "第一行\n第二行".encode("utf-8"),
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(result["file_name"], "demo.txt")
        self.assertEqual(result["extension"], ".txt")
        self.assertEqual(result["content_chars"], len("第一行\n第二行"))
        self.assertEqual(result["line_count"], 2)
        self.assertIn("第二行", result["content"])

    def test_parse_uploaded_docx_uses_stdlib_fallback(self):
        docx_data = make_minimal_docx(["公司定位", "三大核心业务板块"])

        with tempfile.TemporaryDirectory() as temp_dir:
            result = parse_uploaded_file(
                "资料.docx",
                docx_data,
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(result["extension"], ".docx")
        self.assertIn("公司定位", result["content"])
        self.assertIn("三大核心业务板块", result["content"])

    def test_parse_uploaded_xlsx_uses_stdlib_fallback(self):
        xlsx_data = make_minimal_xlsx([["项目", "资料"], ["大健康", "关节健康"]])

        with tempfile.TemporaryDirectory() as temp_dir:
            result = parse_uploaded_file(
                "资料.xlsx",
                xlsx_data,
                project_store=ProjectMaterialStore(Path(temp_dir)),
            )

        self.assertEqual(result["extension"], ".xlsx")
        self.assertIn("表格解析方式：按数据行提取", result["content"])
        self.assertIn("### Sheet1 第 2 行", result["content"])
        self.assertIn("- 项目: 大健康", result["content"])
        self.assertIn("- 资料: 关节健康", result["content"])


def make_minimal_docx(paragraphs):
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {paragraphs}
  </w:body>
</w:document>""".format(
        paragraphs="\n".join(
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
        )
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def make_minimal_xlsx(rows):
    shared_strings = []
    string_indexes = {}

    def shared_index(value):
        if value not in string_indexes:
            string_indexes[value] = len(shared_strings)
            shared_strings.append(value)
        return string_indexes[value]

    row_xml = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(row):
            col = chr(ord("A") + col_index)
            cells.append(f'<c r="{col}{row_index}" t="s"><v>{shared_index(value)}</v></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(f"<si><t>{text}</t></si>" for text in shared_strings)
            + "</sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(row_xml)}</sheetData>'
            "</worksheet>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
