"""
Gateway 请求规范化模块

包含请求体、工具定义、消息、工具选择的规范化逻辑。

从 unified_gateway_router.py 抽取的规范化函数。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, List, Optional, Tuple
import json

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

__all__ = [
    # 工具规范化
    "normalize_tools",
    "normalize_tool_choice",
    # 消息规范化
    "normalize_messages",
    "sanitize_message_content",
    "convert_responses_api_message",
    # 请求体规范化
    "normalize_request_body",
    # Augment/Bugment 辅助
    "build_openai_messages_from_bugment",
    "augment_chat_history_to_messages",
]


# ==================== 工具规范化 ====================

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


# ==================== 消息规范化 ====================

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

    # Type: tool_use - Anthropic 格式的工具调用（Cursor planning/debug 模式）
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

    # Type: tool_result - Anthropic 格式的工具结果（Cursor planning/debug 模式）
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

    # 处理 Cursor 可能发送的其他格式
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

    # 处理工具结果（output + call_id）
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


# ==================== 请求体规范化 ====================

def normalize_request_body(
    body: Dict[str, Any],
    *,
    preserve_extra_fields: bool = False,
    conversation_state_get: Optional[callable] = None,
    conversation_state_put: Optional[callable] = None,
) -> Dict[str, Any]:
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

    Args:
        body: 原始请求体
        preserve_extra_fields: 是否保留额外字段
        conversation_state_get: 获取会话状态的回调函数
        conversation_state_put: 保存会话状态的回调函数

    Returns:
        规范化后的请求体
    """
    # Import config for model routing
    from .config import extract_model_from_prompt

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
                if conversation_state_put:
                    conversation_state_put(conversation_id, model=model, chat_history=body.get("chat_history"))
                log.debug(f"Model normalized: '{body.get('model')}' -> '{model}'", tag="GATEWAY")
            else:
                # Do not force an arbitrary default model. Try per-conversation fallback instead.
                if conversation_state_get:
                    state = conversation_state_get(conversation_id)
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
        if conversation_state_get:
            state = conversation_state_get(conversation_id)
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


# ==================== Augment/Bugment 辅助函数 ====================

def augment_chat_history_to_messages(chat_history: Any) -> List[Dict[str, Any]]:
    """
    将 Augment/Bugment 的 chat_history 转换为 OpenAI 消息格式

    Args:
        chat_history: Augment 格式的聊天历史

    Returns:
        OpenAI 格式的消息列表
    """
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


def build_openai_messages_from_bugment(
    chat_history: List[Dict],
    tool_state: Optional[Dict] = None
) -> List[Dict]:
    """
    从 Bugment 格式构建 OpenAI 消息

    Args:
        chat_history: Bugment 聊天历史
        tool_state: 工具状态

    Returns:
        OpenAI 格式的消息列表
    """
    messages = augment_chat_history_to_messages(chat_history)

    # 如果有工具状态，添加工具相关消息
    if tool_state:
        # 添加工具调用消息
        if "tool_calls" in tool_state:
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_state["tool_calls"]
            })

        # 添加工具结果消息
        if "tool_results" in tool_state:
            for result in tool_state["tool_results"]:
                messages.append({
                    "role": "tool",
                    "tool_call_id": result.get("tool_call_id", ""),
                    "content": result.get("content", "")
                })

    return messages
