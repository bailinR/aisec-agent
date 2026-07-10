#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/17 14:06
# @Author  : GuJR
# @Site    : 
# @File    : chat.py
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from typing import Literal, Union

from aisec_agent.config import MANAGE_PROMPT_PATH, PROMPT_FILE_PATH, BASIS_FILE_PATH, AIDED_LLM_CONF, MCP_SERVICES
from aisec_agent.logic._tools import LoadFileTools, LoadJsonTools, aided_chat
from aisec_agent.logic.agent.agents import Agents
from aisec_agent.logic.agent.trans import TransAgents
from aisec_agent.logic.knowledge.logic import PreFileLogic
from aisec_agent.logic.mcp.logic import CallMCPLogic
from aisec_agent.logic.memory.tools import LongTermMemoryManager
from aisec_agent.model.agent_base import DatabaseQuery
from aisec_agent.model.define import session_ctx, BaseHandler, ModelAnalyzer, SmartQueryGenerator
from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.llm_typing import SelectMCPModel
from aisec_agent.model.model import LLMConfigModel, AIReportTemplateModel
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.prompts import padding_report_prompt, analysis_question, SelectMCPrompt, DuckDBPrompt
from aisec_agent.model.r import classify_data, SELECT_TABLES
from aisec_agent.model.schema import LLMConfigGetSchema
from aisec_agent.model.typing import Ret, LLMConfigEditForm, LLMConfigAddForm

FileType = Literal['csv', 'xlsx', 'parquet', 'json']


class ChatAgentLogic(BaseHandler):  # 大模型聊天逻辑类

    def _init(self):
        self.agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)
        self.agents = Agents()
        self.db_query = DatabaseQuery()
        self.model_analyzer = ModelAnalyzer()
        self.model_info = self.model_analyzer.get_all_models()
        self.query_generator = SmartQueryGenerator(self.model_info)
        self.minio_client = MinioClient()

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

    def aided_chat(self, question: str, prompt: str, options: dict = None) -> dict:
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        text_result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt,
                           message=question, model=AIDED_LLM_CONF["model_name"], options=options,
                           stream=False, max_len_input=AIDED_LLM_CONF["max_len_input"])
        result_dict = LoadJsonTools().process_llm_response(text_result, result_type="dict")
        return result_dict

    def clean_history_content(self, text: str) -> str:
        """
        清理历史记录中的特殊标签内容

        Args:
            text: 需要清理的文本

        Returns:
            str: 清理后的文本
        """
        text = re.sub(r'<think>.*?</think>\n?\n?', '', text, flags=re.DOTALL)
        text = re.sub(r'<AgentDatabase>.*?</AgentDatabase>\n?\n?', '', text, flags=re.DOTALL)
        text = re.sub(r'<KnowDatabase>.*?</KnowDatabase>\n?\n?', '', text, flags=re.DOTALL)
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        return text.strip()

    @staticmethod
    def detect_file_type(path: Union[str, Path]) -> FileType:
        """
        根据文件后缀判断文件类型。

        Args:
            path: 文件路径或文件名

        Returns:
            'csv' | 'xlsx' | 'parquet' | 'json' | 'unknown'
        """
        ext = Path(path).suffix.lower()
        if ext == '.csv':
            return 'csv'
        if ext in ('.xls', '.xlsx'):
            return 'xlsx'
        if ext == '.parquet':
            return 'parquet'
        if ext == '.json':
            return 'json'
        raise ModelAnalyzer.FileTypeError(f"不支持的文件类型: {ext}")

    def fmt_duck(self, filepath):
        buck = ""
        sources = []
        for row in filepath:
            if buck == "":
                buck = row["bucket"]
            sources.append(
                {"object_name": row["path"], 'format': self.detect_file_type(row["path"])}
            )
        return buck, sources

    def _agents_keywards(self, agent_extra: dict):
        return {
            k: v for k, v in agent_extra.items()
            if k in ["url", "key", "max_len_input", "model_name", "func_name", "model_name"] and v
        }

    def agent_completion(self, question: str, stream: bool, agent_id: str, topics: list, url: str, key: str,
                         function_name: str, model_name: str, agent_func: str, padding_json: list, max_len_input: int,
                         file_name: str, file_content: str, uid: str, sid: str, aid: str, file_path: List[dict],
                         history: list, imgs: list, duck: bool, trans: bool, source: bool, prompt: str = None,
                         agent_extra: dict = None, **kwargs):
        """调用agent返回"""
        try:
            agent_dict = self.agents_dict[agent_id]
            question_name = question + "\n" + file_name if file_name else question

            if not prompt:
                prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agent_dict["file_name"], "prompt")
            prompt = f"""prompt: {prompt}\n\n"""
            prompt += f"""
            * 当提供的资料不足以回答用户的问题时，你可以像用户提问，要求补全信息
            * 用户的问题模棱两可时，可以向用户确认方向，询问更具体的问题
            * 不要胡编乱造, 不要胡编乱造, 不要胡编乱造
            * 不要生成```markdown ```、```html ```这种格式,不需要输出这个格式
            """

            if duck and file_path:
                duck_bucket, duck_file, = self.fmt_duck(file_path)
                from aisec_agent.logic.agent.duck_ananly import DataAnalysisAgent
                agent = DataAnalysisAgent()
                self.logger.info(f"开始处理数据 {duck_bucket}-{duck_file}")
                cite = agent.run(bucket=duck_bucket, sources=duck_file, user_need=question,
                                 agent_extra=self._agents_keywards(agent_extra))
                self.logger.info(f"处理数据完成 {cite}")
                if not cite:
                    return {"message": {"content": "获取数据为空，请检查是否上传完毕"}}
                #   return {"message": {"content": f"处理请求时出错: {str(e)}"}}
                prompt += DuckDBPrompt(cite)
            self.logger.info(f"is trans : {trans}")

            search_results = LongTermMemoryManager().search_memories(
                user_id=uid,
                session_id=sid,
                query=question,
                limit=20
            )
            time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            memory = "\n".join(
                [f"{id}.{result['user_question_summary']}" for id, result in enumerate(search_results, 1)])
            prompt = f"历史对话记忆: {memory}\n\n当前对话的sessionID: {sid}\n\n当前时间: {time_now}\n\n" + prompt

            # mcp相关
            tools = CallMCPLogic().get_tools(MCP_SERVICES)
            tool_dict = aided_chat(SelectMCPrompt(tools), question, SelectMCPModel)
            if tool_dict["tool_name"]:
                mcp_result = CallMCPLogic().call_tool(question, MCP_SERVICES, tool_dict["tool_name"])
                prompt += f"""数据库结果(不用在乎筛选条件，筛选条件肯定是对的): \n{mcp_result}\n\n"""

            if file_path:
                prompt += "<file_data>\n"
                for file in file_path:
                    file_data = self.minio_client.get_object(file["bucket"], file["path"])
                    file_name = file["path"]
                    prompt += f"<{file_name}>\n{file_data}\n</{file_name}>\n\n"
                prompt += "</file_data>\n"

            if imgs:
                prompt += "<image_data>\n"
                for idx, img in enumerate(imgs, 1):
                    prompt += f"图片内容{idx}:{img}\n"
                prompt += "</image_data>\n"

            question_text = question + "\n" + file_content if file_content else question

            if agent_func:
                chat_type_dict = self.aided_chat(question_name, analysis_question(
                    table_info=self.query_generator.generate_table_info_str(SELECT_TABLES)), options={"format": "json"})
                if chat_type_dict["is_query"] and not padding_json and chat_type_dict["intent_type"] == "data_query":
                    tuning_input = f"""用户输入: {chat_type_dict["query_logic"]}
                    格式要求: {chat_type_dict["required_fields"]}
                    输出样式: {chat_type_dict["display_mode"]}"""
                    outcome = self.agents.smart_query(tuning_input, SELECT_TABLES).get("smart_results", "")
                    result = f"""根据用户的问题, 结果如下:\n{outcome}\n"""
                    yield result
                    return
                elif chat_type_dict["is_query"]:
                    agent_data = Agents().smart_query(question_name, SELECT_TABLES)
                    agent_data_outcome = agent_data.get("smart_results", "") if agent_data else ""
                    agent_data_explanation = chat_type_dict.get("query_logic", "") if agent_data else ""
                    if agent_data_outcome:
                        prompt += f"agent_data({agent_data_explanation}):\n{agent_data_outcome}\n\n"
                        if not padding_json:
                            yield f"<AgentDatabase>\n\n{agent_data_explanation}:\n{agent_data_outcome}\n\n</AgentDatabase>\n\n"

            basis = "\n\n".join([f"basis {i}:" + LoadFileTools().load_file(BASIS_FILE_PATH, basis, "basis")
                                 for i, basis in enumerate(agent_dict["basis_list"], 1)])
            if basis:
                prompt += f"basis: {basis}\n\n"

            if history:
                prompt += "<history>\n"
                for nb, text in enumerate(history, 1):
                    cleaned_text = self.clean_history_content(text)
                    prompt += f"<history_{nb}>\n{cleaned_text}\n</history_{nb}>\n\n"
                prompt += "</history>\n"

            if topics:
                # keywords = " ".join(TFIDFMath().extract_keywords_tfidf(question_name))
                knowledge_data = "\n\n".join(
                    [data["topic"] + "-" + data["file_name"] + "(**相似度:" + str(data["similarity"]) + "**):\n"
                     + data["content"] for data in PreFileLogic().search_knowledge(question_name, topics)])
                if knowledge_data:
                    prompt += f"knowledge: {knowledge_data}\n\n"
                    if not padding_json:
                        yield f"<KnowDatabase>\n\n{knowledge_data}\n\n</KnowDatabase>\n\n"

            if padding_json:
                text_type, data_type, table_type = classify_data(padding_json)  # 对关键词分类
                prompt_stitching = f"""{padding_report_prompt(text_type)}\n\n{prompt}"""
                result_dict = self.aided_chat(question_name, prompt_stitching, options={f"format": "json"})
                result_dict.update(self.process_type_queries(data_type))  # 处理数值类型
                result_dict.update(self.process_type_queries(table_type))
                yield str(result_dict)
                return

            chat = getattr(LLMChatTools(), function_name)

            # 调用LLM获取回答
            if stream:
                # 流式模式：一个个yield出LLM返回的内容
                for response_chunk in chat(url=url, api_key=key, prompt=prompt, message=question_text, model=model_name,
                                           stream=True, options=agent_dict["options"], max_len_input=max_len_input):
                    yield response_chunk.replace(r'\"', '"')
            else:
                # 非流式模式：一次性返回完整内容
                result = chat(url=url, api_key=key, prompt=prompt, message=question_text, model=model_name,
                              stream=False, options=agent_dict["options"], max_len_input=max_len_input)
                yield result
                return

        except Exception as e:
            logging.error(f"agent_completion 调用失败: {str(e)}")
            if not stream:
                return {"message": {"content": f"处理请求时出错: {str(e)}"}}
            raise


class LLMSelectLogic(BaseHandler):

    def add_config_llm(self, form: LLMConfigAddForm):
        with session_ctx() as ctx:
            ctx.add(LLMConfigModel(**form.dict()))
            ctx.commit()

    def edit_config_llm(self, form: LLMConfigEditForm):
        with session_ctx() as ctx:
            data = ctx.query(LLMConfigModel).filter(LLMConfigModel.id == form.id,
                                                    LLMConfigModel.user_id == form.user_id).one_or_none()
            if not data:
                return
            for field, value in form:
                if value is not None:
                    setattr(data, field, value)
            ctx.commit()

    def del_config_llm(self, id: int, user_id: int):
        with session_ctx() as ctx:
            data = ctx.query(LLMConfigModel).filter(LLMConfigModel.id == id, LLMConfigModel.user_id == user_id).one_or_none()
            if not data:
                return
            ctx.delete(data)
            ctx.commit()

    def get_config_llm(self, id: int, user_id: int):
        with session_ctx() as ctx:
            query = ctx.query(LLMConfigModel)
            if id:
                query = query.filter(LLMConfigModel.id == id, LLMConfigModel.user_id == user_id).limit(1)
            datas = query.all()
            if not datas:
                return []
            result = []
            for data in datas:
                data_dict = LLMConfigGetSchema.from_orm(data).dict()
                if data.user_id != user_id:
                    if data.is_public:
                        data_dict.update({"is_edit": False})
                    else:
                        continue
                else:
                    data_dict.update({"is_edit": True})
                result.append(data_dict)
            return result

    def get_all_llm(self):
        with session_ctx() as ctx:
            datas = ctx.query(LLMConfigModel).all()
            if not datas:
                return []
            result = [LLMConfigGetSchema.from_orm(data).dict() for data in datas]
            return result


class DefaultLLMSelectLogic(BaseHandler):

    def add_default_conf(self, form: LLMConfigAddForm):
        with session_ctx() as ctx:
            ctx.add(LLMConfigModel(**form.dict()))
            ctx.commit()

    def edit_default_conf(self, form: LLMConfigEditForm):
        with session_ctx() as ctx:
            data = ctx.query(LLMConfigModel).filter(LLMConfigModel.id == form.id,
                                                    LLMConfigModel.user_id == form.user_id).one_or_none()
            if not data:
                return
            for field, value in form:
                if value is not None:
                    setattr(data, field, value)
            ctx.commit()
    def get_default_conf(self, id: int, user_id: int):
        with session_ctx() as ctx:
            query = ctx.query(LLMConfigModel)
            if id:
                query = query.filter(LLMConfigModel.id == id, LLMConfigModel.user_id == user_id).limit(1)
            datas = query.all()
            if not datas:
                return []
            result = []
            for data in datas:
                data_dict = LLMConfigGetSchema.from_orm(data).dict()
                if data.user_id != user_id:
                    if data.is_public:
                        data_dict.update({"is_edit": False})
                    else:
                        continue
                else:
                    data_dict.update({"is_edit": True})
                result.append(data_dict)
            return result


class AgentsQuestionsLogic(BaseHandler):

    def _init(self):
        self.agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)
        self.db_query = DatabaseQuery()

    def get_ask(self, question: str, agent_id: str, **kwargs):
        try:
            agent_dict = self.agents_dict[agent_id]
            prompt = f"""
            prompt: {LoadFileTools().load_file(PROMPT_FILE_PATH, agent_dict["file_name"], "prompt")}
            """

            chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
            result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt, message=question,
                          model=AIDED_LLM_CONF["model_name"], stream=False, options=agent_dict["options"],
                          max_len_input=AIDED_LLM_CONF["max_len_input"])
            return json.loads(result)["ask_list"]
        except Exception as e:
            logging.error(f"agent_completion 调用失败: {str(e)}")
            return []

    def get_sift_agent(self, question: str, agent_id: str, **kwargs):
        try:
            _agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)[agent_id]
            prompt = f"""
            prompt: {LoadFileTools().load_file(PROMPT_FILE_PATH, _agents_dict["file_name"], "prompt")}
            """

            chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
            result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt, message=question,
                          model=AIDED_LLM_CONF["model_name"], stream=False, options=_agents_dict["options"],
                          max_len_input=AIDED_LLM_CONF["max_len_input"])
            return Ret(data={"answer": result, "reference": []}).dict()
        except Exception as e:
            logging.error(f"agent_completion 调用失败: {str(e)}")
            raise Ret(err_no=500, msg=str(e)).dict()

    def sift_report_template(self, question: str, agent_id: str, **kwargs):
        try:
            agent_dict = self.agents_dict[agent_id]
            prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agent_dict["file_name"], "prompt")
            conditions = [AIReportTemplateModel.is_enable == 1, AIReportTemplateModel.is_delete == 0]
            with session_ctx(True) as session:
                report_templates = session.query(AIReportTemplateModel).filter(*conditions).all()
            report_templates = [{"id": template.id, "template_name": template.template_name,
                                 "description": template.description} for template in report_templates]
            prompt = prompt.format(report_templates=report_templates)
            chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
            result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt, message=question,
                          model=AIDED_LLM_CONF["model_name"], stream=False, options=agent_dict["options"],
                          max_len_input=AIDED_LLM_CONF["max_len_input"])
            data = LoadJsonTools().process_llm_response(result)
            return Ret(data=data).dict()

        except Exception as e:
            logging.error(f"选择模板调用失败: {str(e)}")
            raise Ret(err_no=500, msg=str(e)).dict()
