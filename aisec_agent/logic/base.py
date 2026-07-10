from aisec_agent.config import DEFAULT_REDIS
from red_cache import RedisCache
import json
from typing import Any

class LocalRedisCache(RedisCache):

    @classmethod
    def json_decoder(cls, ret: bytes) -> Any:
        return json.loads(ret)

redis_cache = LocalRedisCache(DEFAULT_REDIS)