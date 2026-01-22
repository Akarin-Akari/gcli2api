from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Union

from log import log
# [FIX 2026-01-11] 导入 gemini_fix 的清理函数 - 上游同步
from src.converters.gemini_fix import clean_contents, ALLOWED_PART_KEYS
# [FIX 2026-01-16] 导入 thoughtSignature_fix 的签名处理函数
from src.converters.thoughtSignature_fix import (
    decode_tool_id_and_signature,
    has_valid_thoughtsignature,
    sanitize_thinking_block,
    remove_trailing_unsigned_thinking,
    filter_invalid_thinking_blocks,
    MIN_SIGNATURE_LENGTH,
    SKIP_SIGNATURE_VALIDATOR,
)
# [FIX 2026-01-21] 导入跨模型 thinking 隔离函数
from src.converters.model_config import (
    is_claude_model,
    is_gemini_model,
    get_model_family,
    should_preserve_thinking_for_model,
)


# [FIX 2026-01-09] 双向限制策略常量定义
# 核心思路：既要保证足够的输出空间，又不能让 max_tokens 过大触发 429
# [UPDATE 2026-01-09] 经测试确认 32000 完全够用，上调至 65535 提供更大的 thinking 空间
# [FIX 2026-01-11] 提高 MIN_OUTPUT_TOKENS 以支持长文档输出（MD文档可能需要 10K-30K tokens）
MAX_ALLOWED_TOKENS = 65535   # max_tokens 的绝对上限（Claude 最大值）
MIN_OUTPUT_TOKENS = 16384    # 实际输出的最小保障空间（4096 -> 16384，支持长文档）

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
        # 使用 fallback_manager 中定义的 Haiku 降级目标，保持一致性
        from src.fallback_manager import HAIKU_FALLBACK_TARGET
        return HAIKU_FALLBACK_TARGET  # gemini-3-flash

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

    # Haiku 模型统一使用 HAIKU_FALLBACK_TARGET
    from src.fallback_manager import HAIKU_FALLBACK_TARGET, is_haiku_model
    if is_haiku_model(claude_model):
        return HAIKU_FALLBACK_TARGET  # gemini-3-flash

    model_mapping = {
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20240620": "claude-sonnet-4-5",
        "claude-opus-4": "gemini-3-pro-high",
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


# ✅ [FIX 2026-01-17] 统一使用新版本的签名恢复函数
# 旧版本已废弃，改用 converters.signature_recovery 中的新版本
# 新版本返回 RecoveryResult，提供更详细的恢复信息
def recover_signature_for_tool_use(
    tool_id: str,
    encoded_tool_id: str,
    signature: Optional[str],
    last_thought_signature: Optional[str],
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None  # [FIX 2026-01-22] 新增 owner_id 参数，用于多客户端会话隔离
) -> Optional[str]:
    """
    多层签名恢复策略（用于工具调用）- 6层完整实现

    ⚠️ 注意：此函数是旧版本的兼容包装，内部调用新版本的 recover_signature_for_tool_use
    新版本位于 converters.signature_recovery，返回 RecoveryResult 提供更详细信息

    优先级：
    1. 客户端提供的签名
    2. 上下文中的签名
    3. 从编码的工具ID中解码（自研版特有）
    4. 会话级缓存
    5. 工具ID缓存
    6. 最近签名（fallback）
    """
    from src.converters.signature_recovery import recover_signature_for_tool_use as recover_tool_sig

    # [FIX 2026-01-22] 传递 owner_id 进行会话隔离
    result = recover_tool_sig(
        tool_id=tool_id,
        encoded_tool_id=encoded_tool_id,
        client_signature=signature,
        context_signature=last_thought_signature,
        session_id=session_id,
        use_placeholder_fallback=True,  # 允许使用占位符作为 fallback
        owner_id=owner_id
    )

    # 返回签名（兼容旧版本接口）
    return result.signature


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


def _validate_and_fix_tool_chain(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    验证并修复工具调用链的完整性

    检查规则:
    1. 每个 tool_use 必须有对应的 tool_result
    2. 如果发现孤儿 tool_use (没有对应的 tool_result), 将其过滤掉

    这是 P0 Critical Fix: 防止 Claude API 返回 400 错误:
    "tool_use ids were found without tool_result blocks immediately after"

    场景: Cursor 重试时可能发送不完整的历史消息:
    - tool_use 块存在
    - 但对应的 tool_result 块被过滤掉了 (因为 thinking 被禁用)

    Args:
        messages: Anthropic 格式的消息列表

    Returns:
        修复后的消息列表
    """
    if not messages:
        return messages

    # 收集所有 tool_use 和 tool_result
    tool_uses = {}  # tool_id -> message_index
    tool_results = set()  # tool_use_id 集合

    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            if block_type == "tool_use":
                tool_id = block.get("id")
                if tool_id:
                    # 解码 tool_id (可能包含编码的签名)
                    original_id, _ = decode_tool_id_and_signature(tool_id)
                    tool_uses[original_id] = msg_idx
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id:
                    # 解码 tool_use_id
                    original_id, _ = decode_tool_id_and_signature(tool_use_id)
                    tool_results.add(original_id)

    # 找出孤儿 tool_use (没有对应 tool_result)
    orphan_tool_uses = []
    for tool_id in tool_uses:
        if tool_id not in tool_results:
            orphan_tool_uses.append(tool_id)

    if not orphan_tool_uses:
        # 工具链完整, 无需修复
        return messages

    log.warning(
        f"[ANTHROPIC CONVERTER] 检测到孤儿 tool_use: {len(orphan_tool_uses)} 个, "
        f"将过滤以避免 Claude API 400 错误"
    )

    # 过滤掉孤儿 tool_use
    cleaned_messages = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned_messages.append(msg)
            continue

        new_content = []
        has_orphan = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id")
                if tool_id:
                    original_id, _ = decode_tool_id_and_signature(tool_id)
                    if original_id in orphan_tool_uses:
                        log.info(f"[ANTHROPIC CONVERTER] 过滤孤儿 tool_use: {original_id}")
                        has_orphan = True
                        continue
            new_content.append(block)

        # 如果过滤后 content 为空, 添加一个占位符文本块
        if not new_content and has_orphan:
            new_content = [{"type": "text", "text": "..."}]

        if new_content:
            new_msg = msg.copy()
            new_msg["content"] = new_content
            cleaned_messages.append(new_msg)

    log.info(
        f"[ANTHROPIC CONVERTER] 工具链修复完成: "
        f"原始消息={len(messages)}, 修复后={len(cleaned_messages)}, "
        f"过滤的 tool_use={len(orphan_tool_uses)}"
    )

    return cleaned_messages


# ==================== [FIX 2026-01-21] 跨模型 Thinking 隔离 ====================
#
# 问题描述：
# 当模型路由波动（Claude → Gemini → Claude）时，Gemini 返回的 thinking 块没有
# 有效的 signature，这会污染会话状态，导致后续 Claude 请求的 thinking 被禁用。
#
# 解决方案：
# 在消息处理之前，根据目标模型过滤掉不兼容的 thinking 块。
# - Claude → Claude: 保留 thinking（需要 signature 验证）
# - Claude → Gemini: 移除 thinking（Gemini 不需要历史 thinking）
# - Gemini → Claude: 移除 thinking（Gemini thinking 没有 signature，会导致 Claude 400）
# - Gemini → Gemini: 移除 thinking（Gemini 也不使用历史 thinking）
#
# Author: Claude Opus 4.5 (浮浮酱)
# Date: 2026-01-21
# ============================================================================


def filter_thinking_for_target_model(
    messages: List[Dict[str, Any]],
    target_model: str,
    *,
    last_model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    根据目标模型过滤消息中的 thinking 块

    这是解决跨模型 thinking 污染问题的关键函数。当用户在同一对话中切换模型时，
    需要过滤掉不兼容的 thinking 块，避免：
    1. Gemini 的无签名 thinking 污染 Claude 请求（导致 400 错误）
    2. Claude 的 thinking 块发送给 Gemini（浪费 tokens，Gemini 不使用）

    Args:
        messages: Anthropic 格式的消息列表
        target_model: 当前请求的目标模型
        last_model: 上一次请求使用的模型（可选，用于更精确的过滤）

    Returns:
        过滤后的消息列表（深拷贝，不修改原始数据）

    Examples:
        >>> # Claude → Claude: 保留 thinking
        >>> messages = [{"role": "assistant", "content": [{"type": "thinking", ...}]}]
        >>> filtered = filter_thinking_for_target_model(messages, "claude-opus-4-5")
        >>> len(filtered[0]["content"])  # thinking 被保留
        1

        >>> # Gemini → Claude: 移除 thinking
        >>> messages = [{"role": "assistant", "content": [{"type": "thinking", ...}]}]
        >>> filtered = filter_thinking_for_target_model(messages, "claude-opus-4-5", last_model="gemini-3-pro-high")
        >>> len(filtered[0]["content"])  # thinking 被移除
        0
    """
    if not messages:
        return messages

    target_family = get_model_family(target_model)

    # 统计信息
    stats = {
        "total_thinking_blocks": 0,
        "preserved": 0,
        "filtered": 0,
        "filtered_reasons": []
    }

    filtered_messages: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 只处理 assistant 消息中的 thinking 块
        if role != "assistant" or not isinstance(content, list):
            filtered_messages.append(msg)
            continue

        # 过滤 content 中的 thinking 块
        new_content: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                new_content.append(item)
                continue

            item_type = item.get("type", "")

            # 处理 thinking 和 redacted_thinking 块
            if item_type in ("thinking", "redacted_thinking"):
                stats["total_thinking_blocks"] += 1

                # 检查是否有有效的 signature
                signature = item.get("signature", "")
                has_valid_sig = signature and len(signature) >= MIN_SIGNATURE_LENGTH

                # 决策逻辑
                should_preserve = False
                filter_reason = None

                if target_family == "claude":
                    # Claude 需要有效的 signature
                    if has_valid_sig:
                        should_preserve = True
                    else:
                        filter_reason = "no_valid_signature_for_claude"
                elif target_family == "gemini":
                    # Gemini 不使用历史 thinking，全部过滤
                    filter_reason = "gemini_ignores_thinking"
                else:
                    # 其他模型，保守起见过滤掉
                    filter_reason = "unknown_model_family"

                if should_preserve:
                    new_content.append(item)
                    stats["preserved"] += 1
                else:
                    stats["filtered"] += 1
                    stats["filtered_reasons"].append(filter_reason)
                    log.debug(
                        f"[THINKING FILTER] 过滤 thinking 块: "
                        f"type={item_type}, reason={filter_reason}, "
                        f"has_sig={has_valid_sig}, target={target_model}"
                    )
            else:
                # 非 thinking 块，保留
                new_content.append(item)

        # 创建新消息（如果 content 有变化）
        if len(new_content) != len(content):
            new_msg = msg.copy()
            new_msg["content"] = new_content
            filtered_messages.append(new_msg)
        else:
            filtered_messages.append(msg)

    # 记录统计日志
    if stats["total_thinking_blocks"] > 0:
        log.info(
            f"[THINKING FILTER] 跨模型 thinking 过滤完成: "
            f"target={target_model} ({target_family}), "
            f"total={stats['total_thinking_blocks']}, "
            f"preserved={stats['preserved']}, "
            f"filtered={stats['filtered']}"
        )

    return filtered_messages


def convert_messages_to_contents(messages: List[Dict[str, Any]], *, include_thinking: bool = True) -> List[Dict[str, Any]]:
    """
    将 Anthropic messages[] 转换为下游 contents[]（role: user/model, parts: []）。

    Args:
        messages: Anthropic 格式的消息列表
        include_thinking: 是否包含 thinking 块（当请求未启用 thinking 时应设为 False）
    """
    contents: List[Dict[str, Any]] = []

    # [FIX 2026-01-17] 生成会话指纹用于 Session Cache
    # [FIX 2026-01-20] 添加详细诊断日志，用于调试 session_id 变化问题
    session_id: Optional[str] = None
    try:
        from src.signature_cache import generate_session_fingerprint
        session_id = generate_session_fingerprint(messages)
        if session_id:
            log.info(f"[SESSION MONITOR] session_id={session_id}, msg_count={len(messages)}")
    except ImportError:
        log.debug("[ANTHROPIC CONVERTER] Session fingerprint generation not available")
    except Exception as e:
        log.warning(f"[ANTHROPIC CONVERTER] Failed to generate session_id: {e}")

    # [FIX 2026-01-20] 统计消息中的 thinking 块数量（诊断用）
    thinking_block_stats = {"total": 0, "with_signature": 0, "without_signature": 0}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "thinking":
                    thinking_block_stats["total"] += 1
                    if item.get("signature"):
                        thinking_block_stats["with_signature"] += 1
                    else:
                        thinking_block_stats["without_signature"] += 1

    if thinking_block_stats["total"] > 0:
        log.info(
            f"[THINKING MONITOR] 请求统计: "
            f"msg_count={len(messages)}, "
            f"thinking_blocks={thinking_block_stats['total']}, "
            f"with_sig={thinking_block_stats['with_signature']}, "
            f"without_sig={thinking_block_stats['without_signature']}"
        )

    # 第一遍：建立 tool_use_id -> name 的映射
    # Anthropic 的 tool_result 消息不包含 name 字段，但 Gemini 的 functionResponse 需要 name
    # [FIX 2026-01-16] 使用解码后的原始ID作为key，确保与tool_result匹配
    tool_use_id_to_name: Dict[str, str] = {}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    encoded_tool_id = item.get("id") or ""
                    tool_name = item.get("name")
                    # [FIX 2026-01-16] 解码获取原始ID
                    original_tool_id, _ = decode_tool_id_and_signature(encoded_tool_id)
                    if original_tool_id and tool_name:
                        tool_use_id_to_name[str(original_tool_id)] = str(tool_name)

    # [FIX 2026-01-17] 跟踪最近的 thinking 签名，用于工具调用恢复
    # 关键修复：Claude 的 thinking 块和 tool_use 块在同一个 assistant 消息中
    # thinking 块的签名应该传递给同一消息中的 tool_use 块
    last_thinking_signature: Optional[str] = None

    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        raw_content = msg.get("content", "")

        # [FIX 2026-01-17] 每个消息开始时重置当前消息的 thinking 签名
        # 这样可以确保 tool_use 块使用的是同一消息中的 thinking 签名
        current_msg_thinking_signature: Optional[str] = None

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

                    thinking_text = item.get("thinking", "")
                    if thinking_text is None:
                        thinking_text = ""
                    message_signature = item.get("signature", "")

                    # [FIX 2026-01-20] Thinking Block 日志监控（详细记录接收状态）
                    log.info(
                        f"[THINKING MONITOR] 收到 thinking 块: "
                        f"thinking_len={len(thinking_text)}, "
                        f"has_signature={bool(message_signature)}, "
                        f"signature_len={len(message_signature) if message_signature else 0}"
                    )

                    # [FIX 2026-01-16] 多层签名恢复策略
                    # 优先级: 缓存签名 -> 消息签名(如果有效) -> 降级为 text
                    # [FIX 2026-01-20] 移除"替换文本"的 fallback 策略，避免上下文错乱
                    from src.signature_cache import get_cached_signature, get_last_signature

                    if thinking_text:
                        final_signature = None
                        recovery_source = None

                        # 优先级 1: 从缓存恢复（精确匹配）
                        cached_signature = get_cached_signature(thinking_text)
                        if cached_signature:
                            final_signature = cached_signature
                            recovery_source = "cache"
                            log.debug(f"[THINKING MONITOR] 签名恢复成功 (cache): sig_len={len(cached_signature)}")

                        # 优先级 2: 如果缓存未命中，检查消息提供的签名是否有效
                        if not final_signature and message_signature and len(message_signature) >= MIN_SIGNATURE_LENGTH:
                            # 消息签名看起来有效，但我们不能完全信任它
                            # 只有在缓存完全未命中时才考虑使用
                            final_signature = message_signature
                            recovery_source = "message"
                            log.info(f"[THINKING MONITOR] 缓存未命中，使用消息签名: thinking_len={len(thinking_text)}, sig_len={len(message_signature)}")

                        # [FIX 2026-01-20] 移除优先级 3（替换文本策略）
                        # 原策略：使用最近缓存的签名和配对文本，但会替换当前 thinking_text
                        # 问题：替换文本可能导致上下文错乱，模型看到的是旧的 thinking 内容
                        # 新策略：如果前两层都未命中，直接降级为普通 text 块，保留原始内容
                        # 这样既不会触发 API signature 验证错误，又保持了上下文连贯性

                        # 如果成功恢复签名，添加 thinking block
                        if final_signature:
                            part: Dict[str, Any] = {
                                "text": str(thinking_text),
                                "thought": True,
                                "thoughtSignature": final_signature,
                            }
                            parts.append(part)

                            # [FIX 2026-01-20] 详细日志：签名恢复成功
                            log.info(
                                f"[THINKING MONITOR] 签名恢复成功: "
                                f"source={recovery_source}, "
                                f"thinking_len={len(thinking_text)}, "
                                f"sig_len={len(final_signature)}"
                            )

                            # [FIX 2026-01-17] 缓存思维块签名 (P0 Critical Fix)
                            # 只缓存来自消息的签名，不缓存fallback签名（避免污染）
                            if recovery_source == "message":
                                from src.signature_cache import cache_signature, cache_session_signature
                                try:
                                    cache_signature(thinking_text, final_signature)
                                    # [FIX 2026-01-17] 同时缓存到 Session Cache
                                    if session_id:
                                        cache_session_signature(session_id, final_signature, thinking_text)
                                    log.debug(f"[THINKING MONITOR] 已缓存消息签名: thinking_len={len(thinking_text)}")
                                except Exception as e:
                                    log.warning(f"[THINKING MONITOR] 思维块签名缓存失败: {e}")

                            # [FIX 2026-01-17] 保存当前消息的 thinking 签名，用于同一消息中的 tool_use 块
                            # 这是关键修复：Claude 的 thinking 块和 tool_use 块在同一个 assistant 消息中
                            current_msg_thinking_signature = final_signature
                            last_thinking_signature = final_signature
                        else:
                            # [FIX 2026-01-20] 改进的降级策略：直接降级为 text 块，保留原始内容
                            # 不再使用"替换文本"的 fallback 策略，避免上下文错乱
                            if thinking_text and thinking_text.strip():
                                parts.append({"text": f"[Thinking: {thinking_text}]"})
                                # [FIX 2026-01-20] 详细诊断日志：帮助定位签名丢失原因
                                thinking_hash = hashlib.md5(thinking_text.encode('utf-8')).hexdigest()[:16] if thinking_text else "N/A"
                                log.warning(
                                    f"[THINKING MONITOR] 签名恢复失败，降级为 text 块: "
                                    f"session_id={session_id or 'N/A'}, "
                                    f"thinking_hash={thinking_hash}, "
                                    f"thinking_len={len(thinking_text)}, "
                                    f"msg_sig_len={len(message_signature) if message_signature else 0}, "
                                    f"原因=cache_miss+msg_sig_invalid"
                                )
                            else:
                                log.warning(f"[THINKING MONITOR] 跳过空 thinking 块")
                elif item_type == "redacted_thinking":
                    # 如果请求未启用 thinking，则跳过历史 redacted_thinking 块
                    if not include_thinking:
                        continue

                    # redacted_thinking 的具体字段在不同客户端可能不同，这里尽量兼容 data/thinking。
                    thinking_text = item.get("thinking")
                    if thinking_text is None:
                        thinking_text = item.get("data", "")
                    message_signature = item.get("signature", "")

                    # [FIX 2026-01-20] Redacted Thinking Block 日志监控
                    log.info(
                        f"[THINKING MONITOR] 收到 redacted_thinking 块: "
                        f"thinking_len={len(thinking_text) if thinking_text else 0}, "
                        f"has_signature={bool(message_signature)}, "
                        f"signature_len={len(message_signature) if message_signature else 0}"
                    )

                    # [FIX 2026-01-16] 多层签名恢复策略（与 thinking 块相同）
                    # [FIX 2026-01-20] 移除"替换文本"的 fallback 策略，避免上下文错乱
                    from src.signature_cache import get_cached_signature, get_last_signature

                    if thinking_text:
                        final_signature = None
                        recovery_source = None

                        # 优先级 1: 从缓存恢复（精确匹配）
                        cached_signature = get_cached_signature(thinking_text)
                        if cached_signature:
                            final_signature = cached_signature
                            recovery_source = "cache"
                            log.debug(f"[THINKING MONITOR] Redacted 签名恢复成功 (cache): sig_len={len(cached_signature)}")

                        # 优先级 2: 如果缓存未命中，检查消息提供的签名是否有效
                        if not final_signature and message_signature and len(message_signature) >= MIN_SIGNATURE_LENGTH:
                            final_signature = message_signature
                            recovery_source = "message"
                            log.info(f"[THINKING MONITOR] Redacted 缓存未命中，使用消息签名: thinking_len={len(thinking_text)}, sig_len={len(message_signature)}")

                        # [FIX 2026-01-20] 移除优先级 3（替换文本策略）
                        # 与 thinking 块保持一致的策略

                        # 如果成功恢复签名，添加 redacted_thinking block
                        if final_signature:
                            parts.append(
                                {
                                    "text": str(thinking_text or ""),
                                    "thought": True,
                                    "thoughtSignature": final_signature,
                                }
                            )

                            # [FIX 2026-01-20] 详细日志：签名恢复成功
                            log.info(
                                f"[THINKING MONITOR] Redacted 签名恢复成功: "
                                f"source={recovery_source}, "
                                f"thinking_len={len(thinking_text)}, "
                                f"sig_len={len(final_signature)}"
                            )

                            # [FIX 2026-01-17] 缓存 redacted_thinking 块签名 (P0 Critical Fix)
                            # 只缓存来自消息的签名，不缓存fallback签名（避免污染）
                            if recovery_source == "message":
                                from src.signature_cache import cache_signature
                                try:
                                    cache_signature(thinking_text, final_signature)
                                    log.debug(f"[THINKING MONITOR] 已缓存 Redacted 消息签名: thinking_len={len(thinking_text)}")
                                except Exception as e:
                                    log.warning(f"[THINKING MONITOR] Redacted 思维块签名缓存失败: {e}")
                        else:
                            # [FIX 2026-01-20] 改进的降级策略：直接降级为 text 块，保留原始内容
                            if thinking_text and thinking_text.strip():
                                parts.append({"text": f"[Redacted Thinking: {thinking_text}]"})
                                log.warning(
                                    f"[THINKING MONITOR] Redacted 签名恢复失败，降级为 text 块: "
                                    f"thinking_len={len(thinking_text)}, "
                                    f"原因=无有效签名(cache_miss + message_signature_invalid)"
                                )
                            else:
                                log.warning(f"[THINKING MONITOR] 跳过空 redacted_thinking 块")
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
                    # [FIX 2026-01-16] 从编码的工具ID中解码签名
                    # 这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段
                    encoded_id = item.get("id") or ""
                    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)

                    # [FIX 2026-01-17] 缓存工具ID签名 (Layer 1)
                    # 这是关键修复：确保签名被缓存，以便后续请求可以恢复
                    if thoughtsignature and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
                        from src.signature_cache import cache_tool_signature
                        try:
                            cache_tool_signature(original_id, thoughtsignature)
                            log.debug(f"[ANTHROPIC CONVERTER] Cached tool signature for id: {original_id}")
                        except Exception as e:
                            log.warning(f"[SIGNATURE_CACHE] 工具ID签名缓存失败: {e}")

                    # [FIX 2026-01-17] 增强签名恢复策略 - 6层完整实现
                    # 即使客户端删除了 thoughtSignature，我们也可以通过缓存恢复
                    # [FIX 2026-01-17] 关键修复：使用同一消息中的 thinking 签名作为上下文签名
                    # 优先使用当前消息的 thinking 签名，其次使用最近的 thinking 签名
                    context_signature = current_msg_thinking_signature or last_thinking_signature
                    final_sig = recover_signature_for_tool_use(
                        tool_id=original_id,
                        encoded_tool_id=encoded_id,
                        signature=thoughtsignature,
                        last_thought_signature=context_signature,  # [FIX 2026-01-17] 传入上下文签名
                        session_id=session_id  # [FIX 2026-01-17] 传入 session_id 启用 Layer 4
                    )

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": original_id,  # [FIX 2026-01-16] 使用解码后的原始ID
                            "name": item.get("name"),
                            "args": item.get("input", {}) or {},
                        },
                    }

                    if final_sig:
                        fc_part["thoughtSignature"] = final_sig
                        log.debug(f"[ANTHROPIC CONVERTER] Recovered thoughtSignature for tool_id: {original_id}")
                    else:
                        # ⚠️ 所有策略都失败，使用占位符
                        log.warning(f"[ANTHROPIC CONVERTER] No signature found for tool call: {original_id}, using placeholder")
                        fc_part["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR

                    parts.append(fc_part)
                elif item_type == "tool_result":
                    encoded_tool_use_id = item.get("tool_use_id") or ""

                    # [FIX 2026-01-16] 解码获取原始ID（functionResponse不需要签名）
                    original_tool_use_id, _ = decode_tool_id_and_signature(encoded_tool_use_id)

                    # [FIX 2026-01-08] 验证对应的 tool_use 是否存在
                    # 如果 tool_use 不存在，跳过这个 tool_result，避免 Anthropic API 返回 400 错误：
                    # "unexpected `tool_use_id` found in `tool_result` blocks"
                    # 注意：tool_use_id_to_name 映射中的 key 已经是原始ID（因为我们在第一遍扫描时也解码了）
                    if not original_tool_use_id or str(original_tool_use_id) not in tool_use_id_to_name:
                        log.warning(f"[ANTHROPIC CONVERTER] Skipping orphan tool_result: "
                                   f"tool_use_id={original_tool_use_id} (encoded={encoded_tool_use_id}) not found in tool_use_id_to_name mapping. "
                                   f"This may happen when tool_use was filtered out (e.g., thinking disabled) "
                                   f"but tool_result was retained.")
                        continue

                    output = _extract_tool_result_output(item.get("content"))
                    # 从映射中获取 name（此时一定存在，因为上面已经验证过）
                    tool_name = tool_use_id_to_name[str(original_tool_use_id)]
                    parts.append(
                        {
                            "functionResponse": {
                                "id": original_tool_use_id,  # [FIX 2026-01-16] 使用解码后的原始ID
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

    # [FIX 2026-01-11] 应用 ALLOWED_PART_KEYS 白名单过滤和尾随空格清理
    # 这是上游同步的关键修复，防止 cache_control 等不支持字段导致 400/429 错误
    contents = clean_contents(contents)
    
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

            # [FIX 2026-01-20] 只有当存在对应的 functionResponse 时才添加 functionCall
            # 这是 P0 Critical Fix: 防止 Claude API 返回 400 错误:
            # "tool_use ids were found without tool_result blocks immediately after"
            # 场景: Cursor 重试时可能发送不完整的历史消息，tool_use 存在但 tool_result 缺失
            if tool_id is not None and str(tool_id) in tool_results:
                new_contents.append({"role": "model", "parts": [part]})
                new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})
            else:
                # 孤儿 functionCall - 跳过，不添加到输出
                log.warning(
                    f"[ANTHROPIC CONVERTER] Skipping orphan functionCall in reorganize_tool_messages: "
                    f"tool_id={tool_id} has no corresponding functionResponse. "
                    f"This may happen when conversation was interrupted during tool execution."
                )

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
        # 🐛 修复：添加上限保护，防止过大的 max_tokens 导致 Antigravity API 返回 429
        # 参考 gemini_router.py 和 openai_router.py 的上限设置
        MAX_OUTPUT_TOKENS_LIMIT = 65535
        if isinstance(max_tokens, int) and max_tokens > MAX_OUTPUT_TOKENS_LIMIT:
            log.warning(
                f"[ANTHROPIC CONVERTER] maxOutputTokens 超过上限: {max_tokens} -> {MAX_OUTPUT_TOKENS_LIMIT}"
            )
            max_tokens = MAX_OUTPUT_TOKENS_LIMIT

        # [FIX 2026-01-11] 添加下限保护，防止客户端（如Cursor）传来过小的 max_tokens 导致输出被截断
        # 写 MD 文档可能需要 10K-30K tokens，4096 远远不够
        MIN_OUTPUT_TOKENS_FLOOR = 16384  # 最小输出空间保障
        if isinstance(max_tokens, int) and max_tokens < MIN_OUTPUT_TOKENS_FLOOR:
            log.info(
                f"[ANTHROPIC CONVERTER] maxOutputTokens 低于下限: {max_tokens} -> {MIN_OUTPUT_TOKENS_FLOOR}"
            )
            max_tokens = MIN_OUTPUT_TOKENS_FLOOR

        config["maxOutputTokens"] = max_tokens
    else:
        # [FIX 2026-01-12] 客户端未传 max_tokens 时，使用默认值保证足够输出空间
        # 这是之前修复失效的根因：if 块被跳过，config 中没有 maxOutputTokens
        DEFAULT_MAX_OUTPUT_TOKENS = 16384
        log.info(
            f"[ANTHROPIC CONVERTER] max_tokens 未指定，使用默认值: {DEFAULT_MAX_OUTPUT_TOKENS}"
        )
        config["maxOutputTokens"] = DEFAULT_MAX_OUTPUT_TOKENS

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
                if isinstance(budget, int) and budget > 0:
                    # [FIX 2026-01-09] 双向限制策略
                    # 核心思路：既要保证足够的输出空间，又不能让 max_tokens 过大触发 429
                    #
                    # 策略：
                    # 1. 如果 thinkingBudget + MIN_OUTPUT_TOKENS > MAX_ALLOWED_TOKENS，下调 thinkingBudget
                    # 2. maxOutputTokens 不能超过 MAX_ALLOWED_TOKENS
                    # 3. 保证至少 MIN_OUTPUT_TOKENS 的输出空间
                    
                    original_budget = budget
                    original_max_tokens = max_tokens
                    
                    # Step 1: 计算需要的总 tokens
                    required_tokens = budget + MIN_OUTPUT_TOKENS
                    
                    if required_tokens > MAX_ALLOWED_TOKENS:
                        # 需要下调 thinkingBudget
                        adjusted_budget = MAX_ALLOWED_TOKENS - MIN_OUTPUT_TOKENS
                        if adjusted_budget <= 0:
                            # 无法保证输出空间，跳过 thinking
                            if _anthropic_debug_enabled():
                                log.info(
                                    f"[ANTHROPIC][thinking] 双向限制：无法保证 MIN_OUTPUT_TOKENS={MIN_OUTPUT_TOKENS}，"
                                    f"跳过下发 thinkingConfig（budget={budget}, MAX_ALLOWED={MAX_ALLOWED_TOKENS}）"
                                )
                            return config, False
                        
                        thinking_config["thinkingBudget"] = adjusted_budget
                        budget = adjusted_budget
                        log.info(
                            f"[ANTHROPIC][thinking] 双向限制生效：thinkingBudget 下调 {original_budget} -> {adjusted_budget} "
                            f"(MAX_ALLOWED={MAX_ALLOWED_TOKENS}, MIN_OUTPUT={MIN_OUTPUT_TOKENS})"
                        )
                    
                    # Step 2: 确保 max_tokens = budget + MIN_OUTPUT_TOKENS，但不超过 MAX_ALLOWED_TOKENS
                    new_max_tokens = min(budget + MIN_OUTPUT_TOKENS, MAX_ALLOWED_TOKENS)
                    
                    # Step 3: 如果客户端的 max_tokens 小于计算值，需要调整
                    if max_tokens < new_max_tokens:
                        config["maxOutputTokens"] = new_max_tokens
                        log.info(
                            f"[ANTHROPIC][thinking] 双向限制生效：maxOutputTokens 提升 {original_max_tokens} -> {new_max_tokens} "
                            f"(thinkingBudget={budget}, 实际输出空间={new_max_tokens - budget})"
                        )
                    elif max_tokens > MAX_ALLOWED_TOKENS:
                        # 客户端 max_tokens 过大，需要限制
                        config["maxOutputTokens"] = MAX_ALLOWED_TOKENS
                        log.info(
                            f"[ANTHROPIC][thinking] 双向限制生效：maxOutputTokens 下调 {original_max_tokens} -> {MAX_ALLOWED_TOKENS} "
                            f"(防止 429 错误)"
                        )

            # [FIX 2026-01-17] 移除 thinkingLevel 避免与 thinkingBudget 冲突（官方版本修复）
            # 参考: gcli2api_official PR #291 (fix/thinking-budget-level-conflict)
            thinking_config.pop("thinkingLevel", None)
            
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


def convert_tool_choice_to_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: Anthropic 格式的 tool_choice
        - {"type": "auto"}: 模型自动决定是否使用工具
        - {"type": "any"}: 模型必须使用工具
        - {"type": "tool", "name": "tool_name"}: 模型必须使用指定工具

    Returns:
        Gemini 格式的 toolConfig，如果无效则返回 None
    """
    if not tool_choice:
        return None

    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")

        if choice_type == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif choice_type == "any":
            return {"functionCallingConfig": {"mode": "ANY"}}
        elif choice_type == "tool":
            tool_name = tool_choice.get("name")
            if tool_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [tool_name],
                    }
                }
        # 无效或不支持的 tool_choice，返回 None
        return None


def convert_anthropic_request_to_antigravity_components(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Anthropic Messages 请求转换为构造下游请求所需的组件。

    返回字段：
    - model: 下游模型名
    - contents: 下游 contents[]
    - system_instruction: 下游 systemInstruction（可选）
    - tools: 下游 tools（可选）
    - system_instruction: 下游 systemInstruction（可选）
    - tools: 下游 tools（可选）
    - tool_config: 下游 toolConfig（可选）
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

    # [FIX 2026-01-16] 过滤无效的 thinking 块，清理额外字段（如 cache_control）
    # 这是 P1 修复的关键：确保无效签名的 thinking 块不会导致 API 400 错误
    filter_invalid_thinking_blocks(messages)

    # [FIX 2026-01-21] 跨模型 Thinking 隔离
    # 当用户在同一对话中切换模型时（如 Claude → Gemini → Claude），
    # 需要过滤掉不兼容的 thinking 块，避免：
    # 1. Gemini 的无签名 thinking 污染 Claude 请求（导致 400 错误）
    # 2. Claude 的 thinking 块发送给 Gemini（浪费 tokens，Gemini 不使用）
    messages = filter_thinking_for_target_model(messages, model)

    # [FIX 2026-01-20] 验证并修复工具调用链完整性
    # 这是 P0 Critical Fix: 防止 Claude API 返回 400 错误:
    # "tool_use ids were found without tool_result blocks immediately after"
    # 场景: Cursor 重试时可能发送不完整的历史消息
    messages = _validate_and_fix_tool_chain(messages)

    # [FIX 2026-01-17] Tool Loop Recovery - 检测并修复断裂的工具循环
    # 当 Cursor IDE 过滤掉 thinking 块时，工具循环可能断裂
    try:
        from src.converters.tool_loop_recovery import close_tool_loop_for_thinking
        if close_tool_loop_for_thinking(messages):
            log.info("[ANTHROPIC CONVERTER] Tool loop recovered - injected thinking block (no synthetic messages)")
    except ImportError:
        log.debug("[ANTHROPIC CONVERTER] Tool loop recovery module not available")
    except Exception as e:
        log.warning(f"[ANTHROPIC CONVERTER] Tool loop recovery failed: {e}")

    # 先构建 generation_config 以确定是否应包含 thinking
    generation_config, should_include_thinking = build_generation_config(payload)

    # 根据 thinking 配置转换消息
    contents = convert_messages_to_contents(messages, include_thinking=should_include_thinking)
    contents = reorganize_tool_messages(contents)
    system_instruction = build_system_instruction(payload.get("system"))
    tools = convert_tools(payload.get("tools"))
    tool_choice = payload.get("tool_choice")
    tool_config = convert_tool_choice_to_tool_config(tool_choice)

    return {
        "model": model,
        "contents": contents,
        "system_instruction": system_instruction,
        "tools": tools,
        "tool_config": tool_config,
        "generation_config": generation_config,
    }
