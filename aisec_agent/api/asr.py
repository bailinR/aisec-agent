from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate
from aisec_agent.logic.asr.tool import ASRModelTools
from aisec_agent.model.typing import Ret, ASRToTextForm

asr = Blueprint('asr', __name__, url_prefix='/api/v0.1/asr')
api_rest = Api(asr)


@api_rest.resource('/to_text')
class ASRToTextAPI(Resource):
    @form_validate(ASRToTextForm, mode=["file", "form"])
    def post(self):
        form: ASRToTextForm = g.form
        data = ASRModelTools().to_text(form.file)
        return Ret(data=data).dict()
