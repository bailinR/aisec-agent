from flask import Blueprint, g
from flask_restful import Api, Resource

api = Blueprint('action', __name__, url_prefix='/api/v0.1/chat')
api_rest = Api(api)


