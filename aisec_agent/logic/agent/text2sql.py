#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/6/16 15:48
# @Author  : GuJR
# @Site    : 
# @File    : text2sql.py
from datetime import datetime
from typing import Dict, List
from aisec_agent.logic._tools import LoadFileTools
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper, es_client
from aisec_agent.logic.scanner.logic import ScannerHandler
from aisec_agent.model.define import BaseHandler, session_ctx
from aisec_agent.model.llm_typing import SelectHRTable, EsQueryModel, RectifyPromptModel, HRPromptModel, \
    HRSplitPromptModel
from aisec_agent.model.model import TablesDesModel, DbSettingsModel, AIKnowledgeModel
from aisec_agent.model.prompts import select_hr_table, query_daily_es, rectify_prompt, hr_prompt, hr_split_prompt


def rectify_sql(user_query: str, sql_outcome: any, sql: str) -> dict:
    prompt = rectify_prompt(sql_outcome, sql)
    result_dict = aided_chat(prompt, user_query, RectifyPromptModel)
    return result_dict

def re_generate_sql(user_query: str, sql: str, reason: str, prompt: str, _typing):
    query = f"""
<查询方法>
{sql}
</查询方法>
当前查询方法无法满足用户提出的问题:{user_query}
原因如下:{reason}
请重新生成查询方法
"""
    return aided_chat(prompt, query, _typing)

def retry_sql(user_query: str, sql_outcome: any, prompt: str, _typing, result_dict: dict):
    for i in range(3):
        rectify_dict = rectify_sql(user_query, str(sql_outcome), sql=str(result_dict))
        if rectify_dict["conform"]:
            return result_dict
        result_dict = re_generate_sql(user_query, sql=str(result_dict), reason=rectify_dict["reason"], prompt=prompt, _typing=_typing)
    return result_dict


def clean_data(data: list, title: list, filter_: list, agg: dict) -> list:
    import operator
    from collections import defaultdict

    # 支持的操作符
    ops = {
        "==": operator.eq,
        "!=": operator.ne,
        ">=": operator.ge,
        "< =": operator.le,
        ">": operator.gt,
        "<": operator.lt,
    }

    def safe_float(x):
        try:
            return float(x)
        except (ValueError, TypeError):
            return 0.0

    def parse_condition(cond):
        # 支持 in 和 not in
        if " not in " in cond:
            field, value = cond.split(" not in ", 1)
            field = field.strip()
            value = eval(value.strip())  # 这里假设输入可信
            return field, lambda x, y: x not in y, value
        if " in " in cond:
            field, value = cond.split(" in ", 1)
            field = field.strip()
            value = eval(value.strip())  # 这里假设输入可信
            return field, lambda x, y: x in y, value

        # 其他操作符
        for op_str, op_func in ops.items():
            if op_str in cond:
                field, value = cond.split(op_str, 1)
                field = field.strip()
                value = value.strip().strip('"').strip("'")
                try:
                    value = float(value)
                except ValueError:
                    pass
                return field, op_func, value
        raise ValueError(f"不支持的条件表达式: {cond}")

    # 过滤
    filtered = []
    for row in data:
        match = True
        for cond in filter_:
            field, op_func, value = parse_condition(cond)
            row_value = row.get(field)
            try:
                row_value = float(row_value)
            except (ValueError, TypeError):
                pass
            if not op_func(row_value, value):
                match = False
                break
        if match:
            filtered.append(row)

    # 字段筛选
    if title:
        filtered = [{k: row.get(k, None) for k in title} for row in filtered]

    # 聚合
    if not agg:
        return filtered

    # 分组+多字段占比
    if "groupby" in agg and "proportion_fields" in agg:
        group_fields = agg["groupby"]
        if isinstance(group_fields, str):
            group_fields = [group_fields]
        fields = agg["proportion_fields"]
        group_dict = defaultdict(list)
        for row in filtered:
            key = tuple(row.get(f) for f in group_fields)
            group_dict[key].append(row)
        result = []
        for group_value, group_rows in group_dict.items():
            row = {}
            if len(group_fields) == 1:
                row[group_fields[0]] = group_value[0]
            else:
                for idx, f in enumerate(group_fields):
                    row[f] = group_value[idx]
            total = sum(sum(safe_float(r.get(f, 0)) for f in fields) for r in group_rows)
            for f in fields:
                f_sum = sum(safe_float(r.get(f, 0)) for r in group_rows)
                row[f"{f}_proportion"] = (f_sum / total) if total else None
            result.append(row)
        return result

    # 兼容原有的分组单字段占比
    if "groupby" in agg and "proportion" in agg:
        group_field = agg["groupby"]
        prop = agg["proportion"]
        group_dict = defaultdict(list)
        for row in filtered:
            group_dict[row.get(group_field)].append(row)
        result = []
        for group_value, group_rows in group_dict.items():
            row = {group_field: group_value}
            if isinstance(prop, list) and len(prop) >= 2:
                num_field = prop[0]
                denom_fields = prop[1:]
                num = sum(safe_float(r.get(num_field, 0)) for r in group_rows)
                denom = sum(sum(safe_float(r.get(f, 0)) for f in denom_fields) for r in group_rows)
                row[f"{num_field}_to_{'+'.join(denom_fields)}_proportion"] = (num / denom) if denom else None
            result.append(row)
        return result

    # 兼容原有的单字段聚合
    if "sum" in agg and "groupby" not in agg:
        field = agg["sum"]
        total = sum(safe_float(row.get(field, 0)) for row in filtered)
        return [{field + "_sum": total}]
    if "avg" in agg and "groupby" not in agg:
        field = agg["avg"]
        values = [safe_float(row.get(field, 0)) for row in filtered]
        avg = sum(values) / len(values) if values else 0
        return [{field + "_avg": avg}]
    if "count" in agg and "groupby" not in agg:
        return [{"count": len(filtered)}]
    if "max" in agg and "groupby" not in agg:
        field = agg["max"]
        values = [safe_float(row.get(field, 0)) for row in filtered]
        return [{field + "_max": max(values) if values else None}]
    if "min" in agg and "groupby" not in agg:
        field = agg["min"]
        values = [safe_float(row.get(field, 0)) for row in filtered]
        return [{field + "_min": min(values) if values else None}]

    # 兼容原有的分组聚合
    if "groupby" in agg:
        group_field = agg["groupby"]
        group_dict = defaultdict(list)
        for row in filtered:
            group_dict[row.get(group_field)].append(row)
        result = []
        for group_value, group_rows in group_dict.items():
            row = {group_field: group_value}
            for op in ["sum", "avg", "count", "max", "min"]:
                if op in agg:
                    field = agg[op]
                    values = [safe_float(r.get(field, 0)) for r in group_rows]
                    if op == "sum":
                        row[field + "_sum"] = sum(values)
                    elif op == "avg":
                        row[field + "_avg"] = sum(values) / len(values) if values else 0
                    elif op == "count":
                        row["count"] = len(values)
                    elif op == "max":
                        row[field + "_max"] = max(values) if values else None
                    elif op == "min":
                        row[field + "_min"] = min(values) if values else None
            result.append(row)
        return result

    return filtered


def split_data(user_query: str, datas: list):
    split_prompt = hr_split_prompt(datas[:5])
    split_dict = aided_chat(split_prompt, user_query, HRSplitPromptModel)
    return clean_data(data=datas, title=split_dict["title"], filter_=split_dict["filter"], agg=split_dict["agg"])


class Text2SQLLogic(BaseHandler):
    def query_hr_data(self, user_query: str):
        select_table_prompt = select_hr_table(user_query)
        select_table_dict = aided_chat(select_table_prompt, user_query, SelectHRTable)
        tables = select_table_dict.get("selected_tables")
        self.logger.info(f"prompt_len: {str(tables)}")
        if not tables:
            return
        nb_json = LoadFileTools().load_json_file("content/HRTable/HRsql.json")
        table_info, db_config = self.query_db_table(tables)
        query_hr_prompt = hr_prompt(user_query)
        result_sql = aided_chat(query_hr_prompt, user_query, HRPromptModel)
        sql = nb_json[str(result_sql["nb"])]
        result_sql.update({"tables":tables})
        return ScannerHandler().exec_sql(sql=sql, **db_config)
        #
        # table_info, db_config = self.query_db_table(tables)
        # table_info = "\n\n".join([f"<table_name: {table}>"+LoadFileTools().load_file(HR_TABLES_PATH, table, "table") for table in tables])
        # query_hr_prompt = query_hr_table(table_info=table_info,
        # user_query=user_query, time_now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        # result_sql = aided_chat(query_hr_prompt, user_query, QueryHRTable)
        # result_sql.update({"tables":tables})
        # sql = LoadJsonTools().strQ2B(result_sql.get("sql"))
        # return ScannerHandler().exec_sql(sql=sql, **db_config)

    def query_db_table(self, tables: list) -> tuple:
        with session_ctx(True) as ctx:
            db_tables = ctx.query(TablesDesModel).filter(TablesDesModel.table_name.in_(tables)).all()
            db_id_list = []

            # 整合表结构信息
            tables_info = []
            for table in db_tables:
                table_info = {
                    "table_name": table.table_name,
                    "ch_name": table.ch_name,
                    "columns": table.columns,
                }
                tables_info.append(table_info)
                db_id_list.append(table.db_id)

            # 构建完整的表结构描述
            schema_description = "数据库表结构如下：\n"
            for table in tables_info:
                schema_description += f"表名：{table['table_name']}（{table['ch_name']}）\n"
                schema_description += "字段列表：\n"
                for column in table['columns']:
                    schema_description += f"- {column['column']}（{column['ch_name']}）：{column['data_type']}\n"
                schema_description += "\n\n"

            db_id_set = set(db_id_list)
            db_tables = ctx.query(DbSettingsModel).filter(DbSettingsModel.id.in_(db_id_set)).all()[0]
            db_config = {"host":db_tables.host, "db_name":db_tables.db_name, "user":db_tables.user,
                         "port":db_tables.port, "message_typ":2}
            return schema_description, db_config


class Text2ES(BaseHandler):

    def es_mapping_to_prompt(self, mapping_dict: dict) -> str:
        """
        将ES mapping结构转为大模型易懂、节省token的prompt结构
        """

        def get_field_type(field_info):
            t = field_info.get("type", "unknown")
            # 判断是否有keyword子字段
            if "fields" in field_info and "keyword" in field_info["fields"]:
                return f"{t}/keyword"
            return t

        lines = []
        for index, index_info in mapping_dict.items():
            props = index_info.get("properties", {})
            field_strs = []
            for field, info in props.items():
                field_type = get_field_type(info)
                field_strs.append(f"{field}({field_type})")
            # 每个索引一组
            lines.append(f"<索引名: {index}>\n" + ", ".join(field_strs) + f"\n</{index}>")
        return "\n\n".join(lines)

    def get_all_index_mappings(self):
        """
        获取ES中所有索引的mapping信息
        """
        with session_ctx(True) as ctx:
            knowledge_list = [data[0] for data in ctx.query(AIKnowledgeModel.knowledge_name).all()]

        with es_client() as client:
            # 用关键字参数
            indices = client.indices.get_alias(index="*").keys()
            # 获取每个索引的mapping
            all_mappings = {}
            for index in indices:
                if "loginfo" in index or index in knowledge_list:
                    continue
                mapping = client.indices.get_mapping(index=index)
                all_mappings[index] = mapping[index]['mappings']
        return self.es_mapping_to_prompt(all_mappings)

    def select_daily(self, user_query: str):
        table_info = self.get_all_index_mappings()
        daily_prompt = query_daily_es(table_info=table_info, user_query=user_query,
                       time_now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        query_daily_dict = aided_chat(daily_prompt, user_query, EsQueryModel)
        daily_outcome = self.execute_es_query(query_daily_dict)
        daily_result = self.es_result_to_markdown_table(daily_outcome)

        # query_daily_dict = retry_sql(user_query, daily_result, daily_prompt, EsQueryModel, query_daily_dict)
        # daily_outcome = self.execute_es_query(query_daily_dict)
        # daily_result = self.es_result_to_markdown_table(daily_outcome)
        if list(daily_result):
            daily_result = split_data(user_query, daily_result)
        return daily_result

    def es_result_to_markdown_table(self, es_result: dict, source: List[str] = None) -> list:
        """
        将Elasticsearch的查询结果统一转换为适合生成Excel的`[{}, {}]`格式的JSON字符串。
        - 聚合结果（group by/sum/avg）会被展平成列表。
        - 普通查询结果会提取_source。
        """
        hits = es_result.get("hits", {}).get("hits", [])
        if not hits:
            return []

        results = [hit.get("_source", {}) for hit in hits]
        return results

    def execute_es_query(self, query_plan: EsQueryModel, size: int = 10000) -> Dict:
        """
        根据大模型生成的查询计划，执行Elasticsearch检索。
        支持字段过滤和聚合。
        """
        try:
            # 构建请求体
            body = {
                "query": query_plan.get("query", {"match_all": {}})
            }
            # 字段过滤
            if "source" in query_plan:
                body["_source"] = query_plan["source"]
            # 聚合
            if "aggs" in query_plan:
                body["aggs"] = query_plan["aggs"]
            # 执行查询
            search_results = ElasticsearchHelper().search_documents(
                index_name=[query_plan["index"]],
                query=body["query"],
                source=body.get("_source"),
                aggs=body.get("aggs"),
                size=size
            )
            return search_results
        except Exception as e:
            return {"error": str(e), "hits": {"total": {"value": 0}, "hits": []}}