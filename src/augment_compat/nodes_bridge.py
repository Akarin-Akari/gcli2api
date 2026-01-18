# -*- coding: utf-8 -*-
"""
Nodes Bridge - 上游响应转换为 Augment Nodes
==========================================

将上游 LLM 提供商的响应格式转换为 Augment NDJSON 节点格式。

支持的上游格式：
- OpenAI (tool_calls, content)
- Anthropic (tool_use, text)

核心转换（2026-01-15 修正）：
- 上游 tool_call → Augment TOOL_USE node (type=5)
- 上游 text/content → Augment RAW_RESPONSE node (type=0)
- 上游 finish_reason → Augment stop_reason

Node Type 枚举值：
- type=0: RAW_RESPONSE (文本响应)
- type=1: TOOL_RESULT (工具执行结果)
- type=5: TOOL_USE (工具调用请求) ⭐ 关键
"""

import json
import uuid
from typing import Any, Dict, Generator, List, Optional, Tuple

from .types import (
    ChatResultNodeType,
    AugmentNode,
    ToolUseContent,
    StopReason,
)
from .ndjson import (
    create_text_node,
    create_tool_use_node,
    create_stop_node,
    ndjson_encode_line,
)


def generate_tool_id() -> str:
    """生成工具调用唯一ID"""
    return f"toolu_{uuid.uuid4().hex[:24]}"


def convert_openai_tool_call_to_node(tool_call: Dict[str, Any]) -> str:
    """
    将 OpenAI 格式的 tool_call 转换为 Augment TOOL_USE node。

    OpenAI 格式：
    {
        "id": "call_abc123",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": "{\"path\": \"/tmp/a.txt\"}"
        }
    }

    Args:
        tool_call: OpenAI 格式的工具调用

    Returns:
        NDJSON 格式的 TOOL_USE 节点
    """
    tool_id = tool_call.get("id", generate_tool_id())
    function = tool_call.get("function", {})
    tool_name = function.get("name", "unknown")

    # 解析 arguments JSON 字符串
    arguments_str = function.get("arguments", "{}")
    try:
        tool_input = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
    except json.JSONDecodeError:
        tool_input = {"raw_arguments": arguments_str}

    return create_tool_use_node(tool_id, tool_name, tool_input)


def convert_anthropic_tool_use_to_node(tool_use: Dict[str, Any]) -> str:
    """
    将 Anthropic 格式的 tool_use 转换为 Augment TOOL_USE node。

    Anthropic 格式：
    {
        "type": "tool_use",
        "id": "toolu_abc123",
        "name": "read_file",
        "input": {"path": "/tmp/a.txt"}
    }

    Args:
        tool_use: Anthropic 格式的工具调用

    Returns:
        NDJSON 格式的 TOOL_USE 节点
    """
    tool_id = tool_use.get("id", generate_tool_id())
    tool_name = tool_use.get("name", "unknown")
    tool_input = tool_use.get("input", {})

    return create_tool_use_node(tool_id, tool_name, tool_input)


def convert_text_to_node(text: str, is_delta: bool = True) -> str:
    """
    将文本转换为 Augment RAW_RESPONSE node。

    Args:
        text: 文本内容
        is_delta: 是否为增量文本

    Returns:
        NDJSON 格式的 RAW_RESPONSE 节点
    """
    return create_text_node(text, is_delta)


def convert_tool_call_to_node(
    tool_call: Dict[str, Any],
    source_format: str = "openai"
) -> str:
    """
    通用工具调用转换函数。

    Args:
        tool_call: 工具调用数据
        source_format: 源格式 ("openai" 或 "anthropic")

    Returns:
        NDJSON 格式的 TOOL_USE 节点
    """
    if source_format == "anthropic":
        return convert_anthropic_tool_use_to_node(tool_call)
    else:
        return convert_openai_tool_call_to_node(tool_call)


def convert_finish_reason_to_stop_reason(
    finish_reason: Optional[str],
    has_tool_calls: bool = False
) -> str:
    """
    将上游的 finish_reason 转换为 Augment stop_reason。

    映射规则：
    - "stop" / "end_turn" → "end_turn"
    - "tool_calls" / "tool_use" → "tool_use"
    - "length" / "max_tokens" → "max_tokens"
    - None (有tool_calls) → "tool_use"

    Args:
        finish_reason: 上游的停止原因
        has_tool_calls: 是否包含工具调用

    Returns:
        Augment 格式的 stop_reason
    """
    if finish_reason is None:
        return StopReason.TOOL_USE if has_tool_calls else StopReason.END_TURN

    reason_lower = finish_reason.lower() if finish_reason else ""

    if reason_lower in ("tool_calls", "tool_use"):
        return StopReason.TOOL_USE
    elif reason_lower in ("length", "max_tokens"):
        return StopReason.MAX_TOKENS
    elif reason_lower in ("stop_sequence",):
        return StopReason.STOP_SEQUENCE
    else:
        # 默认为 end_turn
        return StopReason.END_TURN


def create_stop_reason_node(
    finish_reason: Optional[str],
    has_tool_calls: bool = False,
    usage: Optional[Dict[str, int]] = None
) -> str:
    """
    创建停止原因节点。

    Args:
        finish_reason: 上游的停止原因
        has_tool_calls: 是否包含工具调用
        usage: token用量统计

    Returns:
        NDJSON 格式的停止节点
    """
    stop_reason = convert_finish_reason_to_stop_reason(finish_reason, has_tool_calls)
    return create_stop_node(stop_reason, usage)


def convert_openai_stream_chunk(
    chunk: Dict[str, Any]
) -> Generator[str, None, None]:
    """
    将 OpenAI 流式响应 chunk 转换为 Augment NDJSON 节点。

    Args:
        chunk: OpenAI SSE chunk

    Yields:
        NDJSON 格式的节点字符串
    """
    choices = chunk.get("choices", [])
    if not choices:
        return

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # 处理文本内容
    content = delta.get("content")
    if content:
        yield convert_text_to_node(content, is_delta=True)

    # 处理工具调用
    tool_calls = delta.get("tool_calls", [])
    for tool_call in tool_calls:
        # OpenAI 流式中 tool_call 可能是增量的
        if tool_call.get("function", {}).get("name"):
            yield convert_openai_tool_call_to_node(tool_call)

    # 处理结束
    if finish_reason:
        has_tool_calls = bool(tool_calls or delta.get("tool_calls"))
        usage = chunk.get("usage")
        yield create_stop_reason_node(finish_reason, has_tool_calls, usage)


def convert_anthropic_stream_event(
    event: Dict[str, Any]
) -> Generator[str, None, None]:
    """
    将 Anthropic 流式事件转换为 Augment NDJSON 节点。

    Args:
        event: Anthropic SSE event

    Yields:
        NDJSON 格式的节点字符串
    """
    event_type = event.get("type")

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                yield convert_text_to_node(text, is_delta=True)

        elif delta_type == "input_json_delta":
            # 工具输入的增量，通常需要累积
            pass

    elif event_type == "content_block_start":
        content_block = event.get("content_block", {})
        block_type = content_block.get("type")

        if block_type == "tool_use":
            yield convert_anthropic_tool_use_to_node(content_block)

    elif event_type == "message_delta":
        delta = event.get("delta", {})
        stop_reason = delta.get("stop_reason")
        usage = event.get("usage")

        if stop_reason:
            yield create_stop_reason_node(stop_reason, usage=usage)

    elif event_type == "message_stop":
        # 消息结束，如果之前没有发送 stop_reason 则发送
        pass


class StreamNodeConverter:
    """
    流式节点转换器，用于有状态的流转换。

    支持累积式的工具调用参数解析（OpenAI 流式特性）。
    """

    def __init__(self, source_format: str = "openai"):
        self.source_format = source_format
        self._tool_calls_buffer: Dict[int, Dict[str, Any]] = {}
        self._has_tool_calls = False

    def process_chunk(self, chunk: Dict[str, Any]) -> Generator[str, None, None]:
        """
        处理单个流式 chunk。

        Args:
            chunk: 流式响应 chunk

        Yields:
            NDJSON 格式的节点字符串
        """
        if self.source_format == "openai":
            yield from self._process_openai_chunk(chunk)
        elif self.source_format == "anthropic":
            yield from convert_anthropic_stream_event(chunk)

    def _process_openai_chunk(self, chunk: Dict[str, Any]) -> Generator[str, None, None]:
        """处理 OpenAI 格式的 chunk"""
        choices = chunk.get("choices", [])
        if not choices:
            return

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # 处理文本内容
        content = delta.get("content")
        if content:
            yield convert_text_to_node(content, is_delta=True)

        # 处理工具调用（需要累积）
        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            index = tc.get("index", 0)

            if index not in self._tool_calls_buffer:
                self._tool_calls_buffer[index] = {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {"name": "", "arguments": ""}
                }

            buffered = self._tool_calls_buffer[index]

            # 累积 ID
            if tc.get("id"):
                buffered["id"] = tc["id"]

            # 累积函数信息
            func = tc.get("function", {})
            if func.get("name"):
                buffered["function"]["name"] = func["name"]
            if func.get("arguments"):
                buffered["function"]["arguments"] += func["arguments"]

        # 处理结束
        if finish_reason:
            # 先输出所有累积的工具调用
            for tool_call in self._tool_calls_buffer.values():
                if tool_call["function"]["name"]:
                    self._has_tool_calls = True
                    yield convert_openai_tool_call_to_node(tool_call)

            # 输出停止节点
            usage = chunk.get("usage")
            yield create_stop_reason_node(finish_reason, self._has_tool_calls, usage)

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
        return self._has_tool_calls
