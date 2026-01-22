"""
Message Converter - Convert messages between OpenAI/Gemini and Antigravity formats
消息转换器 - 在 OpenAI/Gemini 和 Antigravity 格式之间转换消息
"""

import json
import re
from typing import Any, Dict, List, Optional

from log import log
from src.signature_cache import get_cached_signature
# [FIX 2026-01-11] 导入 gemini_fix 的清理函数
from .gemini_fix import clean_contents, ALLOWED_PART_KEYS


def extract_images_from_content(content: Any) -> Dict[str, Any]:
    """
    从 OpenAI content 中提取文本和图片
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result["text"] += item.get("text", "")
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    # 解析 data:image/png;base64,xxx 格式
                    if image_url.startswith("data:image/"):
                        match = re.match(r"^data:image/(\w+);base64,(.+)$", image_url)
                        if match:
                            mime_type = match.group(1)
                            base64_data = match.group(2)
                            result["images"].append({
                                "inlineData": {
                                    "mimeType": f"image/{mime_type}",
                                    "data": base64_data
                                }
                            })

    return result


def strip_thinking_from_openai_messages(messages: List[Any]) -> List[Any]:
    """
    从 OpenAI 格式消息中移除 thinking 内容块。

    当 thinking 被禁用时，历史消息中的 thinking 内容块会导致 400 错误：
    "When thinking is disabled, an `assistant` message..."

    此函数会：
    1. 遍历所有消息
    2. 对于 assistant 消息，移除 content 中的 thinking 相关内容
    3. 处理字符串格式的 content（移除 <think>...</think> 或 <think>...</think> 标签）
    4. 处理数组格式的 content（移除 type="thinking" 的项）
    """
    if not messages:
        return messages

    cleaned_messages = []

    for msg in messages:
        # 处理 Pydantic 模型对象
        if hasattr(msg, "role") and hasattr(msg, "content"):
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)

            # 只处理 assistant 消息
            if role == "assistant" and content:
                # 处理字符串格式的 content
                if isinstance(content, str):
                    # 移除各种 thinking 标签格式
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    # 清理多余的空白行
                    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
                    # 如果内容为空，保留一个占位符
                    if not content.strip():
                        content = "..."
                    # 创建新消息对象，保留 tool_calls
                    # [FIX 2026-01-08] 必须保留 tool_calls 字段，否则会导致孤儿 tool_result
                    from src.models import OpenAIChatMessage
                    tool_calls = getattr(msg, "tool_calls", None)
                    cleaned_msg = OpenAIChatMessage(role=role, content=content, tool_calls=tool_calls)
                    cleaned_messages.append(cleaned_msg)
                    continue

                # 处理数组格式的 content
                elif isinstance(content, list):
                    cleaned_content = []
                    for item in content:
                        if isinstance(item, dict):
                            item_type = item.get("type")
                            # 跳过 thinking 类型的内容块
                            if item_type in ("thinking", "redacted_thinking"):
                                continue
                            # [FIX 2026-01-20] 清理非 thinking items 中的 thoughtSignature 字段
                            # 问题：Cursor 可能在历史消息的 text parts 中错误地保留 thoughtSignature
                            # 这会导致 Claude API 返回 400 错误："Invalid signature in thinking block"
                            # 解决：创建一个新的 dict，只保留必要字段，排除 thoughtSignature
                            if "thoughtSignature" in item or "signature" in item:
                                # 创建一个干净的副本，排除签名字段
                                cleaned_item = {k: v for k, v in item.items() if k not in ("thoughtSignature", "signature")}
                                cleaned_content.append(cleaned_item)
                            else:
                                cleaned_content.append(item)
                        else:
                            cleaned_content.append(item)

                    # 如果清理后为空，添加一个空文本块
                    if not cleaned_content:
                        cleaned_content = [{"type": "text", "text": "..."}]

                    # 创建新消息对象，保留 tool_calls
                    # [FIX 2026-01-08] 必须保留 tool_calls 字段，否则会导致孤儿 tool_result
                    from src.models import OpenAIChatMessage
                    tool_calls = getattr(msg, "tool_calls", None)
                    cleaned_msg = OpenAIChatMessage(role=role, content=cleaned_content, tool_calls=tool_calls)
                    cleaned_messages.append(cleaned_msg)
                    continue

        # 处理字典格式的消息
        elif isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content")

            # 只处理 assistant 消息
            if role == "assistant" and content:
                # 处理字符串格式的 content
                if isinstance(content, str):
                    # 移除各种 thinking 标签格式
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    # 清理多余的空白行
                    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
                    # 如果内容为空，保留一个占位符
                    if not content.strip():
                        content = "..."
                    # 创建新消息对象，保留 tool_calls
                    # [FIX 2026-01-22] 必须保留 tool_calls 字段，否则会导致孤儿 tool_result
                    cleaned_msg = msg.copy()
                    cleaned_msg["content"] = content
                    # 确保 tool_calls 字段被保留（如果存在）
                    if "tool_calls" in msg:
                        cleaned_msg["tool_calls"] = msg["tool_calls"]
                    cleaned_messages.append(cleaned_msg)
                    continue

                # 处理数组格式的 content
                elif isinstance(content, list):
                    cleaned_content = []
                    for item in content:
                        if isinstance(item, dict):
                            item_type = item.get("type")
                            # 跳过 thinking 类型的内容块
                            if item_type in ("thinking", "redacted_thinking"):
                                continue
                            # [FIX 2026-01-20] 清理非 thinking items 中的 thoughtSignature 字段
                            # 问题：Cursor 可能在历史消息的 text parts 中错误地保留 thoughtSignature
                            # 这会导致 Claude API 返回 400 错误："Invalid signature in thinking block"
                            # 解决：创建一个新的 dict，只保留必要字段，排除 thoughtSignature
                            if "thoughtSignature" in item or "signature" in item:
                                # 创建一个干净的副本，排除签名字段
                                cleaned_item = {k: v for k, v in item.items() if k not in ("thoughtSignature", "signature")}
                                cleaned_content.append(cleaned_item)
                            else:
                                cleaned_content.append(item)
                        else:
                            cleaned_content.append(item)

                    # 如果清理后为空，添加一个空文本块
                    if not cleaned_content:
                        cleaned_content = [{"type": "text", "text": "..."}]

                    # 创建新消息对象，保留 tool_calls
                    # [FIX 2026-01-22] 必须保留 tool_calls 字段，否则会导致孤儿 tool_result
                    # 问题：当消息是字典格式时，strip_thinking_from_openai_messages 没有保留 tool_calls 字段
                    # 这会导致 openai_messages_to_antigravity_contents 无法建立 tool_call_id_to_name 映射
                    # 结果：tool_use 存在但 tool_result 找不到对应的 tool_use，导致 400 错误
                    cleaned_msg = msg.copy()
                    cleaned_msg["content"] = cleaned_content
                    # 确保 tool_calls 字段被保留（如果存在）
                    if "tool_calls" in msg:
                        cleaned_msg["tool_calls"] = msg["tool_calls"]
                    cleaned_messages.append(cleaned_msg)
                    continue

        # 其他情况直接保留
        cleaned_messages.append(msg)

    return cleaned_messages


# 工具格式提示常量
TOOL_FORMAT_REMINDER_TEMPLATE = """

[IMPORTANT - Tool Call Format Rules]
When calling tools, you MUST follow these rules strictly:
1. Always use the EXACT parameter names as defined in the current tool schema
2. Do NOT use parameter names from previous conversations - schemas may have changed
3. For terminal/command tools: the parameter name varies - check the tool definition
4. When in doubt: re-read the tool definition and use ONLY the parameters listed there

{tool_params_section}
"""

TOOL_FORMAT_REMINDER_AFTER_ERROR_TEMPLATE = """

[CRITICAL - Tool Call Error Detected]
Previous tool calls failed due to invalid arguments. You MUST:
1. STOP using parameter names from previous conversations
2. Use ONLY the exact parameter names shown below
3. Do NOT guess parameter names

{tool_params_section}

IMPORTANT: If a tool call fails, check the parameter names above and try again with the EXACT names listed.
"""

SEQUENTIAL_THINKING_PROMPT = """
[IMPORTANT: Thinking Capability Redirection]
Internal thinking/reasoning models are currently disabled or limited.
For complex tasks requiring step-by-step analysis, planning, or reasoning, you MUST use the 'sequentialthinking' (or 'sequential_thinking') tool.
Do NOT attempt to output <think> tags or raw reasoning text. Delegate all reasoning steps to the tool.
"""


def openai_messages_to_antigravity_contents(
    messages: List[Any],
    enable_thinking: bool = False,
    tools: Optional[List[Any]] = None,
    recommend_sequential_thinking: bool = False
) -> List[Dict[str, Any]]:
    """
    将 OpenAI 消息格式转换为 Antigravity contents 格式

    Args:
        messages: OpenAI 格式的消息列表
        enable_thinking: 是否启用 thinking（当启用时，最后一条 assistant 消息必须以 thinking block 开头）
        tools: 工具定义列表（用于提取参数摘要）
        recommend_sequential_thinking: 是否推荐使用 Sequential Thinking 工具
    """
    from .tool_converter import extract_tool_params_summary

    # Check for sequential thinking tool
    has_sequential_tool = False
    if recommend_sequential_thinking and tools:
        for tool in tools:
            name = ""
            if isinstance(tool, dict):
                if "function" in tool:
                    name = tool["function"].get("name", "")
                else:
                    name = tool.get("name", "")
            elif hasattr(tool, "function"):
                name = getattr(tool.function, "name", "")
            elif hasattr(tool, "name"):
                name = getattr(tool, "name", "")

            if name and "sequential" in name.lower() and "thinking" in name.lower():
                has_sequential_tool = True
                break

    contents = []
    system_messages = []

    has_tool_error = False
    has_tools = False  # 检测是否有工具调用

    # [FIX 2026-01-08] 建立 tool_call_id -> tool_name 的映射
    # 用于验证 tool 消息是否有对应的 tool_use，避免 Anthropic API 返回 400 错误：
    # "unexpected `tool_use_id` found in `tool_result` blocks"
    tool_call_id_to_name: dict = {}
    for msg in messages:
        msg_tool_calls = getattr(msg, "tool_calls", None)
        if msg_tool_calls:
            for tc in msg_tool_calls:
                tc_id = getattr(tc, "id", None)
                tc_function = getattr(tc, "function", None)
                if tc_id and tc_function:
                    tc_name = getattr(tc_function, "name", "")
                    if tc_name:
                        tool_call_id_to_name[str(tc_id)] = tc_name

    # [FIX 2026-01-20] 建立 tool_result_ids 集合
    # 用于验证 tool_use 是否有对应的 tool_result，避免 Claude API 返回 400 错误：
    # "tool_use ids were found without tool_result blocks immediately after"
    # 场景: Cursor 重试时可能发送不完整的历史消息，tool_use 存在但 tool_result 缺失
    tool_result_ids: set = set()
    for msg in messages:
        msg_role = getattr(msg, "role", "")
        if msg_role == "tool":
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                tool_result_ids.add(str(tc_id))

    for msg in messages:
        msg_content = getattr(msg, "content", "")
        msg_tool_calls = getattr(msg, "tool_calls", None)

        # 检测是否有工具调用
        if msg_tool_calls:
            has_tools = True

        # 检测错误模式
        if msg_content and isinstance(msg_content, str):
            error_patterns = [
                "invalid arguments",
                "Invalid arguments",
                "invalid parameters",
                "Invalid parameters",
                "Unexpected parameters",
                "unexpected parameters",
                "model provided invalid",
                "Tool call arguments",
                "were invalid",
            ]
            for pattern in error_patterns:
                if pattern in msg_content:
                    has_tool_error = True
                    log.info(f"[ANTIGRAVITY] Detected tool error pattern in message: '{pattern}'")
                    break
        if has_tool_error:
            break

    for i, msg in enumerate(messages):
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        # 处理 system 消息 - 合并到第一条用户消息
        if role == "system":
            # Inject Sequential Thinking prompt if recommended and available
            if has_sequential_tool:
                content = content + SEQUENTIAL_THINKING_PROMPT
                log.info("[ANTIGRAVITY] Injected Sequential Thinking prompt into system message")

            # 在 system 消息末尾注入工具格式提示（包含动态参数）
            if has_tools:
                # 提取工具参数摘要（从传入的 tools 参数中提取）
                tool_params = extract_tool_params_summary(tools) if tools else ""

                if not tool_params:
                    tool_params_section = "Check the tool definitions in your context for exact parameter names."
                else:
                    tool_params_section = tool_params

                if has_tool_error:
                    # 检测到错误，注入强化提示
                    reminder = TOOL_FORMAT_REMINDER_AFTER_ERROR_TEMPLATE.format(tool_params_section=tool_params_section)
                    content = content + reminder
                    log.info(f"[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER_AFTER_ERROR with params into system message")
                else:
                    # 预防性注入基础提示
                    reminder = TOOL_FORMAT_REMINDER_TEMPLATE.format(tool_params_section=tool_params_section)
                    content = content + reminder
                    log.debug("[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER with params into system message")
            system_messages.append(content)
            continue

        # 处理 user 消息
        elif role == "user":
            parts = []

            # 如果有系统消息，添加到第一条用户消息
            if system_messages:
                for sys_msg in system_messages:
                    parts.append({"text": sys_msg})
                system_messages = []

            # 提取文本和图片
            extracted = extract_images_from_content(content)
            if extracted["text"]:
                parts.append({"text": extracted["text"]})
            parts.extend(extracted["images"])

            if parts:
                contents.append({"role": "user", "parts": parts})

        # 处理 assistant 消息
        elif role == "assistant":
            # [DEBUG] 打印 assistant 消息的详细信息
            content_type = type(content).__name__
            content_len = len(str(content)) if content else 0
            log.info(f"[MESSAGE_CONVERTER DEBUG] Processing assistant message: content_type={content_type}, content_len={content_len}")
            if isinstance(content, str) and content:
                has_think_tag = "<think>" in content.lower() or "</think>" in content.lower()
                log.info(f"[MESSAGE_CONVERTER DEBUG] String content has_think_tag={has_think_tag}, first_100_chars='{content[:100]}...'")
            elif isinstance(content, list):
                log.info(f"[MESSAGE_CONVERTER DEBUG] List content with {len(content)} items, item_types={[type(i).__name__ for i in content[:3]]}")
            
            # 处理 content：可能是字符串或数组
            content_parts = []
            if content:
                if isinstance(content, str):
                    # 字符串格式：检查是否包含 thinking 标签
                    # 匹配 <think>...</think> 或 <think>...</think>
                    thinking_match = re.search(r'<(?:redacted_)?reasoning>.*?</(?:redacted_)?reasoning>', content, flags=re.DOTALL | re.IGNORECASE)
                    if not thinking_match:
                        thinking_match = re.search(r'<think>.*?</think>', content, flags=re.DOTALL | re.IGNORECASE)

                    if thinking_match:
                        # 提取 thinking 内容
                        thinking_text = thinking_match.group(0)
                        log.info(f"[MESSAGE_CONVERTER DEBUG] Found thinking_match: match_len={len(thinking_text)}")
                        # 移除标签，保留内容
                        thinking_content = re.sub(r'</?(?:redacted_)?reasoning>', '', thinking_text, flags=re.IGNORECASE)
                        thinking_content = re.sub(r'</?think>', '', thinking_content, flags=re.IGNORECASE)
                        thinking_content = thinking_content.strip()
                        log.info(f"[MESSAGE_CONVERTER DEBUG] Extracted thinking_content: len={len(thinking_content)}, first_50='{thinking_content[:50]}...'")

                        # [FIX 2026-01-21] 修正：不再无条件丢弃历史 thinking blocks
                        # 原来的注释说 "signature 是会话绑定的" 是错误理解
                        # 实际上：signature 是用于验证 thinking 内容完整性的，
                        # 只要 signature + thinking 内容匹配，任何请求都可以使用
                        #
                        # 字符串格式的 thinking（如 <think>...</think>）通常来自客户端截断，
                        # 不包含 signature。但我们不应该在这里丢弃它，
                        # 而是让上游（antigravity_router.py）从缓存恢复 signature
                        # 或者让 filter_thinking_for_target_model 根据目标模型决定是否保留
                        #
                        # 策略变更：保留 thinking 内容，让上游处理
                        log.info(f"[MESSAGE_CONVERTER] 保留历史 thinking block (字符串格式) 供上游处理: thinking_len={len(thinking_content)}")

                        # 移除 thinking 标签，但保留内容作为 thinking 块
                        # 移除原始的 thinking 标签
                        content = re.sub(r'<(?:redacted_)?reasoning>.*?</(?:redacted_)?reasoning>', '', content, flags=re.DOTALL | re.IGNORECASE)
                        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE)
                        content = content.strip()

                        # 将 thinking 内容作为 thinking 块添加（不带 signature，让上游恢复）
                        if thinking_content:
                            content_parts.append({
                                "text": thinking_content,
                                "thought": True,
                                # 注意：这里不设置 thoughtSignature，让上游从缓存恢复
                            })

                    extracted = extract_images_from_content(content)
                    if extracted["text"]:
                        content_parts.append({"text": extracted["text"]})
                    content_parts.extend(extracted["images"])
                elif isinstance(content, list):
                    # 数组格式：检查是否有 thinking 类型的内容块
                    for item in content:
                        if isinstance(item, dict):
                            item_type = item.get("type")
                            if item_type == "thinking":
                                # 提取 thinking 内容
                                thinking_text = item.get("thinking", "")
                                # [FIX 2026-01-20] 兼容两种签名字段名：signature 和 thoughtSignature
                                message_signature = item.get("signature") or item.get("thoughtSignature") or ""

                                # [FIX 2026-01-21] 修正：不再无条件丢弃历史 thinking blocks
                                # 如果有 signature，保留它；如果没有，也保留 thinking 内容让上游恢复
                                if thinking_text:
                                    if message_signature:
                                        log.info(f"[MESSAGE_CONVERTER] 保留历史 thinking block (数组格式，有签名): thinking_len={len(thinking_text)}, sig_len={len(message_signature)}")
                                        content_parts.append({
                                            "text": thinking_text,
                                            "thought": True,
                                            "thoughtSignature": message_signature
                                        })
                                    else:
                                        log.info(f"[MESSAGE_CONVERTER] 保留历史 thinking block (数组格式，无签名，待上游恢复): thinking_len={len(thinking_text)}")
                                        content_parts.append({
                                            "text": thinking_text,
                                            "thought": True,
                                            # 不设置 thoughtSignature，让上游从缓存恢复
                                        })
                            elif item_type == "redacted_thinking":
                                # [FIX 2026-01-21] redacted_thinking 也保留，让上游处理
                                thinking_text = item.get("thinking") or item.get("data", "")
                                message_signature = item.get("signature") or item.get("thoughtSignature") or ""
                                if thinking_text:
                                    if message_signature:
                                        log.info(f"[MESSAGE_CONVERTER] 保留历史 redacted_thinking block (有签名): thinking_len={len(thinking_text)}")
                                        content_parts.append({
                                            "text": thinking_text,
                                            "thought": True,
                                            "thoughtSignature": message_signature
                                        })
                                    else:
                                        log.info(f"[MESSAGE_CONVERTER] 保留历史 redacted_thinking block (无签名，待上游恢复): thinking_len={len(thinking_text)}")
                                        content_parts.append({
                                            "text": thinking_text,
                                            "thought": True,
                                        })
                            elif item_type == "text":
                                content_parts.append({"text": item.get("text", "")})
                            elif item_type == "image_url":
                                # 处理图片
                                image_url = item.get("image_url", {}).get("url", "")
                                if image_url.startswith("data:image/"):
                                    match = re.match(r"^data:image/(\w+);base64,(.+)$", image_url)
                                    if match:
                                        mime_type = match.group(1)
                                        base64_data = match.group(2)
                                        content_parts.append({
                                            "inlineData": {
                                                "mimeType": f"image/{mime_type}",
                                                "data": base64_data
                                            }
                                        })
                        else:
                            # 非字典项，转换为文本
                            if item:
                                content_parts.append({"text": str(item)})
                else:
                    # 其他格式，尝试提取文本
                    extracted = extract_images_from_content(content)
                    if extracted["text"]:
                        content_parts.append({"text": extracted["text"]})
                    content_parts.extend(extracted["images"])

            # 添加工具调用
            if tool_calls:
                for tool_call in tool_calls:
                    tc_id = getattr(tool_call, "id", None)
                    tc_type = getattr(tool_call, "type", "function")
                    tc_function = getattr(tool_call, "function", None)

                    # [FIX 2026-01-20] 验证对应的 tool_result 是否存在
                    # 如果 tool_result 不存在，跳过这个 tool_use，避免 Claude API 返回 400 错误：
                    # "tool_use ids were found without tool_result blocks immediately after"
                    # 场景: Cursor 重试时可能发送不完整的历史消息，tool_use 存在但 tool_result 缺失
                    if tc_id and str(tc_id) not in tool_result_ids:
                        log.warning(f"[ANTIGRAVITY] Skipping orphan tool_use: "
                                   f"tool_call_id={tc_id} has no corresponding tool_result. "
                                   f"This may happen when conversation was interrupted during tool execution. "
                                   f"Filtering to avoid Claude API 400 error.")
                        continue

                    if tc_function:
                        func_name = getattr(tc_function, "name", "")
                        func_args = getattr(tc_function, "arguments", "{}")

                        # 解析 arguments（可能是字符串）
                        if isinstance(func_args, str):
                            try:
                                args_dict = json.loads(func_args)
                            except:
                                args_dict = {"query": func_args}
                        else:
                            args_dict = func_args

                        content_parts.append({
                            "functionCall": {
                                "id": tc_id,
                                "name": func_name,
                                "args": args_dict
                            },
                            # Gemini 3 要求 functionCall 必须包含 thoughtSignature
                            "thoughtSignature": "skip_thought_signature_validator",
                        })

            if content_parts:
                contents.append({"role": "model", "parts": content_parts})

        # 处理 tool 消息
        elif role == "tool":
            tool_call_id = getattr(msg, "tool_call_id", None)
            tool_name = getattr(msg, "name", "unknown")
            content = getattr(msg, "content", "")

            # 验证必要字段
            if not tool_call_id:
                log.warning(f"[ANTIGRAVITY] Tool message missing tool_call_id at index {i}, skipping")
                continue  # 跳过无效的工具消息

            # [FIX 2026-01-08] 验证对应的 tool_use 是否存在
            # 如果 tool_use 不存在，跳过这个 tool_result，避免 Anthropic API 返回 400 错误：
            # "unexpected `tool_use_id` found in `tool_result` blocks"
            if str(tool_call_id) not in tool_call_id_to_name:
                log.warning(f"[ANTIGRAVITY] Skipping orphan tool message: "
                           f"tool_call_id={tool_call_id} not found in tool_call_id_to_name mapping. "
                           f"This may happen when tool_use was filtered out (e.g., thinking disabled) "
                           f"but tool_result was retained. Index: {i}")
                continue

            # [FIX 2026-01-20] 确保 tool_name 非空，避免 Gemini API 400 错误
            # 错误: "GenerateContentRequest.contents[4].parts[0].function_response.name: Name cannot be empty."
            # 场景: Cursor 重试时可能发送不完整的历史消息，tool 消息的 name 字段缺失
            if not tool_name or not str(tool_name).strip():
                # 尝试从映射中获取 name
                if str(tool_call_id) in tool_call_id_to_name:
                    tool_name = tool_call_id_to_name[str(tool_call_id)]
                    log.info(f"[ANTIGRAVITY] Recovered tool_name from mapping: {tool_name}")
                else:
                    # 最后的兜底: 使用 tool_call_id 作为 name
                    tool_name = f"tool_{tool_call_id}" if tool_call_id else "unknown_tool"
                    log.warning(f"[ANTIGRAVITY] Tool message missing name, using fallback: {tool_name}")

            # 处理 content 为 None 的情况
            if content is None:
                content = ""
                log.debug(f"[ANTIGRAVITY] Tool message content is None, converting to empty string")

            # 记录工具消息信息（用于诊断）
            if not content:
                log.warning(f"[ANTIGRAVITY] Tool message has empty content: tool_call_id={tool_call_id}, name={tool_name}")
            else:
                content_preview = str(content)[:100] if content else ""
                log.debug(f"[ANTIGRAVITY] Tool message: tool_call_id={tool_call_id}, name={tool_name}, content_length={len(str(content))}, preview={content_preview}")

            # 确保 response.output 是有效的 JSON 可序列化值
            if not isinstance(content, (str, int, float, bool, type(None))):
                try:
                    content = json.dumps(content) if content else ""
                except Exception as e:
                    log.warning(f"[ANTIGRAVITY] Failed to serialize tool content: {e}, using str()")
                    content = str(content) if content else ""

            parts = [{
                "functionResponse": {
                    "id": tool_call_id,
                    "name": tool_name,
                    "response": {"output": content}
                }
            }]
            contents.append({"role": "user", "parts": parts})

    # [FIX 2026-01-11] 应用 ALLOWED_PART_KEYS 白名单过滤和尾随空格清理
    # 这是上游同步的关键修复，防止 cache_control 等不支持字段导致 400/429 错误
    contents = clean_contents(contents)
    
    return contents


def gemini_contents_to_antigravity_contents(gemini_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Gemini 原生 contents 格式转换为 Antigravity contents 格式
    Gemini 和 Antigravity 的 contents 格式基本一致，只需要做少量调整
    """
    contents = []

    for content in gemini_contents:
        role = content.get("role", "user")
        parts = content.get("parts", [])

        contents.append({
            "role": role,
            "parts": parts
        })

    # [FIX 2026-01-11] 应用 ALLOWED_PART_KEYS 白名单过滤和尾随空格清理
    contents = clean_contents(contents)
    
    return contents
