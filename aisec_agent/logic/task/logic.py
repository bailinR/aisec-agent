#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/8 17:24
# @Author  : GuJR
# @Site    : 
# @File    : logic.py
import ast
import json
from aisec_agent.config import MANAGE_PROMPT_PATH, PROMPT_FILE_PATH, AIDED_LLM_CONF
from aisec_agent.logic._tools import LoadFileTools
from aisec_agent.logic.knowledge.logic import PreFileLogic, KnowledgeCRUDLogic
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_chat import LLMChatTools


class AssetsVulnLogic(BaseHandler):

    def search_vuln(self, content: str):
        datas = PreFileLogic().search_knowledge(question=content, topics=["国家漏洞库"])
        for data in datas:
            data["content"] = ast.literal_eval(data["content"])
        return datas

    def create_vuln(self, content: str):
        agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)["116ea243fef711efae311a0ceb41b88d"]
        prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agents_dict["file_name"], "prompt")
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        result = chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt, message=content,
                      model=AIDED_LLM_CONF["model_name"], stream=False, options=agents_dict["options"],
                      max_len_input=AIDED_LLM_CONF["max_len_input"])
        if isinstance(result, bytes):
            result = result.decode('utf-8', errors='replace')
        result = json.loads(result)
        KnowledgeCRUDLogic().add_content(str(result), "国家漏洞库")


