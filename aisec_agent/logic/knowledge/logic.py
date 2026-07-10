#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/28 20:47
# @Author  : GuJR
# @Site    : 
# @File    : embedding.py
import json
from typing import Dict, List, Any, Tuple
import logging
from io import BytesIO
from aisec_agent.config import DEFAULT_REDIS
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper, es_client
from aisec_agent.logic.knowledge.doc_analyzer import DocAnalysisPipeline
from aisec_agent.logic.knowledge.embedding.embedding import JinaEmbedding
import traceback
from aisec_agent.logic.knowledge._tool import PreFileTools
from aisec_agent.model.define import BaseHandler, RedisQueue
from aisec_agent.model.enumerate import DocType
from aisec_agent.logic._tools import FileReaderTools, aided_chat
from aisec_agent.model.llm_typing import OptKnowledgeModel
from aisec_agent.model.prompts import OptKnowledgePrompt
from aisec_agent.model.typing import KnowledgeFileForm, SearchConfForm


class PublishKBFile(BaseHandler):

    def _init(self):
        self.redis_conn = RedisQueue(**DEFAULT_REDIS)

    def task_publish(self, form: KnowledgeFileForm):
        self.redis_conn.rpush('file_process_queue', json.dumps(form.dict()))

    def task_status(self, file_id: str):
        return self.redis_conn.get_status(file_id)


class PreFileLogic(BaseHandler):
    """文件预处理逻辑类"""

    def pre_file(self, file: BytesIO, file_name: str, file_id: str, topic: str) -> Tuple:
        """处理文件预处理请求"""
        try:
            # 使用增强版文件读取工具读取文件内容
            # content = FileReaderTools().get_file_content(file, file_name).replace("  ", " ")
            # # 切片处理
            # slices_data = [chunk for chunk in content.replace("\\n", "\n").split("\n\n") if chunk and len(chunk) >= 10]
            slices_data = DocAnalysisPipeline().analyse(file_name, file)
            # 向量化并存储
            PreFileTools().store_embeddings(slices_data, file_id, file_name, topic)
            return len(slices_data), False
                
        except Exception as e:
            logging.error(f"文件预处理失败: {str(e)}")
            logging.error(traceback.format_exc())

    def opt_knowledge(self, question, topics, size=20, opt_content=5):
        knowledge_datas = self.search_knowledge_logic(question, topics, size)
        knowledge_data = "\n\n".join(
            [data["file_name"] + "(**相似度:" + str(data["similarity"]) + "**):\n"
             + data["content"] for data in knowledge_datas])
        if knowledge_data:
            opt_knowledge_prompt = OptKnowledgePrompt(knowledge_data, opt_content)
            sentences = aided_chat(opt_knowledge_prompt, question, OptKnowledgeModel)["sentences"]
            if sentences:
                size = int(size/opt_content)
                knowledge_datas = knowledge_datas[:size]
                for sentence in sentences:
                    for data in self.search_knowledge_logic(sentence, topics, size):
                        knowledge_datas.append(data)
                datas = {}
                for data in knowledge_datas:
                    datas[data["content"]] = {"topic":data["topic"], "file_name":data["file_name"], "similarity":data["similarity"]}
                knowledge_datas = []
                for data in datas:
                    datas[data].update({"content":data})
                    knowledge_datas.append(datas[data])
        return sorted(knowledge_datas, key=lambda x: x["similarity"], reverse=True)

    def search_knowledge_logic(self, question: str, topics: List[str], size: int):
        # 获取问题的向量表示
        question_embedding = JinaEmbedding().encode(question, task_type="retrieval.query")

        # 检查哪些topic索引存在
        existing_topics = []
        with es_client() as client:
            for topic in topics:
                if client.indices.exists(index=topic):
                    existing_topics.append(topic)

        # 如果没有有效的topic，直接返回空列表
        if not existing_topics:
            logging.info("没有找到有效的索引，返回空结果")
            return []

        # 构造向量检索查询
        query = {
            "script_score": {
                "query": {"match_all": {}},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                    "params": {"query_vector": question_embedding[0].tolist()}
                }
            }
        }

        # 执行搜索
        result = ElasticsearchHelper().search_documents(
            query=query,
            size=size,
            source=["content", "file_name", "doc_type"],
            index_name=existing_topics  # 只搜索存在的索引
        )

        # 处理搜索结果
        hits = result.get("hits", {}).get("hits", [])
        knowledge = []

        for hit in hits:
            score = hit.get("_score", 0) - 1  # 转换回余弦相似度
            source = hit.get("_source", {})
            topic = hit.get("_index", {})
            _similarity = round(float(-0.4 * score ** 2 + 1.8 * score / 2.4 + 0.65), 3)

            knowledge.append({
                "content": source.get("content", ""),
                "file_name": source.get("file_name", ""),
                "doc_type": source.get("doc_type", ""),
                "topic": topic,
                "similarity": _similarity
            })

        return knowledge

    def search_knowledge(self, question: str, topics: List[str], search_conf: SearchConfForm = None, size:int = 4) -> List[Dict[str, Any]]:
        """搜索知识库"""
        try:
            # 如果topics为空，直接返回空列表
            if not topics:
                return []

            if search_conf:
                size = search_conf.top_k if search_conf.top_k else size
                expansion_factor = search_conf.expansion_factor
                if expansion_factor:
                    knowledge = self.opt_knowledge(question, topics, size, expansion_factor)
                    return knowledge

            knowledge = self.search_knowledge_logic(question, topics, size)
            return knowledge

        except Exception as e:
            logging.error(f"搜索知识失败: {str(e)}")
            return []

    def page_knowledge(self, topic: str, file_id: str, page: int, page_size: int) -> dict[
        str, list[dict[str, Any]] | dict[str, int | str | Any]]:

        # 构建ES查询
        query = {
            "from": (page - 1) * page_size,
            "size": page_size,
            "_source": ["file_name", "content", "doc_type",
                        "slice_index", "header", "file_id", "corpus"]
        }
        if file_id != "":
            query["query"] = {
                "term": {
                    "file_id.keyword": {
                        "value": file_id,
                        "case_insensitive": False  # 严格区分大小写
                    }
                }
            }
            query["sort"] = [{"slice_index": {"order": "asc"}}]
        # 执行查询
        with es_client() as client:
            result = client.search(index=topic, body=query)
        # 执行搜索
        # 处理搜索结果
        hits = result.get("hits", {}).get("hits", [])
        total = result['hits']['total']['value']

        knowledge = []

        for hit in hits:
            source = hit.get("_source", {})
            knowledge.append({
                "content": source.get("content", ""),
                "file_id": source.get("file_id", ""),
                "file_name": source.get("file_name", ""),
                "corpus": source.get("corpus", ""),
                "id": hit.get("_id", "")
            })
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "data": knowledge
        }


class KnowledgeCRUDLogic(BaseHandler):

    def _init(self):
        self.es = ElasticsearchHelper()
        self.embedding = JinaEmbedding()

    def add_content(self, content: str, topic: str, file_name: str = "", file_id: str = ""):
        record_data = {"content": content, "file_id": file_id, "topic": topic, "file_name": file_name,
                       "embedding": self.embedding.encode(content)[0]}
        self.es.create_document(document=record_data, index_name=topic, refresh=True)

    def revise_content(self, id: str, topic: str, content: str):
        update_data = {"content": content, "embedding": self.embedding.encode(content)[0]}
        self.es.update_document(doc_id=id, document=update_data, index_name=topic, refresh=True)

    def delete_content(self, id: str, topic: str):
        self.es.delete_document(doc_id=id, index_name=topic, refresh=True)

    def create_knowledge(self, topic: str):
        return self.es.create_index(topic)