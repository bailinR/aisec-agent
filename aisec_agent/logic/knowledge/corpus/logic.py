#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/7/9 18:01
# @Author  : GuJR
# @Site    : 
# @File    : logic.py
import json
import time
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper, ESQueryBuilder, es_client
from aisec_agent.logic.knowledge.logic import PreFileLogic
from aisec_agent.model.define import BaseHandler, RedisHelper
from aisec_agent.model.llm_typing import MakeCorpusModel
from aisec_agent.model.prompts import MakeCorpusQuestion


class RedisCorpusBase(BaseHandler):
    """Redis的方式实现语料库"""
    def _init(self):
        self.redis = RedisHelper()

    def batch_import_qa(self, data_path):
        """批量导入问答数据到Redis有序集合（带复合权重）"""
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
            pipe = self.redis.pipeline()
            count_key = f"corpus:medical:count"
            for item in data:
                instruction = item["instruction"]
                pipe.hget(count_key, instruction)
            count_results = pipe.execute()

            pipe = self.redis.pipeline()
            for i, item in enumerate(data):
                instruction = item["instruction"]
                timestamp = int(time.time())
                current_count = int(count_results[i] or 0) + 1
                score = timestamp * (1 + current_count / 100)
                pipe.hincrby(count_key, instruction, 1)
                pipe.zadd("corpus:medical:json", {instruction: score})

            return pipe.execute()

    def search_data(self, query: str, size=10000):
        """使用ZSCAN实现医疗问答搜索"""
        cursor = 0
        results = []

        while True:
            cursor, items = self.redis.zscan(
                name="corpus:medical:json",
                cursor=cursor,
                match=f"*{query}*",
                count=size
            )

            for item in items:
                value = json.loads(item[0])
                results.append(value["instruction"])

                if len(results) >= size:
                    break

            if cursor == 0 or len(results) >= size:
                break

        return results[:size]


class ESCorpusBase(BaseHandler):
    """ES的方式实现语料库"""
    def _init(self):
        self.es = ElasticsearchHelper()

    def init_medical_index(self):
        """初始化医疗问答索引"""
        mappings = {
            "properties": {
                "instruction": {
                    "type": "text",
                    "analyzer": "ik_max_word",  # 中文分词
                    "search_analyzer": "ik_smart"
                },
                "input": {
                    "type": "text",
                    "analyzer": "ik_max_word"
                },
                "output": {
                    "type": "text",
                    "analyzer": "ik_max_word"
                },
                "history": {
                    "type": "nested"  # 嵌套类型支持历史对话
                },
                "create_time": {
                    "type": "date",
                    "format": "yyyy-MM-dd HH:mm:ss||epoch_millis"
                },
                "tags": {
                    "type": "corpus"  # 用于精确过滤
                }
            }
        }

        settings = {
            "number_of_shards": 5,  # 根据数据量调整
            "number_of_replicas": 1,
            "refresh_interval": "30s",  # 批量导入时临时调大
            "index.store.preload": ["nvd", "dvd"]  # 预加载
        }

        # 使用现有工具类创建索引
        self.es.ensure_index(
            index_name="corpus_json",
            mappings=mappings,
            settings=settings
        )

    def import_data(self, data_path):
        """批量导入问答数据"""
        def gen_actions():
            with open(data_path, encoding="utf-8") as f:
                for line in json.load(f):
                    yield {
                        "_index": "medical_qa",
                        "_source": {
                            "instruction": line["instruction"],
                            "input": line.get("input", ""),
                            "output": line["output"],
                            "history": line.get("history"),
                            "create_time": int(time.time() * 1000),
                            "tags": line.get("tags", [])
                        }
                    }

        # 使用工具类的批量操作
        success, _ = self.es.bulk_operation(
            operations=list(gen_actions()),
            refresh=True
        )
        return success

    def search_data(self, query, page=1, size=1000):
        """医疗问答搜索"""
        builder = ESQueryBuilder()

        # 多字段搜索
        multi_match = {
            "multi_match": {
                "query": query,
                "fields": ["instruction^3", "output^2", "input"],  # 权重设置
                "type": "best_fields",
                "tie_breaker": 0.3
            }
        }

        # 构建查询
        builder.must(multi_match)
        builder.paginate(page, size)
        builder.highlight({
            "fields": {
                "instruction": {},
                "output": {}
            }
        })

        # 使用工具类执行搜索
        result = self.es.search_documents(
            query=builder.build()["query"],
            from_=builder._from,
            size=builder._size,
            highlight=builder._highlight,
            index_name=["corpus_json"]
        )

        # 处理结果
        hits = result["hits"]["hits"]
        return [{
            "id": hit["_id"],
            "score": hit["_score"],
            "instruction": hit["_source"]["instruction"],
            "highlight": hit.get("highlight", {}),
            "answer": hit["_source"]["output"]
        } for hit in hits]


class LLMCorpusBase(BaseHandler):
    """使用大模型创建的语料库"""
    def _init(self):
        self.redis = RedisHelper()
        self.es = ElasticsearchHelper()

    def import_redis_data(self, topic: str, file_id: str = "", page=1, page_size=10000, info_size=10000):
        """导入数据到Redis并持久化"""
        datas = PreFileLogic().page_knowledge(topic, file_id, page, page_size)
        knowledge_info = ""
        result_corpus = []
        for idx, data in enumerate(datas["data"]):
            if not data["content"]:
                continue
            knowledge_info += data["content"]+"\n"
            if len(knowledge_info) >= info_size or idx == len(datas["data"]) - 1:
                if knowledge_info.strip():
                    question = MakeCorpusQuestion(knowledge_info).replace(" ", "")
                    result_corpus += aided_chat(question=question, prompt="你是一个提问者, 根据资料和用户需求生成问题", _typing=MakeCorpusModel)["corpus"]
                knowledge_info = ""
            knowledge_info += data["content"]+"\n"
            if len(knowledge_info) >= info_size:
                question = MakeCorpusQuestion(knowledge_info).replace(" ", "")
                result_corpus += aided_chat(question=question, prompt="你是一个提问者, 根据资料和用户需求生成问题", _typing=MakeCorpusModel)["corpus"]
                knowledge_info = ""
        pipe = self.redis.pipeline()
        for item in result_corpus:
            pipe.zadd(f"corpus:medical:llm:{topic}", {item: int(time.time())})
            pipe.persist(f"corpus:medical:llm:{topic}")  # 设置持久化
        pipe.execute()
        return len(result_corpus)

    def search_redis_data(self, query: str, topics: list, size=10000):
        """搜索redis中的大模型创建的语料库"""
        cursor = 0
        results = []
        while True:
            for topic in topics:
                cursor, items = self.redis.zscan(
                    name=f"corpus:medical:llm:{topic}",
                    cursor=cursor,
                    match=f"*{query}*",
                    count=size
                )
                for item in items:
                    results.append(item[0].decode('utf-8'))
                    if len(results) >= size:
                        break
            if cursor == 0 or len(results) >= size:
                break
        return results

    def import_es_data(self, topic: str, file_id: str, ranger: int, page=1, page_size=10000):
        datas = PreFileLogic().page_knowledge(topic, file_id, page, page_size)
        result_corpus = {}
        for data in datas["data"]:
            if ranger and bool(data["corpus"]):
                continue
            question = MakeCorpusQuestion(data["content"]).replace(" ", "")
            value = aided_chat(question=question, prompt="你是一个提问者, 根据资料和用户需求生成问题", _typing=MakeCorpusModel)["corpus"]
            self.es.add_field(topic=topic, field_name="corpus", id_value_map={data["id"]: value})
        return result_corpus

    def search_es_data(self, query: str, topics: list, page: int=1, size: int=10000):
        """搜索es中的大模型语料"""
        if not query or not isinstance(query, str):
            return []

        builder = ESQueryBuilder()

        # 构建模糊匹配查询
        match_prefix = {
            "match_phrase_prefix": {
                "corpus": {
                    "query": query,
                    "max_expansions": 50,  # 控制前缀扩展数量
                    "slop": 3  # 允许的词间距
                }
            }
        }

        # 添加模糊匹配
        fuzzy_match = {
            "match": {
                "corpus": {
                    "query": query,
                    "fuzziness": "AUTO",
                    "prefix_length": 1
                }
            }
        }
        existing_topics = []
        with es_client() as client:
            for topic in topics:
                if client.indices.exists(index=topic):
                    existing_topics.append(topic)
        # 组合查询：优先前缀匹配，其次模糊匹配
        builder.should(match_prefix)
        builder.should(fuzzy_match)
        builder.paginate(page=page, size=size)

        try:
            result = self.es.search_documents(
                query=builder.build()["query"],
                index_name=existing_topics,
                size=size
            )

            # 提取并返回匹配的corpus内容
            hits = result["hits"]["hits"]
            results = sum([hit["_source"]["corpus"] for hit in hits if "corpus" in hit["_source"]], [])
            return list(set([result for result in results if query in result]))

        except Exception as e:
            return []


class CorpusCRUDLogic(BaseHandler):
    def _init(self):
        self.es = ElasticsearchHelper()
        self.redis = RedisHelper()

    def revise_content(self, id: str, topic: str, corpus: list):
        update_data = {"corpus": corpus}
        self.es.update_document(doc_id=id, document=update_data, index_name=topic, refresh=True)

    def force_replace_bulk(self, topic: str, new_corpus: list, batch_size=1000) -> None:
        """Redis批量替换语料"""
        redis_key = f"corpus:medical:llm:{topic}"

        delete_pipe = self.redis.pipeline()
        delete_pipe.delete(redis_key)
        delete_pipe.execute()
        timestamp = int(time.time())
        added = 0
        pipe = self.redis.pipeline()
        for i, item in enumerate(new_corpus, 1):
            pipe.zadd(redis_key, {item: timestamp})

            if i % batch_size == 0 or i == len(new_corpus):
                added += sum(pipe.execute())
                if i < len(new_corpus):
                    pipe = self.redis.pipeline()

