import json
from typing import Dict, List, Any, Tuple
import logging
from io import BytesIO

import redis
from typing import List, Dict, Any, Optional
import logging
from aisec_agent.model.define import BaseHandler
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper, ESQueryBuilder, es_client

from aisec_agent.config import DEFAULT_REDIS
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper, es_client
from aisec_agent.logic.knowledge.embedding.embedding import JinaEmbedding
import traceback
from aisec_agent.logic.knowledge._tool import PreFileTools
from aisec_agent.model.define import BaseHandler, RedisQueue
from aisec_agent.model.enumerate import DocType
from aisec_agent.logic._tools import FileReaderTools
from aisec_agent.model.typing import KnowledgeFileForm
from aisec_agent.logic._tools import aided_chat
from aisec_agent.model.prompts import  GetSummary,FullTextPrompt
from aisec_agent.model.llm_typing import ChunkExampleOutput
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper


class PreBookLogicHandler(BaseHandler):
    def _init(self):
        self.es =  UserBookService()
        self.embedding = JinaEmbedding()


    def chunk_summary(self, user_id,  file_id: str,   chunk_id:str,text:str) -> Dict:
        prompt =GetSummary(text)
        res =aided_chat(prompt,"", ChunkExampleOutput)
        embedding = self.embedding.encode(res.to_markdown())
        self.es.add_chunk(
            user_id,book_id=file_id,chunk_original=text,chunk_id=chunk_id,
            chunk_summary=res, embedding=embedding.tolist() )



    def add_book(self, user_id: str, tag: str, book_id: str, minio_setting, name:str ):
        """添加一本书的信息"""
        text = self.es.get_chunks(user_id, book_id,  )
        prompt = FullTextPrompt(text)
        res = aided_chat(prompt, "", ChunkExampleOutput)
        embedding = self.embedding.encode(res.to_markdown())
        self.es.add_book(
            user_id = user_id,
            tag=tag,minio_setting=minio_setting,
            book_id = book_id,
            book_name=  name,
            book_summary=res.to_markdown(),
            embedding =embedding.tolist()
        )



    def pre_file(self, file: BytesIO, file_name: str, file_id: str, topic: str) -> Tuple:
        """处理文件预处理请求"""
        try:
            # 使用增强版文件读取工具读取文件内容
            content = FileReaderTools().get_file_content(file, file_name).replace("  ", " ")
            # 切片处理
            slices_data = [chunk for chunk in content.replace("\\n", "\n").split("\n\n") if chunk and len(chunk) >= 10]
            # 向量化并存储
            PreFileTools().store_embeddings(slices_data, file_id, file_name, topic)
            return len(slices_data), False

        except Exception as e:
            logging.error(f"文件预处理失败: {str(e)}")
            logging.error(traceback.format_exc())





class UserBookService(BaseHandler):
    BOOK_INDEX = "user_wikis"
    CHUNK_INDEX = "user_wikis_chunks"

    BOOK_MAPPING = {
        "properties": {
            "user_id":      {"type": "keyword"},
            "tag":          {"type": "keyword"},
            "book_id":      {"type": "keyword"},
            "book_name":    {"type": "text", "analyzer": "standard"},
            "book_summary": {"type": "text", "analyzer": "standard"},
            "embedding":    {"type": "dense_vector", "dims": 1024},
        }
    }

    CHUNK_MAPPING = {
        "properties": {
            "user_id":        {"type": "keyword"},
            "book_id":        {"type": "keyword"},
            "chunk_id":       {"type": "keyword"},
            "chunk_original": {"type": "text",    "analyzer": "standard"},
            "chunk_summary":  {"type": "text",    "analyzer": "standard"},
            "embedding":      {"type": "dense_vector", "dims": 1024},
        }
    }

    def _init(self):
        # 确保索引存在
        self._ensure_index(self.BOOK_INDEX,  self.BOOK_MAPPING)
        self._ensure_index(self.CHUNK_INDEX, self.CHUNK_MAPPING)

    def _ensure_index(self, name: str, mapping: dict):
        """如果索引不存在就创建"""
        ElasticsearchHelper.ensure_index(
            index_name=name,
            mappings=mapping,
            settings={"number_of_shards": 1, "number_of_replicas": 0}
        )

    def get_chunks(self, user_id: str, book_id: str, ) -> str:
        qb = ESQueryBuilder()
        qb.filter({"term": {"user_id": {"value": user_id}}})
        qb.filter({"term": {"book_id": {"value": book_id}}})
        qb.paginate(0, 10000)  # 假设最多 10000 块
        body = qb.build()
        resp = ElasticsearchHelper.search_documents(
            query=body["query"],
            from_=body["from"],
            size=body["size"],
            source=["chunk_summary"],
            index_name=self.es.CHUNK_INDEX
        )
        summaries = [
            hit["_source"]["chunk_summary"]
            for hit in resp.get("hits", {}).get("hits", [])
            if "chunk_summary" in hit["_source"]
        ]
        if not summaries:
            raise ValueError(f"未找到 user_id={user_id}, book_id={book_id} 的任何 chunk_summary")

        return  "\n\n".join(summaries)

    def add_book(
        self,
        user_id: str,
        tag: str,
        book_id: str,minio_setting:dict,
        book_name: str,
        book_summary: str,
        embedding: list[float]
    ):
        """
        单条插入或者更新一本书的信息。
        文档 ID 采用 user_id_book_id 组合，已存在时会覆盖。
        """
        doc_id = f"{user_id}_{book_id}"
        source = {
            "user_id":      user_id,
            "tag":          tag,
            "book_id":      book_id,
            "minio_setting":minio_setting,
            "book_name":    book_name,
            "book_summary": book_summary,
            "embedding":    embedding,
        }
        return ElasticsearchHelper.create_document(
            index_name=self.BOOK_INDEX,
            doc_id=doc_id,
            document=source,
            refresh=True
        )

    def add_chunk(
        self,
        user_id: str,
        book_id: str,
        chunk_id: str,
        chunk_original: str,
        chunk_summary: str,
        embedding: list[float]
    ):
        """
        单条插入或者更新一个 chunk 信息。
        文档 ID 采用 user_id_book_id_chunk_id 组合，已存在时会覆盖。
        """
        doc_id = f"{user_id}_{book_id}_{chunk_id}"
        source = {
            "user_id":        user_id,
            "book_id":        book_id,
            "chunk_id":       chunk_id,
            "chunk_original": chunk_original,
            "chunk_summary":  chunk_summary,
            "embedding":      embedding,
        }
        return ElasticsearchHelper.index_document(
            index_name=self.CHUNK_INDEX,
            id=doc_id,
            body=source,
            refresh=True
        )
