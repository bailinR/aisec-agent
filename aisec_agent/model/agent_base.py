#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/19 13:57
# @Author  : GuJR
# @Site    : 
# @File    : agent_base.py
import logging
from typing import Any, Dict, List, Type, Tuple
from sqlalchemy import text
from sqlalchemy import inspect, and_, not_, or_
from sqlalchemy.sql import select
from aisec_agent.model.define import session_ctx, BaseHandler, Base


def query_mysql(sql: str, params: Dict = None):
    try:
        with session_ctx(True) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            result = session.execute(text(sql), params or {})
        return result
    except Exception as e:
        raise Exception(f"SQL查询异常: {str(e)}")


class DatabaseQuery:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def direct_sql_query_table(self, sql: str, params: Dict = None, **kwargs) -> str:
        """用户明确说明需要报告时才用，将查询结果转为markdown表格"""
        try:
            result = query_mysql(sql, params)
            columns = result.keys()
            records = []
            for row in result.fetchall():
                record = {}
                for i, column in enumerate(columns):
                    value = row[i]
                    if hasattr(value, 'isoformat'):
                        value = value.isoformat()
                    record[column] = value
                records.append(record)
            if not records:
                return "无查询结果"

            # 使用查询到的字段作为列名
            columns = list(records[0].keys())

            # 构建表头
            header = " | ".join(columns)
            # 构建分隔行
            separator = " | ".join(["---" for _ in columns])

            # 构建数据行
            rows = []
            for record in records:
                row_values = []
                for col in columns:
                    value = record.get(col)
                    if value is None:
                        value = ""
                    elif isinstance(value, (list, dict)):
                        value = str(value)
                    row_values.append(str(value).replace("\n", ""))
                rows.append(" | ".join(row_values))

            return f"| {header} |\n| {separator} |\n" + "\n".join(f"| {row} |" for row in rows)

        except Exception as e:
            raise Exception(str(e))

    def direct_sql_query_count(self, sql: str, params: Dict = None, **kwargs) -> int:
        """用户明确声明只进行统计查询时才使用"""
        try:
            result = query_mysql(sql, params).fetchall()[0][0]
            return result
        except Exception as e:
            raise Exception(str(e))

