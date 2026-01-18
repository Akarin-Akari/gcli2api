# -*- coding: utf-8 -*-
"""
NDJSON 流式输出/解析工具
========================

NDJSON (Newline Delimited JSON) 是 Augment chat-stream 的核心协议格式。
每行一个独立的 JSON 对象，便于流式解析。

格式示例（2026-01-15 修正 type 值）：
{"type":0,"data":{"text":"Hello"}}
{"type":0,"data":{"text":" World"}}
{"type":5,"data":{"tool_use":{"id":"toolu_123","name":"read_file","input":{}}}}
{"type":2,"stop_reason":"tool_use"}

Node Type 值：
- type=0: RAW_RESPONSE (文本)
- type=2: MAIN_TEXT_FINISHED (文本完成)
- type=5: TOOL_USE (工具调用请求) ⭐
"""

import json
from typing import Any, AsyncGenerator, Dict, Generator, Optional, Union
from .types import AugmentNode, ChatResultNodeType


def ndjson_encode_line(data: Union[Dict[str, Any], AugmentNode]) -> str:
    """
    将数据编码为 NDJSON 行。

    Args:
        data: 要编码的数据（字典或 AugmentNode）

    Returns:
        NDJSON 格式的字符串行（包含换行符）
    """
    if isinstance(data, AugmentNode):
        json_str = data.model_dump_json(exclude_none=True)
    else:
        json_str = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return json_str + '\n'


def ndjson_decode_line(line: str) -> Optional[Dict[str, Any]]:
    """
    解析 NDJSON 行。

    Args:
        line: NDJSON 格式的字符串行

    Returns:
        解析后的字典，解析失败返回 None
    """
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def create_text_node(text: str, is_delta: bool = True) -> str:
    """
    创建文本节点的 NDJSON 行。

    Args:
        text: 文本内容
        is_delta: 是否为增量文本

    Returns:
        NDJSON 格式的字符串行
    """
    node = {
        "type": ChatResultNodeType.RAW_RESPONSE,
        "data": {"text": text}
    }
    if is_delta:
        node["data"]["delta"] = True
    return ndjson_encode_line(node)


def create_tool_use_node(
    tool_id: str,
    tool_name: str,
    tool_input: Dict[str, Any]
) -> str:
    """
    创建工具调用节点的 NDJSON 行。

    Args:
        tool_id: 工具调用唯一ID
        tool_name: 工具名称
        tool_input: 工具输入参数

    Returns:
        NDJSON 格式的字符串行
    """
    node = {
        "type": ChatResultNodeType.TOOL_USE,
        "data": {
            "tool_use": {
                "id": tool_id,
                "name": tool_name,
                "input": tool_input
            }
        }
    }
    return ndjson_encode_line(node)


def create_stop_node(
    stop_reason: str,
    usage: Optional[Dict[str, int]] = None
) -> str:
    """
    创建停止节点的 NDJSON 行。

    Args:
        stop_reason: 停止原因 (end_turn, tool_use, max_tokens等)
        usage: token用量统计

    Returns:
        NDJSON 格式的字符串行
    """
    node = {
        "type": ChatResultNodeType.MAIN_TEXT_FINISHED,
        "stop_reason": stop_reason
    }
    if usage:
        node["usage"] = usage
    return ndjson_encode_line(node)


def create_ndjson_stream(
    nodes: Generator[Dict[str, Any], None, None]
) -> Generator[str, None, None]:
    """
    将节点生成器转换为 NDJSON 流生成器。

    Args:
        nodes: 节点数据生成器

    Yields:
        NDJSON 格式的字符串行
    """
    for node in nodes:
        yield ndjson_encode_line(node)


async def create_async_ndjson_stream(
    nodes: AsyncGenerator[Dict[str, Any], None]
) -> AsyncGenerator[str, None]:
    """
    将异步节点生成器转换为异步 NDJSON 流生成器。

    Args:
        nodes: 异步节点数据生成器

    Yields:
        NDJSON 格式的字符串行
    """
    async for node in nodes:
        yield ndjson_encode_line(node)


def parse_ndjson_stream(stream: str) -> Generator[Dict[str, Any], None, None]:
    """
    解析 NDJSON 流字符串。

    Args:
        stream: NDJSON 格式的多行字符串

    Yields:
        解析后的字典对象
    """
    for line in stream.split('\n'):
        parsed = ndjson_decode_line(line)
        if parsed is not None:
            yield parsed


class NDJSONStreamBuilder:
    """
    NDJSON 流构建器，用于逐步构建响应流。

    使用示例：
        builder = NDJSONStreamBuilder()
        yield builder.text("Hello ")
        yield builder.text("World!")
        yield builder.tool_use("toolu_1", "read_file", {"path": "/tmp/a.txt"})
        yield builder.stop("tool_use")
    """

    def __init__(self):
        self._tool_call_count = 0

    def text(self, content: str, is_delta: bool = True) -> str:
        """生成文本节点"""
        return create_text_node(content, is_delta)

    def tool_use(
        self,
        tool_id: str,
        tool_name: str,
        tool_input: Dict[str, Any]
    ) -> str:
        """生成工具调用节点"""
        self._tool_call_count += 1
        return create_tool_use_node(tool_id, tool_name, tool_input)

    def stop(
        self,
        reason: str = "end_turn",
        usage: Optional[Dict[str, int]] = None
    ) -> str:
        """生成停止节点"""
        return create_stop_node(reason, usage)

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
        return self._tool_call_count > 0
