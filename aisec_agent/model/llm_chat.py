#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/8/15 9:22
# @Author  : GuJR
# @Site    : 
# @File    : llm_chat.py
import json
import logging
import requests
from datetime import datetime
from typing import Dict, Any, Optional, Generator, Union, Type

from aisec_agent.logic.llm_presets import normalize_anthropic_messages_url

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = None


class BaseLLMProvider:
    """LLM服务商基础类"""

    def __init__(self, timeout: int = 600):
        self.timeout = timeout
        self.session = requests.Session()

    def __del__(self):
        """确保会话正确关闭，避免内存泄漏"""
        if hasattr(self, 'session'):
            self.session.close()

    def _format_message(self, prompt: str, message: str) -> str:
        """格式化消息内容"""
        return f"""
        <system>
        {prompt}

        time_now: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        </system>

        <user>
        {message}
        </user>
        """

    def _truncate_prompt(self, prompt: str, max_length: int) -> str:
        """截断过长的提示词"""
        if len(prompt) > max_length:
            return prompt[:max_length]
        return prompt

    def _parse_stream_line(self, line: bytes) -> Optional[str]:
        """解析流式响应行"""
        if not line:
            return None

        try:
            line_text = line.decode('utf-8')
            if line_text.startswith("data: "):
                line_text = line_text[6:]

            if line_text.strip() in ['', '[DONE]']:
                return None

            return line_text
        except UnicodeDecodeError:
            return None

    def _safe_post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], stream: bool,
                   max_retries: int = 3) -> requests.Response:
        """安全的POST请求，包含错误处理和重试机制"""
        import time
        from requests.exceptions import Timeout, ConnectionError, RequestException

        last_exception = None

        for attempt in range(max_retries):
            try:
                # 根据是否流式调用设置不同的超时时间
                timeout = (30, 300) if stream else (30, 120)  # (连接超时, 读取超时)
                for nb in range(1, 4):# 尝试3次
                    try:
                        response = self.session.post(
                            url,
                            json=payload,
                            headers=headers,
                            stream=stream,
                            timeout=timeout
                        )
                        response.raise_for_status()
                        return response
                    except:
                        continue

            except (Timeout, ConnectionError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避
                    logging.warning(f"API调用超时，{wait_time}秒后重试 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"API调用失败，已达到最大重试次数: {str(e)}")
                    raise

            except RequestException as e:
                logging.error(f"API调用失败: {str(e)}")
                raise

        # 如果所有重试都失败了
        if last_exception:
            raise last_exception


class BaseHandler:
    """基础处理器类"""
    pass


class LLMChatTools(BaseHandler):
    """LLM聊天工具类"""

    def __init__(self):
        super().__init__()
        # 初始化基础提供者实例
        self._ollama_provider = BaseLLMProvider()
        self._lm_studio_provider = BaseLLMProvider()
        self._hsyq_provider = BaseLLMProvider()
        self._openai_provider = BaseLLMProvider()
        self._minimax_provider = BaseLLMProvider()
        self._anthropic_provider = BaseLLMProvider()
        self._qwen_provider = BaseLLMProvider()
        self._siliconflow_provider = BaseLLMProvider()
        self._hunyuan_provider = BaseLLMProvider()
        self._zhipu_provider = BaseLLMProvider()
        self._xunfei_provider = BaseLLMProvider()
        self._deepseek_provider = BaseLLMProvider()
        self._gemini_provider = BaseLLMProvider()
        self._kimi_provider = BaseLLMProvider()

    def ollama_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, options: Dict[str, Any] = None,
                    stream: bool = False, max_len_input: int = 52000, json_format: Union[bool, Type[BaseModel]] = False,
                    **kwargs) -> Union[str, Generator[str, None, None]]:
        if model is None:
            raise ValueError("必须提供模型名称")

        if options is None:
            options = {}

        # 动态设置上下文长度
        message_len = int(len(prompt) * 1.5)
        if message_len < int(max_len_input):
            options["num_ctx"] = message_len
        else:
            options["num_ctx"] = max_len_input
            prompt = self._ollama_provider._truncate_prompt(prompt, max_len_input)

        formatted_message = self._ollama_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream,
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["format"] = json_format.model_json_schema()
            else:
                payload["format"] = "json"

        payload.update(options)

        response = self._ollama_provider._safe_post(url, {}, payload, stream, max_retries=2)

        if not stream:
            return response.json()["message"]["content"]
        else:
            return self._ollama_stream_generator(response)

    def _ollama_stream_generator(self, response) -> Generator[str, None, None]:
        """Ollama流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._ollama_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    content = data.get("message", {}).get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析Ollama响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def gemini_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                    max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """Google Gemini聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._gemini_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._gemini_provider._format_message(prompt, message)

        # Gemini的JSON格式化需要在消息中指定
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                formatted_message += f"\n\n请按照以下JSON Schema格式返回响应：{json.dumps(json_format.model_json_schema(), ensure_ascii=False)}"
            else:
                formatted_message += "\n\n请以JSON格式返回响应。"

        payload = {
            "contents": [{"parts": [{"text": formatted_message}]}],
            "generationConfig": {"temperature": 0.7}
        }
        headers = {"x-goog-api-key": api_key}

        # Gemini使用不同的URL格式
        if stream:
            url = url.replace(":generateContent", ":streamGenerateContent")

        response = self._gemini_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return self._gemini_stream_generator(response)

    def _gemini_stream_generator(self, response) -> Generator[str, None, None]:
        """Google Gemini流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._gemini_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            yield parts[0]["text"]
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析Gemini响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def kimi_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                  max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """Kimi聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._kimi_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._kimi_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,"schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._kimi_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._kimi_stream_generator(response)

    def _kimi_stream_generator(self, response) -> Generator[str, None, None]:
        """Kimi流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._kimi_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析Kimi响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def zhipu_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                   max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """智谱清言聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._zhipu_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._zhipu_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._zhipu_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._zhipu_stream_generator(response)

    def _zhipu_stream_generator(self, response) -> Generator[str, None, None]:
        """智谱清言流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._zhipu_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析智谱清言响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def xunfei_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                    max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """讯飞星火聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._xunfei_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._xunfei_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._xunfei_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._xunfei_stream_generator(response)

    def _xunfei_stream_generator(self, response) -> Generator[str, None, None]:
        """讯飞星火流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._xunfei_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析讯飞星火响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def deepseek_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                      max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """Deepseek聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._deepseek_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._deepseek_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._deepseek_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._deepseek_stream_generator(response)

    def _deepseek_stream_generator(self, response) -> Generator[str, None, None]:
        """Deepseek流式响应生成器"""
        is_think = False
        try:
            for line in response.iter_lines():
                line_text = self._deepseek_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})

                    # Deepseek支持思考内容
                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        if not is_think:
                            yield "<think>"
                            is_think = True
                        yield delta["reasoning_content"]
                    elif delta.get("content"):
                        if is_think:
                            yield "</think>"
                            is_think = False
                        yield delta["content"]

                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析Deepseek响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def hunyuan_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                     max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """腾讯混元聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._hunyuan_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._hunyuan_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._hunyuan_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._hunyuan_stream_generator(response)

    def _hunyuan_stream_generator(self, response) -> Generator[str, None, None]:
        """腾讯混元流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._hunyuan_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析腾讯混元响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def siliconflow_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                         max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> \
            Union[str, Generator[str, None, None]]:
        """硅基流动聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._siliconflow_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._siliconflow_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._siliconflow_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._siliconflow_stream_generator(response)

    def _siliconflow_stream_generator(self, response) -> Generator[str, None, None]:
        """硅基流动流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._siliconflow_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析硅基流动响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def qwen_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                  max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """通义千问聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._qwen_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._qwen_provider._format_message(prompt, message)

        # 通义千问的JSON格式化需要在消息中指定
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                formatted_message += f"\n\n请按照以下JSON Schema格式返回响应：{json.dumps(json_format.model_json_schema(), ensure_ascii=False)}"
            else:
                formatted_message += "\n\n请以JSON格式返回响应。"

        payload = {
            "model": model,
            "input": {
                "messages": [{"role": "user", "content": formatted_message}]
            },
            "parameters": {
                "incremental_output": stream
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        response = self._qwen_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["output"]["text"]
        else:
            return self._qwen_stream_generator(response)

    def _qwen_stream_generator(self, response) -> Generator[str, None, None]:
        """通义千问流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._qwen_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    content = data.get("output", {}).get("text")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析通义千问响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def anthropic_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                       max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> \
            Union[str, Generator[str, None, None]]:
        """Anthropic聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._anthropic_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._anthropic_provider._format_message(prompt, message)

        # Anthropic的JSON格式化需要在消息中指定
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                formatted_message += f"\n\n请按照以下JSON Schema格式返回响应：{json.dumps(json_format.model_json_schema(), ensure_ascii=False)}"
            else:
                formatted_message += "\n\n请以JSON格式返回响应。"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "max_tokens": 4096,
            "stream": stream
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }

        response = self._anthropic_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["content"][0]["text"]
        else:
            return self._anthropic_stream_generator(response)

    def _anthropic_stream_generator(self, response) -> Generator[str, None, None]:
        """Anthropic流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._anthropic_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    if data.get("type") == "content_block_delta":
                        content = data.get("delta", {}).get("text")
                        if content:
                            yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析Anthropic响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def openai_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                    max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        """OpenAI聊天接口"""
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._openai_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._openai_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._openai_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._openai_stream_generator(response)

    def _openai_stream_generator(self, response) -> Generator[str, None, None]:
        """OpenAI流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._openai_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析OpenAI响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def minimax_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                     max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False,
                     options: Dict[str, Any] = None, **kwargs) -> Union[str, Generator[str, None, None]]:
        """MiniMax OpenAI-compatible chat interface."""
        if model is None:
            raise ValueError("必须提供模型名称")

        if options is None:
            options = {}

        prompt = self._minimax_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._minimax_provider._format_message(prompt, message)

        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                schema = json.dumps(json_format.model_json_schema(), ensure_ascii=False)
                formatted_message += f"\n\nReturn JSON only. The JSON must match this schema: {schema}"
            else:
                formatted_message += "\n\nReturn JSON only."

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream,
            "thinking": {"type": "disabled"},
        }
        payload.update(options)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response = self._minimax_provider._safe_post(url, headers, payload, stream, max_retries=3)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._minimax_stream_generator(response)

    def minimax_anthropic_chat(self, url: str, api_key: str, prompt: str, message: str, model: str,
                               stream: bool = False, max_len_input: int = 16000,
                               json_format: Union[bool, Type[BaseModel]] = False,
                               options: Dict[str, Any] = None, **kwargs) -> Union[str, Generator[str, None, None]]:
        """MiniMax Anthropic-compatible Messages interface."""
        if model is None:
            raise ValueError("必须提供模型名称")

        if options is None:
            options = {}

        prompt = self._minimax_provider._truncate_prompt(prompt, max_len_input)
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                schema = json.dumps(json_format.model_json_schema(), ensure_ascii=False)
                prompt += f"\n\nReturn JSON only. The JSON must match this schema: {schema}"
            else:
                prompt += "\n\nReturn JSON only."

        payload = {
            "model": model,
            "max_tokens": int(kwargs.get("max_tokens") or 4096),
            "system": prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": message}],
                }
            ],
            "stream": stream,
        }
        payload.update(options)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response = self._minimax_provider._safe_post(
            normalize_anthropic_messages_url(url),
            headers,
            payload,
            stream,
            max_retries=3,
        )

        if not stream:
            return self._extract_anthropic_text(response.json())
        else:
            return self._minimax_anthropic_stream_generator(response)

    @staticmethod
    def _extract_anthropic_text(data: Dict[str, Any]) -> str:
        content = data.get("content", [])
        if isinstance(content, str):
            return content
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)

    def _minimax_anthropic_stream_generator(self, response) -> Generator[str, None, None]:
        try:
            for line in response.iter_lines():
                line_text = self._minimax_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    if data.get("type") == "content_block_delta":
                        content = data.get("delta", {}).get("text")
                        if content:
                            yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"Unable to parse MiniMax Anthropic response: {line_text}, error: {e}")
                    continue
        finally:
            response.close()

    def _minimax_stream_generator(self, response) -> Generator[str, None, None]:
        """MiniMax OpenAI-compatible stream response generator."""
        try:
            for line in response.iter_lines():
                line_text = self._minimax_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"Unable to parse MiniMax response: {line_text}, error: {e}")
                    continue
        finally:
            response.close()

    def lm_studio_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                       max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> \
            Union[str, Generator[str, None, None]]:
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._lm_studio_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._lm_studio_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": api_key}

        response = self._lm_studio_provider._safe_post(url, headers, payload, stream, max_retries=2)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._lm_studio_stream_generator(response)

    def _lm_studio_stream_generator(self, response) -> Generator[str, None, None]:
        """LM Studio流式响应生成器"""
        try:
            for line in response.iter_lines():
                line_text = self._lm_studio_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析LM Studio响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()

    def hsyq_chat(self, url: str, api_key: str, prompt: str, message: str, model: str, stream: bool = False,
                  max_len_input: int = 16000, json_format: Union[bool, Type[BaseModel]] = False, **kwargs) -> Union[
        str, Generator[str, None, None]]:
        if model is None:
            raise ValueError("必须提供模型名称")

        prompt = self._hsyq_provider._truncate_prompt(prompt, max_len_input)
        formatted_message = self._hsyq_provider._format_message(prompt, message)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": formatted_message}],
            "stream": stream
        }

        # 添加JSON格式化支持
        if json_format:
            if BaseModel and isinstance(json_format, type) and issubclass(json_format, BaseModel):
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": json_format.__name__,
                                                                                     "schema": json_format.model_json_schema()}}
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}

        response = self._hsyq_provider._safe_post(url, headers, payload, stream, max_retries=2)

        if not stream:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return self._hsyq_stream_generator(response)

    def _hsyq_stream_generator(self, response) -> Generator[str, None, None]:
        """火山引擎流式响应生成器，处理思考内容"""
        is_think = False
        try:
            for line in response.iter_lines():
                line_text = self._hsyq_provider._parse_stream_line(line)
                if line_text is None:
                    continue

                try:
                    data = json.loads(line_text)
                    delta = data.get("choices", [{}])[0].get("delta", {})

                    # 处理思考内容
                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        if not is_think:
                            yield "<think>"
                            is_think = True
                        yield delta["reasoning_content"]
                    elif delta.get("content"):
                        if is_think:
                            yield "</think>"
                            is_think = False
                        yield delta["content"]

                except json.JSONDecodeError as e:
                    logging.warning(f"无法解析火山引擎响应: {line_text}, 错误: {e}")
                    continue
        finally:
            response.close()
