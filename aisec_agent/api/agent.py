import time

from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate

from aisec_agent.logic._tools import ImageToTextLogic
from aisec_agent.logic.agent.agents import Agents, Parse, DecoTaskLogic, QueryDataBaseLogic
from aisec_agent.logic.agent.pentest.summary import PentestSummary
from aisec_agent.logic.agent.text2sql import Text2SQLLogic, Text2ES, split_data
from aisec_agent.logic.red import PublishVioceile
from aisec_agent.model.r import SELECT_TABLES
from aisec_agent.model.typing import AgentForm, Ret, BaseLLMChatForm, HRDataSplitForm, \
    ParseFirewallLogForm, Image2TextForm, FieldTidyUPForm, PentestSummaryForm, VoiceRetForm

agent = Blueprint('agent', __name__, url_prefix='/api/v0.1/agent')
api_rest = Api(agent)


# @api_rest.resource('/vr')
# class VoiceInfoAPI(Resource):
#     agent = VoiceAgents()
#
#     def get(self):
#         data = self.agent.list_mp3()
#         return Ret(data=data).dict()
#
#
# @api_rest.resource('/voice')
# class TextToVoiceAPI(Resource):
#     agent = VoiceAgents()
#     red = PublishVioceile()
#
#     @form_validate(VoiceRetForm)
#     def post(self):
#         """接收Minio文件路径，并将任务信息写入Redis队列"""
#         form: VoiceRetForm = g.form
#
#         voices = self.agent.voice_files_sync(form.voice_files)
#         txt = self.agent.text(form.text, voices)
#         _n = f"{form.task_id}_{int(time.time())}.wav"
#         self.red.task_publish(
#             VoiceRetForm(
#                 voice_files=voices, bucket=form.bucket,
#                 task_id=form.task_id,
#                 text=txt,
#             )
#         )
#         # self.agent.sync(
#         #     form.bucket, _n, txt, voices
#         # )
#
#         return Ret(data={'path': _n, }).dict()





@api_rest.resource('/<string:agent_name>')
class LLMAgents(Resource):

    @form_validate(AgentForm)
    def post(self, agent_name):
        form: AgentForm = g.form
        agent = getattr(Agents(), agent_name)
        result = agent(**form.dict())
        return Ret(data=result).dict()


@api_rest.resource('/assets')
class LLMAgentsAssets(Resource):

    @form_validate(AgentForm)
    def post(self):
        form: AgentForm = g.form
        result = Agents().smart_query(form.content, SELECT_TABLES)
        return Ret(data=result).dict()


@api_rest.resource('/hr')
class QueryHRAPI(Resource):
    @form_validate(BaseLLMChatForm)
    def post(self):
        form: BaseLLMChatForm = g.form
        result = Text2SQLLogic().query_hr_data(form.question)
        return Ret(data=result).dict()


@api_rest.resource('/hr/split')
class QueryHRSplitAPI(Resource):
    @form_validate(HRDataSplitForm)
    def post(self):
        form: HRDataSplitForm = g.form
        result = split_data(form.question, form.datas)
        return Ret(data=result).dict()


@api_rest.resource('/daily')
class QueryDailyAPI(Resource):
    @form_validate(BaseLLMChatForm)
    def post(self):
        form: BaseLLMChatForm = g.form
        result = Text2ES().select_daily(form.question)
        return Ret(data=result).dict()


@api_rest.resource('/parse/firewallLog')
class ParseFirewallLogAPI(Resource):
    @form_validate(ParseFirewallLogForm)
    def post(self):
        form: ParseFirewallLogForm = g.form
        result = Parse().firewallLog(form.text)
        return Ret(data=result).dict()


@api_rest.resource('/deco/task')
class DecoTaskAPI(Resource):
    @form_validate(BaseLLMChatForm)
    def post(self):
        form: BaseLLMChatForm = g.form
        result = DecoTaskLogic().deco_task(form.question)
        return Ret(data=result).dict()


@api_rest.resource('/image2text')
class Image2TextAPI(Resource):
    @form_validate(Image2TextForm)
    def post(self):
        form: Image2TextForm = g.form
        result = ImageToTextLogic().describe_image(form.image_base64)
        return Ret(data=result).dict()


@api_rest.resource('/tidyup')
class FieldTidyUPAPI(Resource):
    @form_validate(FieldTidyUPForm)
    def post(self):
        form: FieldTidyUPForm = g.form
        result = QueryDataBaseLogic().tidy_field(**form.dict())
        return Ret(data=result).dict()


@api_rest.resource('/pentest/summary')
class PentestSummaryAPI(Resource):
    @form_validate(PentestSummaryForm)
    def post(self):
        form: PentestSummaryForm = g.form
        result = PentestSummary().note(**form.dict())
        return Ret(data=result).dict()