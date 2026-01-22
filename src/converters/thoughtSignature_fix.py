"""
thoughtSignature 处理公共模块

提供统一的 thoughtSignature 编码/解码功能，用于在工具调用ID中保留签名信息。
这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段。

核心功能：
1. encode_tool_id_with_signature: 将签名编码到工具调用ID中
2. decode_tool_id_and_signature: 从编码的ID中提取原始ID和签名
3. has_valid_thoughtsignature: 验证 thinking 块是否有有效签名
4. sanitize_thinking_block: 清理 thinking 块的额外字段
5. remove_trailing_unsigned_thinking: 移除尾部无签名的 thinking 块
6. filter_invalid_thinking_blocks: 过滤消息中的无效 thinking 块

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-16
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

log = logging.getLogger("gcli2api.thoughtSignature_fix")

# ============================================================================
# 常量定义
# ============================================================================

# 在工具调用ID中嵌入thoughtSignature的分隔符
# 这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段
THOUGHT_SIGNATURE_SEPARATOR = "__thought__"

# 最小有效签名长度
MIN_SIGNATURE_LENGTH = 10

# 占位符签名（用于绕过验证）
SKIP_SIGNATURE_VALIDATOR = "skip_thought_signature_validator"


# ============================================================================
# 工具ID签名编码/解码
# ============================================================================

def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    """
    将 thoughtSignature 编码到工具调用ID中，以便往返保留。

    Args:
        tool_id: 原始工具调用ID
        signature: thoughtSignature（可选）

    Returns:
        编码后的工具调用ID

    Examples:
        >>> encode_tool_id_with_signature("call_123", "abc")
        'call_123__thought__abc'
        >>> encode_tool_id_with_signature("call_123", None)
        'call_123'
    """
    if not tool_id:
        return tool_id or ""

    if not signature:
        return tool_id

    # 跳过占位符签名
    if signature == SKIP_SIGNATURE_VALIDATOR:
        return tool_id

    return f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"


def decode_tool_id_and_signature(encoded_id: str) -> Tuple[str, Optional[str]]:
    """
    从编码的ID中提取原始工具ID和thoughtSignature。

    Args:
        encoded_id: 编码的工具调用ID

    Returns:
        (原始工具ID, thoughtSignature) 元组

    Examples:
        >>> decode_tool_id_and_signature("call_123__thought__abc")
        ('call_123', 'abc')
        >>> decode_tool_id_and_signature("call_123")
        ('call_123', None)
    """
    if not encoded_id:
        return encoded_id or "", None

    if THOUGHT_SIGNATURE_SEPARATOR not in encoded_id:
        return encoded_id, None

    parts = encoded_id.split(THOUGHT_SIGNATURE_SEPARATOR, 1)
    original_id = parts[0]
    signature = parts[1] if len(parts) == 2 and parts[1] else None

    return original_id, signature


# ============================================================================
# Thinking 块验证和清理
# ============================================================================

def has_valid_thoughtsignature(block: Dict[str, Any]) -> bool:
    """
    检查 thinking 块是否有有效签名

    Args:
        block: content block 字典

    Returns:
        bool: 是否有有效签名
    """
    if not isinstance(block, dict):
        return True

    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return True  # 非 thinking 块默认有效

    thinking = block.get("thinking", "")
    # [FIX 2026-01-20] 兼容两种签名字段名：thoughtSignature 和 signature
    # 问题：normalize_content 使用 "signature" 字段名，但这里只检查 "thoughtSignature"
    # 这会导致签名验证失败，进而触发 Claude API 400 错误
    thoughtsignature = block.get("thoughtSignature") or block.get("signature")

    # 空 thinking + 任意 thoughtsignature = 有效 (trailing signature case)
    if not thinking and thoughtsignature is not None:
        return True

    # 有内容 + 足够长度的 thoughtsignature = 有效
    if thoughtsignature and isinstance(thoughtsignature, str) and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
        return True

    return False


def sanitize_thinking_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理 thinking 块,只保留必要字段(移除 cache_control 等)

    Args:
        block: content block 字典

    Returns:
        清理后的 block 字典
    """
    if not isinstance(block, dict):
        return block

    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return block

    # 重建块,移除额外字段
    sanitized: Dict[str, Any] = {
        "type": block_type,
        "thinking": block.get("thinking", "")
    }

    # [FIX 2026-01-20] 兼容两种签名字段名：thoughtSignature 和 signature
    # 问题：normalize_content 使用 "signature" 字段名，但这里只检查 "thoughtSignature"
    # 这会导致签名丢失，进而触发 Claude API 400 错误
    thoughtsignature = block.get("thoughtSignature") or block.get("signature")
    if thoughtsignature:
        sanitized["thoughtSignature"] = thoughtsignature

    return sanitized


def remove_trailing_unsigned_thinking(blocks: List[Dict[str, Any]]) -> None:
    """
    移除尾部的无签名 thinking 块

    Args:
        blocks: content blocks 列表 (会被修改)
    """
    if not blocks:
        return

    # 从后向前扫描
    end_index = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i]
        if not isinstance(block, dict):
            break

        block_type = block.get("type")
        if block_type in ("thinking", "redacted_thinking"):
            if not has_valid_thoughtsignature(block):
                end_index = i
            else:
                break  # 遇到有效签名的 thinking 块,停止
        else:
            break  # 遇到非 thinking 块,停止

    if end_index < len(blocks):
        removed = len(blocks) - end_index
        del blocks[end_index:]
        log.debug(f"Removed {removed} trailing unsigned thinking block(s)")


def filter_invalid_thinking_blocks(messages: List[Dict[str, Any]]) -> None:
    """
    过滤消息中的无效 thinking 块，并清理所有 thinking 块的额外字段（如 cache_control）

    Args:
        messages: Anthropic messages 列表 (会被修改)
    """
    total_filtered = 0

    for msg in messages:
        # 只处理 assistant 和 model 消息
        role = msg.get("role", "")
        if role not in ("assistant", "model"):
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        original_len = len(content)
        new_blocks: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue

            block_type = block.get("type")
            if block_type not in ("thinking", "redacted_thinking"):
                new_blocks.append(block)
                continue

            # 所有 thinking 块都需要清理（移除 cache_control 等额外字段）
            # 检查 thinking 块的有效性
            if has_valid_thoughtsignature(block):
                # 有效签名，清理后保留
                new_blocks.append(sanitize_thinking_block(block))
            else:
                # 无效签名，将内容转换为 text 块
                thinking_text = block.get("thinking", "")
                if thinking_text and str(thinking_text).strip():
                    log.info(
                        f"[thoughtSignature_fix] Converting thinking block with invalid thoughtSignature to text. "
                        f"Content length: {len(thinking_text)} chars"
                    )
                    new_blocks.append({"type": "text", "text": thinking_text})
                else:
                    log.debug("[thoughtSignature_fix] Dropping empty thinking block with invalid thoughtSignature")

        msg["content"] = new_blocks
        filtered_count = original_len - len(new_blocks)
        total_filtered += filtered_count

        # 如果过滤后为空,添加一个空文本块以保持消息有效
        if not new_blocks:
            msg["content"] = [{"type": "text", "text": ""}]

    if total_filtered > 0:
        log.debug(f"Filtered {total_filtered} invalid thinking block(s) from history")


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    # 常量
    "THOUGHT_SIGNATURE_SEPARATOR",
    "MIN_SIGNATURE_LENGTH",
    "SKIP_SIGNATURE_VALIDATOR",
    # 编码/解码
    "encode_tool_id_with_signature",
    "decode_tool_id_and_signature",
    # 验证和清理
    "has_valid_thoughtsignature",
    "sanitize_thinking_block",
    "remove_trailing_unsigned_thinking",
    "filter_invalid_thinking_blocks",
]
