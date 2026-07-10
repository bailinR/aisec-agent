#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/17 14:06
# @Author  : GuJR
# @File    : chat.py
import csv
import io
import json
import logging
import re
import time
from datetime import datetime
from io import BytesIO
from os import path
from pathlib import Path
from typing import Any, Literal
from typing import List, Dict, Union
from typing import Optional

from aisec_agent.config import EXPORT_PATH
from aisec_agent.config import MANAGE_PROMPT_PATH, AIDED_LLM_CONF, MCP_SERVICES
from aisec_agent.logic._tools import LoadFileTools, LoadJsonTools
from aisec_agent.logic._tools import ToMarkdownTable
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.agent.agents import Agents, DecoTaskLogic
from aisec_agent.logic.agent.agents import QueryDataBaseLogic
from aisec_agent.logic.agent.duck_ananly import DataAnalysisAgent
from aisec_agent.logic.agent.report import ReportAgents
from aisec_agent.logic.agent.trans import TransAgents
from aisec_agent.logic.knowledge.logic import PreFileLogic
from aisec_agent.logic.mcp.logic import CallMCPLogic
from aisec_agent.logic.memory.tools import LongTermMemoryManager
from aisec_agent.model.agent_base import DatabaseQuery
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.define import ModelAnalyzer, SmartQueryGenerator
from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.llm_typing import MarkdownResponse, JSTaskModel, SectionsModel, HtmlResponse
from aisec_agent.model.llm_typing import SelectMCPModel, AnalysisOutput, StepSelectToolModel
from aisec_agent.model.mcp import MCPService
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.prompts import (
    MarkdownQuizPrompt,
    SelectMCPrompt,
    TaskPrompt,
    SummaryPrompt,
    StepSelectToolPrompt
)
from aisec_agent.model.prompts import PptListPrompt, PptReviewPrompt, PptPrompt, HtmlPrompt, \
    HtmlToPPTPrompt, HtmlTask, SectionContentPrompt
from aisec_agent.model.r import SELECT_TABLES
from aisec_agent.model.typing import SearchConfForm
from aisec_agent.tools.deep_search import DeepSearchTool

FileType = Literal['csv', 'xlsx', 'parquet', 'json']


class _AgentLogic(BaseHandler):
    """大模型聊天逻辑类"""

    def _report(self, query: str, agent_setting: Optional[Dict[str, Any]] = {},
                file_type: str = "pptlist", language="中文", content=""):
        dt = datetime.now().strftime("%Y-%m-%d")
        prompt = PptListPrompt(query, language, dt, content)
        _typing = MarkdownResponse
        key = "markdown"
        qs = ""
        match file_type:
            case "html":
                prompt = HtmlPrompt()
                qs = HtmlTask(content, query)
                key = "js"
            case "html_to_pptx":
                prompt = HtmlToPPTPrompt(query)
                _typing = JSTaskModel
                key = "js"
            case "section":
                prompt = SectionContentPrompt(query, dt, language)
                _typing = SectionsModel
                key = "data"
            case "ppt_review":
                prompt = PptReviewPrompt(query)
            case "ppt_html":
                prompt = PptPrompt(query, dt, "")
                _typing = HtmlResponse
                key = "html"

        return aided_chat(
            prompt=prompt, _typing=_typing, question=qs, **agent_setting,
        )[key]


    def _init(self):
        self.agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)
        self.agents = Agents()
        self.db_query = DatabaseQuery()
        self.model_analyzer = ModelAnalyzer()
        self.model_info = self.model_analyzer.get_all_models()
        self.query_generator = SmartQueryGenerator(self.model_info)
        self.minio_client = MinioClient()
        self.bucket = "agents"
        self.minio_client.ensure_bucket_exists(self.bucket, is_public=True)
        self.report_agent = ReportAgents()

    # ================= 私有方法封装 =================
    def aided_chat(self, question: str, prompt: str, options: dict = None) -> dict:
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        text_result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt,
                           message=question, model=AIDED_LLM_CONF["model_name"], options=options,
                           stream=False, max_len_input=AIDED_LLM_CONF["max_len_input"])
        result_dict = LoadJsonTools().process_llm_response(text_result, result_type="dict")
        return result_dict

    @staticmethod
    def _detect_file_type(path: Union[str, Path]) -> FileType:
        ext = Path(path).suffix.lower()
        if ext == '.csv':
            return 'csv'
        if ext in ('.xls', '.xlsx'):
            return 'xlsx'
        if ext == '.parquet':
            return 'parquet'
        if ext == '.json':
            return 'json'
        raise f"不支持的文件类型: {ext}"

    def _fmt_duck(self, filepath: List[dict]):
        buck = ""
        sources = []
        for row in filepath:
            if buck == "":
                buck = row["bucket"]
            sources.append({"object_name": row["path"], 'format': self._detect_file_type(row["path"])})
        return buck, sources

    def _append_memory(self, prompt: str, uid: str, sid: str, question: str, limit: int = 20) -> str:
        search_results = LongTermMemoryManager().search_memories(user_id=uid, session_id=sid, query=question,
                                                                 limit=limit)
        time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory = "\n".join([f"{id}.{result['user_question_summary']}" for id, result in enumerate(search_results, 1)])
        return f"历史对话记忆: {memory}\n\n当前对话的sessionID: {sid}\n\n当前时间: {time_now}\n\n" + prompt

    def _inject_mcp_results(self, question: str) -> dict:
        tools = CallMCPLogic().get_tools(MCP_SERVICES)
        tool_dict = aided_chat(SelectMCPrompt(tools), question, SelectMCPModel)
        if tool_dict["tool_name"]:
            res = CallMCPLogic().call_tool(question, MCP_SERVICES, tool_dict["tool_name"])
            if res:
                return {"title": tool_dict["tool_name"], "sql_data": res,
                        'sql': f"调用业务工具:{tool_dict['tool_name']}"}
        return {}

    def _handle_deep_search(self, query, uid, sid, max_loop: int = 1, agent_extra={}, ):

        deep_search = []
        idx = 0
        for row in self.report_agent.deep_search(
                **{
                    "request_id": f"req_{uid}{sid}",
                    "query": query,
                    "max_loop": max_loop,
                }
        ):
            idx += 1
            deep_search.append(row)
            yield {"id": f"search_{idx}", "title": "搜索结果", "content": row, "contentType": "deepsearch"}

    def _handler_report(self, query, file_type: str = "pptlist", language="中文", agent_extra={}, content: str = ""):

        _typ = "ppt_content"
        idx = 0
        _md = self._report(
            query=query, file_type="pptlist", language=language, content=content, agent_setting=agent_extra
        )
        md = self._report(
            query=_md, file_type="ppt_review", language=language, content=content,
        )
        name = f"大纲_{time.time()}"
        content_bytes = BytesIO(md.encode('utf-8'))
        self.minio_client.upload_object(self.bucket, name, content_bytes, len(md))
        

        yield {"id": f"search_{idx}", "title": f"{query}_大纲", "content": md, "minio": [{"path": name}],
               "contentType": _typ}

        idx += 1
        if file_type == "ppt":
            ppt_html = self._report(
                query=md, file_type="ppt_html", language=language, content=content, agent_setting=agent_extra
            )
            name = f"html_{time.time()}"
            self.minio_client.upload_object(self.bucket, name, BytesIO(ppt_html.encode('utf-8')), len(ppt_html))
            idx += 1
            yield {"id": f"search_{idx}", "title": f"{query}_html", "content": ppt_html, "minio": [{"path": name}],
                   "contentType": "ppt_html"}

            html_to_pptx = self._report(
                ppt_html, file_type="html_to_pptx", agent_setting=agent_extra
            )
            idx +=1

            name = f"ppt_js_{time.time()}"
            self.minio_client.upload_object(self.bucket, name, BytesIO(html_to_pptx.encode('utf-8')), len(html_to_pptx))
            yield {"id": f"search_{idx}", "title": f"{query}_js", "content":  html_to_pptx, "minio": [{"path": name}],
                   "contentType": "ppt_js"}
        else:
            html = self._report(
                query=md, file_type="html", language=language, content=content, agent_setting=agent_extra
            )
            name = f"html_{time.time()}"
            self.minio_client.upload_object(self.bucket, name, BytesIO(html.encode('utf-8')), len(html))
            idx += 1
            yield {"id": f"search_{idx}", "title": f"{query}_html", "content": html, "minio": [{"path": name}],
                   "contentType": "ppt_html"}





    def _handler_ppt_json(self, query, uid, sid, language="中文"):
        pl = self.report_agent.ppt_steam_req(**{"language": language,
                                                "request_id": f"req_{uid}{sid}", "query": query})
        idx = 0
        for row in self.report_agent.send_stream_request(self.report_agent.ppt_json_url, pl, ):
            yield {"id": f"search_{idx}", "title": "ppt大纲", "content": row["data"], "contentType": "ppt_json"}
            idx += 1

    def _handle_trans_mode(self, question: str, file_path: List[dict], trans: str, uid: str, agent_extra: dict = {}):
        agent = TransAgents()
        idx = 0

        if not file_path:
            for row in question.split("\n"):
                if row in [None, "\n", "\t", "\r", ""]:
                    yield {"id": f"trans_{idx}", "title": "翻译结果", "content": "\n",
                           "original": row, "remark": "", "contentType": "trans"}
                else:
                    p = agent.process_string(row, trans)
                    res = agent.trans("完整处理我翻译内容:", p, **agent_extra)
                    yield {"id": f"trans_{idx}", "title": "翻译结果", "content": res["translation"],
                           "original": p["cleaned_text"], "remark": res["remark"], "contentType": "trans"}
                idx += 1
        else:
            for row in file_path:
                entries = agent.pre_file(self.bucket, row["path"], row.get("name", row["path"]), language=trans)
                idx = 0
                for entry in entries:

                    if entry["cleaned_text"] in [None, "\n", "\t", "\r", ""]:
                        yield {"id": f"trans_{idx}", "title": "翻译结果", "content": "\n",
                               "original": "", "remark": "", "contentType": "trans"}
                    else:
                        res = agent.trans("", entry, **agent_extra)
                        yield {
                            "id": f"trans_{idx}",
                            "title": "文档翻译", "content": res["translation"],
                            "original": entry["cleaned_text"], "contentType": "trans"
                        }
                    idx += 1

        time.sleep(1)

    def _process_stream(self, chat_func, **kwargs):
        for chunk in chat_func(**kwargs):
            yield chunk

    def _handle_error(self, e: Exception, stream: bool):
        logging.error(f"agent 调用失败: {str(e)}")
        if not stream:
            return {"message": {"content": f"处理请求时出错: {str(e)}"}}
        raise

    def process_type_queries(self, type_data: List[Dict]) -> Dict[str, Any]:
        """
        处理特定类型的查询数据

        Args:
            type_data: 要处理的数据列表

        Returns:
            Dict[str, Any]: 处理结果字典
        """
        return {
            data["key"]: self.agents.smart_query(data["description"], SELECT_TABLES).get("smart_results", "")
            for data in type_data
        }

    @staticmethod
    def json_to_csv(data: Union[List[Dict], Dict], file_path: str = None) -> str:
        if isinstance(data, dict):
            data = [data]
        if not data:
            raise ValueError("输入数据为空")

        # 按出现顺序收集字段
        headers, seen = [], set()
        for item in data:
            for k in item.keys():
                if k not in seen:
                    seen.add(k)
                    headers.append(k)

        def _norm(v):
            if v is None or isinstance(v, (str, int, float, bool)):
                return "" if v is None else v
            return json.dumps(v, ensure_ascii=False)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        for row in data:
            writer.writerow({k: _norm(row.get(k)) for k in headers})

        csv_content = output.getvalue()
        output.close()
        if file_path:
            with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_content)
            return file_path
        return csv_content

    def _get_quiz(self, idx, question, file_path, obj, query_data, typ="sql_result"):

        quiz = aided_chat(MarkdownQuizPrompt(ToMarkdownTable().json_to_markdown_table(query_data)), "", AnalysisOutput)
        if quiz.get('quiz'):
            rq = quiz['quiz'][0]['question']
            if len(quiz['quiz']) >= 1:
                wq = "例如：" + quiz['quiz'][1]['question']
            else:
                wq = "请针对数据中心表字段描述重新提问"
        return {
            "id": idx,
            "title": "请确认生成的结果数据分析结果",
            "content": AnalysisOutput(**quiz).to_markdown(),
            "right_question": f"比如：{rq}",
            "old_question": question,
            "wrong_question": wq,
            "remark": "",
            "minio": obj,
            "old_file": file_path,
            "contentType": typ
        }


class ChatAgentLogicHandler(_AgentLogic):

    def _source_data_sync(self, query_data, idx, minio_list, question, file_path):
        for idx, outcome in enumerate(query_data):
            # 生成 CSV 到本地
            p = path.join(EXPORT_PATH, f"sql_{idx}.csv")
            _m = {}
            if outcome["sql_data"]:
                self.json_to_csv(outcome["sql_data"], p)
                # 统一时间戳（只算一次）
                ts = int(time.time())
                name = f"result_sql_{idx}_{ts}.csv"

                # 上传 & 取链接
                self.minio_client.upload_file(self.bucket, name, p)
                # url = self.minio_client.get_sign_url(self.bucket, name)
                _m = {"path": name}
                minio_list.append({"path": name})
            md_table = ToMarkdownTable().json_to_markdown_table(outcome["sql_data"])
            content = f"## 数据查询语句:\n{outcome['sql']}\n\n## 数据查询结果:\n{md_table}\n\n"
            yield {
                "id": f"sql_{idx}",
                "title": f"{outcome['title']}",
                "content": content,
                "original": "",
                "remark": "",
                "minio": _m,
                "contentType": "sql_result"
            }
            idx += 1
            time.sleep(0.1)
        time.sleep(0.1)
        yield self._get_quiz(f"sql_{idx}", question, file_path, minio_list, query_data, typ="sql_confirm", )

    def _append_context_sections_stream(self, question: str, trans: str, file_path: List[dict], source: bool, uid: str,
                                        duck: bool, agent_extra: dict = {}, ):

        """
        流式输出上下文内容（yield）
        """
        # 如果指定了 source，则先跑数据库查询逻辑
        if source:
            idx = 0
            minio_list = []
            query_data = [self._inject_mcp_results(question), ]
            if query_data[0]:
                yield from self._source_data_sync(query_data, idx, minio_list, question, file_path)
                return
            for nb in range(1, 3):
                query_data = QueryDataBaseLogic().query_data(question, agent_extra)
                if query_data:
                    break
                yield {
                    "id": f"sql_{nb}",
                    "title": f"",
                    "content": f"第{nb}次查询结果为空, 继续重试",
                    "original": "",
                    "remark": "",
                    "contentType": "chat"
                }
                time.sleep(0.1)

                # 如果没有结果，友好返回，直接退出
            if not query_data:
                yield {
                    "id": f"sql_3",
                    "title": f"数据查询结果为空",
                    "content": "## 数据查询结果为空\n已重试3次\n**结束会话**\n请检查数据中心是否存在符合问题的业务数据",
                    "original": "",
                    "remark": "",
                    "contentType": "chat"
                }
                time.sleep(0.1)
                return
            yield from self._source_data_sync(query_data, idx, minio_list, question, file_path)
            return

        # 翻译模式
        if trans:
            yield from self._handle_trans_mode(question, file_path=file_path, trans=trans, uid=uid,
                                               agent_extra=agent_extra or {})
            return

        if file_path and duck:
            duck_bucket, duck_sources = self._fmt_duck(file_path)
            agent = DataAnalysisAgent()
            self.logger.info(f"[duck] start streaming: {duck_bucket} - {duck_sources}")

            # Step A: 预览
            req = agent.setup_data_source(bucket=duck_bucket, sources=duck_sources)
            preview = agent.get_preview(req)

            # 将预览转简表：每张表只展示列头，避免大表撑爆
            def _preview_md(pv: dict) -> str:
                data = pv.get("data", {}) if isinstance(pv, dict) else {}
                rows = []
                for t, trows in data.items():
                    if not trows:
                        continue
                    cols = trows[0]
                    rows.append({"table": t, "columns": ", ".join(cols)})
                return ToMarkdownTable().json_to_markdown_table(rows) if rows else "无预览/空数据"

            yield {
                "id": "duck_0",
                "title": "数据预览",
                "content": _preview_md(preview) if preview else "无法获取预览（数据源可能不存在或不可读）",
                "original": "",
                "remark": "",
                "minio": file_path,
                "contentType": "duck_result",
            }

            if not preview:
                # 预览失败直接结束
                return

            # Step B: 生成 SQL（LLM）
            sql = agent.generate_sql(question, preview, **agent_extra) or ""
            yield {
                "id": "duck_1",
                "title": "查询 SQL",
                "content": sql if sql.strip() else "（未生成到有效 SQL）",
                "original": "",
                "remark": "",
                "minio": file_path,
                "contentType": "duck_result",
            }
            if not sql.strip():
                return  # 无 SQL 不继续

            # Step C: 执行 SQL
            name = f"{duck_bucket}_duck_{int(time.time())}.csv"
            result = agent.execute_sql(req, sql, name) or {}
            rows = result.get("data", {}).get("data") or []
            url = result.get("data", {}).get("url")
            obj = [{"path": name, }] if url else []

            rows_md = ToMarkdownTable().json_to_markdown_table(rows) if rows else "查询无结果"
            yield {
                "id": "duck_2",
                "title": "筛选结果",
                "content": rows_md,
                "remark": "",
                "minio": obj,
                "contentType": "duck_result",
            }
            if not rows:
                return  # 没有数据就不生成确认卡片
            yield self._get_quiz("duck_3", question, file_path, obj, rows_md, typ="duck_confirm", )

            return

    def _concat_result(self, prompt: str, question: str, history: list, trans: str,
                              file_path: List[dict], duck: bool, source: bool):
        """
        返回拼接好的上下文结果，不做 yield
        """
        cite = ""
        if source:
            return prompt, cite, True

        # 历史记录
        if history:
            prompt += "<history>\n"
            for nb, text in enumerate(history, 1):
                prompt += f"<history_{nb}>\n{text}\n</history_{nb}>\n\n"
            prompt += "</history>\n"

        # 翻译模式
        if trans:
            return prompt, cite, True

        if duck and file_path:
            return prompt, cite, True

        return prompt, cite, False

    def select_mcp_tool(self, user_question: str, steps: list, step: dict, prompt: str, cite: str, mcp_tools: dict):
        prompt = StepSelectToolPrompt(user_question, steps, step, prompt, cite, mcp_tools)
        tools = aided_chat(prompt, step["step"], StepSelectToolModel)["tools"]
        return tools

    def agent_chat(self, question: str, stream: bool, agent_id: str, aid: str, topics: list, url: str, key: str,
                   uid: str, sid: str, function_name: str, model_name: str, agent_func: str, padding_json: list,
                   max_len_input: int, file_name: str, file_content: str, file_path: List[dict], history: list,
                   imgs: list, duck: bool, trans: str, source=False, prompt: str = "", deep_search: bool = False,
                   report_typ: str = "", **kwargs):
        try:
            agent_dict = self.agents_dict[agent_id]
            prompt = f"prompt: {prompt}\n\n"
            prompt += f"""
            * 当提供的资料不足以回答用户的问题时，你可以像用户提问，要求补全信息
            * 用户的问题模棱两可时，可以向用户确认方向，询问更具体的问题
            * 不要胡编乱造, 不要胡编乱造, 不要胡编乱造
            * 不要生成```markdown ```、```html ```这种格式,不需要输出这个格式
            """
            with MCPService() as mcp:
                mcp_tools = mcp.list_tools()

            # Topics 知识库（这里只加到 prompt，不 yield）
            if topics:
                search_conf = SearchConfForm().from_orm({"top_k": 20, "expansion_factor": 5})
                knowledge_datas = PreFileLogic().search_knowledge(question, topics, search_conf)
                if knowledge_datas:
                    knowledge_data = "\n\n".join(
                        [data["file_name"] + "(**相似度:" + str(data["similarity"]) + "**):\n"
                         + data["content"] for data in knowledge_datas])
                    _knowledge_data = "\n\n".join(
                        [data["file_name"] + ":\n" + data["content"] for data in knowledge_datas])
                    prompt += f"<文档内容>\n{_knowledge_data}</文档内容>\n\n"
                    yield {"id": f"knowledge_1", "title": "阅读知识库", "content": knowledge_data,
                           "contentType": "knowledge"}

            # 任务拆解
            chat = getattr(LLMChatTools(), function_name)
            steps = DecoTaskLogic().deco_task(prompt, question, mcp_tools)
            if isinstance(steps, dict):
                steps = steps["steps"]
            elif not steps:
                steps = [{'format': 'text', 'remark': '', 'step': f'{question}'}]

            accumulated_content = ""
            chunk_type = ""
            agent_extra = {
                "url": url,
                "key": key,
                "func_name": function_name,
                "model_name": model_name,
                "max_len_input": max_len_input
            }

            # 先处理流式输出部分
            yield from self._append_context_sections_stream(question, trans=trans, file_path=file_path, source=source,
                                                            uid=uid, duck=duck, agent_extra=agent_extra or {}, )

            # 再获取最终上下文结果
            prompt, cite, done = self._concat_result(prompt, question, history, trans, file_path, duck, source)

            if done:
                return

            search_result_show = ""
            prompt = self._append_memory(prompt, uid + aid, sid, question) # 长记忆
            if deep_search:
                search_result, search_result_show = DeepSearchTool().search_logic(prompt, question)
                prompt += f"\n<联网搜索结果>\n{search_result}\n</联网搜索结果>\n"
                yield search_result_show

            if report_typ:
                yield from self._handler_report(
                    question, agent_extra=agent_extra or {}, content=search_result_show or "", file_type=report_typ,
                    language=trans or "中文")

            # 按步骤执行
            if len(steps) == 1:
                select_tool = CallMCPLogic().call_tool(steps[0], MCP_SERVICES)
                mcp_result = ""
                if "isError" in select_tool["result"] and not select_tool["result"]['isError']:
                    mcp_result += f"<工具调用结果>\n"
                    mcp_result += str(CallMCPLogic().call_tool(steps[0], MCP_SERVICES))
                    mcp_result += f"\n</工具调用结果>\n\n"
                step_prompt = TaskPrompt(question, steps, steps[0], mcp_result, cite)
                prompt += f"""{step_prompt}"""
                for response_chunk in chat(url=url, api_key=key, prompt=prompt, message=steps[0]["step"],
                                           model=model_name,
                                           stream=True, options=agent_dict["options"], max_len_input=max_len_input):
                    chunk = response_chunk.replace(r'\"', '"').replace("```makedown", "")
                    if chunk:
                        if chunk == "<think>":
                            chunk_type = "think"
                            continue
                        if chunk == "</think>":
                            chunk_type = "chat"
                            continue
                        if chunk_type == "think" and ("<" in chunk or ">" in chunk):
                            chunk = response_chunk.replace('>', '').replace('<', '')
                        if chunk_type != "think":
                            accumulated_content += chunk
                        yield {"id": f"{chunk_type}_1", "title": steps[0]["step"], "content": chunk,
                               "contentType": chunk_type}
                LongTermMemoryManager().store_conversation(uid + aid, sid, question, accumulated_content)

            elif len(steps) < 1:
                for response_chunk in chat(url=url, api_key=key, prompt=prompt, message=question, model=model_name,
                                           stream=True, options=agent_dict["options"], max_len_input=max_len_input):
                    chunk = response_chunk.replace(r'\"', '"').replace("```markdown", "")
                    if chunk:
                        if chunk == "<think>":
                            chunk_type = "think"
                            continue
                        if chunk == "</think>":
                            chunk_type = "chat"
                            continue
                        if chunk_type == "think" and ("<" in chunk or ">" in chunk):
                            chunk = response_chunk.replace('>', '').replace('<', '')
                        if chunk_type != "think":
                            accumulated_content += chunk
                        yield {"id": f"{chunk_type}_1", "title": question, "content": chunk, "contentType": chunk_type}
                LongTermMemoryManager().store_conversation(uid + aid, sid, question, accumulated_content)

            else:
                for idx, step in enumerate(steps, 1):
                    text = ""
                    mcp_result = ""
                    select_tool = CallMCPLogic().call_tool(step, MCP_SERVICES)
                    if select_tool["result"] and not select_tool["result"]['isError']:
                        mcp_result += f"<工具调用结果>\n"
                        mcp_result += str(CallMCPLogic().call_tool(step, MCP_SERVICES))
                        mcp_result += f"\n</工具调用结果>\n\n"
                    step_prompt = TaskPrompt(question, steps, step, mcp_result, cite)
                    steps_prompt = f"""<步骤{step}>\n{step_prompt}\n</步骤{step}>\n\n""" + prompt
                    for response_chunk in chat(url=url, api_key=key, prompt=steps_prompt, message=step["step"],
                                               model=model_name,
                                               stream=True, options=agent_dict["options"], max_len_input=max_len_input):
                        chunk = response_chunk.replace(r'\"', '"').replace("```markdown", "")
                        text += chunk
                        if chunk:
                            if chunk == "<think>":
                                chunk_type = "think"
                                continue
                            if chunk == "</think>":
                                chunk_type = step["format"]
                                continue
                            if chunk_type == "think" and ("<" in chunk or ">" in chunk):
                                chunk = response_chunk.replace('>', '').replace('<', '')
                            yield {"id": f"{chunk_type}_{idx}", "title": step["step"], "content": chunk,
                                   "contentType": chunk_type}
                    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
                    cite += f"step{idx} outcome:" + text + "\n\n"
                sum_prompt = SummaryPrompt(cite)
                prompt += f"""<总结要求>\n{sum_prompt}\n</总结要求>\n\n"""
                for response_chunk in chat(url=url, api_key=key, prompt=prompt, message=question, model=model_name,
                                           stream=True, options=agent_dict["options"], max_len_input=max_len_input):
                    chunk = response_chunk.replace(r'\"', '"').replace("```markdown", "")
                    if chunk:
                        if chunk == "<think>":
                            chunk_type = "think"
                            continue
                        if chunk == "</think>":
                            chunk_type = "chat"
                            continue
                        if chunk_type == "think" and ("<" in chunk or ">" in chunk):
                            chunk = response_chunk.replace('>', '').replace('<', '')
                        if chunk_type != "think":
                            accumulated_content += chunk
                        yield {"id": f"{chunk_type}_{str(len(steps) + 1)}", "title": "总结", "content": chunk,
                               "contentType": chunk_type}
                LongTermMemoryManager().store_conversation(uid + aid, sid, question, accumulated_content)

        except Exception as e:
            return self._handle_error(e, stream)
