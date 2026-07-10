import json
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Dict, List, Any, Optional
from typing import Literal

import requests

from aisec_agent.config import GENIE_API
from aisec_agent.model.define import BaseHandler


@dataclass
class Doc:
    """文档数据类"""
    doc_type: Literal["web_page"]
    content: str
    title: str
    link: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    unique_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = "",
    is_chunk: bool = False
    chunk_id: int = -1  # chunk标记

    def __str__(self):
        doc_type_map = {
            "web_page": "网页",
        }

        return (
            f"Doc(\n"
            f"  文档搜索条件={self.query},\n"
            f"  文档类型={doc_type_map.get(self.doc_type, self.doc_type)},\n"
            f"  文档标题={self.title},\n"
            f"  文档链接={self.link},\n"
            f"  文档内容={self.content},\n"
            f")"
        )

    def to_html(self):
        return (
            f"<div>\n"
            f"  <p>文档搜索条件:{self.query}</p>\n"
            f"  <p>文档类型:{self.doc_type}</p>\n"
            f"  <p>文档标题:{self.title}</p>\n"
            f"  <p>文档链接:{self.link}</p>\n"
            f"  <p>文档内容:{self.content}</p>\n"
            f"</div>"
        )

    def to_markdown(self, max_content_chars: int = 0, code_block: bool = True) -> str:
        """
        将对象转成 Markdown 文本
        :param max_content_chars: 内容最大字符数，0 表示不截断
        :param code_block: 内容是否用代码块包裹
        """

        def _md_escape(s: str) -> str:
            if s is None:
                return ""
            # 基础 Markdown 转义
            repl = {
                "\\": "\\\\", "`": "\\`", "*": "\\*", "_": "\\_",
                "{": "\\{", "}": "\\}", "[": "\\[", "]": "\\]",
                "(": "\\(", ")": "\\)", "#": "\\#", "+": "\\+",
                "-": "\\-", "!": "\\!", "|": "\\|", ">": "\\>",
            }
            return "".join(repl.get(ch, ch) for ch in str(s))

        def _truncate(s: str) -> str:
            if not s:
                return ""
            if max_content_chars and len(s) > max_content_chars:
                return s[:max_content_chars] + "…"
            return s

        query = _md_escape(getattr(self, "query", ""))
        doc_type = _md_escape(getattr(self, "doc_type", ""))
        title_raw = getattr(self, "title", "") or ""
        title = _md_escape(title_raw)
        link = (getattr(self, "link", "") or "").strip()
        content_raw = getattr(self, "content", "") or ""
        content = _truncate(content_raw)

        # 标题行：有链接则用 [title](link)，否则直接标题
        if link:
            title_md = f"[{title}]({link})"
        else:
            title_md = title or "(无标题)"

        header = "### 文档\n"
        meta = [
            f"- **文档搜索条件**：`{query}`" if query else "- **文档搜索条件**：`-`",
            f"- **文档类型**：`{doc_type}`" if doc_type else "- **文档类型**：`-`",
            f"- **文档标题**：{title_md}",
        ]
        if link and not title_raw:  # 没有标题但有链接，额外展示链接
            meta.append(f"- **文档链接**：{link}")

        # 内容块
        if content:
            if code_block:
                body = f"\n**文档内容：**\n\n```\n{content}\n```\n"
            else:
                body = f"\n**文档内容：**\n\n{_md_escape(content)}\n"
        else:
            body = "\n**文档内容：**（空）\n"

        return header + "\n".join(meta) + body

    def to_dict(self, truncate_len: int = 0):
        content = self.content[0:truncate_len] if truncate_len > 0 else self.content
        return {
            "doc_type": self.doc_type,
            "content": content,
            "title": self.title,
            "link": self.link,
            "data": self.data, "query": self.query,
        }
def timing_decorator(func):
    """
    时间计算装饰器

    Args:
        func: 被装饰的函数

    Returns:
        装饰后的函数，会自动计算并打印执行时间
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        elapsed_time = end_time - start_time
        return result

    return wrapper


class ReportAgents(BaseHandler):
    def _init(self, ):
        self.report_url = f"{GENIE_API}/tool/report"
        self.deep_search_url = f"{GENIE_API}/tool/deepsearch"
        self.ppt_json_url = f"{GENIE_API}/tool/ppt/stream"
        self.ppt_to_js_url = f"{GENIE_API}/tool/html_to_pptx"
        self.headers = {'Content-Type': 'application/json'}


    @timing_decorator
    def send_stream_request(self, url: str, data: Dict[str, Any], timeout: int = 3000, ):
        """
        通用流式请求（SSE 友好）
        - 识别并剥离 'data:' 前缀（SSE）
        - 过滤 heartbeat（大小写）
        - 聚合/缓冲分片 JSON（避免半截 JSON 解析失败）
        - 捕获 [DONE] 结束信号
        - 每成功解析一条 JSON，就 yield {"success": True, "data": <dict>}
          若真遇到非 JSON 文本（且不是 heartbeat），以原文字符串返回
        """
        try:
            with requests.post(url, headers=self.headers, json=data, stream=True, timeout=timeout) as resp:
                if resp.status_code != 200:
                    yield {"success": False, "error": resp.text, "status": resp.status_code}
                    return

                pending = ""  # 用于缓冲被拆分的 JSON 片段

                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        # SSE 事件通常以空行分隔；这里让缓冲在下一次循环处理
                        continue

                    line = raw.strip()

                    # --- 1) 只处理 'data:' 行；其他如 'event:'、'id:' 暂不需要，可按需扩展 ---
                    if line.startswith("data:"):
                        payload = line[5:].strip()  # 剥掉 'data: '

                        # 结束标识
                        if "[DONE]" in payload:
                            break

                        # 过滤心跳（大小写）
                        if payload.lower() == "heartbeat":
                            continue

                        # --- 2) 累加到缓冲，尝试解析 JSON ---
                        if pending:
                            pending += payload
                        else:
                            pending = payload

                        # 有些服务可能一个事件分多次 data: 推送，这里尽量增量解析
                        try:
                            obj = json.loads(pending)
                            yield {"success": True, "data": obj}
                            pending = ""  # 成功解析后清空缓冲
                        except json.JSONDecodeError:
                            # 还不完整，继续累加
                            continue

                    else:
                        # 不是 'data:' 的行：有些实现会把纯文本塞进来，这里按原样吐出（可按需关闭）
                        # 也可选择直接 continue 忽略

                        yield {"success": True, "data": line}

                # 循环结束后若仍有未解析的残留，尽力再 parse 一次
                if pending:
                    try:
                        obj = json.loads(pending)
                        yield {"success": True, "data": obj}
                    except json.JSONDecodeError:
                        # 作为原文返回，便于排查（或改成丢弃）
                        yield {"success": True, "data": pending}

        except requests.exceptions.ConnectionError:
            yield {"success": False, "error": "连接错误：无法连接到服务器，请确保服务正在运行"}
        except requests.exceptions.Timeout:
            yield {"success": False, "error": "请求超时"}
        except requests.exceptions.RequestException as e:
            yield {"success": False, "error": f"请求异常: {e}"}
        except Exception as e:
            yield {"success": False, "error": f"未知错误: {e}"}

    def report(self, query: str, agent_setting: Optional[Dict[str, Any]] = None,
               request_id: Optional[str] = None, is_deepsearch: bool = True, file_type: str = "pptlist",
               language="中文"):
        if request_id is None:
            request_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "task": query, "stream": False, "language": language,
            "is_deepsearch": is_deepsearch, "file_type": file_type
        }
        if agent_setting:
            payload.update(agent_setting)
        res = [row for row in self.send_stream_request(self.report_url, payload)]

        if res:
            return json.loads(res[0].get("data")).get("data")
        return ""

    def ppt_trans(self,query :str ,agent_setting: Optional[Dict[str, Any]] = None,
               request_id: Optional[str] = None,bucket:str = "agents",):
        payload: Dict[str, Any] = {
            "request_id": request_id,"bucket":bucket,
            "task": query, "stream": False, "query": query
        }
        if agent_setting:
            payload.update(agent_setting)

        res = [row for row in self.send_stream_request(self.ppt_to_js_url, payload)]

        if res:
            return json.loads(res[0].get("data")).get("data")
        return ""

    def ppt_steam_req(self, query, request_id: Optional[str] = None, language="中文"):
        return {
            "request_id": request_id, "stream": True,
            "language": language,
            "task": query}

    def deep_search(self, query: str, agent_setting: Optional[Dict[str, Any]] = None,
                    request_id: Optional[str] = None,
                    max_loop: int = 2,
                    search_engines: Optional[List[str]] = None, ):
        if request_id is None:
            request_id = str(uuid.uuid4())
        if search_engines is None:
            search_engines = ["bocha"]

        payload: Dict[str, Any] = {
            "query": query,
            "maxLoop": max_loop,
            "search_engines": search_engines,
            "request_id": request_id,
            "stream": True,
            "streamMode": {"type": "segment", "interval": 1000},
        }
        if agent_setting:
            payload.update(agent_setting)  # 注意：不要写成 q = q.update(...)

        res = []
        for chunk in self.send_stream_request(self.deep_search_url, payload):
            if chunk["success"]:
                try:
                    if chunk['data']['searchResult'].get('doc_type', ):
                        tmp: dict = chunk['data']['searchResult']
                        tmp.pop('query')
                        res.append(
                            Doc(query=query, **tmp).to_markdown()
                        )
                except Exception as e:
                    self.logger.error(f"搜索文档出错:{str(e)}")

        return res
