"""
Unified Gateway Router - 统一API网关路由
将多个后端服务整合到单一端点，支持优先级路由和故障转移

优先级顺序：
1. Antigravity API (gcli2api 本地) - 优先
2. Copilot API (localhost:8141) - 备用
"""

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from log import log
from src.httpx_client import http_client
from src.utils import authenticate_bearer


# ==================== 重试配置 ====================

RETRY_CONFIG = {
    "max_retries": 3,           # 最大重试次数
    "base_delay": 1.0,          # 基础延迟（秒）
    "max_delay": 10.0,          # 最大延迟（秒）
    "exponential_base": 2,      # 指数退避基数
    "retry_on_status": [500, 502, 503, 504],  # 需要重试的状态码
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
                # thinking 类型转换为文本（如果有内容）
                thinking_text = item.get("thinking", "") or item.get("text", "") or item.get("content", "")
                if thinking_text:
                    sanitized.append({"type": "text", "text": f"[Thinking] {thinking_text}"})
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


def normalize_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize request body to standard OpenAI format.
    Handles Cursor's non-standard format and other variations.

    Cursor may send requests with:
    - messages in different locations
    - null values in messages array
    - extra fields like 'reasoning', 'text', 'metadata', etc.
    - non-standard tools format
    - missing required fields
    """
    normalized = {}

    # Extract model (required)
    normalized["model"] = body.get("model", "gpt-4")

    # Extract messages - try multiple possible locations
    messages = None

    # Standard location
    if "messages" in body and body["messages"]:
        messages = body["messages"]
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

    # Handle tools field specially - normalize format
    if "tools" in body and body["tools"]:
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

    # Set default stream to False if not specified
    if "stream" not in normalized:
        normalized["stream"] = False

    # Extract model from prompt markers (if any)
    prompt_model, cleaned_messages = extract_model_from_prompt(normalized["messages"])
    if prompt_model:
        normalized["model"] = prompt_model
        normalized["messages"] = cleaned_messages
        log.info(f"Model overridden by prompt marker: {prompt_model}")

    log.debug(f"Normalized request: model={normalized['model']}, messages_count={len(normalized['messages'])}, stream={normalized.get('stream')}, tools_count={len(normalized.get('tools', []))}")

    return normalized


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
}

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
    1. 检查是否在 Antigravity 支持列表中
    2. 支持 -> Antigravity（按 token 计费，更经济）
    3. 不支持 -> Copilot（按次计费，但支持更多模型）

    Antigravity 支持的模型：
    - Gemini 3 系列: gemini-3-pro, gemini-3-flash
    - Claude 4.5 系列: claude-sonnet-4.5, claude-opus-4.5 (含 thinking 变体)
    - GPT: gpt-oos-120b
    """
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

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            current_time = time.time()

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
            except Exception as e:
                log.error(f"Streaming error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            finally:
                await client.aclose()

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
            finally:
                await client.aclose()

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
