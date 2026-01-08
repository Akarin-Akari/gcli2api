from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Union

from log import log


DEFAULT_THINKING_BUDGET = 1024
DEFAULT_TEMPERATURE = 0.4


def _anthropic_debug_enabled() -> bool:
    return str(os.getenv("ANTHROPIC_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}


def _is_non_whitespace_text(value: Any) -> bool:
    """
    判断文本是否包含"非空白"内容。

    说明：下游（Antigravity/Claude 兼容层）会对纯 text 内容块做校验：
    - text 不能为空字符串
    - text 不能仅由空白字符（空格/换行/制表等）组成

    因此这里统一把仅空白的 text part 过滤掉，以避免 400：
    `messages: text content blocks must contain non-whitespace text`。
    """
    if value is None:
        return False
    try:
        return bool(str(value).strip())
    except Exception:
        return False


def _is_thinking_disabled(thinking_value: Any) -> bool:
    """判断 thinking 是否被显式禁用"""
    if thinking_value is None:
        return False
    if isinstance(thinking_value, bool):
        return not thinking_value
    if isinstance(thinking_value, dict):
        return thinking_value.get("type") == "disabled"
    return False


def _should_strip_thinking_blocks(payload: Dict[str, Any]) -> bool:
    """
    判断是否应该在请求转换时清理 thinking blocks。
    清理条件（满足任一即清理）：
    1. thinking 被显式禁用（type: disabled 或 thinking: false）
    2. thinking=null（不下发 thinkingConfig，下游视为禁用）
    3. 没有 thinking 字段（不下发 thinkingConfig，下游视为禁用）
    4. thinking 启用但历史消息不满足约束（不下发 thinkingConfig，下游视为禁用）

    核心原则：只要不会下发 thinkingConfig，就应该清理 thinking blocks，
    避免下游报错 "When thinking is disabled, an assistant message cannot contain thinking"
    """
    # 没有 thinking 字段 → 不下发 thinkingConfig → 需要清理
    if "thinking" not in payload:
        return True

    thinking_value = payload.get("thinking")

    # thinking=null → 不下发 thinkingConfig → 需要清理
    if thinking_value is None:
        return True

    # thinking 被显式禁用 → 需要清理
    if _is_thinking_disabled(thinking_value):
        return True

    # thinking 启用，检查是否会实际下发 thinkingConfig
    # 如果历史消息不满足约束，thinkingConfig 不会被下发
    thinking_config = get_thinking_config(thinking_value)
    include_thoughts = bool(thinking_config.get("includeThoughts", False))

    if not include_thoughts:
        # includeThoughts=False → 需要清理
        return True

    # 检查最后一条 assistant 消息的第一个 block 类型
    messages = payload.get("messages") or []
    last_assistant_first_block_type = None
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list) or not content:
            continue
        first_block = content[0]
        if isinstance(first_block, dict):
            last_assistant_first_block_type = first_block.get("type")
        else:
            last_assistant_first_block_type = None
        break

    # 如果最后一条 assistant 消息的第一个 block 不是 thinking/redacted_thinking，
    # 则 thinkingConfig 不会被下发 → 需要清理
    if last_assistant_first_block_type not in {None, "thinking", "redacted_thinking"}:
        return True

    # 检查 budget 是否会导致 thinkingConfig 不下发
    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int):
        budget = thinking_config.get("thinkingBudget")
        if isinstance(budget, int) and budget >= max_tokens:
            adjusted_budget = max(0, max_tokens - 1)
            if adjusted_budget <= 0:
                # budget 无法调整 → thinkingConfig 不下发 → 需要清理
                return True

    # 其他情况：thinkingConfig 会被下发，不需要清理
    return False


def _strip_thinking_blocks_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从消息列表中移除所有 thinking/redacted_thinking blocks。
    当 thinking 被禁用时，历史消息中的 thinking blocks 会导致 400 错误：
    "When thinking is disabled, an `assistant` message..."

    此函数会：
    1. 遍历所有消息
    2. 对于 assistant 消息，移除 content 中的 thinking/redacted_thinking blocks
    3. 保留其他所有内容（text, tool_use, tool_result 等）

    注意：thinking blocks 只是模型的内部推理过程，移除它们不会影响对话的核心内容。
    """
    if not messages:
        return messages

    cleaned_messages = []
    for msg in messages:
        if not isinstance(msg, dict):
            cleaned_messages.append(msg)
            continue

        role = msg.get("role")
        content = msg.get("content")

        # 只处理 assistant 消息的 content
        if role != "assistant" or not isinstance(content, list):
            cleaned_messages.append(msg)
            continue

        # 过滤掉 thinking 和 redacted_thinking blocks
        cleaned_content = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type in ("thinking", "redacted_thinking"):
                    # 跳过 thinking blocks
                    continue
            cleaned_content.append(item)

        # 如果清理后 content 为空，添加一个空文本块避免格式错误
        if not cleaned_content:
            cleaned_content = [{"type": "text", "text": "..."}]

        # 创建新的消息对象
        cleaned_msg = msg.copy()
        cleaned_msg["content"] = cleaned_content
        cleaned_messages.append(cleaned_msg)

    return cleaned_messages


def get_thinking_config(thinking: Optional[Union[bool, Dict[str, Any]]]) -> Dict[str, Any]:
    """
    根据 Anthropic/Claude 请求的 thinking 参数生成下游 thinkingConfig。

    该逻辑以根目录 `converter.py` 的语义为准：
    - thinking=None：默认启用 includeThoughts，并使用默认 budget
    - thinking=bool：True 启用 / False 禁用
    - thinking=dict：{'type':'enabled'|'disabled', 'budget_tokens': int}
    """
    if thinking is None:
        return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}

    if isinstance(thinking, bool):
        if thinking:
            return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}
        return {"includeThoughts": False}

    if isinstance(thinking, dict):
        thinking_type = thinking.get("type", "enabled")
        is_enabled = thinking_type == "enabled"
        if not is_enabled:
            return {"includeThoughts": False}

        budget = thinking.get("budget_tokens", DEFAULT_THINKING_BUDGET)
        return {"includeThoughts": True, "thinkingBudget": budget}

    return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}


def map_claude_model_to_gemini(claude_model: str) -> str:
    """
    将 Claude 模型名映射为下游模型名（含“支持列表透传”与固定映射）。

    该逻辑以根目录 `converter.py` 为准。
    """
    claude_model = str(claude_model or "").strip()
    if not claude_model:
        return "claude-sonnet-4-5"

    # claude-cli 常见的版本化模型名，例如：
    # - claude-opus-4-5-20251101
    # - claude-haiku-4-5-20251001
    # 这类名称不在 converter.py 的固定映射中，会落入默认值，从而导致“看起来像被强制用 sonnet”。
    # 这里做一次规范化，使其更贴近用户预期。
    m = re.match(r"^(claude-(?:opus|sonnet|haiku)-4-5)-\d{8}$", claude_model)
    if m:
        claude_model = m.group(1)

    # 对 claude 4.5 系列做更合理的落地映射（保持下游可用性优先）
    if claude_model == "claude-opus-4-5":
        return "claude-opus-4-5-thinking"
    if claude_model == "claude-sonnet-4-5":
        return "claude-sonnet-4-5"
    if claude_model == "claude-haiku-4-5":
        return "gemini-2.5-flash"

    supported_models = {
        # Gemini 系列
        "gemini-2.5-flash",
        "gemini-2.5-flash-thinking",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash-image",
        "gemini-2.5-pro",
        "gemini-3-flash",
        "gemini-3-pro-low",
        "gemini-3-pro-high",
        "gemini-3-pro-image",
        # Claude 系列
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-thinking",
        "claude-opus-4-5-thinking",
        # GPT 系列
        "gpt-oss-120b-medium",
        # 内部测试模型（预留）
        "rev19-uic3-1p",
        "chat_20706",
        "chat_23310",
    }

    if claude_model in supported_models:
        return claude_model

    model_mapping = {
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20240620": "claude-sonnet-4-5",
        "claude-opus-4": "gemini-3-pro-high",
        "claude-haiku-4": "claude-haiku-4.5",
        "claude-3-haiku-20240307": "gemini-2.5-flash",
    }

    return model_mapping.get(claude_model, "claude-sonnet-4-5")


def clean_json_schema(schema: Any) -> Any:
    """
    清理 JSON Schema，移除下游不支持的字段，并把验证要求追加到 description。

    该逻辑以根目录 `converter.py` 的语义为准。
    """
    if not isinstance(schema, dict):
        return schema

    # 下游（Antigravity/Vertex/Gemini）对 tool parameters 的 JSON Schema 支持范围很窄，
    # 一些标准字段会直接触发 400（例如 $ref / exclusiveMinimum）。
    #
    # 这里参考 `src/openai_transfer.py::_clean_schema_for_gemini` 的名单，做一次统一剔除，
    # 以保证 Anthropic tools -> 下游 functionDeclarations 的兼容性。
    unsupported_keys = {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "title",
        "example",
        "examples",
        "readOnly",
        "writeOnly",
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "additionalItems",
        "contains",
        "patternProperties",
        "dependencies",
        "propertyNames",
        "if",
        "then",
        "else",
        "contentEncoding",
        "contentMediaType",
    }

    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }
    fields_to_remove = {"additionalProperties"}

    validations: List[str] = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported_keys or key in fields_to_remove or key in validation_fields:
            continue

        if key == "type" and isinstance(value, list):
            # Roo/Anthropic SDK 常见写法：type: ["string", "null"]
            # 下游（Proto 风格 Schema）通常要求 type 为单值字段，并使用 nullable 表达可空。
            has_null = any(
                isinstance(t, str) and t.strip() and t.strip().lower() == "null" for t in value
            )
            non_null_types = [
                t.strip()
                for t in value
                if isinstance(t, str) and t.strip() and t.strip().lower() != "null"
            ]

            cleaned[key] = non_null_types[0] if non_null_types else "string"
            if has_null:
                cleaned["nullable"] = True
            continue

        if key == "description" and validations:
            cleaned[key] = f"{value} ({', '.join(validations)})"
        elif key == "properties":
            # 特殊处理 properties：确保每个属性的值都是完整的 Schema 对象
            if isinstance(value, dict):
                cleaned_properties: Dict[str, Any] = {}
                for prop_name, prop_schema in value.items():
                    if isinstance(prop_schema, dict):
                        # 递归清理属性 Schema
                        cleaned_prop = clean_json_schema(prop_schema)
                        # 如果属性类型是 object，确保它是一个完整的 Schema 对象
                        if cleaned_prop.get("type") == "object":
                            # 确保 object 类型有完整的 Schema 结构
                            if "properties" not in cleaned_prop:
                                cleaned_prop["properties"] = {}
                            # 确保有 type 字段
                            if "type" not in cleaned_prop:
                                cleaned_prop["type"] = "object"
                        cleaned_properties[prop_name] = cleaned_prop
                    elif isinstance(prop_schema, str) and prop_schema == "object":
                        # 如果值是字符串 "object"，转换为完整的 Schema 对象
                        cleaned_properties[prop_name] = {"type": "object", "properties": {}}
                    else:
                        # 其他情况直接使用原值（但应该不会发生）
                        cleaned_properties[prop_name] = prop_schema
                cleaned[key] = cleaned_properties
            else:
                cleaned[key] = value
        elif isinstance(value, dict):
            cleaned[key] = clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value

    if validations and "description" not in cleaned:
        cleaned["description"] = f"Validation: {', '.join(validations)}"

    # 与 `src/openai_transfer.py::_clean_schema_for_gemini` 保持一致：
    # 如果有 properties 但没有显式 type，则补齐为 object，避免下游校验失败。
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    # 修 Cursor 兼容性：确保 input_schema 始终有 type 字段
    # 错误 "tools.0.custom.input_schema.type: Field required" 表明下游要求 type 必填
    # 如果 cleaned 非空但没有 type，默认补齐为 "object"
    if cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    return cleaned


def convert_tools(anthropic_tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 Anthropic tools[] 转换为下游 tools（functionDeclarations）结构。
    """
    if not anthropic_tools:
        return None

    gemini_tools: List[Dict[str, Any]] = []
    for tool in anthropic_tools:
        name = tool.get("name")
        if not name:
            continue
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {}) or {}
        parameters = clean_json_schema(input_schema)

        gemini_tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    }
                ]
            }
        )

    return gemini_tools or None


def _extract_tool_result_output(content: Any) -> str:
    """
    从 tool_result.content 中提取输出字符串（按 converter.py 的最小语义）。
    """
    if isinstance(content, list):
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return str(first.get("text", ""))
        return str(first)
    if content is None:
        return ""
    return str(content)


def convert_messages_to_contents(messages: List[Dict[str, Any]], *, include_thinking: bool = True) -> List[Dict[str, Any]]:
    """
    将 Anthropic messages[] 转换为下游 contents[]（role: user/model, parts: []）。

    Args:
        messages: Anthropic 格式的消息列表
        include_thinking: 是否包含 thinking 块（当请求未启用 thinking 时应设为 False）
    """
    contents: List[Dict[str, Any]] = []

    # 第一遍：建立 tool_use_id -> name 的映射
    # Anthropic 的 tool_result 消息不包含 name 字段，但 Gemini 的 functionResponse 需要 name
    tool_use_id_to_name: Dict[str, str] = {}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_id = item.get("id")
                    tool_name = item.get("name")
                    if tool_id and tool_name:
                        tool_use_id_to_name[str(tool_id)] = str(tool_name)

    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        raw_content = msg.get("content", "")

        parts: List[Dict[str, Any]] = []
        if isinstance(raw_content, str):
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]
        elif isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    if _is_non_whitespace_text(item):
                        parts.append({"text": str(item)})
                    continue

                item_type = item.get("type")
                if item_type == "thinking":
                    # 如果请求未启用 thinking，则跳过历史 thinking 块
                    if not include_thinking:
                        continue

                    # Anthropic 的历史 thinking block 在回放时通常要求携带 signature；
                    # 若缺失 signature，下游可能会报 "thinking.signature: Field required"。
                    # 为保证兼容性，这里选择丢弃无 signature 的 thinking block。
                    signature = item.get("signature")
                    if not signature:
                        continue

                    thinking_text = item.get("thinking", "")
                    if thinking_text is None:
                        thinking_text = ""
                    part: Dict[str, Any] = {
                        "text": str(thinking_text),
                        "thought": True,
                        "thoughtSignature": signature,
                    }
                    parts.append(part)
                elif item_type == "redacted_thinking":
                    # 如果请求未启用 thinking，则跳过历史 redacted_thinking 块
                    if not include_thinking:
                        continue

                    signature = item.get("signature")
                    if not signature:
                        continue

                    # redacted_thinking 的具体字段在不同客户端可能不同，这里尽量兼容 data/thinking。
                    thinking_text = item.get("thinking")
                    if thinking_text is None:
                        thinking_text = item.get("data", "")
                    parts.append(
                        {
                            "text": str(thinking_text or ""),
                            "thought": True,
                            "thoughtSignature": signature,
                        }
                    )
                elif item_type == "text":
                    text = item.get("text", "")
                    if _is_non_whitespace_text(text):
                        parts.append({"text": str(text)})
                elif item_type == "image":
                    source = item.get("source", {}) or {}
                    if source.get("type") == "base64":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", ""),
                                }
                            }
                        )
                elif item_type == "tool_use":
                    # Gemini 3 要求 functionCall 必须包含 thoughtSignature
                    # 参考: https://ai.google.dev/gemini-api/docs/thought-signatures
                    # 如果没有 signature，使用 dummy 值绕过验证
                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "args": item.get("input", {}) or {},
                        },
                        # 添加 dummy thoughtSignature 以满足 Gemini 3 的要求（在 part 级别）
                        "thoughtSignature": "skip_thought_signature_validator",
                    }
                    parts.append(fc_part)
                elif item_type == "tool_result":
                    output = _extract_tool_result_output(item.get("content"))
                    tool_use_id = item.get("tool_use_id")
                    # 从映射中获取 name，如果找不到则使用 tool_use_id 作为兜底
                    # Gemini 要求 functionResponse.name 必须非空
                    tool_name = tool_use_id_to_name.get(str(tool_use_id), "") if tool_use_id else ""
                    if not tool_name:
                        # 兜底：使用 tool_use_id 作为 name，避免空值导致 400 错误
                        tool_name = str(tool_use_id) if tool_use_id else "unknown_tool"
                    parts.append(
                        {
                            "functionResponse": {
                                "id": tool_use_id,
                                "name": tool_name,
                                "response": {"output": output},
                            }
                        }
                    )
                else:
                    parts.append({"text": json.dumps(item, ensure_ascii=False)})
        else:
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]

        # 避免产生空 parts（下游可能会报错），直接跳过该条空消息。
        if not parts:
            continue

        contents.append({"role": gemini_role, "parts": parts})

    return contents


def reorganize_tool_messages(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    重新组织消息，尽量满足 Anthropic 的 tool_use/tool_result 约束：
    - 每个 tool_use（下游表现为 functionCall）必须紧跟一个对应的 tool_result（下游表现为 functionResponse）

    该逻辑“对齐/移植”根目录 `converter.py` 的 `reorganize_tool_messages` 语义：
    - 将所有 functionResponse 收集起来
    - 将所有 parts 平铺为“每个 part 独立成一条消息”
    - 遇到 functionCall 时，若存在匹配的 functionResponse，则插入到其后

    注意：如果客户端根本没有提供 tool_result，本函数无法凭空补齐，只能尽力重排。
    """
    tool_results: Dict[str, Dict[str, Any]] = {}

    for msg in contents:
        for part in msg.get("parts", []) or []:
            if isinstance(part, dict) and "functionResponse" in part:
                tool_id = (part.get("functionResponse") or {}).get("id")
                if tool_id:
                    tool_results[str(tool_id)] = part

    flattened: List[Dict[str, Any]] = []
    for msg in contents:
        role = msg.get("role")
        for part in msg.get("parts", []) or []:
            flattened.append({"role": role, "parts": [part]})

    new_contents: List[Dict[str, Any]] = []
    i = 0
    while i < len(flattened):
        msg = flattened[i]
        part = msg["parts"][0]

        if isinstance(part, dict) and "functionResponse" in part:
            i += 1
            continue

        if isinstance(part, dict) and "functionCall" in part:
            tool_id = (part.get("functionCall") or {}).get("id")
            new_contents.append({"role": "model", "parts": [part]})

            if tool_id is not None and str(tool_id) in tool_results:
                new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})

            i += 1
            continue

        new_contents.append(msg)
        i += 1

    return new_contents


def build_system_instruction(system: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic system 字段转换为下游 systemInstruction。
    """
    if not system:
        return None

    parts: List[Dict[str, Any]] = []
    if isinstance(system, str):
        if _is_non_whitespace_text(system):
            parts.append({"text": str(system)})
    elif isinstance(system, list):
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if _is_non_whitespace_text(text):
                    parts.append({"text": str(text)})
    else:
        if _is_non_whitespace_text(system):
            parts.append({"text": str(system)})

    if not parts:
        return None

    return {"role": "user", "parts": parts}


def build_generation_config(payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """
    根据 Anthropic Messages 请求构造下游 generationConfig。

    默认值与 `converter.py` 保持一致，并在此基础上兼容 stop_sequences。

    Returns:
        (generation_config, should_include_thinking): 元组，包含生成配置和是否应包含 thinking 块
    """
    config: Dict[str, Any] = {
        "topP": 1,
        "topK": 40,
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>",
        ],
    }

    temperature = payload.get("temperature", None)
    config["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature

    top_p = payload.get("top_p", None)
    if top_p is not None:
        config["topP"] = top_p

    top_k = payload.get("top_k", None)
    if top_k is not None:
        config["topK"] = top_k

    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens

    stop_sequences = payload.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        config["stopSequences"] = config["stopSequences"] + [str(s) for s in stop_sequences]

    # Anthropic 的 extended thinking 并非默认开启；并且部分客户端（claude-cli / CherryStudio）
    # 可能会携带 `thinking: null`，或开启 thinking 但不回放历史 thinking blocks。
    #
    # 下游在 thinking 启用时会更严格校验历史 assistant 消息：
    # - 若历史中存在 assistant 消息，则"最后一条 assistant 消息"必须以 thinking/redacted_thinking block 开头
    # - max_tokens 必须大于 thinking.budget_tokens
    #
    # 为兼容客户端，这里仅在 thinking 值"显式且非 null"时才考虑下发，并做安全兜底：
    # - 若最后一条 assistant 消息不以 thinking/redacted_thinking 开头，则不下发 thinkingConfig（避免 400）
    # - 若 budget >= max_tokens，则自动下调 budget（最低降到 max_tokens-1），否则不下发
    should_include_thinking = False
    if "thinking" in payload:
        thinking_value = payload.get("thinking")
        if thinking_value is not None:
            thinking_config = get_thinking_config(thinking_value)
            include_thoughts = bool(thinking_config.get("includeThoughts", False))

            last_assistant_first_block_type = None
            for msg in reversed(payload.get("messages") or []):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if not isinstance(content, list) or not content:
                    continue
                first_block = content[0]
                if isinstance(first_block, dict):
                    last_assistant_first_block_type = first_block.get("type")
                else:
                    last_assistant_first_block_type = None
                break

            if include_thoughts and last_assistant_first_block_type not in {
                None,
                "thinking",
                "redacted_thinking",
            }:
                if _anthropic_debug_enabled():
                    log.info(
                        "[ANTHROPIC][thinking] 请求显式启用 thinking，但历史 messages 未回放 "
                        "满足约束的 assistant thinking/redacted_thinking 起始块，已跳过下发 thinkingConfig（避免下游 400）"
                    )
                return config, False

            max_tokens = payload.get("max_tokens")
            if include_thoughts and isinstance(max_tokens, int):
                budget = thinking_config.get("thinkingBudget")
                if isinstance(budget, int) and budget >= max_tokens:
                    adjusted_budget = max(0, max_tokens - 1)
                    if adjusted_budget <= 0:
                        if _anthropic_debug_enabled():
                            log.info(
                                "[ANTHROPIC][thinking] thinkingBudget>=max_tokens 且无法下调到正数，"
                                "已跳过下发 thinkingConfig（避免下游 400）"
                            )
                        return config, False
                    if _anthropic_debug_enabled():
                        log.info(
                            f"[ANTHROPIC][thinking] thinkingBudget>=max_tokens，自动下调 budget: "
                            f"{budget} -> {adjusted_budget}（max_tokens={max_tokens}）"
                        )
                    thinking_config["thinkingBudget"] = adjusted_budget

            config["thinkingConfig"] = thinking_config
            should_include_thinking = include_thoughts
            if _anthropic_debug_enabled():
                log.info(
                    f"[ANTHROPIC][thinking] 已下发 thinkingConfig: includeThoughts="
                    f"{thinking_config.get('includeThoughts')}, thinkingBudget="
                    f"{thinking_config.get('thinkingBudget')}"
                )
        else:
            if _anthropic_debug_enabled():
                log.info("[ANTHROPIC][thinking] thinking=null，视为未启用 thinking（不下发 thinkingConfig）")
    else:
        if _anthropic_debug_enabled():
            log.info("[ANTHROPIC][thinking] 未提供 thinking 字段（不下发 thinkingConfig）")
    return config, should_include_thinking


def convert_anthropic_request_to_antigravity_components(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Anthropic Messages 请求转换为构造下游请求所需的组件。

    返回字段：
    - model: 下游模型名
    - contents: 下游 contents[]
    - system_instruction: 下游 systemInstruction（可选）
    - tools: 下游 tools（可选）
    - generation_config: 下游 generationConfig
    """
    model = map_claude_model_to_gemini(str(payload.get("model", "")))
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    # 🔧 优化：提前清理 thinking blocks，避免下游报错后才处理
    # 核心原则：只要不会下发 thinkingConfig，就应该清理 thinking blocks
    # 这样可以避免浪费 token（之前是下游报错后才清理并重试）
    if _should_strip_thinking_blocks(payload):
        original_count = len(messages)
        messages = _strip_thinking_blocks_from_messages(messages)
        if _anthropic_debug_enabled():
            log.info(
                f"[ANTHROPIC][thinking] 检测到 thinkingConfig 不会下发，已提前清理历史消息中的 thinking blocks "
                f"(messages={original_count})"
            )

    # 先构建 generation_config 以确定是否应包含 thinking
    generation_config, should_include_thinking = build_generation_config(payload)

    # 根据 thinking 配置转换消息
    contents = convert_messages_to_contents(messages, include_thinking=should_include_thinking)
    contents = reorganize_tool_messages(contents)
    system_instruction = build_system_instruction(payload.get("system"))
    tools = convert_tools(payload.get("tools"))

    return {
        "model": model,
        "contents": contents,
        "system_instruction": system_instruction,
        "tools": tools,
        "generation_config": generation_config,
    }
