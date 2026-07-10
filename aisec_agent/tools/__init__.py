from datetime import datetime, time
from enum import IntEnum
from typing import List, Dict, AsyncGenerator

import sqlglot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot.errors import ParseError


def is_select_only(sql: str) -> bool:
    """
    使用 sqlglot 判断 SQL 是否全部为 SELECT 或 WITH 查询
    """
    try:
        expressions = sqlglot.parse(sql)
        for expr in expressions:
            if expr is None or expr.key.lower() not in ("SELECT".lower(), "WITH".lower()):
                return False
        return True
    except ParseError:
        return False

def convert_datetime_to_str(dt: datetime) -> str:
    """转化日期时间为字符串"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def convert_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M:%S").time()

class BaseIntEnum(IntEnum):
    @classmethod
    def descriptions(cls) -> Dict[int, str]:
        """
        子类必须实现的方法，返回一个字典，将枚举值映射到其描述。
        """
        raise NotImplementedError("Subclasses must implement this method")

    @classmethod
    def _cache_desc(cls) -> Dict[int, str]:
        """
        缓存描述字典以提高性能。
        """
        if not hasattr(cls, '_description_cache'):
            cls._description_cache = cls.descriptions()
        return getattr(cls, "_description_cache")

    @classmethod
    def get(cls, value: int) -> str:
        """
        获取每个枚举值对应的描述，如果值不存在，则返回"未知"。
        """
        return cls._cache_desc().get(value, "未知")

    @classmethod
    def get_name(cls, value: int) -> str:
        """
        获取每个枚举值对应的name，如果name不存在，则返回"未知"。
        """
        for name, member in cls.__members__.items():
            if member.value == value:
                return name.lower()
        return "未知"

    @classmethod
    def to_ui_format(cls) -> List[Dict[str, str | int]]:
        """
        返回适合前端使用的枚举类型格式化数据，包含描述和枚举值。
        """
        return [dict(name=cls.get(item), value=item.value) for item in cls]


async def yield_model_data(ctx: AsyncSession, stmt, page_size: int = 1000
                           ) -> AsyncGenerator:
    """
    逐行获取数据库数据
    :param ctx: 数据库session
    :param stmt: 数据库查询条件不包含limit offset
    :param page_size: 每次查询获取数据量
    :return: yield
    """
    offset = 0
    while True:
        new_stmt = stmt.limit(page_size).offset(offset)
        result = await ctx.execute(new_stmt)
        rows = result.fetchall()

        if not rows:
            break

        for row in rows:
            yield row

        offset += page_size


def delete_keys_with_prefix(client, prefix):
    """删除redis某个前缀开头的key"""
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor, match=f"{prefix}*", count=100)
        if keys:
            client.delete(*keys)  # 批量删除匹配的键
        if cursor == 0:
            break
