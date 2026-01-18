# -*- coding: utf-8 -*-
"""
Tools Bridge - 工具定义格式转换
==============================

将 Augment 工具定义格式与上游 LLM 提供商的工具格式互相转换。

支持的格式：
- Augment: tool_definitions (name, description, input_schema)
- OpenAI: tools (function with parameters)
- Anthropic: tools (name, description, input_schema)
"""

import json
from typing import Any, Dict, List, Optional

from .types import AugmentToolDefinition, ToolInputSchema


def convert_augment_tool_to_openai(tool: AugmentToolDefinition) -> Dict[str, Any]:
    """
    将 Augment 工具定义转换为 OpenAI 格式。

    Augment 格式：
    {
        "name": "read_file",
        "description": "Read file contents",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    }

    OpenAI 格式：
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    }

    Args:
        tool: Augment 格式的工具定义

    Returns:
        OpenAI 格式的工具定义
    """
    # Bugment sidecar 的 `codebase-retrieval` 在上游调用时如果拿不到参数 schema，
    # 模型会用 `{}` 触发，进而导致后续检索请求 400。
    # 这里做一个最小兜底：当 schema 为空时，要求至少提供 query。
    parameters = tool.input_schema.model_dump()
    try:
        if tool.name == "codebase-retrieval":
            props = parameters.get("properties") if isinstance(parameters, dict) else None
            if not isinstance(props, dict) or len(props) == 0:
                parameters = {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词/问题描述（必填）"}
                    },
                    "required": ["query"],
                }
    except Exception:
        # 保持原样，不影响其它工具
        pass

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters
        }
    }


def convert_openai_tool_to_augment(tool: Dict[str, Any]) -> AugmentToolDefinition:
    """
    将 OpenAI 工具定义转换为 Augment 格式。

    Args:
        tool: OpenAI 格式的工具定义

    Returns:
        Augment 格式的工具定义
    """
    function = tool.get("function", {})
    parameters = function.get("parameters", {"type": "object", "properties": {}})

    return AugmentToolDefinition(
        name=function.get("name", "unknown"),
        description=function.get("description", ""),
        input_schema=ToolInputSchema(
            type=parameters.get("type", "object"),
            properties=parameters.get("properties", {}),
            required=parameters.get("required", [])
        )
    )


def convert_augment_tool_to_anthropic(tool: AugmentToolDefinition) -> Dict[str, Any]:
    """
    将 Augment 工具定义转换为 Anthropic 格式。

    Anthropic 格式与 Augment 几乎相同：
    {
        "name": "read_file",
        "description": "Read file contents",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    }

    Args:
        tool: Augment 格式的工具定义

    Returns:
        Anthropic 格式的工具定义
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema.model_dump()
    }


def convert_anthropic_tool_to_augment(tool: Dict[str, Any]) -> AugmentToolDefinition:
    """
    将 Anthropic 工具定义转换为 Augment 格式。

    Args:
        tool: Anthropic 格式的工具定义

    Returns:
        Augment 格式的工具定义
    """
    input_schema = tool.get("input_schema", {"type": "object", "properties": {}})

    return AugmentToolDefinition(
        name=tool.get("name", "unknown"),
        description=tool.get("description", ""),
        input_schema=ToolInputSchema(
            type=input_schema.get("type", "object"),
            properties=input_schema.get("properties", {}),
            required=input_schema.get("required", [])
        )
    )


def convert_tools_to_openai(tools: List[AugmentToolDefinition]) -> List[Dict[str, Any]]:
    """
    批量将 Augment 工具定义转换为 OpenAI 格式。

    Args:
        tools: Augment 格式的工具定义列表

    Returns:
        OpenAI 格式的工具定义列表
    """
    return [convert_augment_tool_to_openai(tool) for tool in tools]


def convert_tools_from_openai(tools: List[Dict[str, Any]]) -> List[AugmentToolDefinition]:
    """
    批量将 OpenAI 工具定义转换为 Augment 格式。

    Args:
        tools: OpenAI 格式的工具定义列表

    Returns:
        Augment 格式的工具定义列表
    """
    return [convert_openai_tool_to_augment(tool) for tool in tools]


def convert_tools_to_anthropic(tools: List[AugmentToolDefinition]) -> List[Dict[str, Any]]:
    """
    批量将 Augment 工具定义转换为 Anthropic 格式。

    Args:
        tools: Augment 格式的工具定义列表

    Returns:
        Anthropic 格式的工具定义列表
    """
    return [convert_augment_tool_to_anthropic(tool) for tool in tools]


def convert_tools_from_anthropic(tools: List[Dict[str, Any]]) -> List[AugmentToolDefinition]:
    """
    批量将 Anthropic 工具定义转换为 Augment 格式。

    Args:
        tools: Anthropic 格式的工具定义列表

    Returns:
        Augment 格式的工具定义列表
    """
    return [convert_anthropic_tool_to_augment(tool) for tool in tools]


def parse_tool_definitions_from_request(
    raw_tools: Optional[List[Dict[str, Any]]]
) -> List[AugmentToolDefinition]:
    """
    从请求中解析工具定义。

    自动检测格式（OpenAI 或 Augment/Anthropic）并转换。

    Args:
        raw_tools: 原始工具定义列表

    Returns:
        Augment 格式的工具定义列表
    """
    if not raw_tools:
        return []

    def _parse_schema(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    result = []
    for tool in raw_tools:
        try:
            # Augment tool definitions often come as:
            # { definition: { name, description, input_schema_json }, identifier: {...}, ... }
            if isinstance(tool.get("definition"), dict):
                d = tool.get("definition") or {}
                schema_dict = _parse_schema(d.get("input_schema")) or _parse_schema(d.get("input_schema_json"))
                result.append(AugmentToolDefinition(
                    name=d.get("name", "unknown"),
                    description=d.get("description", ""),
                    input_schema=ToolInputSchema(
                        type=schema_dict.get("type", "object"),
                        properties=schema_dict.get("properties", {}),
                        required=schema_dict.get("required", []),
                    ),
                ))
                continue

            # Some clients send `{ name, description, input_schema_json: string }`
            if "name" in tool and isinstance(tool.get("input_schema_json"), str):
                schema_dict = _parse_schema(tool.get("input_schema_json"))
                result.append(AugmentToolDefinition(
                    name=tool.get("name", "unknown"),
                    description=tool.get("description", ""),
                    input_schema=ToolInputSchema(
                        type=schema_dict.get("type", "object"),
                        properties=schema_dict.get("properties", {}),
                        required=schema_dict.get("required", []),
                    ),
                ))
                continue
            # 检测是否是 OpenAI 格式（有 "type": "function"）
            if tool.get("type") == "function" and "function" in tool:
                result.append(convert_openai_tool_to_augment(tool))
            # 兼容另一类常见定义：{ name, description, parameters: JSONSchema }
            # 一些客户端（含部分 sidecar 工具定义）使用 `parameters` 而不是 `input_schema`。
            elif "name" in tool and isinstance(tool.get("parameters"), dict):
                parameters = tool.get("parameters") or {"type": "object", "properties": {}}
                result.append(AugmentToolDefinition(
                    name=tool.get("name", "unknown"),
                    description=tool.get("description", ""),
                    input_schema=ToolInputSchema(
                        type=parameters.get("type", "object"),
                        properties=parameters.get("properties", {}),
                        required=parameters.get("required", []),
                    ),
                ))
            # 否则按 Augment/Anthropic 格式处理
            elif "name" in tool and "input_schema" in tool:
                result.append(convert_anthropic_tool_to_augment(tool))
            # 简化格式（只有 name 和 description）
            elif "name" in tool:
                result.append(AugmentToolDefinition(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    input_schema=ToolInputSchema(
                        type="object",
                        properties=tool.get("properties", {}),
                        required=tool.get("required", [])
                    )
                ))
        except Exception:
            # 跳过无法解析的工具定义
            continue

    return result


def normalize_tool_choice(
    tool_choice: Any,
    tools: List[AugmentToolDefinition]
) -> Dict[str, Any]:
    """
    标准化工具选择参数。

    Args:
        tool_choice: 原始的 tool_choice 参数
        tools: 可用工具列表

    Returns:
        标准化后的 tool_choice（OpenAI 格式）
    """
    if tool_choice is None or not tools:
        return {"type": "auto"} if tools else {}

    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        elif tool_choice == "none":
            return {"type": "none"}
        elif tool_choice == "required" or tool_choice == "any":
            return {"type": "required"}
        else:
            # 假设是具体的工具名
            return {"type": "function", "function": {"name": tool_choice}}

    if isinstance(tool_choice, dict):
        return tool_choice

    # 数字类型（ToolChoiceType 枚举）
    if isinstance(tool_choice, int):
        if tool_choice == 0:  # AUTO
            return {"type": "auto"}
        elif tool_choice == 1:  # ANY
            return {"type": "required"}
        elif tool_choice == 2:  # TOOL
            # 需要指定具体工具，默认使用第一个
            if tools:
                return {"type": "function", "function": {"name": tools[0].name}}
            return {"type": "required"}

    return {"type": "auto"}
