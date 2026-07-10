#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/29 13:53
# @Author  : GuJR
# @Site    : 
# @File    : enumerate.py

from enum import Enum, IntEnum


class TaskType(IntEnum):
    """任务类型枚举"""
    RETRIEVAL_QUERY = 0
    RETRIEVAL_PASSAGE = 1
    SEPARATION = 2
    CLASSIFICATION = 3
    TEXT_MATCHING = 4

    @classmethod
    def to_dict(cls) -> dict:
        """转换为字典映射"""
        return {
            'retrieval.query': cls.RETRIEVAL_QUERY,
            'retrieval.passage': cls.RETRIEVAL_PASSAGE,
            'separation': cls.SEPARATION,
            'classification': cls.CLASSIFICATION,
            'text-matching': cls.TEXT_MATCHING
        }


class DocType(IntEnum):
    """文档类型枚举"""
    LOG = 1
    REPORT = 2
    ASSET = 3
    CODE = 4
    INTRODUCE = 5
    QA = 6
    TEACH = 7

    @classmethod
    def to_dict(cls) -> dict:
        """转换为字典映射"""
        return {
            cls.LOG: "log",      # 日志
            cls.REPORT: "report",   # 报告
            cls.ASSET: "asset",    # 资产表
            cls.CODE: "code",      # 代码
            cls.INTRODUCE: "introduce",      # 介绍
            cls.QA: "QA",      # 问答
            cls.TEACH: "teach"      # 教程
        }
