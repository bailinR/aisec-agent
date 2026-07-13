#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/17 10:57
# @Author  : GuJR
# @Site    : 
# @File    : model.py
import ast
from flask import Blueprint, g, Response
from flask_restful import Api, Resource
from form_validate import form_validate
import logging
import traceback

from aisec_agent.logic.agent.log import AnalysisAgentLogic
from aisec_agent.logic.memory.tools import LongTermMemoryManager
from aisec_agent.model.typing import LLMChatForm, Ret, AnalysisLogForm, LLMConfigAddForm, \
    LLMConfigEditForm, LLMConfigGetForm, LLMChatAgentForm, LLMAgentForm
from aisec_agent.logic.chat import ChatAgentLogic, LLMSelectLogic, AgentsQuestionsLogic
from ._decorator import   handle_stream_or_normal
from aisec_agent.logic.logic import ChatAgentLogicHandler
chat = Blueprint('chat', __name__, url_prefix='/api/v0.1/chat')
api_rest = Api(chat)


@api_rest.resource('/completions')
class LLMAgents(Resource):

    @form_validate(LLMChatForm)
    def post(self):
        form: LLMChatForm = g.form
        stream = form.stream

        try:
            if stream:
                def stream_response():
                    accumulated_content = ""
                    try:
                        for chunk in ChatAgentLogic().agent_completion(**form.dict()):
                            try:
                                if chunk:
                                    accumulated_content += chunk
                                    if "</think>" not in accumulated_content:
                                        accumulated_content = accumulated_content.replace("<mark_file>", "").replace("</mark_file>", "")
                                    response_data = Ret(data={"answer": accumulated_content, "reference": []})
                                    yield f"data: {response_data.json()}\n\n"
                            except Exception as e:
                                logging.error(f"处理流数据块出错: {str(e)}")
                                continue
                        yield f"data: {Ret(data=True).json()}\n\n"
                        LongTermMemoryManager().store_conversation(form.uid, form.sid, form.question, accumulated_content)
                    except Exception as e:
                        error_details = traceback.format_exc()
                        logging.error(f"流响应生成错误: {error_details}")
                        error_response = Ret(code=500, msg=str(error_details))
                        yield f"data: {error_response.json()}\n\n"
                        end_response = Ret(data=True)
                        yield f"data: {end_response.json()}\n\n"

                resp = Response(stream_response(), mimetype="text/event-stream")
                resp.headers.add("Cache-Control", "no-cache")
                resp.headers.add("Connection", "keep-alive")
                resp.headers.add("X-Accel-Buffering", "no")
                resp.headers.add("Content-Type", "text/event-stream; charset=utf-8")
                return resp
            else:
                try:
                    content = None
                    for item in ChatAgentLogic().agent_completion(**form.dict()):
                        content = item
                    try:
                        content = ast.literal_eval(content)
                    except:
                        pass
                    if isinstance(content, bytes):
                        content = content.decode('utf-8', errors='replace')
                    return Ret(data={"answer": content, "reference": []}).dict()
                except Exception as e:
                    error_details = traceback.format_exc()
                    logging.error(f"非流式调用错误: {error_details}")
                    return Ret(code=500, msg=str(error_details)).dict()

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"API调用错误: {error_details}")
            error_msg = str(e)
            return Ret(code=500, msg=str(error_msg)).dict()

@api_rest.resource('/agent')
class LLMAgentsAPI(Resource):

    @form_validate(LLMChatForm)
    @handle_stream_or_normal(
        logic_func=ChatAgentLogicHandler().agent_chat,
        stream_transformer=lambda chunk, acc: chunk,
        normal_transformer=lambda content, form: Ret(data={"answer": content, "reference": []}).dict()
    )
    def post(self):
        pass

# @api_rest.resource('/completions')
# class LLMAgents(Resource):
#
#     @form_validate(LLMChatForm)
#     @handle_stream_or_normal(
#         logic_func=ChatAgentLogic().agent_completion,
#         stream_transformer=lambda chunk, acc: acc + str(chunk).replace("<mark_file>", "").replace("</mark_file>", ""),
#         final_stream_transformer=lambda acc, form: LongTermMemoryManager().store_conversation(
#             form.uid, form.sid, form.question, acc
#         ),
#         normal_transformer=lambda content, form: Ret(data={"answer": content, "reference": []}).dict()
#     )
#     def post(self):
#         pass


# @api_rest.resource('/agent')
# class LLMAgentsAPI(Resource):
#
#     @form_validate(LLMChatForm)
#     def post(self):
#         form: LLMChatForm = g.form
#         stream = form.stream
#
#         try:
#             if stream:
#                 def stream_response():
#                     try:
#                         for chunk in ChatAgentLogic().agent_chat(**form.dict()):
#                             try:
#                                 if chunk:
#                                     response_data = Ret(data=chunk)
#                                     yield f"data: {response_data.json()}\n\n"
#                             except Exception as e:
#                                 logging.error(f"处理流数据块出错: {str(e)}")
#                                 continue
#
#                         yield f"data: {Ret(data=True).json()}\n\n"
#                     except Exception as e:
#                         error_details = traceback.format_exc()
#                         logging.error(f"流响应生成错误: {error_details}")
#                         error_response = Ret(code=500, msg=str(error_details))
#                         yield f"data: {error_response.json()}\n\n"
#                         end_response = Ret(data=True)
#                         yield f"data: {end_response.json()}\n\n"
#
#                 resp = Response(stream_response(), mimetype="text/event-stream")
#                 resp.headers.add("Cache-Control", "no-cache")
#                 resp.headers.add("Connection", "keep-alive")
#                 resp.headers.add("X-Accel-Buffering", "no")
#                 resp.headers.add("Content-Type", "text/event-stream; charset=utf-8")
#                 return resp
#             else:
#                 try:
#                     content = None
#                     for item in ChatAgentLogic().agent_chat(**form.dict()):
#                         content = item
#                     try:
#                         content = ast.literal_eval(content)
#                     except:
#                         pass
#                     if isinstance(content, bytes):
#                         content = content.decode('utf-8', errors='replace')
#                     return Ret(data={"answer": content, "reference": []}).dict()
#                 except Exception as e:
#                     error_details = traceback.format_exc()
#                     logging.error(f"非流式调用错误: {error_details}")
#                     return Ret(code=500, msg=str(error_details)).dict()
#
#         except Exception as e:
#             error_details = traceback.format_exc()
#             logging.error(f"API调用错误: {error_details}")
#             error_msg = str(e)
#             return Ret(code=500, msg=str(error_msg)).dict()


@api_rest.resource('/agent/log')
class AnalysisLog(Resource):
    @form_validate(AnalysisLogForm, mode=["file", "form"])
    def post(self):
        form: AnalysisLogForm = g.form
        return AnalysisAgentLogic().analysis_log(form)


@api_rest.resource('/select')
class LLMSelectAPI(Resource):

    @form_validate(LLMConfigGetForm, mode="query")
    def get(self):
        form: LLMConfigGetForm = g.form
        result = LLMSelectLogic().get_config_llm(**form.dict())
        return Ret(data=result).dict()

    @form_validate(LLMConfigAddForm)
    def post(self):
        form: LLMConfigAddForm = g.form
        LLMSelectLogic().add_config_llm(form)
        return Ret().dict()

    @form_validate(LLMConfigEditForm)
    def put(self):
        form: LLMConfigEditForm = g.form
        LLMSelectLogic().edit_config_llm(form)
        return Ret().dict()

    @form_validate(LLMConfigGetForm, mode="query")
    def delete(self):
        form: LLMConfigGetForm = g.form
        LLMSelectLogic().del_config_llm(**form.dict())
        return Ret().dict()


@api_rest.resource('/select/all')
class LLMSelectAllAPI(Resource):
    """给内部调用获取所有大模型的接口"""

    def get(self):
        result = LLMSelectLogic().get_all_llm()
        return Ret(data=result).dict()


@api_rest.resource('/ask')
class AskingQuestionsAPI(Resource):

    @form_validate(LLMChatAgentForm)
    def post(self):
        form: LLMChatAgentForm = g.form
        content = AgentsQuestionsLogic().get_ask(**form.dict())
        return content


@api_rest.resource('/sift/agent')
class SiftQuestionsAPI(Resource):

    @form_validate(LLMChatAgentForm)
    def post(self):
        form: LLMChatAgentForm = g.form
        content = AgentsQuestionsLogic().get_sift_agent(**form.dict())
        return content


@api_rest.resource('/sift/report/template')
class SiftReportTemplateAPI(Resource):

    @form_validate(LLMChatAgentForm)
    def post(self):
        form: LLMChatAgentForm = g.form
        content = AgentsQuestionsLogic().sift_report_template(**form.dict())
        return content
