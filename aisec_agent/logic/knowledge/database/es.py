#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/24 14:04
# @Author  : GuJR
# @Site    : 
# @File    : es.py

import contextlib
import logging
import time
from typing import Dict, List, Union, ContextManager
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import NotFoundError
from aisec_agent.config import ES_CONFIG
from aisec_agent.model.define import BaseHandler

_client = None


def init_elasticsearch():
    """初始化Elasticsearch客户端连接"""
    global _client

    if _client is not None:
        return _client

    client_config = {
        "hosts": ES_CONFIG["hosts"],
        "verify_certs": ES_CONFIG["verify_certs"],
        "maxsize": 25,
        "retry_on_timeout": True,
        "max_retries": 3,
        "retry_on_status": [429, 500, 502, 503, 504],
        "sniff_on_start": False,
        "sniff_on_connection_fail": False
    }

    # SSL配置
    if ES_CONFIG.get("use_ssl"):
        client_config["scheme"] = "https"
        
    # 认证配置
    if ES_CONFIG.get("auth"):
        username, password = ES_CONFIG["auth"]
        client_config["basic_auth"] = (username, password)

    max_retries = 30
    retry_interval = 10

    for attempt in range(max_retries):
        try:
            _client = Elasticsearch(**client_config)
            # 验证连接
            info = _client.info()
            logging.info(f"连接到Elasticsearch成功: version {info['version']['number']}")
            return _client
        except Exception as e:
            if attempt < max_retries - 1:
                logging.warning(f"连接Elasticsearch失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                logging.info(f"等待 {retry_interval} 秒后重试...")
                time.sleep(retry_interval)
            else:
                logging.error(f"连接Elasticsearch失败，已达最大重试次数: {str(e)}")
                raise

    return _client


init_elasticsearch()


@contextlib.contextmanager
def es_client() -> 'ContextManager[Elasticsearch]':
    """获取Elasticsearch客户端的上下文管理器"""
    global _client
    if _client is None:
        _client = init_elasticsearch()
    try:
        yield _client
    except Exception as e:
        logging.exception(f"Elasticsearch操作异常: {e.__class__.__name__}")
        raise e


class ESQueryBuilder:
    """ES查询构建器"""
    def __init__(self):
        self.query = {"bool": {"must": [], "must_not": [], "should": [], "filter": []}}
        self._from = 0
        self._size = 10
        self._sort = []
        self._source = None
        self._highlight = None
        self._aggs = None

    def must(self, condition):
        self.query["bool"]["must"].append(condition)
        return self

    def must_not(self, condition):
        self.query["bool"]["must_not"].append(condition)
        return self

    def should(self, condition):
        self.query["bool"]["should"].append(condition)
        return self

    def filter(self, condition):
        self.query["bool"]["filter"].append(condition)
        return self

    def sort(self, field, order="asc"):
        self._sort.append({field: {"order": order}})
        return self

    def source(self, fields):
        self._source = fields
        return self

    def highlight(self, config):
        self._highlight = config
        return self

    def aggs(self, config):
        self._aggs = config
        return self

    def paginate(self, page, size=10):
        self._from = (page - 1) * size
        self._size = size
        return self

    def build(self):
        return {
            "query": self.query,
            "from": self._from,
            "size": self._size,
            "sort": self._sort if self._sort else None,
            "highlight": self._highlight,
            "_source": self._source,
            "aggs": self._aggs
        }


class ElasticsearchHelper:
    """Elasticsearch操作帮助类"""

    @staticmethod
    def ensure_index(index_name: str = None, mappings: Dict = None, settings: Dict = None) -> bool:
        """确保索引存在，不存在则创建"""
        if not index_name:
            raise ValueError("索引名称不能为空")
            
        with es_client() as client:
            if not client.indices.exists(index=index_name):
                body = {}
                if mappings:
                    body["mappings"] = mappings
                if settings:
                    body["settings"] = settings

                try:
                    client.indices.create(index=index_name, body=body if body else None)
                    logging.info(f"创建索引 {index_name} 成功")
                    return True
                except Exception as e:
                    logging.error(f"创建索引 {index_name} 失败: {str(e)}")
                    raise
            return False

    @staticmethod
    def create_document(document: Dict, doc_id: str = None, index_name: str = None,
                        refresh: bool = False) -> Dict:
        """创建/插入文档"""
        with es_client() as client:
            try:
                result = client.index(
                    index=index_name,
                    body=document,
                    id=doc_id,
                    refresh=refresh
                )
                return result
            except Exception as e:
                logging.error(f"创建文档失败: {str(e)}")
                raise

    @staticmethod
    def get_document(doc_id: str, index_name: str = None) -> Dict:
        """根据ID获取文档"""
        with es_client() as client:
            try:
                result = client.get(index=index_name, id=doc_id)
                return result
            except NotFoundError:
                logging.warning(f"文档不存在: {index_name}/{doc_id}")
                return {"found": False}
            except Exception as e:
                logging.error(f"获取文档失败: {str(e)}")
                raise

    @staticmethod
    def search_documents(query: Dict, from_: int = 0, size: int = 10,
                         sort: List = None, source: Union[List, bool] = None,
                         index_name=None,
                         highlight: Dict = None,
                         aggs: Dict = None) -> Dict:
        """搜索文档
        
        Args:
            query: 查询条件
            from_: 起始位置
            size: 返回文档数量
            sort: 排序条件
            source: 指定返回的字段
            index_name: 索引名称
            highlight: 高亮配置
            aggs: 聚合查询配置
            
        Returns:
            Dict: 搜索结果
        """
        if index_name is None:
            index_name = []
        if not index_name:
            raise ValueError("索引名称不能为空")
            
        with es_client() as client:
            body = {"query": query}

            if sort:
                body["sort"] = sort
                
            if highlight:
                body["highlight"] = highlight
                
            if aggs:
                body["aggs"] = aggs

            result = client.search(
                index=index_name,
                body=body,
                from_=from_,
                size=size,
                _source=source
            )
            return result


    @staticmethod
    def update_document(doc_id: str, document: Dict, index_name: str = None,
                        doc_as_upsert: bool = False, refresh: bool = False) -> Dict:
        """更新文档"""
        with es_client() as client:
            result = client.update(
                index=index_name,
                id=doc_id,
                body={"doc": document, "doc_as_upsert": doc_as_upsert},
                refresh=refresh
            )
            return result

    @staticmethod
    def delete_document(doc_id: str, index_name: str = None, refresh: bool = False) -> Dict:
        """删除文档"""
        with es_client() as client:
            result = client.delete(
                index=index_name,
                id=doc_id,
                refresh=refresh
            )
            return result

    @staticmethod
    def bulk_operation(operations: List[Dict], index_name: str = None, refresh: bool = False) -> Dict:
        """批量操作（批量增删改）"""
        with es_client() as client:
            # 为没有指定索引的操作添加默认索引
            for op in operations:
                if "_index" not in op:
                    op["_index"] = index_name

            max_retries = 3
            retry_delay = 1  # 秒
            
            for attempt in range(max_retries):
                try:
                    result = helpers.bulk(client, operations, refresh=refresh)
                    return {"success": result[0], "errors": result[1]}
                except Exception as e:
                    if attempt == max_retries - 1:  # 最后一次尝试
                        logging.error(f"批量操作失败，已达到最大重试次数: {str(e)}")
                        raise
                    logging.warning(f"批量操作失败，第{attempt + 1}次重试: {str(e)}")
                    time.sleep(retry_delay * (attempt + 1))  # 指数退避

    @staticmethod
    def count_documents(query: Dict = None, index_name: str = None) -> int:
        """统计文档数量"""
        with es_client() as client:
            body = {"query": query} if query else None
            try:
                result = client.count(index=index_name, body=body)
                return result["count"]
            except Exception as e:
                logging.error(f"统计文档数量失败: {str(e)}")
                raise

    @classmethod
    def delete_by_query(cls, query: Dict, index_name: str = None, refresh: bool = False) -> Dict:
        """根据查询条件删除文档"""
        if not index_name:
            raise ValueError("索引名称不能为空")
            
        with es_client() as client:
            result = client.delete_by_query(
                index=index_name,
                body={"query": query},
                refresh=refresh
            )
            return result

    @classmethod
    def update_by_query(cls, query: Dict, script: Dict, index_name: str = None, refresh: bool = False) -> Dict:
        """根据查询条件更新文档"""
        if not index_name:
            raise ValueError("索引名称不能为空")
            
        with es_client() as client:
            try:
                result = client.update_by_query(
                    index=index_name,
                    body={"query": query, "script": script},
                    refresh=refresh
                )
                return result
            except Exception as e:
                logging.error(f"根据查询条件更新文档失败: {str(e)}")
                raise

    def create_index(self, index_name: str) -> Dict:
        """
        创建ES索引

        Args:
            index_name: 索引名称

        Returns:
            Dict: 创建结果
        """
        # 定义索引的settings和mappings
        index_config = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 1
            },
            "mappings": {
                "properties": {
                    "file_id": {
                        "type": "keyword"
                    },
                    "file_name": {
                        "type": "keyword"
                    },
                    "doc_type": {
                        "type": "keyword"
                    },
                    "content": {
                        "type": "text",
                        "analyzer": "standard"
                    },
                    "embedding": {
                        "type": "dense_vector",
                        "dims": 1024
                    },
                    "created_at": {
                        "type": "date"
                    }
                }
            }
        }

        try:
            with es_client() as client:
                # 检查索引是否已存在
                if not client.indices.exists(index=index_name):
                    client.indices.create(
                        index=index_name,
                        body=index_config
                    )
                    return {"success": True, "message": f"索引 {index_name} 创建成功"}
                else:
                    return {"success": False, "message": f"索引 {index_name} 已存在"}

        except Exception as e:
            error_msg = f"创建索引失败: {str(e)}"
            logging.error(error_msg)
            return {"success": False, "message": error_msg}

    def add_field(self, topic: str, field_name: str, id_value_map: dict) -> dict:
        """批量根据记录id新增字段并填充值"""
        # 参数校验
        if not topic or not isinstance(topic, str):
            return {
                "success": False,
                "message": "索引名称必须是非空字符串"
            }

        if not field_name or not isinstance(field_name, str):
            return {
                "success": False,
                "message": "field_name必须是非空字符串"
            }

        if not id_value_map or not isinstance(id_value_map, dict):
            return {
                "success": False,
                "message": "id_value_map必须是非空字典"
            }

        # 准备批量操作
        operations = []
        for doc_id, field_value in id_value_map.items():
            operations.append({
                "_op_type": "update",
                "_index": topic,
                "_id": doc_id,
                "doc": {field_name: field_value},
                "doc_as_upsert": True
            })

        # 执行批量操作
        result = {
            "success": True,
            "processed_count": len(id_value_map),
            "success_count": 0,
            "errors": [],
            "details": {}
        }

        try:
            with es_client() as client:
                # 使用helpers.bulk执行批量操作
                success_count, errors = helpers.bulk(
                    client,
                    operations,
                    refresh=True,
                    raise_on_error=False  # 不因单个失败中断整个批量
                )

                # 处理结果
                result["success_count"] = success_count
                if errors:
                    result["success"] = False
                    result["errors"] = errors

                # 记录详细结果
                for i, op in enumerate(operations):
                    doc_id = op["_id"]
                    if i < len(errors):
                        result["details"][doc_id] = {
                            "success": False,
                            "error": errors[i]
                        }
                    else:
                        result["details"][doc_id] = {
                            "success": True,
                            "message": "更新成功"
                        }

                logging.info(
                    f"批量添加字段完成 - 索引: {topic}, 字段名: {field_name}, "
                    f"处理: {result['processed_count']}, 成功: {result['success_count']}"
                )

                return result

        except Exception as e:
            error_msg = f"批量添加字段失败: {str(e)}"
            logging.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
                "processed_count": 0,
                "success_count": 0,
                "errors": [str(e)]
            }