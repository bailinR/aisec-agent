#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/18 10:22
# @Author  : GuJR
# @Site    : form-validate==0.0.1.1
# @File    : schema.py
from pydantic import BaseModel, Field
from typing import Dict,Optional,List
from datetime import datetime
from ..tools  import BaseIntEnum
class MessageTyp(BaseIntEnum):
    """
    消息类型
    1 MYSQL
    2 MSSQL
    """
    MYSQL = 1
    MSSQL = 2

    @classmethod
    def descriptions(cls) -> Dict[int, str]:
        return {
            cls.MYSQL.value: "mysql数据库",
            cls.MSSQL.value: "微软SQL Server·",
        }


class EventTyp(BaseIntEnum):
    ALL = 1
    UPDATE = 2
    DELETE = 3
    CREATE = 4

    QUERYSQL = 5

    def descriptions(cls) -> Dict[int, str]:
        return {
            cls.ALL.value: "获取全部表",
            cls.UPDATE.value: "更新表",
            cls.DELETE.value: "删除表",
            cls.CREATE.value: "创建表",
            cls.QUERYSQL.value: "执行sql查询"
        }


class TaskStaus(BaseIntEnum):
    """0 未执行 1 执行中 2 执行完成 3 执行失败"""
    UNEXECUTED = 0
    EXECUTING = 1
    EXECUTED = 2
    FAILED = 3

    @classmethod
    def descriptions(cls) -> Dict[int, str]:
        return {
            cls.UNEXECUTED.value: "未执行",
            cls.EXECUTING.value: "执行中",
            cls.EXECUTED.value: "执行完成",
            cls.FAILED.value: "执行失败",
        }



class BaseSchema(BaseModel):
    class Config:
        orm_mode = True
        from_attributes = True

class BaseUseEnumConf(BaseModel):
    class Config:
        use_enum_values = True

class LLMConfigGetSchema(BaseSchema):
    id: int = Field(..., description='llm id')
    url: str = Field(None, description='api url')
    key: str = Field(None, description='api key')
    function_name: str = Field(None, description='api来源')
    model_name: str = Field(None, description='模型名称')
    max_len_input: int | None = Field(None, description='模型接受的最大输入长度')
    is_public: bool = Field(..., description='是否公开')


class ColumnRetModel(BaseModel):
    column: str = Field(..., alias="column", description="列名称")
    ch_name: Optional[str] = Field(None, alias="ch_name", description="中文列名")
    data_type: str = Field(..., alias="data_type", description="数据类型")


class TableRetModel(BaseModel):
    table_name: str = Field(..., description="表名称")
    ch_name: Optional[str] = Field(None, description="中文表名")
    last_update: Optional[datetime] = Field(None, description="最后更新时间 ")
    columns: Optional[List[ColumnRetModel]] = Field(None, description="列信息")

class DBScannerRetModel(TableRetModel):
    db_name: str = Field(..., description="数据库名称")
    host: str = Field(..., description="主机")
    user: str = Field(..., description="用户")
    port: Optional[int] = Field(None, description="端口")

class DBScannerEvent(BaseUseEnumConf):
    task_id: str = Field(..., description="任务id")
    message_typ: MessageTyp = Field(..., description="消息类型")
    event_typ: EventTyp = Field(..., description="消息类型")
    db_name: str = Field(..., description="数据库名称")
    port: Optional[int] = Field(None, description="端口")
    host: str = Field(..., description="主机")
    user: str = Field(..., description="用户")
    password: Optional[str] = Field(None, description="密码")
    table_name: Optional[str] = Field(None, description="表名称")
    sql: Optional[str] = Field(None, description="sql语句")
    name: str = Field(...)
    mapping: Optional[List[str]] = Field(None, description="映射关系")


class DBScannerEventRetModel(DBScannerEvent, TableRetModel):
    task_id: Optional[str] = Field(None, description="任务id")
