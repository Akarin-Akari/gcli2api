"""
Unified Gateway Router - 统一API网关路由
将多个后端服务整合到单一端点，支持优先级路由和故障转移

优先级顺序：
1. Antigravity API (gcli2api 本地) - 优先
2. Copilot API (localhost:8141) - 备用
3. Kiro Gateway (localhost:9046) - 可配置路由
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import StreamingResponse as StarletteStreamingResponse

from log import log
from src.httpx_client import http_client
from src.utils import authenticate_bearer, authenticate_bearer_allow_local_dummy

# Augment Compatibility Layer - Bugment Tool Loop & Nodes Bridge
try:
    from src.augment_compat import (
        ChatResultNodeType,
        ChatRequestNodeType,
        AugmentNode,
        AugmentToolDefinition,
        ndjson_encode_line,
        create_ndjson_stream,
        convert_tool_call_to_node,
        convert_text_to_node,
        create_stop_reason_node,
        convert_openai_tool_to_augment,
        convert_augment_tool_to_openai,
    )
    from src.augment_compat.nodes_bridge import StreamNodeConverter
    from src.augment_compat.ndjson import NDJSONStreamBuilder
    from src.augment_compat.tools_bridge import (
        convert_tools_to_openai,
        parse_tool_definitions_from_request,
    )
    AUGMENT_COMPAT_AVAILABLE = True
except ImportError as e:
    log.warning(f"augment_compat module not available: {e}", tag="GATEWAY")
    AUGMENT_COMPAT_AVAILABLE = False


# ==================== 重试配置 ====================

BUGMENT_TOOL_RESULT_SHORTCIRCUIT_ENABLED = os.getenv("BUGMENT_TOOL_RESULT_SHORTCIRCUIT", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

RETRY_CONFIG = {
    "max_retries": 3,           # 最大重试次数
    "base_delay": 1.0,          # 基础延迟（秒）
    "max_delay": 10.0,          # 最大延迟（秒）
    "exponential_base": 2,      # 指数退避基数
    # 注意：移除 503 重试，避免把「额度/降级语义」的 503 放大成重试风暴
    "retry_on_status": [500, 502, 504],  # 需要重试的状态码
}


# ==================== Prompt Model Routing ====================

# Supported model names for routing
ROUTABLE_MODELS = {
    # GPT models -> Copilot
    "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5.2",
    "o1", "o1-mini", "o1-pro", "o3", "o3-mini",
    # Claude models -> Antigravity
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3.5-opus", "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
    "claude-sonnet-4.5", "claude-opus-4.5", "claude-haiku-4.5",
    # Gemini models -> Antigravity
    "gemini-pro", "gemini-ultra",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-3-pro", "gemini-3-pro-high", "gemini-3-pro-low", "gemini-3-flash",  # 修复：使用实际存在的模型名
}

# Regex patterns for model markers
# Pattern 1: [use:model-name] - High priority
USE_PATTERN = re.compile(r'\[use:([a-zA-Z0-9._-]+)\]', re.IGNORECASE)
# Pattern 2: @model-name - Low priority (at start of message or after whitespace)
AT_PATTERN = re.compile(r'(?:^|\s)@([a-zA-Z0-9._-]+)(?=\s|$)', re.IGNORECASE)


def extract_model_from_prompt(messages: list) -> tuple:
    """
    Extract model name from prompt markers in messages.

    Priority:
    1. [use:model-name] - Highest priority
    2. @model-name - Lower priority

    Args:
        messages: List of message dicts with 'role' and 'content'

    Returns:
        Tuple of (extracted_model_name or None, cleaned_messages)
    """
    if not messages:
        return None, messages

    extracted_model = None
    cleaned_messages = []

    for msg in messages:
        if not isinstance(msg, dict):
            cleaned_messages.append(msg)
            continue

        content = msg.get("content", "")

        # Handle different content types
        if isinstance(content, list):
            # Multi-modal content (text + images)
            new_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    model, cleaned_text = _extract_and_clean(text, extracted_model)
                    if model:
                        extracted_model = model
                    new_content.append({**item, "text": cleaned_text})
                else:
                    new_content.append(item)
            cleaned_messages.append({**msg, "content": new_content})
        elif isinstance(content, str):
            model, cleaned_content = _extract_and_clean(content, extracted_model)
            if model:
                extracted_model = model
            cleaned_messages.append({**msg, "content": cleaned_content})
        else:
            cleaned_messages.append(msg)

    if extracted_model:
        log.info(f"Extracted model from prompt: {extracted_model}")

    return extracted_model, cleaned_messages


def _extract_and_clean(text: str, current_model: str = None) -> tuple:
    """
    Extract model marker from text and return cleaned text.

    Args:
        text: The text to search
        current_model: Currently extracted model (for priority)

    Returns:
        Tuple of (model_name or None, cleaned_text)
    """
    extracted_model = current_model
    cleaned_text = text

    # Priority 1: [use:model-name]
    use_match = USE_PATTERN.search(text)
    if use_match:
        model_name = use_match.group(1).lower()
        if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
            extracted_model = model_name
            # Remove the marker from text
            cleaned_text = USE_PATTERN.sub('', cleaned_text).strip()

    # Priority 2: @model-name (only if no [use:] found)
    if not use_match:
        at_match = AT_PATTERN.search(text)
        if at_match:
            model_name = at_match.group(1).lower()
            if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
                extracted_model = model_name
                # Remove the marker from text
                cleaned_text = AT_PATTERN.sub(' ', cleaned_text).strip()

    return extracted_model, cleaned_text


def _fuzzy_match_model(model_name: str) -> bool:
    """
    Fuzzy match model name against known patterns.
    Allows variations like 'gpt4o' -> 'gpt-4o', 'claude35' -> 'claude-3.5'
    """
    # Normalize: remove dashes and dots for comparison
    normalized = model_name.replace('-', '').replace('.', '').replace('_', '')

    for known_model in ROUTABLE_MODELS:
        known_normalized = known_model.replace('-', '').replace('.', '').replace('_', '')
        if normalized == known_normalized:
            return True

    # Check prefixes for model families
    model_prefixes = ['gpt', 'claude', 'gemini', 'o1', 'o3']
    for prefix in model_prefixes:
        if normalized.startswith(prefix):
            return True

    return False



def normalize_tools(tools: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize tools to standard OpenAI format.

    Standard format:
    {
        "type": "function",
        "function": {
            "name": "function_name",
            "description": "...",
            "parameters": {...}
        }
    }

    Also supports Cursor's custom tool format:
    {
        "type": "custom",
        "custom": {
            "name": "function_name",
            "description": "...",
            "input_schema": {...}
        }
    }
    """
    normalized_tools = []

    for tool in tools:
        if not isinstance(tool, dict):
            # 非字典类型，尝试转换或跳过
            log.warning(f"Skipping non-dict tool: {type(tool)}")
            continue

        tool_type = tool.get("type", "function")

        # Case 1: Custom tool format (Cursor uses this)
        if tool_type == "custom":
            custom_tool = tool.get("custom", {})
            if isinstance(custom_tool, dict) and "name" in custom_tool:
                # Convert custom tool to function format
                input_schema = custom_tool.get("input_schema", {})
                
                # Use clean_json_schema to ensure nested object types are properly handled
                from src.anthropic_converter import clean_json_schema
                if isinstance(input_schema, dict):
                    # Clean the schema to ensure all nested object types have complete structure
                    cleaned_schema = clean_json_schema(input_schema)
                    # Ensure input_schema has type field (required by Antigravity)
                    if "type" not in cleaned_schema:
                        cleaned_schema["type"] = "object"
                    input_schema = cleaned_schema
                elif not input_schema:
                    # Empty input_schema, create default object schema
                    input_schema = {"type": "object", "properties": {}}
                
                normalized_tool = {
                    "type": "function",
                    "function": {
                        "name": custom_tool.get("name", ""),
                        "description": custom_tool.get("description", ""),
                        # Convert input_schema to parameters
                        "parameters": input_schema
                    }
                }
                normalized_tools.append(normalized_tool)
                log.debug(f"Converted custom tool '{custom_tool.get('name')}' to function format with cleaned schema")
            else:
                log.warning(f"Skipping custom tool without custom.name: {list(custom_tool.keys()) if isinstance(custom_tool, dict) else 'not a dict'}")
            continue

        # Case 2: Standard format - tool has 'function' key
        if "function" in tool and isinstance(tool["function"], dict):
            func = tool["function"]
            # Ensure function has required 'name' field
            if "name" in func:
                normalized_tool = {
                    "type": "function",
                    "function": {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {})
                    }
                }
                normalized_tools.append(normalized_tool)
            else:
                log.warning(f"Skipping tool without function.name: {list(func.keys())}")

        # Case 3: Flat format - tool itself has 'name' key (some clients, including Cursor)
        # Cursor may send: {"name": "...", "description": "...", "parameters": {...}} OR {"name": "...", "description": "...", "input_schema": {...}}
        elif "name" in tool:
            # 优先使用 parameters，如果没有则使用 input_schema（Cursor 可能使用 input_schema）
            parameters = tool.get("parameters")
            if parameters is None:
                # Cursor 可能使用 input_schema 而不是 parameters
                input_schema = tool.get("input_schema")
                if input_schema is not None:
                    # 使用 clean_json_schema 清理 input_schema
                    from src.anthropic_converter import clean_json_schema
                    if isinstance(input_schema, dict):
                        parameters = clean_json_schema(input_schema)
                        # 确保有 type 字段
                        if "type" not in parameters:
                            parameters["type"] = "object"
                    else:
                        log.warning(f"Tool '{tool.get('name')}' has non-dict input_schema: {type(input_schema)}, converting to empty dict")
                        parameters = {}
                else:
                    parameters = {}
            
            # 确保 parameters 是字典
            if not isinstance(parameters, dict):
                log.warning(f"Tool '{tool.get('name')}' has non-dict parameters: {type(parameters)}, converting to dict")
                parameters = {}
            
            normalized_tool = {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": parameters
                }
            }
            normalized_tools.append(normalized_tool)
            log.debug(f"Converted flat format tool '{tool.get('name')}' to standard format with {len(parameters)} parameter keys")

        # Case 4: Unknown format - log and skip
        else:
            log.warning(f"Unknown tool format, type={tool_type}, keys: {list(tool.keys())}")

    return normalized_tools



def convert_responses_api_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert OpenAI Responses API format message to Chat Completions API format.

    Responses API format (what Cursor sends):
    - {"type": "message", "role": "user", "content": [...]}
    - {"type": "function_call", "call_id": "xxx", "name": "func", "arguments": "{}"}
    - {"type": "function_call_output", "call_id": "xxx", "output": "result"}

    Chat Completions API format (what backends expect):
    - {"role": "user", "content": "..."}
    - {"role": "assistant", "tool_calls": [{"id": "xxx", "type": "function", "function": {"name": "func", "arguments": "{}"}}]}
    - {"role": "tool", "tool_call_id": "xxx", "content": "result"}

    Returns:
        Converted message dict, or None if conversion not applicable
    """
    if not isinstance(msg, dict):
        return None

    msg_type = msg.get("type")

    # Already has role - standard Chat Completions format
    if "role" in msg:
        return msg

    # Type: message - extract role and content
    if msg_type == "message":
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Handle content array (multi-modal)
        if isinstance(content, list):
            # Convert Responses API content format to Chat Completions format
            converted_content = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "input_text":
                        converted_content.append({
                            "type": "text",
                            "text": item.get("text", "")
                        })
                    elif item_type == "output_text":
                        converted_content.append({
                            "type": "text",
                            "text": item.get("text", "")
                        })
                    elif item_type == "input_image":
                        # Handle image content
                        converted_content.append({
                            "type": "image_url",
                            "image_url": item.get("image_url", item.get("url", ""))
                        })
                    else:
                        # Keep as-is if already in correct format
                        converted_content.append(item)
                else:
                    converted_content.append(item)
            content = converted_content if converted_content else ""

        return {"role": role, "content": content}

    # Type: function_call - convert to assistant message with tool_calls
    if msg_type == "function_call":
        call_id = msg.get("call_id", msg.get("id", ""))
        name = msg.get("name", "")
        arguments = msg.get("arguments", "{}")

        # Ensure arguments is a string
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)

        log.debug(f"Converting function_call: call_id={call_id}, name={name}")

        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments
                }
            }]
        }

    # Type: function_call_output - convert to tool message
    if msg_type == "function_call_output":
        call_id = msg.get("call_id", msg.get("id", ""))
        output = msg.get("output", "")

        # Ensure output is a string
        if isinstance(output, dict):
            output = json.dumps(output, ensure_ascii=False)
        elif isinstance(output, list):
            output = json.dumps(output, ensure_ascii=False)

        log.debug(f"Converting function_call_output: call_id={call_id}, output_len={len(str(output))}")

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output
        }

    # Type: reasoning - convert to assistant message (for o1/o3 models)
    if msg_type == "reasoning":
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content array
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            content = "\n".join(texts)
        return {"role": "assistant", "content": content}

    # ✅ 新增：Type: tool_use - Anthropic 格式的工具调用（Cursor planning/debug 模式）
    # 格式: {"type": "tool_use", "id": "xxx", "name": "func", "input": {...}}
    if msg_type == "tool_use":
        call_id = msg.get("id", msg.get("call_id", ""))
        name = msg.get("name", "")
        # Anthropic 使用 "input"，OpenAI 使用 "arguments"
        arguments = msg.get("input", msg.get("arguments", {}))

        # Ensure arguments is a string
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif arguments is None:
            arguments = "{}"

        log.debug(f"Converting tool_use (Anthropic format): call_id={call_id}, name={name}")

        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments
                }
            }]
        }

    # ✅ 新增：Type: tool_result - Anthropic 格式的工具结果（Cursor planning/debug 模式）
    # 格式: {"type": "tool_result", "tool_use_id": "xxx", "content": "..."}
    if msg_type == "tool_result":
        call_id = msg.get("tool_use_id", msg.get("call_id", msg.get("id", "")))
        content = msg.get("content", msg.get("output", ""))

        # Ensure content is a string
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        elif isinstance(content, list):
            # 可能是 content 数组，提取文本
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    elif "text" in item:
                        texts.append(item.get("text", ""))
                elif isinstance(item, str):
                    texts.append(item)
            content = "\n".join(texts) if texts else json.dumps(content, ensure_ascii=False)
        elif content is None:
            content = ""

        log.debug(f"Converting tool_result (Anthropic format): call_id={call_id}, content_len={len(str(content))}")

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content
        }

    # ✅ 新增：处理 Cursor 可能发送的其他格式
    # 如果消息有 call_id、name、arguments 字段，尝试作为工具调用处理
    if "call_id" in msg and "name" in msg:
        call_id = msg.get("call_id", "")
        name = msg.get("name", "")
        arguments = msg.get("arguments", msg.get("input", {}))

        # Ensure arguments is a string
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif arguments is None:
            arguments = "{}"

        log.info(f"Converting untyped tool call: call_id={call_id}, name={name}, type={msg_type}")

        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments
                }
            }]
        }

    # ✅ 新增：处理工具结果（output + call_id）
    if "output" in msg and "call_id" in msg:
        call_id = msg.get("call_id", "")
        output = msg.get("output", "")

        # Ensure output is a string
        if isinstance(output, dict):
            output = json.dumps(output, ensure_ascii=False)
        elif isinstance(output, list):
            output = json.dumps(output, ensure_ascii=False)
        elif output is None:
            output = ""

        log.info(f"Converting untyped tool result: call_id={call_id}, output_len={len(str(output))}, type={msg_type}")

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output
        }

    # Unknown type - log and return None
    log.warning(f"Unknown Responses API message type: {msg_type}, keys: {list(msg.keys())}")
    return None




def sanitize_message_content(content: Any) -> Any:
    """
    清理消息 content，确保只包含 Copilot 支持的类型。

    Copilot API 只支持:
    - string: 纯文本内容
    - array: 包含 {"type": "text", "text": "..."} 或 {"type": "image_url", ...} 的数组

    重要：tool_use 和 tool_result 类型的内容块需要特殊处理：
    - 这些是 Anthropic 格式的工具调用，需要转换为 OpenAI 格式
    - 在 message 级别处理（normalize_messages），不在 content 级别丢弃
    """
    if content is None:
        return None

    # 字符串直接返回
    if isinstance(content, str):
        return content

    # 数组类型需要过滤
    if isinstance(content, list):
        sanitized = []
        tool_uses = []  # 收集 tool_use 内容块
        tool_results = []  # 收集 tool_result 内容块

        for item in content:
            if not isinstance(item, dict):
                # 非 dict 项转为文本
                if item is not None:
                    sanitized.append({"type": "text", "text": str(item)})
                continue

            item_type = item.get("type", "")

            # 支持的类型直接保留
            if item_type == "text":
                # 确保有 text 字段
                if "text" in item and item["text"]:
                    sanitized.append({"type": "text", "text": str(item["text"])})
            elif item_type == "image_url":
                # 保留图片类型
                sanitized.append(item)
            elif item_type == "tool_use":
                # 收集 tool_use，稍后在 message 级别转换为 tool_calls
                tool_uses.append(item)
                log.debug(f"Collected tool_use: id={item.get('id')}, name={item.get('name')}")
            elif item_type == "tool_result":
                # 收集 tool_result，稍后在 message 级别转换为 tool message
                tool_results.append(item)
                log.debug(f"Collected tool_result: tool_use_id={item.get('tool_use_id')}")
            elif item_type == "thinking":
                # [FIX 2026-01-17] [AUGMENT兼容] 保留 thinking 块的完整结构
                # 问题：之前将 thinking 转换为 "[Thinking] ..." 格式的普通文本
                # 这会导致 signature 信息丢失，后续请求无法从缓存恢复
                # 解决：保留 thinking 块的原始格式，包括 signature 信息
                thinking_text = item.get("thinking", "") or item.get("text", "") or item.get("content", "")
                signature = item.get("signature", "") or item.get("thoughtSignature", "")
                if thinking_text:
                    thinking_item = {
                        "type": "thinking",
                        "thinking": thinking_text
                    }
                    if signature:
                        thinking_item["signature"] = signature
                    sanitized.append(thinking_item)
                    log.debug(f"[AUGMENT兼容] Preserved thinking block: len={len(thinking_text)}, has_sig={bool(signature)}")
            else:
                # 其他不支持的类型，尝试提取文本内容
                extracted_text = None

                # 尝试从各种字段提取文本
                for field in ["text", "content", "output", "result", "data", "message"]:
                    if field in item and item[field]:
                        if isinstance(item[field], str):
                            extracted_text = item[field]
                            break
                        elif isinstance(item[field], dict):
                            # 嵌套的内容
                            nested = item[field]
                            for nf in ["text", "content", "output"]:
                                if nf in nested and isinstance(nested[nf], str):
                                    extracted_text = nested[nf]
                                    break

                if extracted_text:
                    log.debug(f"Converted {item_type} to text: {extracted_text[:100]}...")
                    sanitized.append({"type": "text", "text": extracted_text})
                else:
                    # 无法提取，记录警告并跳过
                    log.warning(f"Dropping unsupported content type: {item_type}")

        # 返回结果：包含清理后的内容和收集的工具信息
        # 使用特殊标记返回工具信息，供 normalize_messages 处理
        result = {
            "_sanitized_content": sanitized if sanitized else None,
            "_tool_uses": tool_uses,
            "_tool_results": tool_results,
        }

        # 如果没有工具相关内容，直接返回清理后的内容
        if not tool_uses and not tool_results:
            if not sanitized:
                return None
            # 如果只有一个纯文本项，直接返回字符串
            if len(sanitized) == 1 and sanitized[0].get("type") == "text":
                return sanitized[0].get("text")
            return sanitized

        # 有工具相关内容，返回特殊结构
        return result

    # 其他类型尝试转为字符串
    return str(content)


def normalize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize and filter messages array.
    - Remove null/None values
    - Remove invalid message objects
    - Convert OpenAI Responses API format to Chat Completions API format
    - Convert Anthropic tool_use/tool_result to OpenAI tool_calls/tool format
    - Ensure each message has required fields
    - Merge consecutive assistant messages with tool_calls
    """
    normalized_messages = []
    pending_tool_calls = []  # Collect tool_calls to merge into single assistant message

    for msg in messages:
        # Skip null/None values
        if msg is None:
            continue

        # Skip non-dict values
        if not isinstance(msg, dict):
            log.warning(f"Skipping non-dict message: {type(msg)}")
            continue

        # Try to convert Responses API format to Chat Completions format
        if "role" not in msg and "type" in msg:
            converted = convert_responses_api_message(msg)
            if converted is None:
                log.warning(f"Could not convert message: {list(msg.keys())}")
                continue
            msg = converted

        # Ensure message has 'role' field after conversion
        if "role" not in msg:
            log.warning(f"Skipping message without role after conversion: {list(msg.keys())}")
            continue

        # Handle tool_calls merging - collect consecutive function_call messages
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # If we have pending tool_calls, merge them
            pending_tool_calls.extend(msg.get("tool_calls", []))
            # Don't add yet - wait for non-tool_call message or end
            continue

        # If we have pending tool_calls and hit a non-assistant-with-tool_calls message
        if pending_tool_calls:
            # Flush pending tool_calls as a single assistant message
            merged_assistant = {
                "role": "assistant",
                "content": None,
                "tool_calls": pending_tool_calls
            }
            normalized_messages.append(merged_assistant)
            log.debug(f"Merged {len(pending_tool_calls)} tool_calls into single assistant message")
            pending_tool_calls = []

        # 清理 content 以确保 Copilot 兼容性
        # sanitize_message_content 可能返回特殊结构（包含 tool_use/tool_result）
        if "content" in msg:
            sanitized = sanitize_message_content(msg["content"])

            # 检查是否返回了特殊结构（包含工具信息）
            if isinstance(sanitized, dict) and "_sanitized_content" in sanitized:
                # 提取工具信息
                tool_uses = sanitized.get("_tool_uses", [])
                tool_results = sanitized.get("_tool_results", [])
                actual_content = sanitized.get("_sanitized_content")

                # 处理 tool_use（Anthropic 格式 -> OpenAI tool_calls）
                if tool_uses:
                    # 将 tool_use 转换为 OpenAI 格式的 tool_calls
                    converted_tool_calls = []
                    for tu in tool_uses:
                        tool_call = {
                            "id": tu.get("id", f"call_{tu.get('name', 'unknown')}"),
                            "type": "function",
                            "function": {
                                "name": tu.get("name", ""),
                                "arguments": json.dumps(tu.get("input", {}), ensure_ascii=False) if isinstance(tu.get("input"), dict) else str(tu.get("input", "{}"))
                            }
                        }
                        converted_tool_calls.append(tool_call)
                        log.debug(f"Converted tool_use to tool_call: id={tool_call['id']}, name={tool_call['function']['name']}")

                    # 如果当前消息是 assistant，添加 tool_calls
                    if msg.get("role") == "assistant":
                        msg = {**msg, "content": actual_content, "tool_calls": converted_tool_calls}
                    else:
                        # 如果不是 assistant 消息但包含 tool_use，需要先添加一个 assistant 消息
                        # 这种情况比较少见，但为了完整性处理
                        if actual_content:
                            normalized_messages.append({**msg, "content": actual_content})
                        assistant_msg = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": converted_tool_calls
                        }
                        normalized_messages.append(assistant_msg)
                        log.debug(f"Created assistant message with {len(converted_tool_calls)} tool_calls from content")
                        continue  # 跳过后面的 append，因为已经添加了

                # 处理 tool_result（Anthropic 格式 -> OpenAI tool message）
                if tool_results:
                    # 先添加当前消息（如果有内容）
                    if actual_content and not tool_uses:
                        normalized_messages.append({**msg, "content": actual_content})

                    # 为每个 tool_result 创建一个 tool role 消息
                    for tr in tool_results:
                        tool_use_id = tr.get("tool_use_id", "")
                        result_content = tr.get("content", "")

                        # 处理 content 可能是数组的情况
                        if isinstance(result_content, list):
                            # 提取文本内容
                            texts = []
                            for item in result_content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    texts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    texts.append(item)
                            result_content = "\n".join(texts) if texts else json.dumps(result_content, ensure_ascii=False)
                        elif isinstance(result_content, dict):
                            result_content = json.dumps(result_content, ensure_ascii=False)

                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": str(result_content)
                        }
                        normalized_messages.append(tool_msg)
                        log.debug(f"Converted tool_result to tool message: tool_call_id={tool_use_id}")

                    continue  # 跳过后面的 append

                # 如果只有 sanitized content，没有工具信息
                msg = {**msg, "content": actual_content}
            else:
                # 普通的 sanitized 结果
                msg = {**msg, "content": sanitized}

        normalized_messages.append(msg)

    # Flush any remaining pending tool_calls
    if pending_tool_calls:
        merged_assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": pending_tool_calls
        }
        normalized_messages.append(merged_assistant)
        log.debug(f"Merged {len(pending_tool_calls)} remaining tool_calls into single assistant message")

    return normalized_messages



def normalize_tool_choice(tool_choice: Any) -> Any:
    """
    Normalize tool_choice to standard OpenAI format.

    Valid formats:
    1. String: "auto", "none", "required"
    2. Object: {"type": "function", "function": {"name": "func_name"}}

    Cursor may send non-standard formats like:
    - {"type": "auto"} -> should be just "auto"
    - {"type": "function", "name": "func"} -> missing nested function object
    """
    if tool_choice is None:
        return None

    # Already a valid string
    if isinstance(tool_choice, str):
        if tool_choice in ("auto", "none", "required"):
            return tool_choice
        # Unknown string, default to auto
        log.warning(f"Unknown tool_choice string: {tool_choice}, defaulting to 'auto'")
        return "auto"

    # Object format
    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "")

        # Case 1: {"type": "auto"} or {"type": "none"} or {"type": "required"}
        # Should be converted to just the string
        if tc_type in ("auto", "none", "required"):
            if len(tool_choice) == 1:  # Only has "type" key
                return tc_type

        # Case 2: {"type": "function", ...}
        if tc_type == "function":
            # Check if it has proper "function" nested object
            if "function" in tool_choice and isinstance(tool_choice["function"], dict):
                func_obj = tool_choice["function"]
                if "name" in func_obj:
                    # Valid format
                    return {
                        "type": "function",
                        "function": {"name": func_obj["name"]}
                    }

            # Case 2b: {"type": "function", "name": "func_name"} - missing nested function
            if "name" in tool_choice:
                return {
                    "type": "function",
                    "function": {"name": tool_choice["name"]}
                }

            # Invalid function format, log and return auto
            log.warning(f"Invalid tool_choice function format: {tool_choice}, defaulting to 'auto'")
            return "auto"

        # Unknown type, default to auto
        log.warning(f"Unknown tool_choice type: {tc_type}, defaulting to 'auto'")
        return "auto"

    # Unknown format, default to auto
    log.warning(f"Unknown tool_choice format: {type(tool_choice)}, defaulting to 'auto'")
    return "auto"


def normalize_request_body(body: Dict[str, Any], *, preserve_extra_fields: bool = False) -> Dict[str, Any]:
    """
    Normalize request body to standard OpenAI format.
    Handles Cursor's non-standard format and other variations.

    Cursor may send requests with:
    - messages in different locations
    - null values in messages array
    - extra fields like 'reasoning', 'text', 'metadata', etc.
    - non-standard tools format
    - missing required fields
    
    Augment Code may send requests with:
    - 'message' field (single message string)
    - 'chat_history' field (list of previous messages)
    - model name may contain special characters like '/'
    """
    # IMPORTANT:
    # Some clients (e.g. Augment/Bugment chat-stream) send many extra top-level fields such as
    # `mode`, `tool_definitions`, `nodes`, `agent_memories`, etc.
    #
    # If we normalize into a brand new dict, those fields are lost and downstream services (or logs)
    # will misleadingly look like "missing mode/tools".
    #
    # When `preserve_extra_fields=True`, we keep all unknown fields and only normalize/override the
    # OpenAI-compatible fields we care about (model/messages/tools/stream/etc).
    normalized: Dict[str, Any] = dict(body) if preserve_extra_fields else {}

    # Extract model (required) - handle Augment format with special characters
    #
    # Augment/Bugment may provide the real target model in `third_party_override.provider_model_name`
    # when using a custom gateway. Prefer that when present.
    conversation_id = body.get("conversation_id") if isinstance(body, dict) else None
    model = body.get("model")
    third_party_override = body.get("third_party_override") if isinstance(body, dict) else None
    if (model is None or model == "" or (isinstance(model, str) and model.strip() == "")) and isinstance(third_party_override, dict):
        override_model = third_party_override.get("provider_model_name") or third_party_override.get("providerModelName")
        if override_model and isinstance(override_model, str):
            model = override_model
    if model:
        # Clean model name - remove special prefixes like "流式抗截断/"
        if isinstance(model, str):
            # Remove common prefixes
            if "/" in model:
                model = model.split("/")[-1]  # Take the last part after /
            model = model.strip()
            # 确保清理后的模型名不为空
            if model:
                normalized["model"] = model
                _bugment_conversation_state_put(conversation_id, model=model, chat_history=body.get("chat_history"))
                log.debug(f"Model normalized: '{body.get('model')}' -> '{model}'", tag="GATEWAY")
            else:
                # Do not force an arbitrary default model. Try per-conversation fallback instead.
                state = _bugment_conversation_state_get(conversation_id)
                fallback_model = state.get("model")
                if isinstance(fallback_model, str) and fallback_model.strip():
                    normalized["model"] = fallback_model.strip()
                    log.warning(
                        f"Model became empty after cleaning; using conversation model fallback: {normalized['model']}",
                        tag="GATEWAY",
                    )
        else:
            normalized["model"] = str(model) if model is not None else None
    else:
        # Do not force an arbitrary default model. Try per-conversation fallback instead.
        state = _bugment_conversation_state_get(conversation_id)
        fallback_model = state.get("model")
        if isinstance(fallback_model, str) and fallback_model.strip():
            normalized["model"] = fallback_model.strip()
            log.warning(f"Model was empty; using conversation model fallback: {normalized['model']}", tag="GATEWAY")

    # ---------------------------------------------------------------------
    # Augment/Bugment mode handling (minimal behavioral isolation)
    #
    # Augment may issue multiple requests within the same conversation_id:
    # - AGENT: tool-using "work" requests (workspace/tools required)
    # - CHAT: internal classify/distill/memory/title requests (must be JSON-clean)
    #
    # For CHAT mode we MUST avoid:
    # - enabling tools (or upstream tool_calls), because chat-stream NDJSON doesn't carry tool steps
    # - enabling thinking output (<think>/<thoughtSignature>), because some client steps JSON.parse()
    #
    # We implement the smallest isolation here:
    # - force tool_choice="none" and drop tools
    # - if a "-thinking" model was selected, strip the suffix to route to the non-thinking variant
    raw_mode = body.get("mode")
    mode_str = raw_mode.strip().upper() if isinstance(raw_mode, str) else None
    is_chat_mode = mode_str == "CHAT"
    if is_chat_mode:
        try:
            m = normalized.get("model")
            if isinstance(m, str) and m.endswith("-thinking"):
                normalized["model"] = m[: -len("-thinking")]
                log.debug(f"CHAT mode: stripped thinking suffix: '{m}' -> '{normalized['model']}'", tag="GATEWAY")
        except Exception:
            pass
        # Disable tools for CHAT-mode requests
        normalized.pop("tools", None)
        normalized["tool_choice"] = "none"

    # Extract messages - try multiple possible locations
    messages = None

    # Standard location
    if "messages" in body and body["messages"]:
        messages = body["messages"]
    # Augment Code format: 'message' + 'chat_history'
    elif "message" in body:
        # Augment sends: { "message": "text", "chat_history": [...] }
        messages = []
        
        # Add chat history first
        if "chat_history" in body and isinstance(body["chat_history"], list):
            for hist_msg in body["chat_history"]:
                if isinstance(hist_msg, dict):
                    # Augment format: { "role": "user/assistant", "content": "..." }
                    if "role" in hist_msg and "content" in hist_msg:
                        messages.append({
                            "role": hist_msg["role"],
                            "content": hist_msg["content"]
                        })
                    # Or might be: { "user": "...", "assistant": "..." }
                    elif "user" in hist_msg:
                        messages.append({
                            "role": "user",
                            "content": str(hist_msg["user"])
                        })
                    elif "assistant" in hist_msg:
                        messages.append({
                            "role": "assistant",
                            "content": str(hist_msg["assistant"])
                        })
        
        # Add current message
        current_message = body.get("message", "")
        if current_message:
            messages.append({
                "role": "user",
                "content": str(current_message)
            })
        
        if messages:
            log.debug(f"Converted Augment format: {len(messages)} messages from message+chat_history", tag="GATEWAY")
    # Some clients put messages in 'prompt' or 'input'
    elif "prompt" in body:
        prompt = body["prompt"]
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        elif isinstance(prompt, list):
            messages = prompt
    elif "input" in body:
        input_val = body["input"]
        if isinstance(input_val, str):
            messages = [{"role": "user", "content": input_val}]
        elif isinstance(input_val, list):
            messages = input_val

    # If still no messages, create a default one
    if not messages:
        log.warning("No messages found in request body, creating default")
        log.warning(f"Request body keys: {list(body.keys())}")
        # Log first 500 chars of body for debugging
        body_str = json.dumps(body, ensure_ascii=False)
        log.warning(f"Request body preview: {body_str[:500]}...")
        messages = [{"role": "user", "content": "Hello"}]

    # Normalize and filter messages (remove null values, etc.)
    normalized["messages"] = normalize_messages(messages)

    # Augment/Bugment guidance fields (not part of OpenAI standard) need to be injected into the prompt
    # if we are forwarding to OpenAI-compatible backends. Otherwise the upstream model will ignore them.
    try:
        guidance_parts: List[str] = []
        ug = body.get("user_guidelines")
        if isinstance(ug, str) and ug.strip():
            guidance_parts.append(f"# User Guidelines\n{ug.strip()}")
        wg = body.get("workspace_guidelines")
        if isinstance(wg, str) and wg.strip():
            guidance_parts.append(f"# Workspace Guidelines\n{wg.strip()}")
        am = body.get("agent_memories")
        if isinstance(am, str) and am.strip():
            guidance_parts.append(f"# Agent Memories\n{am.strip()}")
        rules = body.get("rules")
        if isinstance(rules, list) and rules:
            # Rules sometimes are structured; keep a compact JSON form.
            guidance_parts.append(f"# Rules\n{json.dumps(rules, ensure_ascii=False)}")
        persona = body.get("persona_type")
        if persona is not None and str(persona).strip():
            guidance_parts.append(f"# Persona Type\n{persona}")

        if guidance_parts:
            system_text = "\n\n".join(guidance_parts)
            # Prepend as a system message (do not overwrite existing history).
            normalized["messages"] = [{"role": "system", "content": system_text}] + list(normalized["messages"])
    except Exception as e:
        log.warning(f"Failed to inject Augment guidance into messages: {e}", tag="GATEWAY")

    # Copy standard OpenAI fields (except tools which needs special handling)
    standard_fields = [
        "stream", "temperature", "top_p", "max_tokens", "stop",
        "n", "frequency_penalty", "presence_penalty", "logit_bias",
        "logprobs", "response_format", "seed", "tool_choice",
        "user", "functions", "function_call"
    ]

    for field in standard_fields:
        if field in body:
            # Special handling for tool_choice
            if field == "tool_choice":
                normalized[field] = normalize_tool_choice(body[field])
            else:
                normalized[field] = body[field]

    # Handle tools field specially - normalize format (skip in CHAT mode)
    if not is_chat_mode and "tools" in body and body["tools"]:
        original_tools_count = len(body["tools"])
        original_tool_types = [tool.get("type", "unknown") if isinstance(tool, dict) else "non-dict" for tool in body["tools"]]
        
        # DEBUG: Log first original tool structure
        if body["tools"] and isinstance(body["tools"][0], dict):
            first_original = body["tools"][0]
            log.debug(f"First original tool keys: {list(first_original.keys())}, has_type={'type' in first_original}, has_function={'function' in first_original}, has_name={'name' in first_original}")
        
        normalized_tools = normalize_tools(body["tools"])
        if normalized_tools:
            normalized["tools"] = normalized_tools
            normalized_tool_types = [tool.get("type", "unknown") for tool in normalized_tools if isinstance(tool, dict)]
            log.debug(f"Tools normalized: {original_tools_count} tools, types={original_tool_types[:10]}... -> {len(normalized_tools)} tools, types={normalized_tool_types[:10]}...")
            
            # DEBUG: Log first normalized tool structure for debugging
            if normalized_tools and isinstance(normalized_tools[0], dict):
                first_tool = normalized_tools[0]
                log.debug(f"First normalized tool keys: {list(first_tool.keys())}, has_type={'type' in first_tool}, has_function={'function' in first_tool}")
                if "function" in first_tool:
                    func = first_tool["function"]
                    params = func.get("parameters", {})
                    params_type = params.get("type") if isinstance(params, dict) else type(params).__name__
                    log.debug(f"First normalized tool: name={func.get('name')}, params_type={params_type}, has_properties={'properties' in params if isinstance(params, dict) else False}")
                else:
                    log.warning(f"First normalized tool missing 'function' key! Keys: {list(first_tool.keys())}")
            else:
                log.warning(f"First normalized tool is not a dict! Type: {type(normalized_tools[0]) if normalized_tools else 'empty'}")

    # Augment/Bugment sends tool definitions under `tool_definitions` (not `tools`).
    # To preserve tool calling capability when we forward to OpenAI-compatible backends,
    # best-effort treat `tool_definitions` as `tools` when `tools` is absent.
    if (not is_chat_mode) and ("tools" not in normalized or not normalized.get("tools")) and isinstance(body.get("tool_definitions"), list) and body.get("tool_definitions"):
        try:
            raw_tool_defs = body.get("tool_definitions") or []
            log.debug(f"Using tool_definitions as tools: count={len(raw_tool_defs)}", tag="GATEWAY")
            normalized_tools = normalize_tools(raw_tool_defs)
            if normalized_tools:
                normalized["tools"] = normalized_tools
        except Exception as e:
            log.warning(f"Failed to convert tool_definitions to tools: {e}", tag="GATEWAY")

    # Set default stream to False if not specified
    if "stream" not in normalized:
        normalized["stream"] = False

    # Extract model from prompt markers (if any)
    prompt_model, cleaned_messages = extract_model_from_prompt(normalized["messages"])
    if prompt_model:
        normalized["model"] = prompt_model
        normalized["messages"] = cleaned_messages
        log.info(f"Model overridden by prompt marker: {prompt_model}")

    log.debug(f"Normalized request: model={normalized.get('model')}, messages_count={len(normalized['messages'])}, stream={normalized.get('stream')}, tools_count={len(normalized.get('tools', []))}")

    return normalized


# ==================== Augment-Compatible Client Tool Loop (Gateway) ====================
#
# Prefer client-side tool loop for Bugment:
# - Upstream returns tool_calls
# - Gateway converts tool_calls -> Augment TOOL_USE (type=5) and stop_reason=tool_use
# - Bugment executes local tools and sends TOOL_RESULT nodes back
# - Gateway forwards tool results to upstream and continues streaming
#
# The legacy implementation (stream_openai_with_tool_loop) executes tools inside the gateway and
# prevents the client from seeing TOOL_USE nodes.
#


def _augment_chat_history_to_messages(chat_history: Any) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if not isinstance(chat_history, list):
        return messages

    for item in chat_history:
        if not isinstance(item, dict):
            continue

        # Bugment log format: { request_message, response_text, ... }
        request_message = item.get("request_message") or item.get("user") or item.get("requestMessage")
        response_text = item.get("response_text") or item.get("assistant") or item.get("responseText")

        if isinstance(request_message, str) and request_message.strip():
            messages.append({"role": "user", "content": request_message})
        if isinstance(response_text, str) and response_text.strip():
            messages.append({"role": "assistant", "content": response_text})

        # Alternate Augment format: { role, content }
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str) and role in ("user", "assistant", "system") and content.strip():
            messages.append({"role": role, "content": content})

    return messages


# In-memory tool-call state for Bugment client-side tool loop.
# Keyed by conversation_id + tool_use_id so that TOOL_RESULT continuations can be sent upstream
# using OpenAI-compatible tool message format.
_BUGMENT_TOOL_STATE: Dict[str, Dict[str, Any]] = {}
_BUGMENT_TOOL_STATE_TTL_SEC = 60 * 30  # 30 minutes

# In-memory conversation state to preserve UI-selected model + chat_history across internal requests.
# Bugment sometimes sends internal requests (e.g. prompt enhancer) with empty `model` and/or empty
# `chat_history`. Using per-conversation state avoids falling back to an arbitrary default model.
_BUGMENT_CONVERSATION_STATE: Dict[str, Dict[str, Any]] = {}
_BUGMENT_CONVERSATION_STATE_TTL_SEC = 60 * 60  # 60 minutes


def _bugment_conversation_state_key(conversation_id: Optional[str]) -> str:
    return conversation_id or "no_conversation"


def _bugment_conversation_state_prune(now_ts: Optional[float] = None) -> None:
    now = now_ts if isinstance(now_ts, (int, float)) else time.time()
    expired = [
        k
        for k, v in _BUGMENT_CONVERSATION_STATE.items()
        if isinstance(v, dict) and (now - float(v.get("ts", 0))) > _BUGMENT_CONVERSATION_STATE_TTL_SEC
    ]
    for k in expired:
        _BUGMENT_CONVERSATION_STATE.pop(k, None)


def _bugment_conversation_state_put(
    conversation_id: Optional[str],
    *,
    model: Optional[str] = None,
    chat_history: Any = None,
) -> None:
    _bugment_conversation_state_prune()
    key = _bugment_conversation_state_key(conversation_id)
    cur = _BUGMENT_CONVERSATION_STATE.get(key) if isinstance(_BUGMENT_CONVERSATION_STATE.get(key), dict) else {}
    next_state: Dict[str, Any] = dict(cur) if isinstance(cur, dict) else {}
    next_state["ts"] = time.time()
    if isinstance(model, str) and model.strip():
        next_state["model"] = model.strip()
    if isinstance(chat_history, list) and chat_history:
        next_state["chat_history"] = chat_history
    _BUGMENT_CONVERSATION_STATE[key] = next_state


def _bugment_conversation_state_get(conversation_id: Optional[str]) -> Dict[str, Any]:
    _bugment_conversation_state_prune()
    state = _BUGMENT_CONVERSATION_STATE.get(_bugment_conversation_state_key(conversation_id))
    return state if isinstance(state, dict) else {}


def _bugment_tool_state_key(conversation_id: Optional[str], tool_use_id: str) -> str:
    cid = conversation_id or "no_conversation"
    return f"{cid}:{tool_use_id}"


def _bugment_tool_state_prune(now_ts: Optional[float] = None) -> None:
    now = now_ts if isinstance(now_ts, (int, float)) else time.time()
    expired = [k for k, v in _BUGMENT_TOOL_STATE.items() if isinstance(v, dict) and (now - float(v.get("ts", 0))) > _BUGMENT_TOOL_STATE_TTL_SEC]
    for k in expired:
        _BUGMENT_TOOL_STATE.pop(k, None)


def _bugment_tool_state_put(conversation_id: Optional[str], tool_use_id: str, *, tool_name: str, arguments_json: str) -> None:
    _bugment_tool_state_prune()
    _BUGMENT_TOOL_STATE[_bugment_tool_state_key(conversation_id, tool_use_id)] = {
        "ts": time.time(),
        "tool_name": tool_name,
        "arguments_json": arguments_json,
    }


def _bugment_tool_state_get(conversation_id: Optional[str], tool_use_id: str) -> Optional[Dict[str, Any]]:
    _bugment_tool_state_prune()
    return _BUGMENT_TOOL_STATE.get(_bugment_tool_state_key(conversation_id, tool_use_id))


def _extract_tool_result_nodes(nodes: Any) -> List[Dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    results: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == 1 and isinstance(n.get("tool_result_node"), dict):
            results.append(n["tool_result_node"])
    return results


def _build_openai_messages_from_bugment(raw_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert Bugment's request (message/chat_history/nodes) into OpenAI-compatible messages.
    Supports TOOL_RESULT continuation by replaying the original assistant tool_calls (from state)
    and appending tool messages.
    """
    messages: List[Dict[str, Any]] = []
    conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None

    messages.extend(_augment_chat_history_to_messages(raw_body.get("chat_history")))

    tool_results = _extract_tool_result_nodes(raw_body.get("nodes"))
    if tool_results:
        assistant_tool_calls: List[Dict[str, Any]] = []
        tool_messages: List[Dict[str, Any]] = []
        fallback_user_notes: List[str] = []

        for tr in tool_results:
            tool_use_id = tr.get("tool_use_id")
            content = tr.get("content")
            is_error = tr.get("is_error")

            if not isinstance(tool_use_id, str) or not tool_use_id.strip():
                continue
            text = content if isinstance(content, str) else (str(content) if content is not None else "")

            state = _bugment_tool_state_get(conversation_id, tool_use_id)
            if isinstance(state, dict) and isinstance(state.get("tool_name"), str):
                assistant_tool_calls.append(
                    {
                        "id": tool_use_id,
                        "type": "function",
                        "function": {"name": state["tool_name"], "arguments": state.get("arguments_json") or "{}"},
                    }
                )
                tool_messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": text})
            else:
                note = f"[Bugment] Tool result received but missing tool_call state: tool_use_id={tool_use_id}"
                if is_error:
                    note += " (is_error=true)"
                fallback_user_notes.append(note + "\n" + text)

        if assistant_tool_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": assistant_tool_calls})
            messages.extend(tool_messages)

        for note in fallback_user_notes:
            messages.append({"role": "user", "content": note})

    # Current user message (may be empty on tool_result continuations)
    current_message = raw_body.get("message")
    if isinstance(current_message, str) and current_message.strip():
        messages.append({"role": "user", "content": current_message})

    if not messages:
        messages = [{"role": "user", "content": raw_body.get("message") or "Hello"}]

    return messages


def _prepend_bugment_guidance_system_message(raw_body: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Bugment/Augment sends guidance via out-of-band fields like:
    - user_guidelines / workspace_guidelines
    - rules (list)
    - agent_memories
    - persona_type

    Upstream OpenAI-compatible backends will ignore these unless we inject them into a system message.
    This is critical for preserving "agent" behavior and to avoid the model hallucinating local files
    like `.augment/` or `User Guidelines.md` inside the workspace.
    """
    if not isinstance(raw_body, dict):
        return messages

    guidance_parts: List[str] = []

    # Stable prelude: keep it short to avoid fighting user-provided prompts.
    guidance_parts.append(
        "\n".join(
            [
                "# Runtime",
                "You are running inside a VSCode agent environment (Bugment/Augment-like).",
                "You can call provided tools to read/write workspace files and perform codebase retrieval.",
                "Do not assume there is a `.augment/` directory or `User Guidelines.md` file in the workspace unless you can list it.",
            ]
        )
    )

    ug = raw_body.get("user_guidelines")
    if isinstance(ug, str) and ug.strip():
        guidance_parts.append(f"# User Guidelines\n{ug.strip()}")

    wg = raw_body.get("workspace_guidelines")
    if isinstance(wg, str) and wg.strip():
        guidance_parts.append(f"# Workspace Guidelines\n{wg.strip()}")

    am = raw_body.get("agent_memories")
    if isinstance(am, str) and am.strip():
        guidance_parts.append(f"# Agent Memories\n{am.strip()}")

    rules = raw_body.get("rules")
    if isinstance(rules, list) and rules:
        guidance_parts.append(f"# Rules\n{json.dumps(rules, ensure_ascii=False)}")

    persona = raw_body.get("persona_type")
    if persona is not None and str(persona).strip():
        guidance_parts.append(f"# Persona Type\n{persona}")

    if not guidance_parts:
        return messages

    system_text = "\n\n".join(guidance_parts).strip()
    if not system_text:
        return messages

    return [{"role": "system", "content": system_text}] + list(messages)


async def stream_openai_with_nodes_bridge(
    *,
    headers: Dict[str, str],
    raw_body: Dict[str, Any],
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream upstream /chat/completions and emit Bugment-compatible NDJSON objects.

    Bugment expects each NDJSON line to be a "BackChatResult"-like object with:
    - text: string (required)
    - nodes: optional list of nodes (e.g. type=5 tool_use)
    - stop_reason: optional string

    Tool loop contract (from vanilla extension):
    - Tool use node: { id, type: 5, tool_use: { tool_use_id, tool_name, input_json } }
    - Tool result node (client->gateway): { id, type: 1, tool_result_node: { tool_use_id, content, is_error } }
    """
    conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None
    messages = _prepend_bugment_guidance_system_message(raw_body, _build_openai_messages_from_bugment(raw_body))

    tools = None
    try:
        raw_tool_defs = raw_body.get("tool_definitions")
        if isinstance(raw_tool_defs, list) and raw_tool_defs:
            augment_tools = parse_tool_definitions_from_request(raw_tool_defs)
            tools = convert_tools_to_openai(augment_tools) if augment_tools else None
    except Exception as e:
        log.warning(f"Failed to convert tool_definitions to OpenAI tools: {e}", tag="GATEWAY")

    request_body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        request_body["tools"] = tools
        request_body["tool_choice"] = "auto"

    sse_stream = await route_request_with_fallback(
        endpoint="/chat/completions",
        method="POST",
        headers=headers,
        body=request_body,
        model=model,
        stream=True,
    )

    buffer = ""
    tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
    saw_tool_calls = False
    saw_done = False

    async for chunk in sse_stream:
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="ignore")
        buffer += chunk

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue

            json_str = line[6:].strip()
            if json_str == "[DONE]":
                buffer = ""
                saw_done = True
                break

            try:
                evt = json.loads(json_str)
            except Exception:
                continue

            choices = evt.get("choices") or []
            if not choices:
                continue
            choice0 = choices[0] if isinstance(choices[0], dict) else None
            if not choice0:
                continue

            # Text streaming
            delta = choice0.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield json.dumps({"text": content}, ensure_ascii=False, separators=(",", ":")) + "\n"

            # Tool calls streaming (OpenAI-like)
            tool_calls = delta.get("tool_calls") or []
            if isinstance(tool_calls, list) and tool_calls:
                saw_tool_calls = True
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    if not isinstance(idx, int):
                        idx = 0
                    cur = tool_calls_by_index.setdefault(idx, {"id": None, "type": "function", "function": {"name": None, "arguments": ""}})
                    if isinstance(tc.get("id"), str):
                        cur["id"] = tc["id"]
                    if isinstance(tc.get("type"), str):
                        cur["type"] = tc["type"]
                    func = tc.get("function")
                    if isinstance(func, dict):
                        if isinstance(func.get("name"), str):
                            cur["function"]["name"] = func["name"]
                        if isinstance(func.get("arguments"), str):
                            cur["function"]["arguments"] += func["arguments"]

            finish_reason = choice0.get("finish_reason")
            if finish_reason in ("tool_calls", "function_call"):
                saw_tool_calls = True

        if saw_done:
            break

    if saw_tool_calls and tool_calls_by_index:
        nodes: List[Dict[str, Any]] = []
        next_node_id = 0
        for idx in sorted(tool_calls_by_index.keys()):
            tc = tool_calls_by_index[idx]
            tool_use_id = tc.get("id") or f"call_{int(time.time())}_{idx}"
            func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            tool_name = func.get("name") if isinstance(func, dict) else None
            arg_str = func.get("arguments") if isinstance(func, dict) else ""
            if not isinstance(tool_use_id, str) or not tool_use_id.strip():
                continue
            if not isinstance(tool_name, str) or not tool_name.strip():
                tool_name = "unknown"
            if not isinstance(arg_str, str):
                arg_str = ""
            arguments_json = arg_str.strip() or "{}"
            # Bugment will JSON.parse(input_json); ensure it's valid JSON.
            try:
                parsed_args = json.loads(arguments_json)
                # Compatibility: some upstreams call codebase-retrieval with `query`, while Bugment expects
                # `information_request` (per sidecar tool schema). Map when needed.
                if tool_name == "codebase-retrieval" and isinstance(parsed_args, dict):
                    if "information_request" not in parsed_args:
                        if isinstance(parsed_args.get("query"), str) and parsed_args.get("query"):
                            parsed_args["information_request"] = parsed_args["query"]
                        elif isinstance(parsed_args.get("informationRequest"), str) and parsed_args.get("informationRequest"):
                            parsed_args["information_request"] = parsed_args["informationRequest"]
                    arguments_json = json.dumps(parsed_args, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                arguments_json = json.dumps({"raw_arguments": arguments_json}, ensure_ascii=False, separators=(",", ":"))

            # Persist tool call mapping for TOOL_RESULT continuations.
            _bugment_tool_state_put(conversation_id, tool_use_id, tool_name=tool_name, arguments_json=arguments_json)

            nodes.append(
                {
                    "id": next_node_id,
                    "type": 5,
                    "tool_use": {"tool_use_id": tool_use_id, "tool_name": tool_name, "input_json": arguments_json},
                }
            )
            next_node_id += 1

        if nodes:
            yield json.dumps({"text": "", "nodes": nodes, "stop_reason": "tool_use"}, ensure_ascii=False, separators=(",", ":")) + "\n"
        return

    # No tool calls: end of turn (send a deterministic terminal marker for clients that rely on it).
    yield json.dumps({"text": "", "stop_reason": "end_turn"}, ensure_ascii=False, separators=(",", ":")) + "\n"


# ==================== Local Tool Execution (Gateway) ====================
#
# Augment's VSCode extension can advertise tools to the model, but in this gateway setup we're
# forwarding to OpenAI-compatible upstreams that return `tool_calls` in the streaming response.
# The VSCode client does not execute OpenAI `tool_calls` directly in the `/chat-stream` NDJSON
# protocol, so we implement a minimal server-side tool loop here to keep conversations alive.
#
# Security note: This is intended for local development. Tools that write to disk are intentionally
# not implemented here. Add an allowlist + root path restrictions if you extend this.


def _safe_read_text_file(path_str: str, *, max_chars: int = 200_000) -> str:
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path_str}")
    if p.is_dir():
        raise IsADirectoryError(f"Path is a directory: {path_str}")
    text = p.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n...[truncated to {max_chars} chars]..."
    return text


def _tool_view(args: Dict[str, Any]) -> str:
    path_str = args.get("path") or args.get("file_path") or args.get("filePath")
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError("Missing required argument: path")
    content = _safe_read_text_file(path_str.strip())
    return content


def _tool_view_range_untruncated(args: Dict[str, Any]) -> str:
    path_str = args.get("path") or args.get("file_path") or args.get("filePath")
    start = args.get("start_line") or args.get("startLine") or args.get("start")
    end = args.get("end_line") or args.get("endLine") or args.get("end")
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError("Missing required argument: path")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        raise ValueError("Invalid line range: start_line/end_line must be ints and 1 <= start <= end")

    text = _safe_read_text_file(path_str.strip(), max_chars=2_000_000)
    lines = text.splitlines()
    # Convert 1-based inclusive to Python slice
    selected = lines[start - 1:end]
    return "\n".join(selected)


def _tool_search_untruncated(args: Dict[str, Any]) -> str:
    # Very small subset: search within a single file.
    path_str = args.get("path") or args.get("file_path") or args.get("filePath")
    query = args.get("query") or args.get("pattern") or args.get("text")
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError("Missing required argument: path")
    if not isinstance(query, str) or not query:
        raise ValueError("Missing required argument: query")

    text = _safe_read_text_file(path_str.strip(), max_chars=2_000_000)
    lines = text.splitlines()
    matches: List[Dict[str, Any]] = []
    for i, line in enumerate(lines, start=1):
        if query in line:
            matches.append({"line": i, "text": line})
            if len(matches) >= 200:
                break
    return json.dumps({"path": path_str, "query": query, "matches": matches}, ensure_ascii=False)


def run_local_tool(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "view":
        return _tool_view(args)
    if tool_name == "view-range-untruncated":
        return _tool_view_range_untruncated(args)
    if tool_name == "search-untruncated":
        return _tool_search_untruncated(args)
    raise NotImplementedError(f"Tool not implemented in gateway: {tool_name}")


async def stream_openai_with_tool_loop(
    *,
    headers: Dict[str, str],
    body: Dict[str, Any],
    model: str,
    max_tool_rounds: int = 6,
) -> AsyncGenerator[str, None]:
    """
    Call upstream /chat/completions with stream=True, proxy text to Augment NDJSON, and if upstream
    returns tool_calls, execute them locally and continue the loop until a final answer is produced.
    """
    debug_tool_loop = str(headers.get("x-debug-tool-loop", "")).strip().lower() in ("1", "true", "yes", "on")

    # We mutate messages across rounds
    messages = list(body.get("messages") or [])
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")

    for round_idx in range(max_tool_rounds):
        request_body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools
        if tool_choice is not None:
            request_body["tool_choice"] = tool_choice
        # Preserve a few common OpenAI params if present
        for k in ("temperature", "top_p", "max_tokens", "stop", "seed"):
            if k in body:
                request_body[k] = body[k]

        sse_stream = await route_request_with_fallback(
            endpoint="/chat/completions",
            method="POST",
            headers=headers,
            body=request_body,
            model=model,
            stream=True,
        )

        buffer = ""
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
        saw_tool_calls = False
        saw_done = False

        async for chunk in sse_stream:
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="ignore")
            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue

                json_str = line[6:].strip()
                if json_str == "[DONE]":
                    saw_done = True
                    break

                try:
                    evt = json.loads(json_str)
                except Exception:
                    continue

                choices = evt.get("choices") or []
                if not choices:
                    continue
                choice0 = choices[0] if isinstance(choices[0], dict) else None
                if not choice0:
                    continue

                # Text streaming
                delta = choice0.get("delta") or {}
                if isinstance(delta, dict) and "content" in delta and delta["content"] is not None:
                    yield json.dumps({"text": delta["content"]}, separators=(",", ":"), ensure_ascii=False) + "\n"

                # Tool calls streaming
                tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                if isinstance(tool_calls, list) and tool_calls:
                    saw_tool_calls = True
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        idx = tc.get("index")
                        if not isinstance(idx, int):
                            idx = 0
                        cur = tool_calls_by_index.setdefault(idx, {"id": None, "type": "function", "function": {"name": None, "arguments": ""}})
                        if "id" in tc and isinstance(tc["id"], str):
                            cur["id"] = tc["id"]
                        if "type" in tc and isinstance(tc["type"], str):
                            cur["type"] = tc["type"]
                        func = tc.get("function")
                        if isinstance(func, dict):
                            if "name" in func and isinstance(func["name"], str):
                                cur["function"]["name"] = func["name"]
                            if "arguments" in func and isinstance(func["arguments"], str):
                                cur["function"]["arguments"] += func["arguments"]

                finish_reason = choice0.get("finish_reason")
                if finish_reason in ("tool_calls", "function_call"):
                    # Some upstreams keep streaming tool arguments after emitting finish_reason. We only
                    # finalize tool execution after [DONE] to avoid running with partial JSON.
                    log.warning(
                        f"[TOOL LOOP] finish_reason={finish_reason} round={round_idx} tool_calls_indexes={list(tool_calls_by_index.keys())}",
                        tag="GATEWAY",
                    )

            # If a finish_reason tool_calls was hit, break out of async-for to run tools.
            if saw_tool_calls and tool_calls_by_index:
                # We intentionally wait for finish_reason signal; the stream may still contain lines in buffer,
                # but further deltas are tool args.
                pass

            if saw_done:
                break

        if not saw_tool_calls or not tool_calls_by_index:
            return

        # Build ordered tool calls
        ordered: List[Dict[str, Any]] = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
        # Append assistant tool_calls message (OpenAI format)
        assistant_tool_calls = []
        for tc in ordered:
            tool_id = tc.get("id") or f"call_round{round_idx}_{len(assistant_tool_calls)}"
            fn = tc.get("function") or {}
            assistant_tool_calls.append(
                {
                    "id": tool_id,
                    "type": tc.get("type") or "function",
                    "function": {"name": fn.get("name"), "arguments": fn.get("arguments", "")},
                }
            )

        if debug_tool_loop:
            yield json.dumps(
                {
                    "text": f"\n[Gateway debug] round={round_idx} tool_calls={json.dumps(assistant_tool_calls, ensure_ascii=False)[:2000]}"
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ) + "\n"

        # Some backends are picky about `content` being null; use empty string for tool-call turns.
        messages.append({"role": "assistant", "content": "", "tool_calls": assistant_tool_calls})

        # Execute each tool and append tool result messages
        for tc in assistant_tool_calls:
            tool_id = tc["id"]
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or "unknown"
            arg_str = fn.get("arguments") or ""

            def _infer_windows_path_from_messages() -> Optional[str]:
                try:
                    import re as _re

                    for m in reversed(messages):
                        if not isinstance(m, dict):
                            continue
                        if m.get("role") != "user":
                            continue
                        content = m.get("content")
                        if not isinstance(content, str) or not content:
                            continue
                        match = _re.search(r"([A-Za-z]:\\\\[^\\s\"']+)", content)
                        if not match:
                            continue
                        p = match.group(1).strip()
                        p = p.rstrip("，。,;:)]}>'\"")
                        return p
                except Exception:
                    return None
                return None

            def _repair_tool_args(tool: str, raw: str) -> Optional[Dict[str, Any]]:
                # Best-effort fallback for upstreams that stream non-JSON argument fragments.
                if tool in ("view", "view-range-untruncated") and (not raw or not raw.strip()):
                    p = _infer_windows_path_from_messages()
                    return {"path": p} if p else None
                if tool == "view" and isinstance(raw, str):
                    # If JSON parsing fails, try to infer path from the user message.
                    p = _infer_windows_path_from_messages()
                    return {"path": p} if p else None
                return None

            try:
                args = json.loads(arg_str) if isinstance(arg_str, str) and arg_str.strip() else {}
            except Exception as e:
                repaired = _repair_tool_args(tool_name, arg_str)
                if repaired is None:
                    tool_out = f"Failed to parse tool arguments as JSON: {e}\nRaw arguments: {arg_str[:5000]}"
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": tool_out})
                    continue
                args = repaired

            # If args are empty/missing required fields, try a small inference for common tools.
            if tool_name == "view" and isinstance(args, dict) and not args.get("path"):
                inferred = _infer_windows_path_from_messages()
                if inferred:
                    args["path"] = inferred

            try:
                tool_out = run_local_tool(tool_name, args if isinstance(args, dict) else {"value": args})
            except Exception as e:
                tool_out = f"Tool execution error: {e}"

            if debug_tool_loop:
                preview = tool_out if isinstance(tool_out, str) else str(tool_out)
                yield json.dumps(
                    {
                        "text": f"\n[Gateway debug] tool={tool_name} args={json.dumps(args, ensure_ascii=False)[:500]} out_preview={preview[:500]}"
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                ) + "\n"

            # Keep tool output bounded to avoid runaway context growth
            if isinstance(tool_out, str) and len(tool_out) > 200_000:
                tool_out = tool_out[:200_000] + "\n\n...[truncated]..."

            messages.append({"role": "tool", "tool_call_id": tool_id, "content": tool_out})

        # Continue loop for next model call (with updated messages)

    yield json.dumps(
        {"text": "\n[Gateway] 工具循环次数超限，已终止（max_tool_rounds reached）。"},
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"


# 创建路由器
router = APIRouter(prefix="/gateway", tags=["Unified Gateway"])

# 后端服务配置
BACKENDS = {
    "antigravity": {
        "name": "Antigravity",
        "base_url": "http://127.0.0.1:7861/antigravity/v1",
        "priority": 1,  # 数字越小优先级越高
        "timeout": 60.0,  # 普通请求超时
        "stream_timeout": 300.0,  # 流式请求超时（5分钟）
        "max_retries": 2,  # 最大重试次数
        "enabled": True,
    },
    "copilot": {
        "name": "Copilot",
        "base_url": "http://127.0.0.1:8141/v1",
        "priority": 2,
        "timeout": 120.0,  # 思考模型需要更长时间
        "stream_timeout": 600.0,  # 流式请求超时（10分钟，GPT-5.2思考模型）
        "max_retries": 3,  # 最大重试次数
        "enabled": True,
    },
    "kiro-gateway": {
        "name": "Kiro Gateway",
        # 注意：endpoint 传入时是 /chat/completions（不带 /v1）
        # 如果 kiro-gateway 提供 /v1 端点，base_url 应该包含 /v1
        # 最终 URL = base_url + endpoint = http://127.0.0.1:9046/v1 + /chat/completions = http://127.0.0.1:9046/v1/chat/completions
        # 如果 kiro-gateway 不使用 /v1 前缀，可以通过环境变量设置为 http://127.0.0.1:9046
        "base_url": os.getenv("KIRO_GATEWAY_BASE_URL", "http://127.0.0.1:9046/v1"),  # 默认包含 /v1（假设 kiro-gateway 提供 /v1 端点）
        "priority": 3,  # 优先级最低，作为兜底或特定模型路由
        "timeout": float(os.getenv("KIRO_GATEWAY_TIMEOUT", "120.0")),  # 普通请求超时
        "stream_timeout": float(os.getenv("KIRO_GATEWAY_STREAM_TIMEOUT", "600.0")),  # 流式请求超时
        "max_retries": int(os.getenv("KIRO_GATEWAY_MAX_RETRIES", "2")),  # 最大重试次数
        "enabled": os.getenv("KIRO_GATEWAY_ENABLED", "true").lower() in ("true", "1", "yes"),  # 可通过环境变量控制
    },
}

# Kiro Gateway 路由配置
# 通过环境变量 KIRO_GATEWAY_MODELS 指定哪些模型路由到 kiro-gateway
# 格式：逗号分隔的模型名称列表，例如: "gpt-4,claude-3-opus,gemini-pro"
KIRO_GATEWAY_MODELS_ENV = os.getenv("KIRO_GATEWAY_MODELS", "").strip()
KIRO_GATEWAY_MODELS = (
    [m.strip().lower() for m in KIRO_GATEWAY_MODELS_ENV.split(",") if m.strip()]
    if KIRO_GATEWAY_MODELS_ENV
    else []
)

# ==================== 智能模型路由 ====================
# 策略：根据 Antigravity 实际支持的模型精确路由
# Antigravity 按 token 计费，Copilot 按次计费（用一次少一次）

# Antigravity 实际支持的模型（精确列表）
# 基于用户提供的信息：
# - Gemini 3 系列: gemini-3-pro (high/low), gemini-3-flash
# - Claude 4.5 系列: claude-sonnet-4.5, claude-sonnet-4.5-thinking, claude-opus-4.5-thinking
# - GPT: gpt-oos-120b (medium)


# ==================== Copilot 模型名称映射 ====================
# Copilot API 需要特定格式的模型ID

COPILOT_MODEL_MAPPING = {
    # Claude Haiku 系列 -> claude-haiku-4.5
    "claude-3-haiku": "claude-haiku-4.5",
    "claude-3.5-haiku": "claude-haiku-4.5",
    "claude-haiku-3": "claude-haiku-4.5",
    "claude-haiku-3.5": "claude-haiku-4.5",
    "claude-haiku": "claude-haiku-4.5",

    # Claude Sonnet 系列
    "claude-3-sonnet": "claude-sonnet-4",
    "claude-3.5-sonnet": "claude-sonnet-4",
    "claude-sonnet-3": "claude-sonnet-4",
    "claude-sonnet-3.5": "claude-sonnet-4",
    "claude-sonnet": "claude-sonnet-4",

    # Claude 4 系列
    "claude-4-sonnet": "claude-sonnet-4",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-4.5-sonnet": "claude-sonnet-4.5",
    "claude-sonnet-4.5": "claude-sonnet-4.5",

    "claude-4-opus": "claude-opus-4.5",
    "claude-opus-4": "claude-opus-4.5",
    "claude-4.5-opus": "claude-opus-4.5",
    "claude-opus-4.5": "claude-opus-4.5",

    "claude-4-haiku": "claude-haiku-4.5",
    "claude-haiku-4": "claude-haiku-4.5",
    "claude-4.5-haiku": "claude-haiku-4.5",
    "claude-haiku-4.5": "claude-haiku-4.5",

    # GPT 系列
    "gpt-4-turbo": "gpt-4-0125-preview",
    "gpt-4-turbo-preview": "gpt-4-0125-preview",
    "gpt-4o-latest": "gpt-4o",
    "gpt-4o-mini-latest": "gpt-4o-mini",

    # Gemini 系列
    "gemini-2.5-pro-latest": "gemini-2.5-pro",
    "gemini-2.5-pro-preview": "gemini-2.5-pro",
    "gemini-3-pro": "gemini-3-pro-high",  # 修复：映射到实际存在的模型
    "gemini-3-pro-preview": "gemini-3-pro-high",  # 修复：映射到实际存在的模型
    "gemini-3-flash": "gemini-3-flash",  # 保持原样
    "gemini-3-flash-preview": "gemini-3-flash",  # 修复：映射到实际存在的模型
}


def map_model_for_copilot(model: str) -> str:
    """
    将模型名称映射为 Copilot API 能识别的格式

    Args:
        model: 原始模型名称

    Returns:
        Copilot 能识别的模型ID
    """
    if not model:
        return "gpt-4o"  # 默认模型

    model_lower = model.lower()

    # 移除常见后缀进行匹配
    base_model = model_lower
    for suffix in ["-thinking", "-think", "-extended", "-preview", "-latest",
                   "-20241022", "-20240620", "-20250101", "-20250514"]:
        base_model = base_model.replace(suffix, "")

    # 移除日期后缀
    base_model = re.sub(r'-\d{8}$', '', base_model).strip("-")

    # 1. 直接匹配原始名称
    if model_lower in COPILOT_MODEL_MAPPING:
        return COPILOT_MODEL_MAPPING[model_lower]

    # 2. 匹配去除后缀的名称
    if base_model in COPILOT_MODEL_MAPPING:
        return COPILOT_MODEL_MAPPING[base_model]

    # 3. 智能模糊匹配 Claude 模型
    if "claude" in model_lower:
        # 检测模型类型
        if "haiku" in model_lower:
            return "claude-haiku-4.5"
        elif "opus" in model_lower:
            return "claude-opus-4.5"
        elif "sonnet" in model_lower:
            # 检查版本号
            if "4.5" in model_lower or "45" in model_lower:
                return "claude-sonnet-4.5"
            else:
                return "claude-sonnet-4"
        else:
            # 默认 Claude -> sonnet
            return "claude-sonnet-4"

    # 4. 智能模糊匹配 GPT 模型
    if "gpt" in model_lower:
        if "5.2" in model_lower:
            return "gpt-5.2"
        elif "5.1" in model_lower:
            if "codex" in model_lower:
                if "mini" in model_lower:
                    return "gpt-5.1-codex-mini"
                elif "max" in model_lower:
                    return "gpt-5.1-codex-max"
                return "gpt-5.1-codex"
            return "gpt-5.1"
        elif "gpt-5" in model_lower or "gpt5" in model_lower:
            if "mini" in model_lower:
                return "gpt-5-mini"
            return "gpt-5"
        elif "4.1" in model_lower or "41" in model_lower:
            return "gpt-4.1"
        elif "4o-mini" in model_lower or "4o mini" in model_lower:
            return "gpt-4o-mini"
        elif "4o" in model_lower:
            return "gpt-4o"
        elif "4-turbo" in model_lower:
            return "gpt-4-0125-preview"
        elif "3.5" in model_lower:
            return "gpt-3.5-turbo"
        else:
            return "gpt-4"

    # 5. 智能模糊匹配 Gemini 模型
    if "gemini" in model_lower:
        if "3" in model_lower:
            if "flash" in model_lower:
                return "gemini-3-flash"  # 修复：使用实际存在的模型名
            return "gemini-3-pro-high"  # 修复：使用实际存在的模型名
        elif "2.5" in model_lower:
            return "gemini-2.5-pro"
        else:
            return "gemini-2.5-pro"  # 默认

    # 6. O1/O3 模型 (如果 Copilot 支持)
    if model_lower.startswith("o1") or model_lower.startswith("o3"):
        # 目前 Copilot 可能不支持，返回原名尝试
        return model

    # 7. 返回原始模型名（可能 Copilot 直接支持）
    return model



ANTIGRAVITY_SUPPORTED_PATTERNS = {
    # Gemini 3 系列 - 只支持 3 系列
    "gemini-3", "gemini3",
    # Claude 4.5 系列 - 只支持 4.5 版本的 sonnet 和 opus
    "claude-sonnet-4.5", "claude-4.5-sonnet", "claude-45-sonnet",
    "claude-opus-4.5", "claude-4.5-opus", "claude-45-opus",
    # GPT OOS
    "gpt-oos",
}

# 用于提取模型核心信息的辅助函数
def normalize_model_name(model: str) -> str:
    """规范化模型名称，移除变体后缀"""
    model_lower = model.lower()

    # 移除常见后缀
    suffixes = [
        "-thinking", "-think", "-extended", "-preview", "-latest",
        "-high", "-low", "-medium",
        "-20241022", "-20240620", "-20250101", "-20250514",
    ]
    for suffix in suffixes:
        model_lower = model_lower.replace(suffix, "")

    # 移除日期后缀
    import re
    model_lower = re.sub(r'-\d{8}$', '', model_lower)

    return model_lower.strip("-")


def is_antigravity_supported(model: str) -> bool:
    """
    检查模型是否被 Antigravity 支持

    Antigravity 支持：
    - Gemini 2.5 系列 (gemini-2.5-pro, gemini-2.5-flash 等)
    - Gemini 3 系列 (gemini-3-pro, gemini-3-flash)
    - Claude 4.5 系列 (sonnet-4.5, opus-4.5, haiku-4.5)
    - GPT OOS 120B

    注意：haiku 模型会被映射到 gemini-3-flash，但仍然走 Antigravity
    """
    import re
    normalized = normalize_model_name(model)
    model_lower = model.lower()

    # 检查 Gemini - 支持 2.5 和 3 系列
    if "gemini" in model_lower:
        # 检查是否是 Gemini 2.5 或 3
        if any(x in normalized for x in ["gemini-2.5", "gemini-2-5", "gemini2.5", "gemini25"]):
            return True
        if any(x in normalized for x in ["gemini-3", "gemini3"]):
            return True
        # 其他 Gemini 版本（2.0, 1.5 等）不支持
        return False

    # 检查 Claude - 支持 4.5 系列的 sonnet, opus, haiku
    if "claude" in model_lower:
        # 检查版本号 4.5 / 4-5
        # 支持格式: claude-sonnet-4.5, claude-4.5-sonnet, claude-opus-4-5-20251101 等
        # 使用正则匹配 4.5 或 4-5 格式
        has_45 = bool(re.search(r'4[.\-]5', normalized))

        # 检查模型类型
        has_sonnet = "sonnet" in normalized
        has_opus = "opus" in normalized
        has_haiku = "haiku" in normalized

        if has_45 and (has_sonnet or has_opus or has_haiku):
            return True

        # 其他 Claude 版本不支持
        return False

    # 检查 GPT OOS
    if "gpt-oos" in model_lower or "gptoos" in model_lower:
        return True

    # 其他模型都不支持
    return False


def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表"""
    enabled_backends = [(k, v) for k, v in BACKENDS.items() if v.get("enabled", True)]
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])


def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端

    路由策略：
    1. 检查是否配置了 Kiro Gateway 路由（优先级最高，用于特定模型）
    2. 检查是否在 Antigravity 支持列表中
    3. 支持 -> Antigravity（按 token 计费，更经济）
    4. 不支持 -> Copilot（按次计费，但支持更多模型）

    Antigravity 支持的模型：
    - Gemini 3 系列: gemini-3-pro, gemini-3-flash
    - Claude 4.5 系列: claude-sonnet-4.5, claude-opus-4.5 (含 thinking 变体)
    - GPT: gpt-oos-120b
    
    Kiro Gateway 路由：
    - 通过环境变量 KIRO_GATEWAY_MODELS 配置
    - 格式：逗号分隔的模型名称列表
    """
    if not model:
        model = ""
    
    model_lower = model.lower()
    
    # 1. 优先检查 Kiro Gateway 路由配置
    if KIRO_GATEWAY_MODELS:
        # 精确匹配
        if model_lower in KIRO_GATEWAY_MODELS:
            log.route(f"Model {model} -> Kiro Gateway (configured)", tag="GATEWAY")
            return "kiro-gateway"
        
        # 模糊匹配（检查模型名是否包含配置的模式）
        normalized_model = normalize_model_name(model)
        for kiro_model in KIRO_GATEWAY_MODELS:
            if normalized_model == kiro_model.lower() or normalized_model.startswith(kiro_model.lower()):
                log.route(f"Model {model} -> Kiro Gateway (pattern match: {kiro_model})", tag="GATEWAY")
                return "kiro-gateway"
    
    # 2. 检查 Antigravity 支持
    if is_antigravity_supported(model):
        log.route(f"Model {model} -> Antigravity", tag="GATEWAY")
        return "antigravity"
    else:
        log.route(f"Model {model} -> Copilot (not in AG list)", tag="GATEWAY")
        return "copilot"


def calculate_retry_delay(attempt: int, config: Dict = None) -> float:
    """
    计算重试延迟时间（指数退避）
    
    Args:
        attempt: 当前重试次数（从0开始）
        config: 重试配置
    
    Returns:
        延迟时间（秒）
    """
    if config is None:
        config = RETRY_CONFIG
    
    base_delay = config.get("base_delay", 1.0)
    max_delay = config.get("max_delay", 10.0)
    exponential_base = config.get("exponential_base", 2)
    
    delay = base_delay * (exponential_base ** attempt)
    return min(delay, max_delay)


def should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    """
    判断是否应该重试
    
    Args:
        status_code: HTTP 状态码
        attempt: 当前重试次数
        max_retries: 最大重试次数
    
    Returns:
        是否应该重试
    """
    if attempt >= max_retries:
        return False
    
    retry_on_status = RETRY_CONFIG.get("retry_on_status", [500, 502, 503, 504])
    return status_code in retry_on_status


async def check_backend_health(backend_key: str) -> bool:
    """检查后端服务健康状态"""
    backend = BACKENDS.get(backend_key)
    if not backend or not backend.get("enabled", True):
        return False

    try:
        async with http_client.get_client(timeout=5.0) as client:
            response = await client.get(f"{backend['base_url']}/models")
            return response.status_code == 200
    except Exception as e:
        log.warning(f"Backend {backend_key} health check failed: {e}")
        return False


async def proxy_request_to_backend(
    backend_key: str,
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    stream: bool = False,
) -> Tuple[bool, Any]:
    """
    代理请求到指定后端（带重试机制）

    Returns:
        Tuple[bool, Any]: (成功标志, 响应内容或错误信息)
    """
    backend = BACKENDS.get(backend_key)
    if not backend:
        return False, f"Backend {backend_key} not found"

    # ==================== 本地 Antigravity：service 直调（避免 127.0.0.1 回环） ====================
    if backend_key == "antigravity" and endpoint == "/chat/completions" and method.upper() == "POST":
        try:
            from src.services.antigravity_service import handle_openai_chat_completions
            resp = await handle_openai_chat_completions(body=body, headers=headers)

            status_code = getattr(resp, "status_code", 200)
            if stream:
                if status_code >= 400:
                    async def error_stream():
                        error_msg = json.dumps({"error": "Backend error", "status": status_code})
                        yield f"data: {error_msg}\n\n"
                    return True, error_stream()

                if isinstance(resp, StarletteStreamingResponse):
                    return True, resp.body_iterator

                # 非预期：流式请求返回了非 StreamingResponse
                return False, f"Backend error: {status_code}"

            # 非流式
            if status_code >= 400:
                return False, f"Backend error: {status_code}"

            resp_body = getattr(resp, "body", b"")
            if isinstance(resp_body, bytes):
                return True, json.loads(resp_body.decode("utf-8", errors="ignore") or "{}")
            if isinstance(resp_body, str):
                return True, json.loads(resp_body or "{}")
            return True, resp_body

        except HTTPException as e:
            if stream:
                status = int(getattr(e, "status_code", 500))

                async def error_stream(status_code: int = status):
                    error_msg = json.dumps({"error": "Backend error", "status": status_code})
                    yield f"data: {error_msg}\n\n"
                return True, error_stream()
            return False, f"Backend error: {e.status_code}"
        except Exception as e:
            log.error(f"Local antigravity service call failed: {e}", tag="GATEWAY")
            if stream:
                msg = str(e)

                async def error_stream(error_message: str = msg):
                    error_msg = json.dumps({"error": error_message})
                    yield f"data: {error_msg}\n\n"
                return True, error_stream()
            return False, str(e)

    # 对 Copilot 后端应用模型名称映射
    if backend_key == "copilot" and body and isinstance(body, dict) and "model" in body:
        original_model = body.get("model", "")
        mapped_model = map_model_for_copilot(original_model)
        if mapped_model != original_model:
            log.route(f"Model mapped: {original_model} -> {mapped_model}", tag="COPILOT")
            body = {**body, "model": mapped_model}

    url = f"{backend['base_url']}{endpoint}"

    # 根据请求类型选择超时时间
    if stream:
        timeout = backend.get("stream_timeout", backend.get("timeout", 300.0))
    else:
        timeout = backend.get("timeout", 60.0)

    # 获取最大重试次数
    max_retries = backend.get("max_retries", RETRY_CONFIG.get("max_retries", 3))

    # 构建请求头
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": headers.get("authorization", "Bearer dummy"),
    }
    # Preserve upstream client identity (important for backend routing/features)
    user_agent = headers.get("user-agent") or headers.get("User-Agent")
    if user_agent:
        request_headers["User-Agent"] = user_agent
        # Keep a copy even if a downstream client overwrites User-Agent
        request_headers["X-Forwarded-User-Agent"] = user_agent

    # Forward a small allowlist of gateway control headers
    for h in (
        "x-augment-client",
        "x-bugment-client",
        "x-augment-request",
        "x-bugment-request",
        # Augment signed-request headers (preserve for downstream logging/compat)
        "x-signature-version",
        "x-signature-timestamp",
        "x-signature-signature",
        "x-signature-vector",
        "x-disable-thinking-signature",
        "x-request-id",
    ):
        v = headers.get(h) or headers.get(h.lower()) or headers.get(h.upper())
        if v:
            request_headers[h] = v

    last_error = None
    last_status_code = None

    for attempt in range(max_retries + 1):  # +1 因为第一次不算重试
        try:
            if attempt > 0:
                delay = calculate_retry_delay(attempt - 1)
                log.warning(f"Retry {attempt}/{max_retries} for {backend_key} after {delay:.1f}s delay")
                await asyncio.sleep(delay)

            if stream:
                # 流式请求（带超时）
                return await proxy_streaming_request_with_timeout(
                    url, method, request_headers, body, timeout, backend_key
                )
            else:
                # 非流式请求
                async with http_client.get_client(timeout=timeout) as client:
                    if method.upper() == "POST":
                        response = await client.post(url, json=body, headers=request_headers)
                    elif method.upper() == "GET":
                        response = await client.get(url, headers=request_headers)
                    else:
                        return False, f"Unsupported method: {method}"

                    last_status_code = response.status_code

                    if response.status_code >= 400:
                        error_text = response.text
                        log.warning(f"Backend {backend_key} returned error {response.status_code}: {error_text[:200]}")

                        # 检查是否应该重试
                        if should_retry(response.status_code, attempt, max_retries):
                            last_error = f"Backend error: {response.status_code}"
                            continue

                        return False, f"Backend error: {response.status_code}"

                    return True, response.json()

        except httpx.TimeoutException:
            log.warning(f"Backend {backend_key} timeout (attempt {attempt + 1}/{max_retries + 1})")
            last_error = "Request timeout"
            if attempt < max_retries:
                continue
        except httpx.ConnectError:
            log.warning(f"Backend {backend_key} connection failed (attempt {attempt + 1}/{max_retries + 1})")
            last_error = "Connection failed"
            if attempt < max_retries:
                continue
        except Exception as e:
            log.error(f"Backend {backend_key} request failed: {e}")
            last_error = str(e)
            # 对于未知错误，不重试
            break

    # 所有重试都失败
    log.error(f"Backend {backend_key} failed after {max_retries + 1} attempts. Last error: {last_error}")
    return False, last_error or "Unknown error"


async def proxy_streaming_request_with_timeout(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout: float,
    backend_key: str = "unknown",
) -> Tuple[bool, Any]:
    """
    处理流式代理请求（带超时和错误处理）

    Args:
        url: 请求URL
        method: HTTP方法
        headers: 请求头
        body: 请求体
        timeout: 超时时间（秒）
        backend_key: 后端标识（用于日志）
    """
    try:
        import asyncio

        # 创建带超时的客户端
        timeout_config = httpx.Timeout(
            connect=30.0,      # 连接超时
            read=timeout,      # 读取超时（流式数据）
            write=30.0,        # 写入超时
            pool=30.0,         # 连接池超时
        )
        client = httpx.AsyncClient(timeout=timeout_config)

        async def stream_generator():
            # 注意：chunk_timeout 检查已移除
            # 原因：之前的逻辑是在收到 chunk 后才检查时间差，这是错误的。
            # 当模型需要长时间思考（如 Claude 写长文档）时，两个 chunk 之间可能超过 120 秒，
            # 但只要最终收到了数据，就不应该超时。
            # httpx 的 read=timeout 配置已经处理了真正的读取超时。


            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"Streaming request to {backend_key} failed: {response.status_code}")
                        error_msg = json.dumps({'error': 'Backend error', 'status': response.status_code})
                        yield f"data: {error_msg}\n\n"
                        return

                    log.success(f"Streaming started", tag=backend_key.upper())

                    yielded_any = False
                    saw_done = False
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yielded_any = True
                            if b"[DONE]" in chunk:
                                saw_done = True
                            yield chunk.decode("utf-8", errors="ignore")

                    log.success(f"Streaming completed", tag=backend_key.upper())

            except httpx.ReadTimeout:
                log.warning(f"Read timeout from {backend_key} after {timeout}s")
                error_msg = json.dumps({'error': 'Read timeout', 'message': f'No response within {timeout}s'})
                yield f"data: {error_msg}\n\n"
            except httpx.ConnectTimeout:
                log.warning(f"Connect timeout to {backend_key}")
                error_msg = json.dumps({'error': 'Connect timeout'})
                yield f"data: {error_msg}\n\n"
            except httpx.RemoteProtocolError as e:
                # Some upstreams (notably enterprise proxies) may close a chunked response
                # without a proper terminating chunk, even though the client has already
                # received the semantic end marker (e.g. SSE "[DONE]").
                #
                # If we already forwarded any bytes (or saw "[DONE]"), treat this as a
                # benign end-of-stream to avoid breaking Bugment parsers and spamming logs.
                try:
                    if "incomplete chunked read" in str(e).lower():
                        if "saw_done" in locals() and (saw_done or yielded_any):
                            log.warning(
                                f"Ignoring benign upstream RemoteProtocolError after completion: {e}",
                                tag=backend_key.upper(),
                            )
                            return
                except Exception:
                    pass
                log.error(f"Streaming protocol error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            except asyncio.CancelledError:
                # Downstream client disconnected/cancelled (common for prompt enhancer or UI refresh).
                # Stop consuming the upstream stream quietly.
                return
            except Exception as e:
                log.error(f"Streaming error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            finally:
                try:
                    await client.aclose()
                except Exception:
                    # Avoid noisy event-loop "connection_lost" traces on Windows Proactor when the
                    # peer has already reset the connection.
                    pass

        return True, stream_generator()

    except Exception as e:
        log.error(f"Failed to start streaming from {backend_key}: {e}")
        return False, str(e)


async def proxy_streaming_request(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout: float,
) -> Tuple[bool, Any]:
    """处理流式代理请求（兼容旧接口）"""
    try:
        import asyncio

        client = httpx.AsyncClient(timeout=None)

        async def stream_generator():
            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"Streaming request failed: {response.status_code}")
                        error_msg = json.dumps({'error': 'Backend error', 'status': response.status_code})
                        yield f"data: {error_msg}\n\n"
                        return

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode("utf-8", errors="ignore")
            except httpx.RemoteProtocolError as e:
                # See proxy_streaming_request_with_timeout() for rationale.
                if "incomplete chunked read" in str(e).lower():
                    return
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            except asyncio.CancelledError:
                return
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass

        return True, stream_generator()

    except Exception as e:
        log.error(f"Streaming request failed: {e}")
        return False, str(e)


async def route_request_with_fallback(
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    model: Optional[str] = None,
    stream: bool = False,
) -> Any:
    """
    带故障转移的请求路由

    优先使用指定后端，失败时自动切换到备用后端
    """
    # 确定后端顺序
    specified_backend = get_backend_for_model(model) if model else None
    sorted_backends = get_sorted_backends()

    if specified_backend:
        # 将指定后端移到最前面
        sorted_backends = [(k, v) for k, v in sorted_backends if k == specified_backend] + \
                         [(k, v) for k, v in sorted_backends if k != specified_backend]

    last_error = None

    for backend_key, backend_config in sorted_backends:
        log.info(f"Trying backend: {backend_config['name']} for {endpoint}")

        success, result = await proxy_request_to_backend(
            backend_key, endpoint, method, headers, body, stream
        )

        if success:
            log.success(f"Request succeeded via {backend_config['name']}", tag="GATEWAY")
            return result

        last_error = result
        log.warning(f"Backend {backend_config['name']} failed: {result}, trying next...")

    # 所有后端都失败
    raise HTTPException(
        status_code=503,
        detail=f"All backends failed. Last error: {last_error}"
    )


# ==================== API 端点 ====================


@router.get("/v1/models")
@router.get("/models")  # 别名路由，兼容不同客户端配置
async def list_models(request: Request):
    """获取所有后端的模型列表（合并去重）"""
    log.debug(f"Models request received", tag="GATEWAY")
    all_models = set()

    for backend_key, backend_config in get_sorted_backends():
        try:
            async with http_client.get_client(timeout=10.0) as client:
                response = await client.get(
                    f"{backend_config['base_url']}/models",
                    headers={"Authorization": "Bearer dummy"}
                )
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("data", [])
                    for model in models:
                        model_id = model.get("id") if isinstance(model, dict) else model
                        if model_id:
                            all_models.add(model_id)
        except Exception as e:
            log.warning(f"Failed to get models from {backend_key}: {e}")

    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "owned_by": "gateway"} for m in sorted(all_models)]
    }


@router.get("/usage/api/get-models")  # Augment Code 兼容路由 - 返回对象数组
@router.get("/v1/usage/api/get-models")  # Augment Code 兼容路由（带版本号）- 返回对象数组
async def list_models_for_augment(request: Request):
    """获取所有后端的模型列表（合并去重）- Augment Code 格式（对象数组）"""
    log.debug(f"Augment models request received from {request.url.path}", tag="GATEWAY")
    # 使用字典存储模型信息，key 是 model_id，value 是完整的模型对象
    all_models_dict = {}

    for backend_key, backend_config in get_sorted_backends():
        try:
            async with http_client.get_client(timeout=10.0) as client:
                response = await client.get(
                    f"{backend_config['base_url']}/models",
                    headers={"Authorization": "Bearer dummy"}
                )
                if response.status_code == 200:
                    data = response.json()
                    log.debug(f"Backend {backend_key} response: {data}", tag="GATEWAY")
                    models = data.get("data", [])
                    for model in models:
                        if isinstance(model, dict):
                            model_id = model.get("id", "")
                            if model_id:
                                # 保留完整的模型信息，如果已存在则合并（优先保留更详细的信息）
                                if model_id not in all_models_dict or len(str(model)) > len(str(all_models_dict[model_id])):
                                    all_models_dict[model_id] = model
                                log.debug(f"Added model: {model_id} from {backend_key}", tag="GATEWAY")
                        elif isinstance(model, str):
                            # 如果上游返回的是字符串，创建基本对象
                            if model not in all_models_dict:
                                all_models_dict[model] = {"id": model}
        except Exception as e:
            log.warning(f"Failed to get models from {backend_key}: {e}")

    # Augment Code 期望返回对象数组，每个对象包含 id、name、displayName 等字段
    model_list = []
    for model_id in sorted(all_models_dict.keys()):
        model_info = all_models_dict[model_id]
        # 构造 Augment Code 期望的格式
        augment_model = {
            "id": model_id,
            "name": model_info.get("name", model_id),
            "displayName": model_info.get("display_name") or model_info.get("displayName") or model_id,
        }
        # 保留其他可能有用的字段
        if "object" in model_info:
            augment_model["object"] = model_info["object"]
        if "owned_by" in model_info:
            augment_model["owned_by"] = model_info["owned_by"]
        if "type" in model_info:
            augment_model["type"] = model_info["type"]
        
        model_list.append(augment_model)
    
    log.debug(f"Returning {len(model_list)} models", tag="GATEWAY")
    return model_list


def _build_bugment_get_models_result() -> Dict[str, Any]:
    """
    Build an Augment-compatible `get-models` response (BackGetModelsResult).

    Bugment/VSCode uses this endpoint to populate:
    - default model
    - available models
    - feature flags (tasklist, prompt enhancer, model registry, etc.)

    Keep the payload minimal but schema-correct to avoid breaking client parsing.
    """
    # Use a stable default. The UI-selected model can override this, but the backend response
    # must provide a non-empty `default_model` or the client will fall back unpredictably.
    default_model = "gpt-4.1"

    # Provide a conservative, non-empty list for clients that expect at least one model.
    # Bugment can still load a richer model registry via `/usage/api/get-models`.
    models: List[Dict[str, Any]] = [
        {
            "name": model_id,
            "suggested_prefix_char_count": 8000,
            "suggested_suffix_char_count": 2000,
            "completion_timeout_ms": 120_000,
            "internal_name": model_id,
        }
        for model_id in ["gpt-4.1", "gpt-4", "gpt-5.1"]
    ]

    # Minimal feature flags needed to restore core agent behaviors.
    # Include both camelCase and snake_case variants for compatibility across client builds.
    feature_flags: Dict[str, Any] = {
        # Prompt enhancer
        "enablePromptEnhancer": True,
        "enable_prompt_enhancer": True,
        # Model registry (used by model selector + prompt enhancer model resolution)
        "enableModelRegistry": True,
        "enable_model_registry": True,
        # Agent auto mode & task list (enables task root initialization)
        "enableAgentAutoMode": True,
        "enable_agent_auto_mode": True,
        "vscodeTaskListMinVersion": "0.482.0",
        "vscode_task_list_min_version": "0.482.0",
        "vscodeSupportToolUseStartMinVersion": "0.485.0",
        "vscode_support_tool_use_start_min_version": "0.485.0",
    }

    return {
        "default_model": default_model,
        "models": models,
        # Do not include `languages` to allow the client to use its built-in defaults.
        "feature_flags": feature_flags,
        "user_tier": "COMMUNITY_TIER",
        "user": {"id": "local"},
    }


@router.post("/get-models")
@router.post("/v1/get-models")
async def get_models_for_bugment(request: Request, token: str = Depends(authenticate_bearer_allow_local_dummy)):
    """Bugment/VSCode: returns BackGetModelsResult (POST /get-models)."""
    log.debug(f"Bugment get-models request received from {request.url.path}", tag="GATEWAY")
    return _build_bugment_get_models_result()


@router.post("/bugment/conversation/set-model")
@router.post("/v1/bugment/conversation/set-model")
async def bugment_conversation_set_model(request: Request, token: str = Depends(authenticate_bearer_allow_local_dummy)):
    """
    Bugment helper: proactively bind the currently selected model to a conversation.

    Why:
    - Before the first user message is sent, some internal requests (e.g. prompt enhancer)
      may be issued with `model: ""`.
    - We intentionally removed the hardcoded `gpt-4` fallback to avoid conflicts with the UI model.
    - This endpoint lets the extension sync the selected model immediately on model change,
      so follow-up requests with missing model can use conversation fallback deterministically.
    """
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload: expected JSON object")

    conversation_id = payload.get("conversation_id") or payload.get("conversationId")
    model = payload.get("model")
    if isinstance(model, str):
        model = model.strip()

    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise HTTPException(status_code=400, detail="Missing required field: conversation_id")
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    _bugment_conversation_state_put(conversation_id.strip(), model=model)
    log.info(
        f"Bugment conversation model updated: conversation_id={conversation_id.strip()} model={model}",
        tag="GATEWAY",
    )
    return {"success": True}


@router.post("/v1/chat/completions")
@router.post("/chat/completions")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """统一聊天完成端点 - 自动路由到最佳后端"""
    log.info(f"Chat request received", tag="GATEWAY")
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # DEBUG: Log incoming messages to diagnose tool call issues
    raw_messages = raw_body.get("messages", [])
    log.debug(f" Incoming messages count: {len(raw_messages)}")
    for i, msg in enumerate(raw_messages[-5:]):  # Only log last 5 messages
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            has_content = "content" in msg and msg["content"] is not None
            has_tool_calls = "tool_calls" in msg
            tool_call_id = msg.get("tool_call_id", None)
            log.debug(f" Message {i}: role={role}, has_content={has_content}, has_tool_calls={has_tool_calls}, tool_call_id={tool_call_id}")
            if role == "tool":
                log.debug(f" Tool result message: {json.dumps(msg, ensure_ascii=False)[:500]}")
            if role == "assistant" and has_tool_calls:
                log.debug(f" Assistant tool_calls: {json.dumps(msg.get('tool_calls', []), ensure_ascii=False)[:500]}")

    # Normalize request body to standard OpenAI format
    body = normalize_request_body(raw_body)

    model = body.get("model", "")
    stream = body.get("stream", False)

    headers = dict(request.headers)

    result = await route_request_with_fallback(
        endpoint="/chat/completions",
        method="POST",
        headers=headers,
        body=body,
        model=model,
        stream=stream,
    )

    if stream and hasattr(result, "__anext__"):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    return JSONResponse(content=result)


async def convert_sse_to_augment_ndjson(sse_stream: AsyncGenerator) -> AsyncGenerator[str, None]:
    """
    将 SSE 格式流转换为 Augment Code 期望的 NDJSON 格式流
    
    OpenAI SSE 格式: data: {"choices":[{"delta":{"content":"你好"}}]}\n\n
    Augment NDJSON 格式: {"text":"你好"}\n
    
    Args:
        sse_stream: SSE 格式的异步生成器（可能返回 bytes 或 str）
        
    Yields:
        Augment NDJSON 格式的字符串（每行一个 {"text": "..."} 对象）
    """
    buffer = ""
    
    async for chunk in sse_stream:
        if not chunk:
            continue
        
        # 处理字节类型，转换为字符串
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="ignore")
        elif not isinstance(chunk, str):
            chunk = str(chunk)
            
        # 将 chunk 添加到缓冲区
        buffer += chunk
        
        # 按行处理缓冲区（SSE 格式以 \n\n 分隔事件）
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 检查是否是 SSE 格式的 data: 行
            if line.startswith("data: "):
                # 提取 JSON 数据
                json_str = line[6:].strip()  # 移除 "data: " 前缀
                
                # 跳过 [DONE] 标记
                if json_str == "[DONE]":
                    continue
                
                # 验证是否是有效的 JSON
                try:
                    # 解析 OpenAI 格式的 JSON
                    json_obj = json.loads(json_str)
                    
                    # 提取 content 字段转换为 Augment 格式
                    # OpenAI: {"choices":[{"delta":{"content":"xxx"}}]}
                    # Augment: {"text":"xxx"}
                    if "choices" in json_obj and len(json_obj["choices"]) > 0:
                        choice = json_obj["choices"][0]
                        
                        # 处理流式响应的 delta
                        if "delta" in choice:
                            delta = choice["delta"]

                            # NOTE:
                            # When upstream chooses to call tools, OpenAI streaming returns `delta.tool_calls`
                            # (often with no `delta.content`). If we drop these deltas, the VSCode client will
                            # look like it "ended immediately" when a tool is attempted.
                            tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                            if isinstance(tool_calls, list) and tool_calls:
                                try:
                                    log.warning(
                                        f"[TOOL CALL] Upstream returned tool_calls (count={len(tool_calls)}), "
                                        f"first={json.dumps(tool_calls[0], ensure_ascii=False)[:500]}",
                                        tag="GATEWAY",
                                    )
                                except Exception:
                                    log.warning("[TOOL CALL] Upstream returned tool_calls (unable to dump)", tag="GATEWAY")

                                # Emit a visible message so the user isn't left with an empty response.
                                augment_obj = {
                                    "text": (
                                        "\n[Gateway] 上游模型触发了工具调用(tool_calls)，但当前网关尚未实现将 tool_calls "
                                        "转换/执行为 Augment 工具链的逻辑，因此工具步骤无法继续。"
                                    )
                                }
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"

                            if "content" in delta and delta["content"] is not None:
                                augment_obj = {"text": delta["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"
                        
                        # 处理完整响应的 message
                        elif "message" in choice:
                            message = choice["message"]
                            if "content" in message and message["content"] is not None:
                                augment_obj = {"text": message["content"]}
                                yield json.dumps(augment_obj, separators=(',', ':'), ensure_ascii=False) + "\n"
                        
                        # 处理 finish_reason
                        if "finish_reason" in choice and choice["finish_reason"] is not None:
                            # Augment 不需要 finish_reason，跳过
                            if choice["finish_reason"] in ("tool_calls", "function_call"):
                                log.warning(f"[TOOL CALL] finish_reason={choice['finish_reason']}", tag="GATEWAY")
                            continue
                    
                except json.JSONDecodeError:
                    # 如果不是有效的 JSON，记录警告但继续处理
                    log.warning(f"Invalid JSON in SSE stream: {json_str[:100]}")
                    continue
            elif line.startswith(":"):
                # SSE 注释行，跳过
                continue
            elif line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                # 其他 SSE 字段，跳过
                continue


@router.post("/chat-stream")
async def chat_stream(
    request: Request,
    token: str = Depends(authenticate_bearer_allow_local_dummy)
):
    """
    Augment Code 兼容路由：统一聊天流式端点（NDJSON 格式）
    
    - 路径：/gateway/chat-stream（因为 router 前缀为 /gateway）
    - 功能：等价于 /chat/completions 且强制开启 stream 模式
    - 格式：返回 NDJSON 格式（每行一个 JSON 对象），而非 SSE 格式
    """
    log.info(f"Chat stream request received (Augment NDJSON format)", tag="GATEWAY")
    
    # DEBUG: 先读取原始字节，看看真实的请求体
    raw_bytes = await request.body()
    log.info(f"Raw bytes length: {len(raw_bytes)}", tag="GATEWAY")
    log.info(f"Raw bytes preview (first 2000 chars):\n{raw_bytes[:2000].decode('utf-8', errors='ignore')}", tag="GATEWAY")
    
    # 解析 JSON
    try:
        import json as json_module
        raw_body = json_module.loads(raw_bytes.decode('utf-8'))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # ------------------------------------------------------------------
    # Conversation-scoped state (model + chat history)
    #
    # Bugment may send some internal requests (notably prompt enhancer) with:
    # - model: "" (empty)
    # - chat_history: [] (empty)
    #
    # To avoid falling back to a hardcoded default model and to keep prompt enhancer context-aware,
    # reuse the most recent model/chat_history seen for the same conversation_id.
    # ------------------------------------------------------------------
    try:
        conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None
        if isinstance(raw_body, dict):
            msg = raw_body.get("message")
            model_field = raw_body.get("model")
            chat_history_field = raw_body.get("chat_history")

            if isinstance(chat_history_field, list) and chat_history_field:
                _bugment_conversation_state_put(conversation_id, chat_history=chat_history_field)

            if isinstance(model_field, str) and model_field.strip():
                _bugment_conversation_state_put(conversation_id, model=model_field.strip())
            else:
                state = _bugment_conversation_state_get(conversation_id)
                fallback_model = state.get("model")
                if isinstance(fallback_model, str) and fallback_model.strip():
                    raw_body["model"] = fallback_model.strip()

            # Prompt enhancer requests usually embed "NO TOOLS ALLOWED" and (currently) omit chat_history.
            # Restore chat_history so the enhancer can actually consider context as instructed.
            if (not isinstance(chat_history_field, list) or not chat_history_field) and isinstance(msg, str) and "NO TOOLS ALLOWED" in msg:
                state = _bugment_conversation_state_get(conversation_id)
                fallback_history = state.get("chat_history")
                if isinstance(fallback_history, list) and fallback_history:
                    raw_body["chat_history"] = fallback_history
    except Exception as e:
        log.warning(f"Failed to apply Bugment conversation state: {e}", tag="GATEWAY")

    # ------------------------------------------------------------------
    # Optional short-circuit: tool_result continuation (debug/mock)
    #
    # Default: disabled.
    # When enabled, it echoes tool output as NDJSON to avoid UI hang if upstream can't continue.
    # This is NOT correct for a real agent/tool-loop because it bypasses the model continuation step.
    # ------------------------------------------------------------------
    if BUGMENT_TOOL_RESULT_SHORTCIRCUIT_ENABLED:
        try:
            nodes = raw_body.get("nodes") if isinstance(raw_body, dict) else None
            message = raw_body.get("message") if isinstance(raw_body, dict) else None
            if isinstance(nodes, list) and any(
                isinstance(n, dict)
                and n.get("type") == 1
                and isinstance(n.get("tool_result_node"), dict)
                for n in nodes
            ):
                if message is None or (isinstance(message, str) and message.strip() == ""):
                    tool_contents = []
                    for n in nodes:
                        trn = n.get("tool_result_node") if isinstance(n, dict) else None
                        if not isinstance(trn, dict):
                            continue
                        content = trn.get("content")
                        if isinstance(content, str) and content.strip():
                            tool_contents.append(content)

                    tool_text = tool_contents[0] if tool_contents else ""
                    if isinstance(tool_text, str) and tool_text.strip():
                        # Some tool outputs are JSON strings like {"text": "...", "images":[...]}
                        try:
                            parsed = json_module.loads(tool_text)
                            if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                                tool_text = parsed["text"]
                        except Exception:
                            pass

                        async def _tool_result_shortcircuit():
                            yield json.dumps(
                                {"text": tool_text},
                                separators=(",", ":"),
                                ensure_ascii=False,
                            ) + "\n"

                        log.info(
                            f"[TOOL_RESULT] short-circuit chat-stream (len={len(tool_text)})",
                            tag="GATEWAY",
                        )
                        return StreamingResponse(
                            _tool_result_shortcircuit(),
                            media_type="application/x-ndjson",
                        )
        except Exception as e:
            log.warning(f"[TOOL_RESULT] short-circuit failed: {e}", tag="GATEWAY")

    # ------------------------------------------------------------------
    # Short-circuit: Augment internal message-analysis/memory requests
    #
    # Some Augment flows send a second-stage CHAT request that replaces the user's message with an
    # "ENTER MESSAGE ANALYSIS MODE ... ONLY JSON" prompt and then JSON.parse() the model output.
    # If that request is routed to a thinking model/backend, any <think> or markdown fences can
    # break JSON parsing in the client.
    #
    # To keep the external NDJSON protocol unchanged and avoid affecting real AGENT/tool requests,
    # we detect this internal analysis prompt and return a deterministic JSON response locally.
    # ------------------------------------------------------------------
    try:
        msg = raw_body.get("message") if isinstance(raw_body, dict) else None
        mode = raw_body.get("mode") if isinstance(raw_body, dict) else None
        msg_str = msg if isinstance(msg, str) else ""
        mode_str = mode.strip().upper() if isinstance(mode, str) else ""
        is_message_analysis = (
            mode_str == "CHAT"
            and (
                ("ENTER MESSAGE ANALYSIS MODE" in msg_str)
                or ("ONLY JSON" in msg_str and "worthRemembering" in msg_str)
                or ("worthRemembering" in msg_str and "Return JSON" in msg_str)
            )
        )
        if is_message_analysis:
            analysis_result = {"explanation": "", "worthRemembering": False, "content": ""}
            json_text = json.dumps(analysis_result, ensure_ascii=False, separators=(",", ":"))
            ndjson_line = json.dumps({"text": json_text}, ensure_ascii=False, separators=(",", ":")) + "\n"
            return StreamingResponse(
                iter([ndjson_line]),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
    except Exception as e:
        log.warning(f"Failed to short-circuit message-analysis request: {e}", tag="GATEWAY")

    # DEBUG: 记录原始请求中的模型字段
    log.info(f"Raw request model field: '{raw_body.get('model')}' (type: {type(raw_body.get('model')).__name__})", tag="GATEWAY")
    log.info(f"Raw request keys: {list(raw_body.keys())}", tag="GATEWAY")
    # 检查常见的模型字段位置
    for key in ['model', 'model_name', 'model_id', 'third_party_override', 'mode', 'persona_type']:
        if key in raw_body:
            log.info(f"Found '{key}': {raw_body[key]}", tag="GATEWAY")

    # Normalize to OpenAI format (messages/tools/etc). For /chat-stream we run a server-side tool loop
    # because the client-side Augment NDJSON protocol does not carry OpenAI `tool_calls`.
    conversation_id = raw_body.get("conversation_id") if isinstance(raw_body, dict) else None
    body = normalize_request_body(raw_body, preserve_extra_fields=False)

    # 获取模型名称并记录
    model = body.get("model")
    log.info(f"Model after normalization: '{model}' (type: {type(model).__name__})", tag="GATEWAY")
    
    # 确保 model 字段不为 None 或空字符串
    if model is None or model == "" or (isinstance(model, str) and model.strip() == ""):
        state = _bugment_conversation_state_get(conversation_id)
        fallback_model = state.get("model")
        if isinstance(fallback_model, str) and fallback_model.strip():
            model = fallback_model.strip()
            body["model"] = model
            log.warning(f"Model was None/empty; using conversation model fallback: {model}", tag="GATEWAY")
        else:
            raise HTTPException(
                status_code=400,
                detail="Missing required field: model (Bugment should send the currently selected model).",
            )

    headers = dict(request.headers)

    # Only apply the "disable thinking/signature-cache" bypass for Augment/Bugment-originated requests.
    # We detect this via explicit Augment/Bugment headers, or Augment's signed-request headers on chat-stream.
    lower_header_keys = {k.lower() for k in headers.keys()}
    is_augment_request = (
        ("x-augment-client" in lower_header_keys)
        or ("x-bugment-client" in lower_header_keys)
        or ("x-augment-request" in lower_header_keys)
        or ("x-bugment-request" in lower_header_keys)
        or ("x-signature-version" in lower_header_keys)
        or ("x-signature-vector" in lower_header_keys)
        or ("x-signature-signature" in lower_header_keys)
    )
    if is_augment_request:
        # Preserve caller-provided marker if present; otherwise set a reasonable default.
        if "x-augment-client" not in lower_header_keys and "x-bugment-client" not in lower_header_keys:
            headers.setdefault("x-augment-client", "augment")

        # Bugment uses CHAT-mode requests for internal JSON parsing workflows (prompt enhancer, message analysis).
        # Thinking output (<think>/signature-carrying blocks) can break those client-side parsers.
        # Only disable thinking/signature-cache for CHAT-mode; keep AGENT-mode thinking intact.
        raw_mode = raw_body.get("mode") if isinstance(raw_body, dict) else None
        mode_str = raw_mode.strip().upper() if isinstance(raw_mode, str) else ""
        if mode_str == "CHAT":
            headers.setdefault("x-disable-thinking-signature", "1")

    try:
        # Prefer Augment-compatible client-side tool loop when available.
        if AUGMENT_COMPAT_AVAILABLE:
            ndjson_stream = stream_openai_with_nodes_bridge(headers=headers, raw_body=raw_body, model=model)
        else:
            # Legacy fallback (server-side tool loop; client will not see TOOL_USE nodes)
            ndjson_stream = stream_openai_with_tool_loop(headers=headers, body=body, model=model)
        return StreamingResponse(
            ndjson_stream,
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException as e:
        # HTTP 异常转换为 NDJSON 格式的错误响应
        # Augment 期望的错误格式：{"error": {"message": "...", "type": "...", "code": ...}}
        error_obj = {
            "error": {
                "message": str(e.detail) if e.detail else "Request failed",
                "type": "api_error",
                "code": e.status_code
            }
        }
        error_ndjson = json.dumps(error_obj, separators=(',', ':')) + "\n"
        return StreamingResponse(
            iter([error_ndjson]),
            media_type="application/x-ndjson",
            status_code=e.status_code,
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    except Exception as e:
        # 其他异常也转换为 NDJSON 格式
        log.error(f"Unexpected error in chat_stream: {e}", tag="GATEWAY")
        error_obj = {
            "error": {
                "message": str(e),
                "type": "internal_error",
                "code": 500
            }
        }
        error_ndjson = json.dumps(error_obj, separators=(',', ':')) + "\n"
        return StreamingResponse(
            iter([error_ndjson]),
            media_type="application/x-ndjson",
            status_code=500,
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )


# ==================== Augment Agent Tools Compatibility ====================
# The Augment VSCode extension expects these endpoints when tool calling is enabled.
# If they are missing, the client can abort a conversation as soon as a tool is attempted.


@router.post("/agents/check-tool-safety")
async def agents_check_tool_safety(
    request: Request,
    token: str = Depends(authenticate_bearer_allow_local_dummy),
):
    """
    Minimal compatibility endpoint for Augment tool safety checks.

    Expected request:
      { "tool_id": <int>, "tool_input_json": "<json string>" }

    Expected response:
      { "is_safe": true/false }
    """
    try:
        await request.json()
    except Exception:
        # Even if payload parsing fails, default to safe to avoid hard failures in the client.
        return JSONResponse(content={"is_safe": True})

    return JSONResponse(content={"is_safe": True})


@router.post("/agents/run-remote-tool")
async def agents_run_remote_tool(
    request: Request,
    token: str = Depends(authenticate_bearer_allow_local_dummy),
):
    """
    Mock compatibility endpoint for Augment remote tool execution.

    Returns a mock SUCCESS response (status=1) to test if the Augment client
    can be tricked into thinking the tool executed successfully.

    Expected response keys (as used by the extension):
      - tool_output: The tool's output content
      - tool_result_message: Human-readable result message
      - status: 1 = success, other values = error
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            content={
                "tool_output": "",
                "tool_result_message": f"Invalid JSON: {e}",
                "status": 0,  # Error status
            },
            status_code=400,
        )

    tool_name = payload.get("tool_name") or payload.get("toolName") or "unknown"
    tool_id = payload.get("tool_id") if "tool_id" in payload else payload.get("toolId")
    tool_input_json = payload.get("tool_input_json") or payload.get("toolInputJson") or ""

    # Parse tool input for logging
    tool_input = {}
    if isinstance(tool_input_json, str) and tool_input_json.strip():
        try:
            tool_input = json.loads(tool_input_json)
        except Exception:
            pass

    log.info(
        f"[AGENTS] run-remote-tool MOCK SUCCESS - "
        f"tool_name={tool_name}, tool_id={tool_id}, input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else 'N/A'}",
        tag="GATEWAY",
    )

    # Generate mock tool output based on tool name
    mock_output = _generate_mock_tool_output(tool_name, tool_input)

    return JSONResponse(
        content={
            "tool_output": mock_output,
            "tool_result_message": f"[MOCK] Tool '{tool_name}' executed successfully (Gateway mock response)",
            "status": 1,  # SUCCESS status!
        }
    )


def _generate_mock_tool_output(tool_name: str, tool_input: dict) -> str:
    """
    Generate mock tool output based on tool name and input.
    This helps test the protocol flow without real tool execution.
    """
    tool_name_lower = tool_name.lower()

    # File reading tools
    if "read" in tool_name_lower or "file" in tool_name_lower:
        path = tool_input.get("path") or tool_input.get("file_path") or "unknown_file"
        return f"[MOCK] Content of '{path}':\n# This is mock file content\n# Real file reading is not implemented in Gateway"

    # Search/grep tools
    if "search" in tool_name_lower or "grep" in tool_name_lower:
        pattern = tool_input.get("pattern") or tool_input.get("query") or "pattern"
        return f"[MOCK] Search results for '{pattern}':\nNo matches found (mock response)"

    # Shell/command tools
    if "shell" in tool_name_lower or "command" in tool_name_lower or "bash" in tool_name_lower:
        command = tool_input.get("command") or tool_input.get("cmd") or "unknown command"
        return f"[MOCK] Command output for '{command}':\n$ {command}\n(mock: command not actually executed)"

    # List/directory tools
    if "list" in tool_name_lower or "dir" in tool_name_lower:
        path = tool_input.get("path") or tool_input.get("directory") or "."
        return f"[MOCK] Directory listing for '{path}':\nfile1.txt\nfile2.py\nsubdir/\n(mock response)"

    # Write tools
    if "write" in tool_name_lower or "create" in tool_name_lower:
        path = tool_input.get("path") or tool_input.get("file_path") or "unknown_file"
        return f"[MOCK] Successfully wrote to '{path}' (mock - no actual file written)"

    # Default mock output
    return f"[MOCK] Tool '{tool_name}' executed with input: {json.dumps(tool_input, ensure_ascii=False)[:500]}"


@router.post("/v1/messages")
@router.post("/messages")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def anthropic_messages(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """Anthropic Messages API 兼容端点"""
    log.info(f"Messages request received", tag="GATEWAY")
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    model = body.get("model", "")
    stream = body.get("stream", False)

    headers = dict(request.headers)

    result = await route_request_with_fallback(
        endpoint="/messages",
        method="POST",
        headers=headers,
        body=body,
        model=model,
        stream=stream,
    )

    if stream and hasattr(result, "__anext__"):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    return JSONResponse(content=result)


@router.post("/v1/messages/count_tokens")
@router.post("/messages/count_tokens")  # 别名路由，兼容 Base URL 为 /gateway 的客户端
async def anthropic_messages_count_tokens(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    Anthropic Messages API 兼容的 token 计数端点。

    Claude CLI 在执行 /context 命令时会调用此端点来统计 token 使用量。
    这是一个辅助端点，不消耗配额，只返回估算的 token 数量。
    """
    log.info(f"Count tokens request received", tag="GATEWAY")

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # 简单估算 token 数量
    input_tokens = 0

    try:
        messages = body.get("messages", [])
        system_prompt = body.get("system", "")

        # 粗略估算：每4个字符约等于1个token（对于混合中英文）
        total_chars = len(system_prompt) if isinstance(system_prompt, str) else 0

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 多模态内容
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total_chars += len(item.get("text", ""))
                        elif item.get("type") == "image":
                            # 图片大约消耗 1000 tokens
                            total_chars += 4000

        # 粗略估算
        input_tokens = max(1, total_chars // 4)

    except Exception as e:
        log.warning(f"Token estimation failed: {e}", tag="GATEWAY")
        input_tokens = 100  # 默认值

    log.debug(f"Estimated input tokens: {input_tokens}", tag="GATEWAY")

    return JSONResponse(content={"input_tokens": input_tokens})


@router.get("/health")
async def gateway_health():
    """网关健康检查 - 返回所有后端状态"""
    backend_status = {}

    for backend_key, backend_config in BACKENDS.items():
        is_healthy = await check_backend_health(backend_key)
        backend_status[backend_key] = {
            "name": backend_config["name"],
            "url": backend_config["base_url"],
            "priority": backend_config["priority"],
            "enabled": backend_config.get("enabled", True),
            "healthy": is_healthy,
        }

    all_healthy = any(s["healthy"] for s in backend_status.values())

    return {
        "status": "healthy" if all_healthy else "degraded",
        "backends": backend_status,
        "timestamp": time.time(),
    }


@router.post("/config/backend/{backend_key}/toggle")
async def toggle_backend(
    backend_key: str,
    token: str = Depends(authenticate_bearer)
):
    """启用/禁用指定后端"""
    if backend_key not in BACKENDS:
        raise HTTPException(status_code=404, detail=f"Backend {backend_key} not found")

    BACKENDS[backend_key]["enabled"] = not BACKENDS[backend_key].get("enabled", True)

    return {
        "backend": backend_key,
        "enabled": BACKENDS[backend_key]["enabled"],
    }


# ==================== Augment Code 兼容路由 ====================
# 创建不带 /gateway 前缀的路由器，用于处理 augment code 的请求

augment_router = APIRouter(tags=["Augment Code Compatibility"])


@augment_router.get("/usage/api/get-models")
async def augment_list_models(request: Request):
    """Augment Code 兼容路由：获取模型列表（不带 /gateway 前缀）- 返回对象数组"""
    # 调用 Augment Code 格式的模型列表函数
    return await list_models_for_augment(request)


@augment_router.post("/get-models")
@augment_router.post("/v1/get-models")
async def augment_get_models_for_bugment(request: Request, token: str = Depends(authenticate_bearer_allow_local_dummy)):
    """Bugment/VSCode: returns BackGetModelsResult (POST /get-models) without /gateway prefix."""
    log.debug(f"Bugment get-models request received from {request.url.path}", tag="GATEWAY")
    return _build_bugment_get_models_result()


@augment_router.post("/bugment/conversation/set-model")
@augment_router.post("/v1/bugment/conversation/set-model")
async def augment_bugment_conversation_set_model(
    request: Request, token: str = Depends(authenticate_bearer_allow_local_dummy)
):
    """Same as `/gateway/bugment/conversation/set-model`, but without `/gateway` prefix."""
    return await bugment_conversation_set_model(request, token)


@augment_router.get("/usage/api/balance")
@router.get("/usage/api/balance")  # 也支持 /gateway 前缀
async def get_balance(request: Request):
    """Augment Code 兼容路由：获取账户余额信息"""
    log.debug(f"Balance request received from {request.url.path}", tag="GATEWAY")
    
    # 尝试从凭证管理器获取用户信息（如果有的话）
    user_email = "用户"
    try:
        from web import get_credential_manager
        cred_mgr = get_credential_manager()
        if cred_mgr:
            # 获取当前使用的凭证信息
            cred_result = await cred_mgr.get_valid_credential()
            if cred_result:
                _, credential_data = cred_result
                # 尝试从凭证数据中提取用户信息
                user_email = credential_data.get("email") or credential_data.get("user_email") or "用户"
    except Exception as e:
        log.warning(f"Failed to get credential info for balance: {e}")
    
    # 返回余额信息（模拟数据，实际应该从数据库或配置中读取）
    # 如果系统有实际的余额系统，应该从这里查询真实余额
    balance_data = {
        "success": True,
        "data": {
            "balance": 100.00,  # 默认余额，实际应该从配置或数据库读取
            "name": user_email.split("@")[0] if "@" in user_email else user_email,  # 从邮箱提取用户名
            "plan_name": "标准套餐",  # 默认套餐名称
            "end_date": "2025-12-31"  # 默认到期日期
        }
    }
    
    log.debug(f"Returning balance info: {balance_data}", tag="GATEWAY")
    return balance_data


@augment_router.get("/usage/api/getLoginToken")
@router.get("/usage/api/getLoginToken")  # 也支持 /gateway 前缀
async def get_login_token(request: Request):
    """Augment Code 兼容路由：获取登录令牌"""
    log.debug(f"Login token request received from {request.url.path}", tag="GATEWAY")
    
    import secrets
    import time
    
    # 生成一个简单的令牌（实际应该基于认证机制生成）
    # 这里生成一个基于时间戳的令牌，确保每次请求都不同
    token = secrets.token_urlsafe(32)
    timestamp = int(time.time())
    
    # 返回令牌信息
    token_data = {
        "success": True,
        "data": {
            "token": token,
            "expires_in": 3600,  # 1小时过期
            "token_type": "Bearer",
            "timestamp": timestamp
        }
    }
    
    log.debug(f"Generated login token (length: {len(token)})", tag="GATEWAY")
    return token_data
