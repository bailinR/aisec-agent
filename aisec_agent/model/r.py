#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/4/27 18:00
# @Author  : GuJR
# @Site    : 
# @File    : r.py.py

def classify_data(padding_json):
    type_mapping = {
        "文本": "text_type",
        "图表": "text_type",
        "数值": "data_type",
        "表格": "table_type"
    }

    result = {
        "text_type": [],
        "data_type": [],
        "table_type": []
    }

    for data in padding_json:
        category = type_mapping.get(data["type"])
        if category:
            result[category].append(data)

    return result["text_type"], result["data_type"], result["table_type"]


SELECT_TABLES = ["ai_assets", "ai_assets_manage", "ai_vuln"]
MINIO_IMAGE = "doc-img-file"
