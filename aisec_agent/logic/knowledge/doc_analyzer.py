#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/8/22 10:54
# @Author  : GuJR
# @Site    : 
# @File    : doc_analyzer.py
from io import BytesIO
from typing import List, Dict, Any, Set
from aisec_agent.logic._tools import FileReaderTools, aided_chat
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_typing import DocAnalysisModel
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.prompts import DocAnalysisPrompt


class DocAnalyzerMergeTool(BaseHandler):
    """将分析结果合并到原文的工具"""

    def format_analysis_to_markdown(self, analysis_result: Dict[str, Any]) -> str:
        """将分析结果格式化为markdown文本"""
        markdown_parts = []

        # 添加主题
        if analysis_result.get('topic'):
            markdown_parts.append(f"\n## 主题\n{analysis_result['topic']}")

        # 添加核心思想
        if analysis_result.get('core_idea'):
            markdown_parts.append(f"\n## 核心思想\n{analysis_result['core_idea']}")

        # 添加关键信息
        key_info = analysis_result.get('key_info', [])
        if key_info:
            markdown_parts.append("\n## 关键信息")
            for info in key_info:
                if isinstance(info, dict) and 'title' in info and 'description' in info:
                    markdown_parts.append(f"- **{info['title']}**: {info['description']}")
                else:
                    markdown_parts.append(f"- {str(info)}")

        # 添加纠错信息
        corrections = analysis_result.get('corrections', [])
        if corrections:
            markdown_parts.append("\n## 纠错信息")
            for correction in corrections:
                if isinstance(correction, dict):
                    original = correction.get('original', '')
                    corrected = correction.get('corrected', '')
                    reason = correction.get('reason', '')
                    markdown_parts.append(f"- 原文: {original}")
                    markdown_parts.append(f"  - 修正: {corrected}")
                    if reason:
                        markdown_parts.append(f"  - 原因: {reason}")
                else:
                    markdown_parts.append(f"- {str(correction)}")

        # 添加公式
        formulas = analysis_result.get('formulas', [])
        if formulas:
            markdown_parts.append("\n## 公式")
            for formula in formulas:
                if isinstance(formula, dict):
                    name = formula.get('name', '')
                    expression = formula.get('expression', '')
                    description = formula.get('description', '')
                    if name:
                        markdown_parts.append(f"- **{name}**: `{expression}`")
                        if description:
                            markdown_parts.append(f"  - {description}")
                    else:
                        markdown_parts.append(f"- `{expression}`")
                else:
                    markdown_parts.append(f"- `{str(formula)}`")

        # 添加对象引用
        object_references = analysis_result.get('object_references', [])
        if object_references:
            markdown_parts.append("\n## 对象引用")
            for ref in object_references:
                if isinstance(ref, dict):
                    obj_type = ref.get('type', '')
                    name = ref.get('name', '')
                    description = ref.get('description', '')
                    if obj_type and name:
                        markdown_parts.append(f"- **{obj_type}**: {name}")
                        if description:
                            markdown_parts.append(f"  - {description}")
                    else:
                        markdown_parts.append(f"- {str(ref)}")
                else:
                    markdown_parts.append(f"- {str(ref)}")

        return "\n".join(markdown_parts)

    def format_global_info_to_markdown(self, global_infos: List[str]) -> str:
        """将全局信息格式化为markdown文本"""
        if not global_infos:
            return ""

        markdown_parts = ["\n## 全局信息"]
        for info in global_infos:
            markdown_parts.append(f"- {info}")

        return "\n".join(markdown_parts)

    def merge_analysis_with_chunks(self, original_chunks: List[str],
                                   analysis_results: List[Dict[str, Any]],
                                   global_infos: List[str] = None) -> List[str]:
        """
        将分析结果合并到原始块中

        Args:
            original_chunks: 原始文档块列表
            analysis_results: 分析结果列表
            global_infos: 全局信息列表

        Returns:
            合并后的块列表
        """
        enhanced_chunks = []

        for i, chunk in enumerate(original_chunks):
            enhanced_chunk = chunk

            if i < len(analysis_results):
                analysis_markdown = self.format_analysis_to_markdown(analysis_results[i])
                if analysis_markdown:
                    enhanced_chunk += "\n\n---\n### 补充分析信息" + analysis_markdown

            enhanced_chunks.append(enhanced_chunk)

        if global_infos:
            global_markdown = self.format_global_info_to_markdown(global_infos)
            if global_markdown:
                for i in range(len(enhanced_chunks)):
                    enhanced_chunks[i] += "\n\n---" + global_markdown

        return enhanced_chunks

    def process_doc_analysis_results(self, original_chunks: List[str],
                                     analysis_results: List[Dict[str, Any]],
                                     global_infos: List[str] = None) -> List[str]:
        """
        处理文档分析结果的主函数

        Args:
            original_chunks: 原始文档块列表
            analysis_results: 分析结果列表（来自DocAnalysisPipeline.analyse的返回值）
            global_infos: 全局信息列表

        Returns:
            增强后的文档块列表，可用于向量化
        """
        try:
            # 合并分析信息到原始块中
            enhanced_chunks = self.merge_analysis_with_chunks(original_chunks, analysis_results, global_infos)
            return enhanced_chunks

        except Exception as e:
            raise


class DocAnalysisPipeline(BaseHandler):
    """文档分析管道"""

    def _init(self):
        self.global_info_cache: Set[str] = set()
        self.key_info_cache: Set[str] = set()
        self.minio_client = MinioClient()
        self.merge_tool = DocAnalyzerMergeTool()

    def get_file(self, file_name: str, file_path: str, bucket: str) -> list:
        """获取minio的文件并分块"""
        file_data = self.minio_client.get_object(bucket, file_path)
        content = FileReaderTools().get_file_content(BytesIO(file_data), file_name).replace("  ", " ")
        slices_data = [chunk for chunk in content.replace("\\n", "\n").split("\n\n") if chunk]
        return slices_data

    def merge_chunk(self, slices_data: List[str], chunk_size: int) -> List[str]:
        """根据chunk_size重新合并块"""
        merged_chunks = []
        current_chunk = ""

        for slice_item in slices_data:
            if len(current_chunk) + len(slice_item) + 2 <= chunk_size:
                if current_chunk:
                    current_chunk += "\n\n" + slice_item
                else:
                    current_chunk = slice_item
            else:
                if current_chunk:
                    merged_chunks.append(current_chunk)
                current_chunk = slice_item

        if current_chunk:
            merged_chunks.append(current_chunk)

        return merged_chunks

    def title_description_to_text(self, infos: List[Dict[str, str]]) -> List[str]:
        """将信息列表转换为文本格式"""
        result = []
        for info in infos:
            if isinstance(info, dict) and 'title' in info and 'description' in info:
                result.append(f"{info['title']}：{info['description']}")
            else:
                result.append(str(info))
        return result

    def convert_single_dict_to_text(self, data_dict: Dict[str, Any]) -> str:
        """将单个包含summary和key_info的字典转换为纯文本描述"""
        summary = data_dict.get('summary', '')
        key_info = data_dict.get('key_info', [])

        if key_info:
            key_info_text = "；".join(self.title_description_to_text(key_info))
            return f"{summary} 关键信息包括：{key_info_text}。"
        else:
            return summary

    def deduplicate_global_info(self, new_global_info: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """去重全局信息"""
        deduplicated = []
        for info in new_global_info:
            info_key = f"{info['title']}:{info['description']}"
            if info_key not in self.global_info_cache:
                self.global_info_cache.add(info_key)
                deduplicated.append(info)
        return deduplicated

    def validate_and_enhance_result(self, result: Dict[str, Any], chunk_nb: int) -> Dict[str, Any]:
        """验证和增强分析结果"""
        if not result.get('key_info'):
            self.logger.warning(f"块 {chunk_nb} 的关键信息为空，需要人工检查")
        for field in ['key_info', 'global_info', 'corrections', 'formulas', 'object_references']:
            if field not in result:
                result[field] = []
        return result

    def analyse(self, file_name: str, file: BytesIO, chunk_size: int = 5000) -> List[str]:
        """主要分析函数 - 改进版"""
        try:
            # 重置缓存
            self.global_info_cache.clear()
            self.key_info_cache.clear()

            # 获取和合并文档块
            content = FileReaderTools().get_file_content(file, file_name).replace("  ", " ")
            slices_data = [chunk for chunk in content.replace("\\n", "\n").split("\n\n") if chunk]
            merged_chunks = self.merge_chunk(slices_data, chunk_size)
            chunk_count = len(merged_chunks)

            self.logger.info(f"开始分析文档 {file_name}，共 {chunk_count} 个块")

            summary = ""
            summary_list = []
            global_infos = []

            for nb, chunk in enumerate(merged_chunks, 1):
                self.logger.info(f"处理第 {nb}/{chunk_count} 块")

                try:
                    prompt = DocAnalysisPrompt(chunk_count, nb, summary, chunk, global_infos)
                    analyse_outcome = aided_chat(prompt, "", DocAnalysisModel)
                    analyse_outcome = self.validate_and_enhance_result(analyse_outcome, nb)
                    new_global_info = analyse_outcome.get("global_info", [])
                    deduplicated_global_info = self.deduplicate_global_info(new_global_info)
                    if deduplicated_global_info:
                        global_info_text = self.title_description_to_text(deduplicated_global_info)
                        global_infos.extend(global_info_text)

                    analyse_outcome.pop("global_info", None)
                    summary = analyse_outcome.get("content", "")
                    summary_list.append(analyse_outcome)

                except Exception as e:
                    self.logger.error(f"处理第 {nb} 块时出错: {e}")

            results = self.merge_tool.process_doc_analysis_results(merged_chunks, summary_list, global_infos)
            return results

        except Exception as e:
            self.logger.error(f"文档分析失败: {e}")
            raise

