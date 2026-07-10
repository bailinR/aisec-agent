#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/8 17:17
# @Author  : GuJR
# @Site    : 
# @File    : task.py
import logging

from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate
from aisec_agent.logic.task.logic import AssetsVulnLogic
from aisec_agent.model.typing import AssetsVulnForm, Ret

task = Blueprint('task', __name__, url_prefix='/api/v0.1/task')
api_rest = Api(task)


@api_rest.resource('/assets')
class AssetsVulnAPI(Resource):

    @form_validate(AssetsVulnForm)
    def post(self):
        form: AssetsVulnForm = g.form
        return Ret(data=AssetsVulnLogic().search_vuln(form.content)).dict()

    @form_validate(AssetsVulnForm)
    def put(self):
        form: AssetsVulnForm = g.form
        try:
            AssetsVulnLogic().create_vuln(form.content)
            return Ret().dict()
        except Exception as e:
            logging.warning(f"预处理漏洞数据失败, 错误: {e}")
            return Ret(err_no=500, msg=str(e)).dict()