import abc
import logging
import time
from typing import Optional, Generator, Iterator, TypeVar, Generic

from redis import Redis

from ..logic.base import redis_cache

T = TypeVar('T')


class RedQueue(Generic[T], metaclass=abc.ABCMeta):

    def __init__(self, redis: Redis, resource: str):
        self._redis = redis
        self.logger = logging.getLogger(self.__class__.__name__)
        self._resource = resource

    @property
    def redis(self) -> Redis:
        return self._redis

    @property
    def resource(self) -> str:
        return self._resource

    @abc.abstractmethod
    def decode(self, ret: bytes) -> T:
        pass

    @abc.abstractmethod
    def encode(self, obj: T) -> bytes:
        pass

    def __len__(self):
        return self._redis.llen(self._resource)

    def pop(self) -> Optional[T]:
        ret = self._redis.lpop(self.resource)
        if ret is None:
            return None
        return self.decode(ret)

    def append(self, *args: T) -> int:
        return self._redis.rpush(self.resource, *tuple(map(self.encode, args)))

    def gen(self) -> Generator[T, None, None]:
        while True:
            p = self.pop()
            if p is None:
                break
            yield p

    @property
    def size(self):
        return self.__len__()

    @property
    def empty(self):
        return not self.size

    def read_n(self, n: int = 1, wait: float = 1.0, delay: float = 0.01) -> Iterator[T]:
        deadline = time.time() + wait
        counter = 0
        while counter < n and time.time() < deadline:
            try:
                item = self.pop()
            except Exception as e:
                self.logger.warning("read item error: {}".format(e))
                return
            if item:
                counter += 1
                yield item
            else:
                time.sleep(delay)

    def all(self):
        return self.redis.lrange(self.resource, 0, -1)


class _RedObj:
    redis: Redis

    def __init__(self, resource: str):
        self._resource = resource

    @property
    def resource(self):
        return self._resource


class RedObjMeta(type):
    def __new__(mcs, name, base, attributes):
        if not any(map(lambda v: v is _RedObj or issubclass(v, _RedObj), base)):
            raise Exception('class {} must be subclass of {}'.format(name, _RedObj))
        attributes.update(redis=redis_cache.redis)
        return super().__new__(mcs, name, base, attributes)


class RedHash(_RedObj):
    redis = redis_cache.redis

    def set(self, key: str, value: str, ex=None):
        self.redis.hset(self._resource, key, value)
        if ex is not None:
            self.redis.expire(self._resource, ex)

    def get(self, key):
        return self.redis.hget(self._resource, key)

    def get_all(self):
        return self.redis.hgetall(self._resource, )

    def remove(self, key: str, ):
        self.redis.hdel(self._resource, key, )

    def has(self, key: str) -> bool:
        return self.redis.hexists(self.resource, key)

    def get_len(self):
        return self.redis.hlen(self._resource)


class RedCounterManager(_RedObj, metaclass=RedObjMeta):
    def incr(self) -> int:
        return self.redis.incr(self.resource)

    def decr(self) -> int:
        return self.redis.decr(self.resource)

    def get(self):
        ret = self.redis.get(self.resource)
        return (ret and int(ret.decode())) or 0

    def __sub__(self, other):
        return self.get() - other.get()


class InUseCounter(RedCounterManager):
    def __init__(self):
        super().__init__("box::backup-task:count:in_use")


class RedZSet(_RedObj):
    redis = redis_cache.redis

    def add(self, member: str, score: float):
        """添加成员到 ZSET"""
        self.redis.zadd(self._resource, {member: score})

    def iter_by_prefix(self, prefix: str):
        """不传参，迭代 ZSET 中所有成员及其分数"""
        cursor = 0
        while True:
            cursor, items = self.redis.zscan(self._resource, cursor=cursor,
                                             match=f"{prefix}*")
            for member, score in items:
                yield member.decode(), float(score)
            if cursor == 0:
                break

    def remove(self, member: str):
        """移除成员"""
        self.redis.zrem(self._resource, member)

    def get_score(self, member: str):
        """获取某个成员的 score"""
        return self.redis.zscore(self._resource, member)

    def incr_score(self, member: str, increment: float = 1.0):
        """增加成员的 score"""
        return self.redis.zincrby(self._resource, increment, member)

    def count_by_score(self, min_score: float, max_score: float):
        """获取某个分数区间的数量"""
        return self.redis.zcount(self._resource, min_score, max_score)

    def clear(self):
        """删除整个 ZSET"""
        self.redis.delete(self._resource)

    def expire(self, seconds: int) -> bool:
        """设置 ZSET 的过期时间（单位：秒）"""
        return self.redis.expire(self._resource, seconds)