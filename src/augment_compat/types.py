# -*- coding: utf-8 -*-
"""
Augment Protocol Types
======================

Pydantic 类型定义，对应 Augment 的 NDJSON 协议数据结构。

基于 vanilla extension.js 中的枚举分析：
- ChatResultNodeType: 响应节点类型
- ChatRequestNodeType: 请求节点类型
- ToolChoiceType: 工具选择类型
"""

from enum import IntEnum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class ChatResultNodeType(IntEnum):
    """
    Augment chat-stream 响应节点类型枚举。

    ⚠️ 2026-01-15 修正：基于 installed extension.js 实际代码分析：
    - type=0: RAW_RESPONSE (包含 text_content)
    - type=1: TOOL_RESULT (包含 tool_result_node)
    - type=2: MAIN_TEXT_FINISHED
    - type=3: IMAGE_ID (包含 image_id_node)
    - type=5: TOOL_USE (包含 tool_use) ⭐ 关键：触发 tool loop
    - type=6: CHECKPOINT

    证据链：
    - nodes.filter(e => 5===e.type || 0===e.type)
    - response_nodes?.find(e => 5===e.type) && n?.tool_use
    - 1===a.type && a.tool_result_node
    """
    RAW_RESPONSE = 0           # 文本响应
    TOOL_RESULT = 1            # 工具执行结果 (请求节点类型)
    MAIN_TEXT_FINISHED = 2     # 文本完成标记
    IMAGE_ID = 3               # 图片ID节点
    SAFETY = 4                 # 安全检查
    TOOL_USE = 5               # ⭐ 工具调用请求 (触发 tool loop)
    CHECKPOINT = 6             # 检查点


class ChatRequestNodeType(IntEnum):
    """
    Augment chat-stream 请求节点类型枚举。

    基于 extension.js 中的 ChatRequestNodeType 定义：
    - TEXT = 0
    - TOOL_RESULT = 1
    - IMAGE = 2
    - IMAGE_URL = 3
    - GIF = 3
    - WEBP = 4
    """
    TEXT = 0
    TOOL_RESULT = 1
    IMAGE = 2
    IMAGE_URL = 3
    GIF = 3
    WEBP = 4


class ToolChoiceType(IntEnum):
    """
    工具选择类型枚举。

    - AUTO = 0: 自动决定是否使用工具
    - ANY = 1: 使用任意可用工具
    - TOOL = 2: 使用指定工具
    """
    AUTO = 0
    ANY = 1
    TOOL = 2


class StopReason(str):
    """
    停止原因字符串常量。
    """
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"


# ============== NDJSON Node 类型 ==============

class ToolUseContent(BaseModel):
    """工具调用内容"""
    id: str = Field(..., description="工具调用唯一ID")
    name: str = Field(..., description="工具名称")
    input: Dict[str, Any] = Field(default_factory=dict, description="工具输入参数")


class TextContent(BaseModel):
    """文本内容"""
    text: str = Field(..., description="文本内容")


class ToolResultContent(BaseModel):
    """工具执行结果内容"""
    tool_use_id: str = Field(..., description="对应的工具调用ID")
    content: str = Field(..., description="工具执行结果")
    is_error: bool = Field(default=False, description="是否为错误结果")


class AugmentNode(BaseModel):
    """
    Augment NDJSON 协议节点。

    对应 chat-stream 响应中的每一行 JSON。
    """
    type: ChatResultNodeType = Field(..., description="节点类型")
    data: Optional[Dict[str, Any]] = Field(default=None, description="节点数据")

    # 可选字段
    text: Optional[str] = Field(default=None, description="文本内容")
    tool_use: Optional[ToolUseContent] = Field(default=None, description="工具调用内容")
    stop_reason: Optional[str] = Field(default=None, description="停止原因")
    usage: Optional[Dict[str, int]] = Field(default=None, description="token用量")

    class Config:
        use_enum_values = True


# ============== Tool Definition 类型 ==============

class ToolParameter(BaseModel):
    """工具参数定义"""
    type: str = Field(..., description="参数类型 (string, number, boolean, object, array)")
    description: Optional[str] = Field(default=None, description="参数描述")
    enum: Optional[List[str]] = Field(default=None, description="枚举值")
    required: bool = Field(default=False, description="是否必需")
    default: Optional[Any] = Field(default=None, description="默认值")


class ToolInputSchema(BaseModel):
    """工具输入 schema"""
    type: str = Field(default="object")
    properties: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class AugmentToolDefinition(BaseModel):
    """
    Augment 工具定义格式。

    对应 extension.js 中的 tool_definitions 结构。
    """
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    input_schema: ToolInputSchema = Field(..., description="输入参数 schema")

    # 可选元数据
    category: Optional[str] = Field(default=None, description="工具类别")
    requires_confirmation: bool = Field(default=False, description="是否需要用户确认")


# ============== 请求/响应类型 ==============

class RequestNode(BaseModel):
    """请求节点"""
    type: ChatRequestNodeType
    text: Optional[str] = None
    tool_result: Optional[ToolResultContent] = None
    image_data: Optional[str] = None

    class Config:
        use_enum_values = True


class AugmentChatRequest(BaseModel):
    """
    Augment chat-stream 请求体。
    """
    request_id: str = Field(..., description="请求唯一ID")
    nodes: List[RequestNode] = Field(..., description="请求节点列表")

    # 工具相关
    tool_definitions: List[AugmentToolDefinition] = Field(
        default_factory=list,
        description="可用工具定义"
    )
    tool_choice: Optional[ToolChoiceType] = Field(
        default=ToolChoiceType.AUTO,
        description="工具选择模式"
    )

    # 上下文
    chat_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="历史对话"
    )
    blobs: Optional[Dict[str, Any]] = Field(default=None, description="附加数据")

    # 配置
    model: Optional[str] = Field(default=None, description="模型名称")
    max_tokens: Optional[int] = Field(default=None, description="最大输出token")
    temperature: Optional[float] = Field(default=None, description="温度参数")

    class Config:
        use_enum_values = True


class AugmentChatResponse(BaseModel):
    """
    Augment chat-stream 响应汇总。

    注意：实际响应是 NDJSON 流，此类用于内部状态追踪。
    """
    request_id: str
    nodes: List[AugmentNode] = Field(default_factory=list)
    stop_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None

    # 工具调用追踪
    pending_tool_calls: List[ToolUseContent] = Field(default_factory=list)
    completed_tool_results: List[ToolResultContent] = Field(default_factory=list)
