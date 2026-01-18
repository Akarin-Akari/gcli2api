# -*- coding: utf-8 -*-
"""
Request Normalize - 请求白名单化与脱敏
=====================================

对 Bugment 入站请求进行：
- 白名单验证
- 敏感数据脱敏
- 请求标准化
- 限流控制
"""

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from .types import AugmentChatRequest, ChatRequestNodeType


# ============== 白名单配置 ==============

# 允许的端点路径
ALLOWED_ENDPOINTS: Set[str] = {
    "/gateway/chat-stream",
    "/chat-stream",
    "/gateway/v1/chat/completions",
    "/v1/chat/completions",
    # ACE 相关端点
    "/context",
    "/embeddings",
    "/search",
    "/codebase_search",
    "/find_symbol",
    "/workspace_context",
}

# 需要阻断的端点（遥测、统计等）
BLOCKED_ENDPOINTS: Set[str] = {
    "/agents/list-remote-tools",
    "/notifications/read",
    "/remote-agents/list-stream",
    "/record-session-events",
    "/client-metrics",
    "/report-error",
    "/report-feature-vector",
}

# 敏感字段名（需要脱敏或移除）
SENSITIVE_FIELDS: Set[str] = {
    "api_key",
    "apiKey",
    "access_token",
    "accessToken",
    "refresh_token",
    "refreshToken",
    "password",
    "secret",
    "credential",
    "authorization",
}


def is_endpoint_allowed(path: str) -> bool:
    """
    检查端点是否在白名单中。

    Args:
        path: 请求路径

    Returns:
        是否允许
    """
    path = path.lower().rstrip('/')

    # 检查阻断列表
    for blocked in BLOCKED_ENDPOINTS:
        if blocked.lower() in path:
            return False

    # 检查允许列表
    for allowed in ALLOWED_ENDPOINTS:
        if allowed.lower() in path:
            return True

    # 默认拒绝
    return False


def is_endpoint_blocked(path: str) -> bool:
    """
    检查端点是否应被阻断。

    Args:
        path: 请求路径

    Returns:
        是否阻断
    """
    path = path.lower()
    for blocked in BLOCKED_ENDPOINTS:
        if blocked.lower() in path:
            return True
    return False


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """
    清理请求头，移除敏感信息。

    Args:
        headers: 原始请求头

    Returns:
        清理后的请求头
    """
    sanitized = {}
    for key, value in headers.items():
        key_lower = key.lower()

        # 保留必要的头
        if key_lower in ("content-type", "accept", "user-agent", "x-request-id"):
            sanitized[key] = value
        # 脱敏敏感头
        elif any(s in key_lower for s in SENSITIVE_FIELDS):
            sanitized[key] = "[REDACTED]"
        # 其他头保留但可能需要处理
        else:
            sanitized[key] = value

    return sanitized


def sanitize_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理请求体，脱敏敏感字段。

    Args:
        body: 原始请求体

    Returns:
        清理后的请求体
    """
    if not isinstance(body, dict):
        return body

    sanitized = {}
    for key, value in body.items():
        # 检查是否是敏感字段
        if key.lower() in {s.lower() for s in SENSITIVE_FIELDS}:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = sanitize_body(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_body(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value

    return sanitized


def normalize_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    标准化请求格式。

    Args:
        request: 原始请求

    Returns:
        标准化后的请求
    """
    normalized = dict(request)

    # 确保有 request_id
    if "request_id" not in normalized:
        normalized["request_id"] = generate_request_id()

    # 标准化 nodes
    if "nodes" in normalized:
        normalized["nodes"] = normalize_nodes(normalized["nodes"])

    # 标准化 tool_definitions
    if "tool_definitions" in normalized:
        normalized["tool_definitions"] = [
            normalize_tool_definition(td)
            for td in normalized["tool_definitions"]
        ]

    # 清空敏感的 blobs 数据
    if "blobs" in normalized:
        blobs = normalized.get("blobs", {})
        if isinstance(blobs, dict):
            normalized["blobs"] = {
                "checkpoint_id": blobs.get("checkpoint_id"),
                "added_blobs": [],
                "deleted_blobs": []
            }

    return normalized


def normalize_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    标准化节点列表。

    Args:
        nodes: 原始节点列表

    Returns:
        标准化后的节点列表
    """
    if not nodes:
        return []

    normalized = []
    for node in nodes:
        if not isinstance(node, dict):
            continue

        norm_node = dict(node)

        # 确保有 type
        if "type" not in norm_node:
            norm_node["type"] = ChatRequestNodeType.TEXT

        # 移除可能的敏感内容
        if "text" in norm_node:
            norm_node["text"] = sanitize_text(norm_node["text"])

        normalized.append(norm_node)

    return normalized


def normalize_tool_definition(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    """
    标准化工具定义。

    Args:
        tool_def: 原始工具定义

    Returns:
        标准化后的工具定义
    """
    normalized = {
        "name": tool_def.get("name", "unknown"),
        "description": tool_def.get("description", ""),
        "input_schema": tool_def.get("input_schema", {
            "type": "object",
            "properties": {},
            "required": []
        })
    }
    return normalized


def sanitize_text(text: str) -> str:
    """
    清理文本内容中的敏感信息。

    Args:
        text: 原始文本

    Returns:
        清理后的文本
    """
    if not text:
        return text

    # 移除可能的 API key 模式
    text = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]', text)
    text = re.sub(r'api[_-]?key["\s:=]+["\']?[a-zA-Z0-9-_]{20,}["\']?', 'api_key=[REDACTED]', text, flags=re.IGNORECASE)

    return text


def generate_request_id() -> str:
    """
    生成请求ID。

    Returns:
        唯一的请求ID
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    hash_suffix = hashlib.md5(timestamp.encode()).hexdigest()[:8]
    return f"req_{timestamp}_{hash_suffix}"


def validate_request(request: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    验证请求是否有效。

    Args:
        request: 请求数据

    Returns:
        (是否有效, 错误信息)
    """
    # 检查必需字段
    if not request.get("nodes"):
        return False, "Missing required field: nodes"

    nodes = request.get("nodes", [])
    if not isinstance(nodes, list):
        return False, "Invalid nodes format: expected list"

    if len(nodes) == 0:
        return False, "Empty nodes list"

    # 验证节点格式
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            return False, f"Invalid node at index {i}: expected dict"

    return True, None


class RequestRateLimiter:
    """
    简单的请求限流器。
    """

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = {}

    def is_allowed(self, client_id: str) -> bool:
        """
        检查客户端是否允许请求。

        Args:
            client_id: 客户端标识

        Returns:
            是否允许
        """
        import time
        now = time.time()

        # 获取或创建客户端的请求记录
        if client_id not in self._requests:
            self._requests[client_id] = []

        # 清理过期记录
        cutoff = now - self.window_seconds
        self._requests[client_id] = [
            ts for ts in self._requests[client_id]
            if ts > cutoff
        ]

        # 检查是否超过限制
        if len(self._requests[client_id]) >= self.max_requests:
            return False

        # 记录本次请求
        self._requests[client_id].append(now)
        return True

    def get_remaining(self, client_id: str) -> int:
        """
        获取剩余可用请求数。

        Args:
            client_id: 客户端标识

        Returns:
            剩余请求数
        """
        import time
        now = time.time()
        cutoff = now - self.window_seconds

        if client_id not in self._requests:
            return self.max_requests

        valid_requests = [
            ts for ts in self._requests[client_id]
            if ts > cutoff
        ]
        return max(0, self.max_requests - len(valid_requests))
