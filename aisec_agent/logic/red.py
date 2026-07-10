import json

from aisec_agent.config import DEFAULT_REDIS
from aisec_agent.model.define import BaseHandler, RedisQueue
from aisec_agent.model.typing import VoiceRetForm


class PublishVioceile(BaseHandler):

    def _init(self):
        self.redis_conn = RedisQueue(queue_name="vibe_voice_queue", status_key="voice_status", **DEFAULT_REDIS)

    def task_publish(self, form: VoiceRetForm):
        self.redis_conn.rpush("vibe_voice_queue", json.dumps(form.dict()))

    def task_status(self, file_id: str):
        return self.redis_conn.get_status(file_id)
