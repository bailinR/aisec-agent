#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/21 11:34
# @Author  : GuJR
# @Site    : 
# @File    : prompt.py
import logging
import shutil
from os import path

from aisec_agent.config import MANAGE_PROMPT_PATH, PROMPT_FILE_PATH, PROMPT_BACKUP_FILE_PATH
from aisec_agent.logic._tools import LoadFileTools
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.typing import Ret


class PromptLogic(BaseHandler):

    def _init(self):
        self.agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)

    def load_prompt(self, file_path: str, agent_id: str):
        try:
            agent_dict = self.agents_dict[agent_id]
            file_name = agent_dict["file_name"]
            prompt = LoadFileTools().load_file(file_path, file_name, "prompt")
            backup_path = path.join(PROMPT_BACKUP_FILE_PATH, f"{file_name}.prompt")
            is_backup = path.exists(backup_path)
            data = {"prompt": prompt, "is_backup": is_backup}
            return Ret(data=data).dict()
        except Exception as e:
            self.logger.error(f"prompt查询失败: {str(e)}")
            return Ret(err_no=500, msg=f"prompt查询失败: {str(e)}").dict()

    def edit_prompt(self, agent_id: str, prompt: str):
        try:
            agent_dict = self.agents_dict[agent_id]
            file_name = agent_dict["file_name"]
            prompt_path = path.join(PROMPT_FILE_PATH, f"{file_name}.prompt")
            backup_path = path.join(PROMPT_BACKUP_FILE_PATH, f"{file_name}.prompt")
            if not path.exists(backup_path):    # 备份初始prompt, 以防后续要一键恢复
                shutil.copy(prompt_path, backup_path)
            with open(prompt_path, "w", encoding="utf-8") as file:
                file.write(prompt)
            return Ret().dict()
        except Exception as e:
            self.logger.error(f"prompt编辑失败: {str(e)}")
            return Ret(err_no=500, msg=f"prompt编辑失败: {str(e)}").dict()

    def recover_prompt(self, agent_id: str):
        try:
            agent_dict = self.agents_dict[agent_id]
            file_name = agent_dict["file_name"]
            prompt_path = path.join(PROMPT_FILE_PATH, f"{file_name}.prompt")
            backup_path = path.join(PROMPT_BACKUP_FILE_PATH, f"{file_name}.prompt")
            if path.exists(backup_path):
                shutil.copy(backup_path, prompt_path)
                return Ret().dict()
            else:
                raise
        except Exception as e:
            self.logger.error(f"prompt恢复失败: {str(e)}")
            return Ret(err_no=500, msg=f"prompt恢复失败: {str(e)}").dict()