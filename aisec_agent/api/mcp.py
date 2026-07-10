#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/7/9 15:05
# @Author  : GuJR
# @Site    : 
# @File    : mcp.py
from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate
from aisec_agent.logic.mcp.logic import CallMCPLogic
from aisec_agent.model.typing import Ret, CallMCPForm, MCPToolsForm

mcp = Blueprint('mcp', __name__, url_prefix='/api/v0.1/mcp')
api_rest = Api(mcp)


@api_rest.resource('/tools')
class MCPToolsAPI(Resource):
    @form_validate(MCPToolsForm)
    def post(self):
        form: MCPToolsForm = g.form
        result = CallMCPLogic().get_tools(form.services)
        return Ret(data=result).dict()


@api_rest.resource('/service')
class ServiceMCPAPI(Resource):
    def get(self):
        result = CallMCPLogic().get_service()
        return Ret(data=result).dict()


@api_rest.resource('/call')
class CallMCPAPI(Resource):
    @form_validate(CallMCPForm)
    def post(self):
        form: CallMCPForm = g.form
        result = CallMCPLogic().call_tool(form.question, form.services, form.tool_name)
        return Ret(data=result).dict()


@api_rest.resource('/tools/info')
class MCPToolsInfoAPI(Resource):
    @form_validate(MCPToolsForm)
    def post(self):
        form: MCPToolsForm = g.form
        result = CallMCPLogic().get_tools_info(form.services)
        return Ret(data=result).dict()