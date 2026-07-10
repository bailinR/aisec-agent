#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
import hashlib
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from aisec_agent.logic.knowledge.database.es import es_client, ElasticsearchHelper
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import MemoryModel
from aisec_agent.model.prompts import MemoryPrompt


@dataclass
class MemoryRecord:
    """记忆记录数据结构"""
    memory_id: str
    user_id: str
    session_id: str
    sequence_number: int
    user_question: str
    ai_response: str
    user_question_summary: str = ""  # 现在存储合并文本的压缩版本
    user_question_embedding: Optional[List[float]] = None  # 现在存储合并文本的embedding
    importance_score: float = 0.5
    access_count: int = 0
    last_accessed: str = ""
    timestamp: str = ""
    tags: List[str] = None
    context_keywords: List[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.last_accessed:
            self.last_accessed = self.timestamp
        if self.tags is None:
            self.tags = []
        if self.context_keywords is None:
            self.context_keywords = []
            

class LongTermMemoryManager(BaseHandler):
    """长时记忆管理器"""

    def _init(self):
        """初始化长时记忆管理器"""
        self.index_prefix = "longterm_memory"

        # 初始化组件
        self._init_embedding()
        self._ensure_es_connection()

        self.logger.info("长时记忆管理器初始化完成")

    def _init_embedding(self):
        """初始化Jina Embedding"""
        try:
            from aisec_agent.logic.knowledge.embedding.embedding import JinaEmbedding

            self.embedding_model = JinaEmbedding()
            self.logger.info("Jina Embedding初始化成功")
        except Exception as e:
            self.logger.error(f"Jina Embedding初始化失败: {e}")
            self.embedding_model = None

    def _ensure_es_connection(self):
        """确保ES连接"""
        try:
            with es_client() as client:
                info = client.info(request_timeout=5)
                self.logger.info(f"ES连接成功: {info['version']['number']}")
        except Exception as e:
            self.logger.error(f"ES连接失败: {e}")

    def _get_user_index(self, user_id: str) -> str:
        """获取用户专属索引"""
        return f"{self.index_prefix}_user_{user_id}"

    def _ensure_user_index(self, user_id: str):
        """确保用户索引存在"""
        index_name = self._get_user_index(user_id)

        mappings = {
            "properties": {
                "memory_id": {"type": "keyword"},
                "user_id": {"type": "keyword"},
                "session_id": {"type": "keyword"},
                "sequence_number": {"type": "integer"},
                "user_question": {"type": "text", "analyzer": "standard"},
                "ai_response": {"type": "text", "analyzer": "standard"},
                "user_question_summary": {"type": "text", "analyzer": "standard"},  # 合并文本的压缩版本
                "user_question_embedding": {"type": "dense_vector", "dims": 1024},  # 合并文本的embedding
                "importance_score": {"type": "float"},
                "access_count": {"type": "integer"},
                "last_accessed": {"type": "date"},
                "timestamp": {"type": "date"},
                "tags": {"type": "keyword"},
                "context_keywords": {"type": "keyword"}
            }
        }

        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0
        }

        try:
            with es_client() as client:
                if not client.indices.exists(index=index_name):
                    ElasticsearchHelper.ensure_index(index_name, mappings, settings)
                    self.logger.info(f"创建用户索引: {index_name}")
        except Exception as e:
            if "resource_already_exists_exception" not in str(e):
                self.logger.error(f"创建索引失败: {e}")
                raise

    def _calculate_importance(self, user_question: str) -> float:
        """计算记忆重要性"""
        importance = 0.5

        # 长度权重
        if len(user_question) > 100:
            importance += 0.1

        # 关键词权重
        high_keywords = ['错误', '问题', '重要', '紧急', '帮助', 'error', 'bug', 'important', 'urgent']
        tech_keywords = ['代码', '算法', '数据库', '接口', 'API', 'code', 'algorithm', 'database']

        text_combined = user_question.lower()

        for keyword in high_keywords:
            if keyword.lower() in text_combined:
                importance += 0.15

        for keyword in tech_keywords:
            if keyword.lower() in text_combined:
                importance += 0.1

        # 代码块权重
        if '```' in user_question:
            importance += 0.2

        return min(importance, 1.0)

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        # 简单的关键词提取
        keywords = []
        tech_terms = ['python', 'javascript', 'java', 'api', 'database', 'mysql', 'redis',
                      'elasticsearch', 'docker', 'kubernetes', 'git', 'github']

        text_lower = text.lower()
        for term in tech_terms:
            if term in text_lower:
                keywords.append(term)

        return keywords

    def _summarize_with_llm(self, text: str, sequence_number: int, session_id: str, old_combined_text: str) -> str:
        """使用Ollama压缩文本"""
        if len(text) < 100:
            return old_combined_text

        prompt = MemoryPrompt(text)

        try:
            from aisec_agent.logic._tools import aided_chat

            summary = aided_chat(prompt, "将此文本进行精简成记忆", MemoryModel)["text"]
            combined_text = f"{session_id}_{sequence_number}:{{对话精简:{summary}}}"
            return combined_text if combined_text else old_combined_text
        except Exception as e:
            self.logger.warning(f"LLM压缩失败: {e}")
            return text

    def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """生成文本向量"""
        if not self.embedding_model:
            return [[] for _ in texts]

        try:
            embeddings = self.embedding_model.encode(texts, task_type='retrieval.query')
            return embeddings.tolist()
        except Exception as e:
            self.logger.error(f"向量生成失败: {e}")
            return [[] for _ in texts]

    def _get_next_sequence_number(self, user_id: str, session_id: str) -> int:
        """获取下一个序号"""
        try:
            index_name = self._get_user_index(user_id)

            # 检查索引是否存在
            with es_client() as client:
                if not client.indices.exists(index=index_name):
                    self.logger.info(f"索引 {index_name} 不存在，返回序号 1")
                    return 1

            query = {
                "bool": {
                    "must": [
                        {"term": {"user_id": user_id}},
                        {"term": {"session_id": session_id}}
                    ]
                }
            }

            result = ElasticsearchHelper.search_documents(
                query=query,
                index_name=index_name,
                size=1,
                sort=[{"sequence_number": {"order": "desc"}}]
            )

            if result['hits']['total']['value'] > 0:
                return result['hits']['hits'][0]['_source']['sequence_number'] + 1
            return 1

        except Exception as e:
            self.logger.warning(f"获取序号失败: {e}")
            return 1

    def store_conversation(self,
                           user_id: str,
                           session_id: str,
                           user_question: str,
                           ai_response: str) -> str:
        """
        存储对话到长时记忆

        Args:
            user_id: 用户ID
            session_id: 会话ID
            user_question: 用户问题
            ai_response: AI回答

        Returns:
            memory_id: 记忆ID
        """
        try:
            # 确保索引存在
            self._ensure_user_index(user_id)

            # 获取序号
            sequence_number = self._get_next_sequence_number(user_id, session_id)

            # 生成记忆ID
            memory_id = hashlib.md5(
                f"{user_id}_{session_id}_{sequence_number}_{time.time()}".encode()
            ).hexdigest()

            # 合并用户问题和AI回复
            old_combined_text = f"{session_id}_{sequence_number}:{{问题:{user_question}  回复:{ai_response}}}"
            time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            combined_text = f"第{sequence_number}次对话:{{问题:{user_question},回复:{ai_response},时间：{time_now}}}"

            # 计算重要性（基于原始问题）
            importance = self._calculate_importance(user_question)

            # 对合并文本生成embedding（在压缩之前）
            embeddings = self._generate_embeddings([combined_text])
            combined_embedding = embeddings[0] if embeddings[0] else None

            # LLM压缩合并的文本
            combined_summary = self._summarize_with_llm(combined_text, sequence_number, session_id, old_combined_text)

            # 提取关键词（从合并文本中提取）
            keywords = self._extract_keywords(combined_text)

            # 构建记录
            record = MemoryRecord(
                memory_id=memory_id,
                user_id=user_id,
                session_id=session_id,
                sequence_number=sequence_number,
                user_question=user_question,
                ai_response=ai_response,
                user_question_summary=combined_summary,  # 存储合并文本的压缩版本
                user_question_embedding=combined_embedding,  # 存储合并文本的embedding
                importance_score=importance,
                context_keywords=keywords
            )

            # 存储到ES
            index_name = self._get_user_index(user_id)
            ElasticsearchHelper.create_document(
                document=asdict(record),
                doc_id=memory_id,
                index_name=index_name,
                refresh=True
            )

            return memory_id

        except Exception as e:
            self.logger.error(f"存储记忆失败: {e}")
            raise

    def search_memories(self,
                        user_id: str,
                        session_id: str,
                        query: str,
                        limit: int = 10,
                        max_tokens: int = 2000) -> List[Dict[str, Any]]:
        """
        搜索相关记忆

        Args:
            user_id: 用户ID
            session_id: 当前会话ID
            query: 搜索查询
            limit: 最大返回数量
            max_tokens: 最大token数量

        Returns:
            相关记忆列表
        """
        try:
            index_name = self._get_user_index(user_id)

            # 检查索引是否存在
            with es_client() as client:
                if not client.indices.exists(index=index_name):
                    self.logger.info(f"用户 {user_id} 还没有记忆数据")
                    return []

            # 生成查询向量
            query_embedding = None
            if self.embedding_model:
                embeddings = self._generate_embeddings([query])
                query_embedding = embeddings[0] if embeddings[0] else None

            results = []

            if query_embedding:
                try:
                    semantic_query = {
                        "script_score": {
                            "query": {
                                "bool": {
                                    "must": [
                                        {"term": {"user_id": user_id}},
                                        {"exists": {"field": "user_question_embedding"}}
                                    ]
                                }
                            },
                            "script": {
                                "source": "cosineSimilarity(params.query_vector, 'user_question_embedding') + 1.0",
                                "params": {"query_vector": query_embedding}
                            }
                        }
                    }

                    semantic_results = ElasticsearchHelper.search_documents(
                        query=semantic_query,
                        index_name=index_name,
                        size=limit
                    )
                except Exception as e:
                    self.logger.warning(f"语义搜索失败，跳过: {e}")
                    semantic_results = {'hits': {'hits': []}}

                for hit in semantic_results['hits']['hits']:
                    source = hit['_source']
                    score = hit['_score']

                    # 会话权重
                    session_weight = 2.0 if source['session_id'] == session_id else 1.0

                    # 重要性权重
                    importance_weight = source.get('importance_score', 0.5)

                    # 最终评分
                    final_score = score * session_weight * importance_weight

                    results.append({
                        'memory_id': source['memory_id'],
                        'user_question': source['user_question'],
                        'ai_response': source['ai_response'],
                        'user_question_summary': source['user_question_summary'],
                        'session_id': source['session_id'],
                        'importance_score': importance_weight,
                        'timestamp': source['timestamp'],
                        'score': final_score
                    })
            # 按评分排序
            results.sort(key=lambda x: x['score'], reverse=True)

            # Token优化
            optimized_results = self._optimize_for_tokens(results[:limit], max_tokens)
            self.logger.info(f"搜索完成: 找到 {len(optimized_results)} 条相关记忆")
            return optimized_results

        except Exception as e:
            self.logger.error(f"搜索记忆失败: {e}")
            return []

    def _optimize_for_tokens(self, memories: List[Dict], max_tokens: int) -> List[Dict]:
        """优化记忆列表以控制token数量"""
        if not memories:
            return []

        # 简单的token估算（1个中文字符≈1.5个token）
        def estimate_tokens(text: str) -> int:
            return int(len(text) * 1.5)

        optimized = []
        total_tokens = 0

        for memory in memories:
            memory_tokens = estimate_tokens(memory['user_question'] + memory['ai_response'])

            if total_tokens + memory_tokens <= max_tokens:
                optimized.append(memory)
                total_tokens += memory_tokens
            else:
                # 如果单个记忆太长，尝试使用摘要
                if 'user_question_summary' in memory and 'ai_response_summary' in memory:
                    summary_tokens = estimate_tokens(
                        memory.get('user_question_summary', memory['user_question']) +
                        memory.get('ai_response_summary', memory['ai_response'])
                    )

                    if total_tokens + summary_tokens <= max_tokens:
                        memory_copy = memory.copy()
                        memory_copy['user_question'] = memory.get('user_question_summary', memory['user_question'])
                        memory_copy['ai_response'] = memory.get('ai_response_summary', memory['ai_response'])
                        optimized.append(memory_copy)
                        total_tokens += summary_tokens
                break

        return optimized

    def _get_session_records(self, user_id: str, session_id: str, size: int = 200) -> List[Dict[str, Any]]:
        """Return current-session memory records ordered by sequence_number."""
        try:
            index_name = self._get_user_index(user_id)
            with es_client() as client:
                if not client.indices.exists(index=index_name):
                    return []

            query = {
                "bool": {
                    "must": [
                        {"term": {"user_id": user_id}},
                        {"term": {"session_id": session_id}}
                    ]
                }
            }
            result = ElasticsearchHelper.search_documents(
                query=query,
                index_name=index_name,
                size=size,
                sort=[{"sequence_number": {"order": "asc"}}],
                source=[
                    "sequence_number",
                    "user_question",
                    "ai_response",
                    "user_question_summary",
                    "timestamp"
                ]
            )
            hits = result.get("hits", {}).get("hits", [])
            return [hit.get("_source", {}) for hit in hits]
        except Exception as e:
            self.logger.error(f"Get session records failed: {e}")
            return []

    @staticmethod
    def _clip_context(text: str, max_chars: int) -> str:
        if max_chars and max_chars > 0 and len(text) > max_chars:
            return text[-max_chars:]
        return text

    def get_session_summary_context(self, user_id: str, session_id: str, max_chars: int = 4000) -> str:
        """Return full raw context for short sessions, otherwise compressed context."""
        records = self._get_session_records(user_id, session_id)
        raw_lines = []
        for record in records:
            sequence_number = record.get("sequence_number", "")
            question = record.get("user_question", "")
            answer = record.get("ai_response", "")
            raw_lines.append(f"[{sequence_number}]\nUser: {question}\nAssistant: {answer}")
        raw_context = "\n\n".join(raw_lines)
        if raw_context and len(raw_context) <= max_chars:
            return raw_context

        lines = []
        for record in records:
            sequence_number = record.get("sequence_number", "")
            summary = record.get("user_question_summary") or ""
            if not summary:
                question = record.get("user_question", "")
                answer = record.get("ai_response", "")
                summary = f"Q: {question}\nA: {answer}"
            lines.append(f"[{sequence_number}] {summary}")
        return self._clip_context("\n\n".join(lines), max_chars)

    def get_session_raw_context(self, user_id: str, session_id: str, max_chars: int = 12000) -> str:
        """Return raw current-session question/answer context for second-pass answers."""
        records = self._get_session_records(user_id, session_id)
        lines = []
        for record in records:
            sequence_number = record.get("sequence_number", "")
            question = record.get("user_question", "")
            answer = record.get("ai_response", "")
            lines.append(f"[{sequence_number}]\nUser: {question}\nAssistant: {answer}")
        return self._clip_context("\n\n".join(lines), max_chars)

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """获取用户记忆统计"""
        try:
            index_name = self._get_user_index(user_id)

            # 检查索引是否存在
            with es_client() as client:
                if not client.indices.exists(index=index_name):
                    self.logger.info(f"用户 {user_id} 还没有记忆数据")
                    return {'user_id': user_id, 'total_memories': 0, 'unique_sessions': 0,
                            'avg_memories_per_session': 0}

            # 使用聚合查询同时获取总数和会话数
            agg_query = {
                "query": {"term": {"user_id": user_id}},
                "aggs": {
                    "unique_sessions": {
                        "cardinality": {
                            "field": "session_id"
                        }
                    }
                }
            }

            result = ElasticsearchHelper.search_documents(
                query=agg_query,
                index_name=index_name,
                size=0
            )

            total_memories = result['hits']['total']['value']
            unique_sessions = result['aggregations']['unique_sessions']['value']

            return {
                'user_id': user_id,
                'total_memories': total_memories,
                'unique_sessions': unique_sessions,
                'avg_memories_per_session': round(total_memories / max(unique_sessions, 1), 2)
            }

        except Exception as e:
            self.logger.error(f"获取统计失败: {e}")
            return {'user_id': user_id, 'total_memories': 0, 'unique_sessions': 0, 'avg_memories_per_session': 0}
