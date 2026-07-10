#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/1 14:25
# @Author  : GuJR
# @Site    : 
# @File    : log.py
import re
from io import BytesIO
from aisec_agent.config import MANAGE_PROMPT_PATH, PROMPT_FILE_PATH
from aisec_agent.logic._tools import FileReaderTools, LoadFileTools
from aisec_agent.logic.agent._tools import AgentTools
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.typing import AnalysisLogForm


class AnalysisAgentLogic(BaseHandler):

    def _init(self):
        self.agents_dicts = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)

    def analysis(self, prompt_id: str, question: str, content: str):
        global log_content
        try:
            log_content = content
            agents_dict = self.agents_dicts[prompt_id]
            prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agents_dict["file_name"], "prompt")
            message = f"""
                        question: {question}
                        file_content: {log_content[:2000]}
                        """
            agents_dict["message"] = message
            agents_dict["prompt"] = prompt
            result_code = AgentTools().make_code(**agents_dict)
            code = re.findall("```python\n(.*?)\n```", result_code, re.DOTALL)[0]
            try:
                locals_dict = {}
                exec(code, globals(), locals_dict)
                result = locals_dict.get('report_str') or locals_dict.get('report')
            except Exception as e:
                while True:
                    code = AgentTools().auto_debug(code, str(e))
                    try:
                        locals_dict = {}
                        exec(code, globals(), locals_dict)
                        result = locals_dict.get('report_str') or locals_dict.get('report')
                        break
                    except Exception as e:
                        pass
            return result
        except Exception as e:
            return "大模型最终返回的变量名称出错"

    def analysis_log(self, form: AnalysisLogForm):
        """分析日志相关逻辑"""
        # 将 FileStorage 转换为 BinaryIO
        file_storage = form.file
        binary_io = BytesIO(file_storage.read())
        # 读取文件内容
        content = FileReaderTools().get_file_content(binary_io, form.file_name).replace("  ", "")
        return self.analysis(form.prompt_id, form.question, content)

