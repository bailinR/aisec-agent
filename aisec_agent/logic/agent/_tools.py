#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/1 14:30
# @Author  : GuJR
# @Site    : 
# @File    : _tools.py
import json
import time
from datetime import datetime
from typing import Dict
from aisec_agent.model.agent_base import DatabaseQuery

from aisec_agent.config import AIDED_LLM_CONF, AIDED_LLM_CONF
from aisec_agent.logic._tools import FileReaderTools, LoadJsonTools
from aisec_agent.model.define import ModelAnalyzer, SmartQueryGenerator
from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.llm_typing import SQLQueryResult
from aisec_agent.model.prompts import query_template_prompt
import logging


class AgentTools(FileReaderTools):

    def make_step(self):
        pass

    def make_code(self, **kwargs):
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        return chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], stream=False,
                    model=AIDED_LLM_CONF["model_name"], max_len_input=AIDED_LLM_CONF["max_len_input"], **kwargs)

    def exec_code(self):
        pass

    def check_import(self):
        pass

    def auto_debug(self, code: str, error_str: str):
        pass


class QueryExecutor:
    def __init__(self):
        self.db_query = DatabaseQuery()
        self.model_analyzer = ModelAnalyzer()
        self.model_info = self.model_analyzer.get_all_models()
        self.query_generator = SmartQueryGenerator(self.model_info)

    def execute_smart_query(self, user_query: str, select_tables: list | None = None, table_info: str = None) -> Dict:
        """
        执行智能查询

        Args:
            user_query: 用户的查询需求描述
            select_tables: 指定需要查的表

        Returns:
            Dict: 查询结果
        """
        if select_tables:
            table_info = self.query_generator.generate_table_info_str(select_tables)
        prompt = query_template_prompt(table_info=table_info,
                                       user_query=user_query, time_now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            llm_response = self._query(prompt=prompt, user_query=user_query)
            return llm_response
        except Exception as e:
            error_msg = f"大模型对话错误：{str(e)}"
            logging.error(error_msg)
            raise Exception(error_msg)

    def call_llm(self, **kwargs) -> dict:
        """调用大模型"""
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        llm_response = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], stream=False,
                            options={"format": SQLQueryResult.model_json_schema()}, model=AIDED_LLM_CONF["model_name"],
                            max_len_input=AIDED_LLM_CONF["max_len_input"], **kwargs)
        response = LoadJsonTools().process_llm_response(llm_response)
        return response

    def _query(self, prompt: str, user_query: str):
        response = self.call_llm(prompt=prompt, message=user_query)
        try:
            smart_query = getattr(self.db_query, response.get("query_type"))
            smart_results = smart_query(**response)
        except Exception as e:
            error_msg = f"查询数据库信息失败：{str(e)}"
            error_sql = response['sql']
            prompt = f"{prompt}\n\n根据报错重新生成json:{error_msg}\n\nerror_sql:{error_sql}"
            response = self.call_llm(prompt=prompt, message=user_query)
            try:
                smart_query = getattr(self.db_query, response.get("query_type"))
                smart_results = smart_query(**response)
            except Exception as e:
                error_msg = f"查询数据库信息失败：{str(e)}"
                logging.error(error_msg)
                raise Exception(error_msg)

        return {
            'smart_results': smart_results,
            'explanation': response['explanation'],
            'sql': response['sql']
        }
