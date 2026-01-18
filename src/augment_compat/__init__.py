# -*- coding: utf-8 -*-
"""
Augment Compatibility Layer for Bugment
========================================

此模块实现 Augment VSCode 扩展与内网网关之间的协议兼容层。

核心功能：
- NDJSON 流式协议输出
- Tool Loop 桥接 (tool_call → TOOL_USE node)
- Tool Definitions 转换
- TOOL_RESULT 回注与续写

模块结构：
- types.py: Pydantic 类型定义 (nodes, stop_reason, tool_defs)
- ndjson.py: NDJSON 流式输出/解析工具
- request_normalize.py: 入站请求白名单化/脱敏
- tools_bridge.py: tool_definitions ↔ 上游 tools schema 转换
- nodes_bridge.py: 上游 tool_call/text → Augment nodes/stop_reason
- routes.py: /gateway/chat-stream 路由对接层

作者: Claude Sonnet 4 + Codex
日期: 2026-01-15
"""

from .types import (
    ChatResultNodeType,
    ChatRequestNodeType,
    ToolChoiceType,
    AugmentNode,
    AugmentToolDefinition,
    AugmentChatRequest,
    AugmentChatResponse,
)
from .ndjson import (
    ndjson_encode_line,
    ndjson_decode_line,
    create_ndjson_stream,
)
from .nodes_bridge import (
    convert_tool_call_to_node,
    convert_text_to_node,
    create_stop_reason_node,
)
from .tools_bridge import (
    convert_openai_tool_to_augment,
    convert_augment_tool_to_openai,
)

__version__ = "0.1.0"
__all__ = [
    # Types
    "ChatResultNodeType",
    "ChatRequestNodeType",
    "ToolChoiceType",
    "AugmentNode",
    "AugmentToolDefinition",
    "AugmentChatRequest",
    "AugmentChatResponse",
    # NDJSON
    "ndjson_encode_line",
    "ndjson_decode_line",
    "create_ndjson_stream",
    # Nodes Bridge
    "convert_tool_call_to_node",
    "convert_text_to_node",
    "create_stop_reason_node",
    # Tools Bridge
    "convert_openai_tool_to_augment",
    "convert_augment_tool_to_openai",
]
