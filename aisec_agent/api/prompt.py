from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate

from aisec_agent.config import PROMPT_FILE_PATH, PROMPT_BACKUP_FILE_PATH
from aisec_agent.logic.prompt import PromptLogic
from aisec_agent.model.typing import PromptConfigGetForm, Ret, PromptConfigEditForm

prompt = Blueprint('prompt', __name__, url_prefix='/api/v0.1/prompt')
api_rest = Api(prompt)


@api_rest.resource('/config')
class PromptAPI(Resource):
    @form_validate(PromptConfigGetForm, mode="query")
    def get(self):
        return PromptLogic().load_prompt(PROMPT_FILE_PATH, g.form.agent_id)

    @form_validate(PromptConfigEditForm)
    def put(self):
        result = PromptLogic().edit_prompt(**g.form.dict())
        return result


@api_rest.resource('/config/recover')
class PromptRecoverAPI(Resource):
    @form_validate(PromptConfigGetForm, mode="query")
    def get(self):
        return PromptLogic().load_prompt(PROMPT_BACKUP_FILE_PATH, g.form.agent_id)
