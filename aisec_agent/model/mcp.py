import uuid
import json
import time
import threading
import requests
from typing import List, Dict, Optional

from aisec_agent.config import MCP_SERVICES


class MCPService:
    """同步版本的MCP服务客户端，支持并发安全"""

    def __init__(self, endpoints: List[dict] = MCP_SERVICES, timeout: int = 15, max_retries: int = 3):
        """
        初始化MCP服务客户端

        :param endpoints: MCP服务端点列表
        :param timeout: 请求超时时间(秒)
        :param max_retries: 最大重试次数
        """
        self.endpoints = []
        for endpoint in endpoints:
            self.endpoints.append(endpoint["url"])
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = None  # 持久化的Session
        self._sessions: Dict[str, Optional[str]] = {}  # 端点 -> 会话ID
        self._tool_mapping: Dict = {}  # 工具名 -> 端点信息
        self._lock = threading.Lock()  # 并发控制锁

    def __enter__(self):
        """支持同步上下文管理"""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb):
        """退出时关闭会话"""
        self.close()

    def initialize(self) -> None:
        """初始化服务连接和工具发现"""
        # 创建持久化会话
        self._session = requests.Session()

        # 使用锁保证并发安全
        with self._lock:
            # 初始化所有端点的会话
            for endpoint in self.endpoints:
                session_id = self._get_session_id(endpoint)
                self._sessions[endpoint] = session_id

            # 发现所有工具
            for endpoint in self.endpoints:
                tools = self._fetch_tools(endpoint, self._sessions[endpoint])
                if tools:
                    for tool in tools:
                        self._tool_mapping[tool["name"]] = {
                            "endpoint": endpoint,
                            "description": tool["description"],
                            "inputSchema": tool["inputSchema"]
                        }

    def close(self) -> None:
        """关闭所有连接"""
        if self._session:
            self._session.close()
            self._session = None

    def call_tool(self, tool_name: str, params: Dict = None) -> Dict:
        """
        调用MCP工具（并发安全版本）

        :param tool_name: 工具名称
        :param params: 调用参数
        :return: 工具响应结果
        :raises: ValueError 当工具不存在时
        """
        if not self._session:
            self.initialize()

        tool_info = self._tool_mapping.get(tool_name)
        if not tool_info:
            raise ValueError(f"Tool '{tool_name}' not found in any MCP service")

        endpoint = tool_info["endpoint"]
        session_id = self._sessions.get(endpoint)
        params.update({"name": tool_name})
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": params or {},
            "id": str(uuid.uuid4())
        }

        # 使用持久化会话进行调用
        with self._lock:  # 保证并发安全
            for attempt in range(self.max_retries):
                try:
                    response = self._session.post(
                        endpoint,
                        json=payload,
                        timeout=self.timeout,
                        headers={"Mcp-Session-Id": session_id}
                    )
                    response.raise_for_status()
                    try:
                        return response.json()
                    except json.JSONDecodeError as e:
                        if attempt == self.max_retries - 1:
                            raise ValueError(
                                f"Invalid JSON response: {response.text[:200]}"
                            ) from e
                except (requests.RequestException, requests.Timeout) as e:
                    if attempt == self.max_retries - 1:
                        raise
                    time.sleep(1 << attempt)  # 指数退避

    def list_tools(self) -> Dict:
        """获取所有可用工具名称和描述"""
        return {name: info["description"] for name, info in self._tool_mapping.items()}

    def get_tool_info(self, tool_name: str) -> Optional[Dict]:
        """获取指定工具的详细信息"""
        return self._tool_mapping.get(tool_name)

    def _get_session_id(self, endpoint: str) -> Optional[str]:
        """获取MCP会话ID（使用持久化会话）"""
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {},
            "id": str(uuid.uuid4())
        }
        try:
            response = self._session.post(
                endpoint,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            sid = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
            data = response.json()
            return sid or data.get("result", {}).get("sessionId")
        except (requests.RequestException, json.JSONDecodeError):
            return None

    def _fetch_tools(self, endpoint: str, session_id: str = None) -> List[Dict]:
        """获取端点支持的工具列表（使用持久化会话）"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": str(uuid.uuid4())
        }
        headers = {}
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        try:
            response = self._session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get("result", {}).get("tools", [])
        except (requests.RequestException, json.JSONDecodeError):
            return []