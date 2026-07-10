#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DataAnalysisAgent
~~~~~~~~~~~~~~~~~
按照 “数据预览 → LLM 生成 SQL → 执行查询 → 分析结果” 的流程，
自动完成常见指标（同比、环比、占比、最大/最小/平均/中位数/标准差）的分析。
"""
import re
import requests
import time
from typing import List, Dict, Any

import pandas as pd

from aisec_agent.config import DUCK_URL
from aisec_agent.logic._tools import aided_chat
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import DuckTaskModel
from aisec_agent.model.prompts import DuckPrompt


# -----------------------------------------------------------
# 工具函数
# -------------------------------------------------------

def detect_time_columns(row: Dict[str, Any]) -> List[str]:
    """根据列名简单识别时间列"""
    pattern = re.compile(r'(date|时间|year|month|day)', re.I)
    return [k for k in row if pattern.search(k)]

def parse_intent(user_need: str) -> Dict[str, bool]:
    text = user_need.lower()
    return {
        'yoy':  any(k in text for k in ['同比', 'yoy', 'year over year']),
        'mom':  any(k in text for k in ['环比', 'mom', 'month over month']),
        'ratio': '占比' in text or 'ratio' in text,
    }

# -----------------------------------------------------------
# 主类
# -----------------------------------------------------------
class DataAnalysisAgent(BaseHandler):
    def  _init(self,  ):
        self.preview_url, self.query_url = f'{DUCK_URL }scanner/duck/preview',  f'{DUCK_URL }scanner/duck/query',
        self.headers = {'Content-Type': 'application/json'}

    # ---------- 数据源 ----------
    @staticmethod
    def setup_data_source(bucket: str,sources, ) -> Dict:
        return {'bucket': bucket,
                'sources': [{'object_name': entry["object_name"],
                             'table_name':f"t{i+1}",
                             'format':  entry['format']}  for i, entry in enumerate(sources)]}

    # ---------- 预览 ----------
    def get_preview(self, req: Dict) -> Dict:
        try:
            r = requests.post(self.preview_url, json=req,  headers=self.headers, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            self.logger.debug('❌ 预览失败:', exc)
            return {}

    # ---------- 生成 SQL ----------
    def generate_sql(self, user_need: str, preview: Dict, **kwargs) -> str:
        tables, sample_row = [], {}
        for t, rows in preview.get('data', {}).items():
            if rows:
                tables.append(f'表 {t} 列: {",".join(rows[0])}')
                sample_row = {c: None for c in rows[0]}

        prompt = DuckPrompt(tables, user_need)
        try:
            sql_text = aided_chat(prompt, user_need, DuckTaskModel, **kwargs)
            return  sql_text.get("sql")
        except Exception as exc:
            self.logger.debug('⚠️ LLM 生成 SQL 失败，使用兜底:', exc)
            # 为每个表生成查询，并用UNION ALL连接
            queries = [f"SELECT * FROM {t} LIMIT 1000" for  t, rows in preview.get('data', {}).items()]
            return " UNION ALL ".join(queries)

    # ---------- 执行 SQL ----------
    def execute_sql(self, req: Dict, sql: str,name:str) -> Dict:
        payload = dict( sql=sql,export=True, export_path=name)
        payload.update(req)
        # print("请求结构",payload)
        try:

            r = requests.post(self.query_url, json=payload,
                              headers=self.headers, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            self.logger.debug('❌ 查询失败:', exc)
            return {}

    # ---------- 结果分析 ----------
    def analyze(self, result: Dict, intents: Dict[str, bool]) -> None:
        if not (result.get('data') and result['data'].get('data')):
            self.logger.debug('结果为空'); return

        df = pd.DataFrame(result['data']['data'])
        self.logger.debug(f'\n✅ 共 {len(df)} 行, {len(df.columns)} 列')
        self.logger.debug('字段:', ', '.join(df.columns))

        # ---------- 基础统计 ----------
        num = df.select_dtypes(include='number')
        if not num.empty:
            desc = num.describe(percentiles=[]).T
            desc['median'] = num.median()
            desc = desc[['count', 'mean', 'min', 'median', 'max', 'std']]
            self.logger.debug('\n📊 基本统计 (count/mean/min/median/max/std):')
            self.logger.debug(desc)

        # ---------- 时间维度 ----------
        time_cols = detect_time_columns(df.iloc[0].to_dict())
        if time_cols:
            self.time_series_analysis(df, time_cols[0], intents, num.columns.tolist())

        # ---------- 占比 ----------
        if intents['ratio'] and not num.empty:
            total = num.sum().sum()
            self.logger.debug('\n📌 占比 (各数值列占整体百分比):')
            for col in num.columns:
                pct = num[col].sum() * 100 / total
                self.logger.debug(f' - {col}: {pct:.2f}%')

    def time_series_analysis(self, df: pd.DataFrame,
                             tcol: str,
                             intents: Dict[str, bool],
                             value_cols: List[str]) -> None:
        try:
            df[tcol] = pd.to_datetime(df[tcol], errors='coerce')
            if df[tcol].isna().all():
                return
            df['year'] = df[tcol].dt.year
            df['month'] = df[tcol].dt.month
            for val in value_cols[:1]:  # 只演示首个数值列
                grp = (df.groupby(['year', 'month'])[val]
                         .sum().reset_index()
                         .sort_values(['year', 'month']))
                if intents['mom']:
                    grp['MoM_%'] = grp[val].pct_change().mul(100).round(2)
                if intents['yoy']:
                    grp['YoY_%'] = grp[val].pct_change(12).mul(100).round(2)
                self.logger.debug(f'\n📈 {val} 月度汇总:')
                self.logger.debug(grp.tail(12).fillna('-'))
        except Exception as exc:
            self.logger.debug('⚠️ 时间分析失败:', exc)

    # ---------- 运行 ----------
    def run(self,
            bucket: str,
            sources: List[Dict],
            user_need: str, agent_extra: dict = {}
           )  :
        req = self.setup_data_source(bucket, sources )

        preview = self.get_preview(req)
        if not preview:
            self.logger.debug('无法获取预览，终止'); return

        sql = self.generate_sql(user_need, preview, **agent_extra)

        self.logger.debug('\n📝 生成 SQL:', sql)

        name =  f"{bucket}_duck_{int(time.time())}.csv"
        result = self.execute_sql(req, sql,name)

        return result.get("data",{}).get("data"),result.get("data",{}).get("urls"),result.get("data",{}).get("count"),name
        # self.analyze(result, intents)
 