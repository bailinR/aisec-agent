#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/29 13:41
# @Author  : GuJR
# @Site    : 
# @File    : tool.py
import re
import traceback
from aisec_agent.config import AIDED_LLM_CONF
from aisec_agent.logic._tools import FileReaderTools
from typing import Dict
import logging
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper
from aisec_agent.logic.knowledge.embedding.embedding import JinaEmbedding
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.enumerate import DocType
from sklearn.feature_extraction.text import TfidfVectorizer
import jieba

from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.llm_typing import DocumentAnalysisResultDetailed
from aisec_agent.model.prompts import knowledge_split_prompt


class PretreatmentTool(FileReaderTools):

    def classify(self, part_content):
        prompt = knowledge_split_prompt()
        message = f"""{part_content}"""

        options = {
            "temperature": 0.2,
            "top_p": 0.6,
            "top_k": 40,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.5,
            "format": DocumentAnalysisResultDetailed.model_json_schema()
        }
        chat = getattr(LLMChatTools(), AIDED_LLM_CONF["func_name"])
        return chat(url=AIDED_LLM_CONF["url"], api_key=AIDED_LLM_CONF["key"], prompt=prompt,options=options, stream=False,
                    message=message, model=AIDED_LLM_CONF["model_name"], max_len_input=AIDED_LLM_CONF["max_len_input"])


class PreFileTools(PretreatmentTool):

    def classify_doc_type(self, content: str) -> tuple:
        """识别文档类型和切片建议"""
        # 取前2000字符进行分类
        sample = content[:2000]
        response_content = self.classify(sample)

        try:
            # 提取文档类型
            doc_type_match = re.search(r'"doc_type"\s*:\s*(\d+)', response_content)
            doc_type = int(doc_type_match.group(1)) if doc_type_match else DocType.LOG.value

            slice_strategy = response_content
            # logging.info(f"文档分析结果: 类型={doc_type}, 结构={structure}, 切片策略={slice_strategy}")

            return doc_type, slice_strategy

        except Exception as e:
            logging.error(f"文档类型识别失败: {str(e)}")
            logging.error(f"详细错误: {traceback.format_exc()}")
            # 返回默认值
            return DocType.LOG.value, {"has_chapters": False, "has_tables": False}, {"method": "行", "min_size": 1,
                                                                                     "has_header": False}

    def slice_content(self, content: str, doc_type: int, slice_strategy: dict = None) -> Dict:
        """根据文档类型和切片配置进行智能切片"""
        slices = []
        metadata = {}

        # 获取切片配置
        split_pattern = slice_strategy.get("split_pattern", "\n\n")
        chunk_size = slice_strategy.get("chunk_size", 200)
        use_regex = slice_strategy.get("use_regex", False)

        try:
            # 根据配置进行分割
            if use_regex:
                import re
                parts = re.split(split_pattern, content)
            else:
                parts = content.split(split_pattern)

            # 处理分割后的文本块
            current_chunk = []
            current_size = 0

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                # 如果单个部分超过chunk_size，需要进一步分割
                if len(part) > chunk_size:
                    # 按句子分割
                    sentences = re.split(r'([。！？.!?])', part)
                    for i in range(0, len(sentences), 2):
                        sentence = sentences[i]
                        if i + 1 < len(sentences):
                            sentence += sentences[i + 1]  # 添加标点符号
                        
                        if current_size + len(sentence) > chunk_size and current_chunk:
                            slices.append("".join(current_chunk))
                            current_chunk = []
                            current_size = 0
                        
                        current_chunk.append(sentence)
                        current_size += len(sentence)
                else:
                    if current_size + len(part) > chunk_size and current_chunk:
                        slices.append("".join(current_chunk))
                        current_chunk = []
                        current_size = 0
                    
                    current_chunk.append(part)
                    current_size += len(part)

            # 添加最后一个块
            if current_chunk:
                slices.append("".join(current_chunk))

        except Exception as e:
            logging.error(f"文档切片失败: {str(e)}")
            logging.error(f"详细错误: {traceback.format_exc()}")
            # 使用默认切片方法
            return self._defaultslice_content(content, doc_type)

        return {'slices': slices, 'metadata': metadata}

    def _defaultslice_content(self, content: str, doc_type: int) -> Dict:
        """默认的切片方法"""
        slices = []
        metadata = {}

        # 根据文档类型选择默认的分割方式
        if doc_type == DocType.LOG.value:  # 日志
            split_pattern = "\n"
        elif doc_type == DocType.CODE.value:  # 代码
            split_pattern = "\n\n"
        else:  # 其他类型
            split_pattern = "\n\n"

        # 默认块大小
        chunk_size = 200

        try:
            # 分割文本
            parts = content.split(split_pattern)
            
            # 处理分割后的文本块
            current_chunk = []
            current_size = 0

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                if current_size + len(part) > chunk_size and current_chunk:
                    slices.append("\n".join(current_chunk))
                    current_chunk = []
                    current_size = 0

                current_chunk.append(part)
                current_size += len(part)

            # 添加最后一个块
            if current_chunk:
                slices.append("\n".join(current_chunk))

        except Exception as e:
            logging.error(f"默认切片失败: {str(e)}")
            # 如果所有方法都失败，按行切分
            for line in content.splitlines():
                if line.strip():
                    slices.append(line)

        return {'slices': slices, 'metadata': metadata}



    def store_embeddings(self, slices: list, file_id: str, file_name: str, topic: str, batch_size=30) -> None:
        """向量化并存储到ES"""
        # 提取实际内容用于嵌入
        contents_to_embed = []
        for slice_item in slices:
            if isinstance(slice_item, dict) and 'content' in slice_item:
                contents_to_embed.append(slice_item['content'])
            else:
                contents_to_embed.append(slice_item)

        # 批量处理
        for i in range(0, len(slices), batch_size):
            batch_slices = slices[i:i + batch_size]
            batch_contents = contents_to_embed[i:i + batch_size]

            # 获取向量表示
            embeddings = JinaEmbedding().encode(
                batch_contents,
                task_type="retrieval.passage"
            )

            # 准备批量操作
            operations = []
            for j, (slice_item, embedding) in enumerate(zip(batch_slices, embeddings)):
                # 处理不同类型的切片项
                if isinstance(slice_item, dict) and 'content' in slice_item:
                    content = slice_item['content']
                    header = slice_item.get('header', '')
                    doc = {
                        "file_name": file_name,
                        "content": content,
                        "doc_type": "unknow",
                        "embedding": embedding.tolist(),
                        "slice_index": i + j,
                        "file_id": file_id,
                        "header": header
                    }
                else:
                    content = slice_item
                    doc = {
                        "file_name": file_name,
                        "file_id": file_id,
                        "content": content,
                        "doc_type": "unknow",
                        "embedding": embedding.tolist(),
                        "slice_index": i + j
                    }

                operations.append({
                    "_index": topic,
                    "_source": doc
                })

            # 执行批量插入
            try:
                result = ElasticsearchHelper().bulk_operation(operations, topic)
                if result.get("errors"):
                    logging.error(f"部分文档插入失败: {result}")
            except Exception as e:
                logging.error(f"批量插入文档失败: {str(e)}")
                raise


class TFIDFMath(BaseHandler):
    def extract_keywords_tfidf(self, text, top_n=5):
        """
        使用TF-IDF提取文本中的关键词

        Args:
            text: 待分析的文本
            top_n: 返回前N个关键词

        Returns:
            list: 关键词列表
        """
        seg_list = jieba.cut(text)
        texts = [" ".join(seg_list)]
        vectorizer = TfidfVectorizer(
            token_pattern=r"(?u)\b\w+\b",  # 匹配单词
            max_features=100,  # 最多考虑100个特征
            stop_words='english'  # 移除英文停用词
        )

        tfidf_matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()
        scores = tfidf_matrix.toarray()[0]
        word_scores = [(feature_names[i], scores[i]) for i in range(len(feature_names))]
        word_scores.sort(key=lambda x: x[1], reverse=True)
        top_keywords = [word for word, score in word_scores[:top_n]]

        return top_keywords
