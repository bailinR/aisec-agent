from ..base import redis_cache
from ...model.schema import DBScannerEvent, DBScannerEventRetModel
from ...tools.red import RedQueue, T


class  DBScannerQueue(RedQueue[ DBScannerEvent]):
    def __init__(self):
        super().__init__(
            redis_cache.redis, "scanner::queue:read:db")

    def decode(self, ret: bytes) -> T:
        return DBScannerEvent.parse_raw(ret)

    def encode(self, obj:DBScannerEvent) -> bytes:
        return obj.json().encode()

class DBScannerRetQueue(RedQueue[ DBScannerEventRetModel]):
    def __init__(self):
        super().__init__(
            redis_cache.redis, "scanner::queue:write:db")

    def decode(self, ret: bytes) -> T:
        return DBScannerEventRetModel.parse_raw(ret)

    def encode(self, obj:DBScannerEventRetModel) -> bytes:
        return obj.json().encode()