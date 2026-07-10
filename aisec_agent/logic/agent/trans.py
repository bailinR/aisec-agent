import hashlib
import re
from datetime import datetime
from io import BytesIO
from typing import Dict, Union
from typing import List

from aisec_agent.logic._tools import FileReaderTools
from aisec_agent.logic._tools import aided_chat
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import TransformModel, ChunkExampleOutput
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.prompts import TransPrompt


class TransAgents(BaseHandler):
    """
    一个为大模型提供翻译数据预处理的工具类。

    功能:
    1.  清洗和标准化文本输入。
    2.  检测文本的语言。
    3.  支持处理直接的字符串(str)或来自MinIO对象存储的文件。

    输出:
    - 一个包含 'cleaned_text' 和 'language' 的字典。
    """
    def _init(self):
        self.minio_client = MinioClient()
        self.index_name ="file_translations"
        self._ensure_trans_index(index_name=self.index_name)

    def _clean_text(self, text: str) -> str:
        """
        对文本进行基础清洗。

        - 移除多余的空白字符 (空格, 制表符, 换行符)。
        - 您可以在此添加更多清洗规则 (例如: 移除HTML标签, URL等)。
        """
        # 将多个空白符合并为一个空格
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def process_string(self, text: str,language:str) -> Dict[str, str]:
        """
        处理单个字符串输入。

        Args:
            text (str): 原始输入字符串。

        Returns:
            Dict[str, str]: 包含 'cleaned_text' 和 'language' 的字典。
        """

        cleaned_text = self._clean_text(text)
        return {
            "cleaned_text": cleaned_text,
            "language": language
        }

    def _ensure_trans_index(self, index_name: str):
        """
        确保用于存储翻译结果的 ES 索引存在（按需可自定义 mappings）
        """
        mappings = {
            "properties": {
                "user_id": {"type": "keyword"},
                "file_name": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                # "source_lang": {"type": "keyword"},
                "target_lang": {"type": "keyword"},
                "source_text": {"type": "text"},
                "translated": {"type": "text"},
                "chunk_summary": {"type": "text", "analyzer": "standard"},
                "embedding": {"type": "dense_vector", "dims": 1024},
                "created_at": {"type": "date"}
            }
        }
        # 如果索引不存在则创建
        ElasticsearchHelper.ensure_index(index_name=index_name, mappings=mappings)

    def _index_one(self, index_name: str, doc: dict, refresh: bool = False):
        # 使用内容+文件+chunk 生成稳定 doc_id，避免重复插入
        rid = f"{doc.get('user_id', '')}|{doc.get('file_name', '')}|{doc.get('chunk_index', '')}|{doc.get('source_text', '')[:64]}"
        doc_id = hashlib.md5(rid.encode("utf-8")).hexdigest()
        return ElasticsearchHelper.create_document(
            document=doc, doc_id=doc_id, index_name=index_name, refresh=refresh
        )

    def smart_split(self, text, max_length=1000):
        """智能切分长文本，确保每个块的长度不超过 max_length。"""
        delimiters = r'([。？！\.?! ]+)'
        chunks_with_delimiters = re.split(delimiters, text)
        result = []
        current_chunk = ""
        for i, chunk in enumerate(chunks_with_delimiters):
            if not chunk:
                continue
            if len(current_chunk) + len(chunk) > max_length and current_chunk:
                result.append(current_chunk.strip())
                current_chunk = chunk
            else:
                current_chunk += chunk
        if current_chunk:
            result.append(current_chunk.strip())

        return result

    def pre_file(self,bucket, file_path: str,file_name: str,language:str) -> List:
        """处理文件预处理请求"""
        try:
            # 使用增强版文件读取工具读取文件内容
            file_data = self.minio_client.get_object(bucket, file_path)
            content = FileReaderTools().get_file_content(  BytesIO(file_data), file_name).replace("  ", " ")
            chunks = []
            for chunk in content.replace("\\n", "\n").split("\n\n"):
                chunks += self.smart_split(chunk, 500)
            return [self.process_string(chunk,language) for chunk in chunks]
        except Exception as e:
            self.logger.error(f"文件预处理失败: {str(e)}")
            return []

    def trans(self, question: str, row, **kwargs):
        return aided_chat(TransPrompt(user_need=question, trans=row["cleaned_text"], language=row['language']),
                          question, TransformModel, **kwargs)

    def save_in_es(self, row,  translated_text:str,user_id: str = "",  refresh_es: bool = False,  ):
        summary =  aided_chat(translated_text, "针对该段进行总结 "*7, ChunkExampleOutput)
        doc = {
            "user_id": user_id,
            "file_name": row.get("file_name", ""),
            "chunk_index": int(row.get("chunk_index", -1)) if str(row.get("chunk_index", "")).isdigit() else -1,
            "target_lang": row.get("language", "unknown"),
            "source_text": row.get("cleaned_text", ""),
            "translated": translated_text,
            # "prompt":      question,
            "created_at": datetime.now().isoformat(),
        }
        doc.update(summary)
        try:
            self._index_one(self.index_name, doc, refresh=refresh_es)
        except Exception as e:
            self.logger.error(f"写入 ES 失败: {e}")

    def generate_msg(self, question: str, preview: Union[Dict[str, str], List[Dict[str, str]]], *, user_id: str = "",
                     refresh_es: bool = False, agent_extra: dict = {}):
        """生成翻译消息并写入 ES（每个 chunk 一条）"""
        if isinstance(preview, dict):
            preview = [preview]

        # 确保索引存在
        # try:
        #     self._ensure_trans_index(es_index)
        # except Exception as e:
        #     self.logger.error(f"确保 ES 索引失败: {e}")

        for row in preview:
            if not isinstance(row, dict):
                self.logger.error(f"无效的输入格式: {type(row)}")
                continue
            try:
                stream = self.trans(question, row, **agent_extra)  # 仍为流
                # 1) 向外继续流式输出
                collected = []
                for token in stream:
                    collected.append(token)
                    yield token

                # 2) 汇总完整译文后写 ES
                translated_text = "".join(collected).strip()
                doc = {
                    "user_id":     user_id,
                    "file_name":   row.get("file_name", ""),
                    "chunk_index": int(row.get("chunk_index", -1)) if str(row.get("chunk_index","")).isdigit() else -1,
                    "target_lang": row.get("language", "unknown"),
                    "source_text": row.get("cleaned_text", ""),
                    "translated":  translated_text,
                    # "prompt":      question,
                    "created_at":  datetime.now().isoformat(),
                }
                try:
                    self._index_one(self.index_name, doc, refresh=refresh_es)
                except Exception as e:
                    self.logger.error(f"写入 ES 失败: {e}")

            except KeyError as e:
                self.logger.error(f"缺少必要字段: {str(e)}")
            except Exception as e:
                self.logger.error(f"生成消息失败: {str(e)}")

    def run(self, filepath: List[Dict], question: str, trans: str, agent_extra: dict = {}):
        """主运行方法"""
        if not filepath:
            for row in question.split("\n"):
                if row.strip():
                    processed = self.process_string(row.strip(),trans )
                    yield from self.generate_msg("完整处理我翻译内容:", [processed], **agent_extra)
        else:
            for row in filepath:
                try:
                    entries = self.pre_file(row["bucket"], row["path"], row["name"],trans )
                    for entry in entries:
                        yield from self.generate_msg(question, entry, **agent_extra)
                except KeyError as e:
                    self.logger.error(f"文件路径信息不完整: {str(e)}")
                except Exception as e:
                    self.logger.error(f"处理文件失败: {str(e)}")
