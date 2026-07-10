import json
import re
from datetime import datetime

from aisec_agent.config import AIDED_LLM_CONF, MANAGE_PROMPT_PATH, PROMPT_FILE_PATH, MCP_SERVICES
from aisec_agent.logic._tools import LoadFileTools
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.agent._tools import QueryExecutor
from aisec_agent.logic.scanner.logic import ScannerBaseHandler
from aisec_agent.model.define import BaseHandler, session_ctx
from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.llm_typing import ParseFirewallLogModel, DecoTaskModel, MakeSQLModel, SelectDBModel, \
    SelectDataCenterModel, FieldTidyUPModel
from aisec_agent.model.mcp import MCPService
from aisec_agent.model.model import AIDataCenterModel, ColumnDesModel
from aisec_agent.model.prompts import ParsePrompt, DecoTaskPrompt, MakeSQLPrompt, SelectDBPrompt, \
    SelectDataCenterPrompt, FieldTidyUPPrompt


class Agents(BaseHandler):

    def semantic_split(self, content: str, llm_func_name: str = AIDED_LLM_CONF["func_name"],
                       url: str = AIDED_LLM_CONF["url"], model_name: str = AIDED_LLM_CONF["model_name"],
                       api_key: str = AIDED_LLM_CONF["key"], max_len_input: int = AIDED_LLM_CONF["max_len_input"]):
        agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)["222ea243fef711efae311a0ceb41b88d"]
        prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agents_dict["file_name"], "prompt")
        chat = getattr(LLMChatTools(), llm_func_name)
        result = chat(url=url, api_key=api_key, model=model_name, prompt=prompt, message=content, stream=False,
                      options=agents_dict["options"], max_len_input=max_len_input)
        return json.loads(result)

    def task_split_step(self, content: str, llm_func_name: str = AIDED_LLM_CONF["func_name"],
                        url: str = AIDED_LLM_CONF["url"], model_name: str = AIDED_LLM_CONF["model_name"],
                        api_key: str = AIDED_LLM_CONF["key"], max_len_input: int = AIDED_LLM_CONF["max_len_input"]):
        agents_dict = LoadFileTools().load_json_file(MANAGE_PROMPT_PATH)["222ea243fef711efae311a0ceb41b88d"]
        prompt = LoadFileTools().load_file(PROMPT_FILE_PATH, agents_dict["file_name"], "prompt")
        prompt += "\n\n当前时间:"+datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chat = getattr(LLMChatTools(), llm_func_name)
        result = chat(url=url, api_key=api_key, model=model_name, prompt=prompt, message=content, stream=False,
                      options={"format":"json"}, max_len_input=max_len_input)
        return json.loads(result)

    def smart_query(self, question: str, selected_query: list | None = None, **kwargs):
        """智能查询"""
        try:
            results = QueryExecutor().execute_smart_query(question, selected_query)
            return results
        except Exception as e:
            # 处理异常
            raise e


class Parse(BaseHandler):

    def firewallLog(self, text: str):
        prompt = ParsePrompt()
        params_dict = aided_chat(prompt, text, ParseFirewallLogModel)
        return params_dict


class DecoTaskLogic(BaseHandler):
    def deco_task(self, prompt: str, question: str, mcp_tools: dict):
        prompt += DecoTaskPrompt(mcp_tools)
        result = aided_chat(prompt, question, DecoTaskModel)
        return result


class QueryDataBaseLogic(BaseHandler):

    def select_db(self, question: str):
        data_center_list = []
        with session_ctx(True) as ctx:
            data_center = ctx.query(AIDataCenterModel).all()
        for data in data_center:
            data_center_list.append({"db_id": data.db_id, "name": data.name, "description": data.description})
        dc_prompt = SelectDataCenterPrompt(data_center_list)
        select_dc= aided_chat(dc_prompt, question, SelectDataCenterModel)  # 选择数据中心
        db_list = list(set(select_dc["db_id"]))
        prompt = SelectDBPrompt(ScannerBaseHandler().get_table_list(db_list))

        result = aided_chat(prompt, question, SelectDBModel)
        db_ids = result["need_db"].keys()
        values = result["need_db"].values()

        if isinstance(values, str):
            values = [values]
        tables = list(set([item for sublist in list(values) for item in sublist]))

        db_info =None
        # 尝试三次，得到结果就返回
        for nb in range(0, 3):
            db_info = ScannerBaseHandler().get_db_and_tables_markdown(tables, db_ids)

            if db_info:
                break
        return db_info

    def sql_query(self,info,question):
        data = {}
        try:
            prompt = MakeSQLPrompt(info)
            result = aided_chat(prompt, question, MakeSQLModel)
            info["db_config"].update(result)
            params = {"arguments": info["db_config"]}
            with MCPService(MCP_SERVICES) as mcp:
                data["sql_data"] = json.loads(mcp.call_tool("sql_query", params)["result"]["content"][0]["text"])
        except Exception as e:
            self.logger.error(f"查询数据库失败: {str(e)}")
            return {}

        if data:
            data["title"] = result["title"]
        return data

    def choice_tools(self, question: str):
        pass

    def query_data(self, question: str, agent_extra: dict = {}):
        data_center_list = []
        with session_ctx(True) as ctx:
            data_center = ctx.query(AIDataCenterModel).filter(AIDataCenterModel.db_id != None).all()
        for data in data_center:
            data_center_list.append({"db_id": data.db_id, "name": data.name, "description": data.description})
        dc_prompt = SelectDataCenterPrompt(data_center_list)
        select_dc = aided_chat(dc_prompt, question, SelectDataCenterModel)  # 选择数据中心
        if not select_dc:  # 如果没有，直接退出
            return

        db_list = list(set(select_dc["db_id"]))
        if not db_list:  # 如果没有，直接退出
            return
        tables_dict = ScannerBaseHandler().get_table_list(db_list)
        tables_list = [item['table_name'] for sublist in tables_dict.values() for item in sublist if 'table_name' in item]
        if not tables_list:  # 如果没有，直接退出
            return
        prompt = SelectDBPrompt(tables_dict)
        query_status = False
        for nb in range(1, 4):
            results = aided_chat(prompt, question, SelectDBModel)
            for result in results["need_db"]:
                if result["db_id"] in db_list:
                    is_tables = []
                    for _table in result["tables"]:
                        if _table in tables_list:
                            is_tables.append(_table)
                    if is_tables:
                        query_status = True
                        break
                elif nb == 3:
                    return
            if query_status:
                break

        db_ids = [result["db_id"] for result in results["need_db"]]
        values = [result["tables"] for result in results["need_db"]]

        if isinstance(values, str):
            values = [values]
        tables = list(set([item for sublist in list(values) for item in sublist]))

        # 尝试三次，得到结果就返回
        for nb in range(0, 3):
            db_info = ScannerBaseHandler().get_db_and_tables_markdown(tables, db_ids)
            if db_info:
                break

        datas = []
        for info in db_info:
            data = {}
            try:
                prompt = MakeSQLPrompt(info)
                result = aided_chat(prompt, question, MakeSQLModel, **agent_extra)
                pattern = r'AS\s*[^,]+?(?=,|FROM)'
                result["sql"] = re.sub(pattern, lambda m: re.sub(r'（[^）]*）|\([^)]*\)', '', m.group(0)),
                                       result["sql"], flags=re.IGNORECASE)
                info["db_config"].update(result)
                params = {"arguments": info["db_config"]}
                with MCPService(MCP_SERVICES) as mcp:
                    data["sql_data"] = json.loads(mcp.call_tool("sql_query", params)["result"]["content"][0]["text"])
                    data.update(result)
                    datas.append(data)

            except Exception as e:
                self.logger.error(f"查询数据库失败: {str(e)}")
                # print(e)

            # if data:
            #     data["title"] = result["title"]
            #     datas.append(data)

        return datas

    def tidy_field(self, db_id: str, flag: bool):
        """整理字段"""
        with session_ctx(True) as ctx:
            data_center = ctx.query(ColumnDesModel).filter(ColumnDesModel.name=="", ColumnDesModel.db_id == db_id).all()
            for data in data_center:
                prompt = FieldTidyUPPrompt(data.to_dict())
                name = aided_chat(prompt, "根据上述信息推断字段意思", FieldTidyUPModel, )["name"]
                if flag:
                    setattr(data, "name", name)
                else:
                    setattr(data, "ai_name", name)
                ctx.commit()
