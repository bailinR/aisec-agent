#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/17 16:38
# @Author  : GuJR
# @Site    : 
# @File    : _tools.py
import ast
import base64
import io
import os
import time
from datetime import datetime
import re
from typing import Dict, Any, BinaryIO, Optional, Union, List
import requests
import json
import logging
import traceback
from zipfile import ZipFile, BadZipFile
from file_extractor.docx_extractor import DocxExtractor
from file_extractor.html_extractor import HtmlExtractor
from file_extractor.excel_extractor import ExcelExtractor
from file_extractor.csv_extractor import CSVExtractor
from file_extractor.markdown_extractor import MarkdownExtractor
from file_extractor.text_extractor import TextExtractor
from file_extractor.pdf_extractor import PdfExtractor as PDFExtractor
from pydantic._internal._model_construction import ModelMetaclass
from aisec_agent.config import AIDED_LLM_CONF, VISION_LLM_CONF
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.llm_chat import LLMChatTools
from aisec_agent.model.oss import MinioClient
from aisec_agent.model.r import MINIO_IMAGE


class LoadFileTools(BaseHandler):  # 工具类
    @staticmethod
    def load_json_file(file_path: str) -> json:
        with open(file_path, encoding='utf-8') as f:
            return json.load(f)

    @staticmethod
    def load_file(dir_path: str, file_name: str, suffix: str) -> str:
        """加载prompt文件内容"""
        try:
            file_path = os.path.join(dir_path, f"{file_name}.{suffix}")
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.error(f"加载提示文件失败: {file_name}.prompt, 错误: {str(e)}")
            return f"未能加载提示文件 {file_name}"


class LoadJsonTools(BaseHandler):

    def remove_json_comments(self, json_str: str) -> str:
        """清理JSON字符串中的注释和格式问题"""
        try:
            json_str = re.sub(r'\s*//.*$', '', json_str, flags=re.MULTILINE)  # 去除单行注释
            json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)  # 去除多行注释
            return json_str.strip()
        except Exception as e:
            logging.error(f"清理JSON字符串失败: {str(e)}")
            return json_str

    def safe_json_parse(self, json_str: str) -> dict:
        """安全解析JSON字符串"""
        try:
            return ast.literal_eval(json_str)
        except json.JSONDecodeError as e:
            try:
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                quote_count = json_str.count('"') - json_str.count('\\"')
                if quote_count % 2 != 0:
                    logging.warning("检测到未闭合的引号，尝试修复")
                    json_str = json_str.rstrip()[:-1] + '"}'
                return ast.literal_eval(json_str)
            except json.JSONDecodeError as e2:
                # 打印详细错误信息以帮助调试
                logging.error(f"JSON解析错误: {str(e2)}")
                error_position = e2.pos
                context = json_str[max(0, error_position - 50):min(len(json_str), error_position + 50)]
                logging.error(f"错误位置附近的内容: {context}")
                raise

    def remove_think_tags(self, text):
        pattern = r'<think>.*?</think>\n?\n?'
        return re.sub(pattern, '', text, flags=re.DOTALL)

    def strQ2B(self, ustring: str):
        """全角转半角（修正版）"""
        result = []
        for char in ustring:
            code = ord(char)
            if code == 0x3000:
                result.append(' ')
            elif 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            else:
                result.append(char)
        return ''.join(result)

    def process_llm_response(self, text_result: str, result_type: str = "json") -> dict:
        """处理大模型返回的结果（增强版）"""
        try:
            # 预处理全角符号
            normalized_text = self.strQ2B(text_result)
            json_matches = re.findall(r'({(?:[^{}]|(?:{[^{}]*}))*})', normalized_text, re.DOTALL)
            if json_matches:
                json_str = self.remove_json_comments(json_matches[0])
            else:
                del_think_str = self.remove_think_tags(normalized_text)
                json_str = self.remove_json_comments(del_think_str)

            # 二次校验转换结果
            if any(0xFF00 <= ord(c) <= 0xFFEF for c in json_str):  # [7](@ref)
                json_str = self.strQ2B(json_str)

            if result_type == "dict":
                return self.safe_json_parse(json_str)
            return json.loads(json_str)
        except Exception as e:
            raise ValueError(f"JSON解析失败: {str(e)}")


class FileReaderTools(BaseHandler):
    """增强的文件读取工具类，整合了RAG系统中的高级功能"""

    def _init(self):
        self.minio_client = MinioClient()
        self.image2text = ImageToTextLogic()

    def upload_image(self, bucket_name: str, images: list[dict]):
        self.minio_client.make_bucket(bucket_name, is_public=True)
        images_dict = {}
        for idx, image in enumerate(images):
            try:
                image_data = io.BytesIO(image["content"])
                filename = f"image_{int(time.time())}_{idx}.png"
                self.minio_client.upload_object(
                    bucket_name=bucket_name,
                    filename=filename,
                    file_data=image_data,
                    file_size=len(image["content"]),
                    content_type="image/png"
                )
                image_base64_string = base64.b64encode(image["content"]).decode('utf-8')
                image_text = self.image2text.describe_image(image_base64_string)
                pattern = re.compile(r'<think>.*?</think>', re.DOTALL)
                cleaned_text = pattern.sub('', image_text).strip().replace("\n\n", "\n")
                images_dict[
                    f"![image_{idx}]"] = f"![{filename}](/api/sys/resource/download?filename={filename})\n上图描述: {cleaned_text}\n"
            except Exception as e:
                logging.error(f"保存图片到Minio失败: {str(e)}")
        return images_dict

    def read_zip(self, file: BinaryIO) -> str:
        """读取ZIP文件内容"""
        try:
            file.seek(0)
            with ZipFile(file) as zip_file:
                # 列出文件名
                file_list = zip_file.namelist()
                # 提取常见文本文件
                text_files = [f for f in file_list if f.endswith(
                    ('.txt', '.log', '.xml', '.json', '.md', '.py', '.java', '.js', '.html', '.css'))]

                content = [f"压缩包内容({len(file_list)}个文件):"]
                content.append(
                    "文件列表: " + ", ".join(file_list[:20]) + ("..." if len(file_list) > 20 else ""))

                # 提取部分内容
                for text_file in text_files[:5]:  # 限制提取数量
                    try:
                        file_content = zip_file.read(text_file).decode('utf-8', errors='replace')
                        # 截取部分内容
                        preview = file_content[:500] + ("..." if len(file_content) > 500 else "")
                        content.append(f"\n文件: {text_file}\n{preview}")
                    except:
                        content.append(f"\n文件: {text_file} (无法读取)")

                return "\n".join(content)
        except BadZipFile:
            return "文件不是有效的ZIP格式"
        except Exception as e:
            logging.error(f"读取ZIP文件失败: {str(e)}")
            return f"无法读取ZIP文件: {str(e)}"

    def read_file(self, file: BinaryIO, file_ext: str) -> str:
        """根据文件类型读取内容，使用 py-extractor 包。"""
        try:
            file.seek(0)

            def process_text_and_images(text_content: str, images_data: list) -> str:
                if images_data:
                    images_dict = self.upload_image(MINIO_IMAGE, images_data)
                    for image_placeholder, image_url in images_dict.items():
                        if image_placeholder in text_content:
                            text_content = text_content.replace(image_placeholder, image_url)
                        else:
                            text_content += f"\n{image_url}"
                return text_content

            if file_ext.lower() == '.docx':
                extractor = DocxExtractor(file)
                result = extractor.extract()
                text = result["text_content"]
                text = process_text_and_images(text, result.get("images", []))
                return text
            elif file_ext.lower() == '.pdf':
                extractor = PDFExtractor(file)
                result = extractor.extract()
                all_text = []
                for page_data in result:
                    text = page_data["text_content"]
                    # text = process_text_and_images(text, page_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)
            elif file_ext.lower() in ['.xls', '.xlsx']:
                extractor = ExcelExtractor(file)
                result = extractor.extract()
                all_text = []
                for row_data in result:
                    text = row_data["text_content"]
                    text = process_text_and_images(text, row_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)
            elif file_ext.lower() == '.csv':
                extractor = CSVExtractor(file)
                result = extractor.extract()
                all_text = []
                for row_data in result:
                    text = row_data["text_content"]
                    text = process_text_and_images(text, row_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)
            elif file_ext.lower() == '.md':
                extractor = MarkdownExtractor(file)
                result = extractor.extract()
                all_text = []
                for doc_data in result:
                    text = doc_data["text_content"]
                    text = process_text_and_images(text, doc_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)
            elif file_ext.lower() in ['.txt', '.log', '.xml', '.json', '.py', '.java', '.js', '.html', '.css']:
                if file_ext.lower() == '.html':
                    extractor = HtmlExtractor(file)
                elif file_ext.lower() == '.md':
                    extractor = MarkdownExtractor(file)
                else:
                    extractor = TextExtractor(file)
                result = extractor.extract()
                all_text = []
                for doc_data in result:
                    text = doc_data["text_content"]
                    text = process_text_and_images(text, doc_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)
            elif file_ext.lower() in ['.zip', '.jar']:
                return self.read_zip(file)
            else:
                extractor = TextExtractor(file)
                result = extractor.extract()
                all_text = []
                for doc_data in result:
                    text = doc_data["text_content"]
                    text = process_text_and_images(text, doc_data.get("images", []))
                    all_text.append(text)
                return "\n\n".join(all_text)

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"读取文件失败: {str(e)}\n{error_details}")
            return f"读取文件时发生错误: {str(e)}"

    def get_file_content(self, file: BinaryIO, file_name: str) -> str:
        """根据文件后缀读取内容"""
        file_ext = os.path.splitext(file_name)[1].lower()
        return self.read_file(file, file_ext)


def aided_chat(prompt: str, question: str, _typing: ModelMetaclass = None,
               url: str = AIDED_LLM_CONF["url"], key: str = AIDED_LLM_CONF["key"],
               max_len_input: int = AIDED_LLM_CONF["max_len_input"],
               model_name: str = AIDED_LLM_CONF["model_name"], func_name: str = AIDED_LLM_CONF["func_name"]):
    chat = getattr(LLMChatTools(), func_name)

    text_result = chat(
        url=url, api_key=key, prompt=prompt, message=question, model=model_name,
        json_format=_typing if _typing else True, stream=False, max_len_input=max_len_input
    )

    try:
        result_dict = json.loads(text_result)
    except json.decoder.JSONDecodeError:
        if _typing:
            schema = _typing.model_json_schema()
            result_dict = {field: "" for field in schema.get("required", [])}
        else:
            result_dict = {}

    return result_dict


class ImageToTextLogic(BaseHandler):

    def _init(self):
        self.url = VISION_LLM_CONF.get("url")
        self.model_name = VISION_LLM_CONF.get("model_name")
        self.image2text_prompt = """
            你是一个专业的图像分析员。你的任务是根据提供的图片，生成一份对该图的精准、详细、连贯的纯文本描述。
            请严格遵循以下要求：
            * 避免任何寒暄、前缀、后缀或无关解释。
            * 明确描述这张图希望表达的重点
            * 需要简短说明整张图片的主题
            * 识别并说明主要模块、决策点、输入和输出。
            * 识别并说明图例或不同形状的含义（如果图中有明确图例）。
            * 对重复出现的相同标签，仅在第一次出现时详细说明其作用，后续提及只需指出其位置或连接关系，避免冗余重复文字。
            * 确保描述的流畅性和准确性，旨在让一个不看图的人也能理解流程。
            * 仅输出描述文本
            """
        self.image2text_massage = "请根据您的角色和指令，对这张图进行精准、详细、连贯的描述，直接输出描述文本。"

    def describe_image(self, image_base64: Optional[str] = None, timeout: int = 900, **kwargs) -> str:
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dynamic_system_prompt = f"{self.image2text_prompt.strip()}\n当前时间: {current_time_str}"
        messages_payload = [
            {
                "role": "system",
                "content": dynamic_system_prompt
            },
            {
                "role": "user",
                "content": self.image2text_massage,
                "images": [image_base64]
            }
        ]
        payload = {
            "model": self.model_name,
            "messages": messages_payload,
            "stream": False,
            "options": {
                "num_predict": -1,
                "repeat_penalty": 1.6,  # 应用重复惩罚
                "temperature": 0.2
            }
        }
        payload.update(kwargs)

        try:
            response = requests.post(self.url, json=payload, timeout=timeout)
            response.raise_for_status()
            response_json = response.json()
            if "message" in response_json and "content" in response_json["message"]:
                description = response_json["message"]["content"].strip()
                return description
            else:
                logging.error(f"Ollama API 响应格式异常或无内容: {response_json}")
                raise ValueError(f"API响应格式异常或无内容 - {response_json}")

        except requests.exceptions.Timeout as e:
            logging.error(f"Ollama API 调用超时: {e}")
            raise requests.exceptions.Timeout(f"Ollama API 调用超时: {e}")
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Ollama API 连接错误: {e}")
            raise requests.exceptions.ConnectionError(f"Ollama API 连接错误: {e}")
        except requests.exceptions.HTTPError as e:
            logging.error(f"Ollama API HTTP错误: {e.response.status_code} - {e.response.text}")
            raise requests.exceptions.HTTPError(f"Ollama API HTTP错误: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logging.error(f"调用 Ollama API 失败: {str(e)}")
            raise


class ToMarkdownTable(BaseHandler):
    def json_to_markdown_table(self, data: Union[Dict, List[Dict]], max_depth: int = 2) -> str:
        def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.', depth: int = 0) -> Dict[str, Any]:
            """递归扁平化字典"""
            items = []

            for k, v in d.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k

                if isinstance(v, dict) and depth < max_depth:
                    items.extend(flatten_dict(v, new_key, sep=sep, depth=depth + 1).items())
                elif isinstance(v, list):
                    if v and isinstance(v[0], dict) and depth < max_depth:
                        # 如果是字典列表，取第一个作为示例
                        items.extend(flatten_dict(v[0], f"{new_key}[0]", sep=sep, depth=depth + 1).items())
                        if len(v) > 1:
                            items.append((f"{new_key}_count", len(v)))
                    else:
                        # 简单列表，用逗号连接
                        items.append((new_key, ', '.join(map(str, v)) if v else ''))
                else:
                    # 简单值或超过最大深度的复杂对象
                    if isinstance(v, (dict, list)):
                        items.append((new_key, json.dumps(v, ensure_ascii=False)))
                    else:
                        items.append((new_key, v))

            return dict(items)

        def escape_markdown(text: str) -> str:
            """转义Markdown特殊字符"""
            if text is None:
                return ''
            text = str(text)
            # 转义管道符和换行符
            text = text.replace('|', '\\|').replace('\n', '<br>').replace('\r', '')
            return text

        if isinstance(data, dict):
            data = [data]

        # 扁平化所有数据并收集字段
        flattened_data = []
        all_keys = set()

        for item in data:
            if isinstance(item, dict):
                flattened = flatten_dict(item)
                flattened_data.append(flattened)
                all_keys.update(flattened.keys())
            else:
                # 如果列表中有非字典项，转换为字典
                flattened_data.append({"value": item})
                all_keys.add("value")

        # 排序字段名
        sorted_keys = sorted(all_keys)

        # 构建Markdown表格
        markdown_lines = []

        # 表头
        header = "| " + " | ".join(sorted_keys) + " |"
        markdown_lines.append(header)

        # 分隔线
        separator = "|" + "|".join([" --- " for _ in sorted_keys]) + "|"
        markdown_lines.append(separator)

        # 数据行
        for item in flattened_data:
            row_values = []
            for key in sorted_keys:
                value = item.get(key, '')
                escaped_value = escape_markdown(value)
                row_values.append(escaped_value)

            row = "| " + " | ".join(row_values) + " |"
            markdown_lines.append(row)

        return "\n".join(markdown_lines)

    def json_to_simple_table(self, data: Union[Dict, List[Dict]]) -> str:
        if isinstance(data, list):
            if len(data) == 1:
                data = data[0]
            else:
                # 多个对象，使用标准方法
                return self.json_to_markdown_table(data)

        if not isinstance(data, dict):
            return f"| 值 |\n| --- |\n| {data} |"

        markdown_lines = []
        markdown_lines.append("| 字段 | 值 |")
        markdown_lines.append("| --- | --- |")

        def format_value(v):
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False, indent=2).replace('\n', '<br>').replace('|', '\\|')
            return str(v).replace('|', '\\|').replace('\n', '<br>')

        for key, value in data.items():
            escaped_key = str(key).replace('|', '\\|')
            escaped_value = format_value(value)
            markdown_lines.append(f"| {escaped_key} | {escaped_value} |")

        return "\n".join(markdown_lines)

    def json_string_to_markdown_table(self, json_string: str, max_depth: int = 2) -> str:
        """
        从JSON字符串转换为Markdown表格

        Args:
            json_string: JSON格式的字符串
            max_depth: 最大嵌套深度

        Returns:
            str: Markdown表格
        """
        try:
            data = json.loads(json_string)
            return self.json_to_markdown_table(data, max_depth)
        except json.JSONDecodeError as e:
            return f"JSON解析错误: {e}"
