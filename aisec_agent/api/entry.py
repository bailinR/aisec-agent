from flask_session import Session
from ugly_code.tool import load_variables
from werkzeug.middleware.proxy_fix import ProxyFix


__author__ = 'Memory_Leak<yuz@cnns.net>'

from aisec_agent.api.app import make_app
# from aisec_agent.api.asr import asr_bp

APP_CFG = {
    'WTF_CSRF_ENABLE': False,
    'WTF_CSRF_ENABLED': False,
    'SESSION_KEY_PREFIX': 'ctrl:session:'
}
include_apps = [
    '.api:api',
    '.chat:chat',
    '.knowledge:knowledge',
    '.task:task',
    '.asr:asr',
    '.agent:agent',
    '.prompt:prompt',
    '.mcp:mcp',
]

app = make_app(
    name=__name__, plugins=(Session,), config=APP_CFG,
    blue_prints=load_variables(include_apps, __package__),
)

app.wsgi_app = ProxyFix(app.wsgi_app)