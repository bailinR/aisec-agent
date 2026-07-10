#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/7/9 14:38
# @Author  : GuJR
# @Site    : 
# @File    : logic.py
from aisec_agent.config import MCP_SERVICES
from aisec_agent.logic._tools import aided_chat
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import SelectMCPModel, CallMCPModel, DismantleInfoModel
from aisec_agent.model.mcp import MCPService
from aisec_agent.model.prompts import SelectMCPrompt, CallMCPrompt, DismantleInfoPrompt


class CallMCPLogic(BaseHandler):

    def get_service(self):
        return MCP_SERVICES

    def get_tools(self, services: list) -> dict:
        with MCPService(services) as mcp:
            tools_dict = mcp.list_tools()
        return tools_dict

    def get_tools_info(self, services: list[dict]) -> list:
        with MCPService(services) as mcp:
            tools_dict = mcp.list_tools()
            tools_info = []
            for tool in tools_dict:
                tool_info = {}
                tool_info["tool_name"] = tool
                tool_info.update(mcp.get_tool_info(tool))
                tools_info.append(tool_info)
        return tools_info

    def call_tool(self, question: str, services: list, tool_name: str = None) -> dict:
        with MCPService(services) as mcp:
            if not tool_name:
                tools_dict = mcp.list_tools()
                select_prompt = SelectMCPrompt(tools_dict)
                tool_dict = aided_chat(select_prompt, question, SelectMCPModel)
                tool_name = tool_dict["tool_name"]
            tool_info = mcp.get_tool_info(tool_name)
            if not (tool_info and "inputSchema" in tool_info):
                return {"result":{'content': [{'text': f'未找到"{tool_name}"工具', 'type': 'text'}], 'isError': True}}
            tool_info = mcp.get_tool_info(tool_name)["inputSchema"]
            dismantle_info = aided_chat(DismantleInfoPrompt(tool_info), question, DismantleInfoModel)
            dismantle_info.update({"user_input": question})
            call_prompt = CallMCPrompt(tool_info)
            params = aided_chat(call_prompt, str(dismantle_info), CallMCPModel)
            result = mcp.call_tool(tool_name, params)
            return result