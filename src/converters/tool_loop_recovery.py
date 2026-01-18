"""
Tool Loop Recovery Module

用于检测和修复 Cursor IDE 过滤 thinking 块导致的工具循环断裂问题。

核心功能:
1. 分析对话状态,检测是否在工具循环中
2. 检测 thinking 块是否被过滤
3. 注入合成消息关闭断裂的工具循环

设计原则:
- 不修改原始消息结构(只追加)
- 添加详细日志
- 处理边界情况
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConversationState:
    """对话状态数据类"""
    in_tool_loop: bool = False  # 是否在工具循环中
    has_thinking: bool = False  # 最后的 assistant 消息是否有 thinking 块
    last_assistant_index: int = -1  # 最后一条 assistant 消息的索引
    pending_tool_results: List[str] = field(default_factory=list)  # 待处理的工具结果ID

    def __str__(self) -> str:
        return (
            f"ConversationState("
            f"in_tool_loop={self.in_tool_loop}, "
            f"has_thinking={self.has_thinking}, "
            f"last_assistant_index={self.last_assistant_index}, "
            f"pending_tool_results={len(self.pending_tool_results)} items)"
        )


def analyze_conversation_state(messages: List[Dict]) -> ConversationState:
    """
    分析对话状态

    Args:
        messages: 消息列表

    Returns:
        ConversationState: 对话状态对象

    检测逻辑:
    1. 遍历消息找到最后一条 assistant 消息
    2. 检查该消息是否包含 thinking 块
    3. 检查最后一条消息是否为 tool_result (判断是否在工具循环中)
    4. 收集所有待处理的 tool_result ID
    """
    state = ConversationState()

    # 边界情况: 空消息列表
    if not messages:
        logger.debug("Empty messages list, returning default state")
        return state

    try:
        # 遍历消息,找到最后一条 assistant 消息
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                logger.warning(f"Message at index {idx} is not a dict: {type(msg)}")
                continue

            role = msg.get("role")

            if role == "assistant":
                state.last_assistant_index = idx

                # 检查是否有 thinking 块
                content = msg.get("content", [])
                if isinstance(content, list):
                    state.has_thinking = any(
                        isinstance(block, dict) and block.get("type") == "thinking"
                        for block in content
                    )
                elif isinstance(content, str):
                    # 字符串内容视为没有 thinking 块
                    state.has_thinking = False
                else:
                    logger.warning(f"Unexpected content type at index {idx}: {type(content)}")
                    state.has_thinking = False

        # 检查是否在工具循环中
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                last_role = last_msg.get("role")

                # 如果最后一条消息是 user 且包含 tool_result
                if last_role == "user":
                    content = last_msg.get("content", [])
                    if isinstance(content, list):
                        tool_results = [
                            block for block in content
                            if isinstance(block, dict) and block.get("type") == "tool_result"
                        ]
                        if tool_results:
                            state.in_tool_loop = True
                            # 收集待处理的 tool_result ID
                            state.pending_tool_results = [
                                tr.get("tool_use_id", "unknown")
                                for tr in tool_results
                            ]

        logger.debug(f"Analyzed conversation state: {state}")
        return state

    except Exception as e:
        logger.error(f"Error analyzing conversation state: {e}", exc_info=True)
        return ConversationState()  # 返回默认状态


def detect_thinking_stripped(messages: List[Dict]) -> bool:
    """
    检测 thinking 块是否被过滤

    Args:
        messages: 消息列表

    Returns:
        bool: 如果检测到 thinking 块被过滤返回 True

    判断条件:
    - 存在 assistant 消息
    - 该消息包含 tool_use 块
    - 但不包含 thinking 块
    """
    if not messages:
        return False

    try:
        # 找到最后一条 assistant 消息
        last_assistant = None
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_assistant = msg
                break

        if not last_assistant:
            logger.debug("No assistant message found")
            return False

        content = last_assistant.get("content", [])
        if not isinstance(content, list):
            logger.debug(f"Assistant content is not a list: {type(content)}")
            return False

        # 检查是否有 tool_use 块
        has_tool_use = any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )

        # 检查是否有 thinking 块
        has_thinking = any(
            isinstance(block, dict) and block.get("type") == "thinking"
            for block in content
        )

        # 如果有 tool_use 但没有 thinking,可能被过滤了
        is_stripped = has_tool_use and not has_thinking

        if is_stripped:
            logger.info(
                "Detected thinking block stripped: "
                f"has_tool_use={has_tool_use}, has_thinking={has_thinking}"
            )

        return is_stripped

    except Exception as e:
        logger.error(f"Error detecting thinking stripped: {e}", exc_info=True)
        return False


def close_tool_loop_for_thinking(messages: List[Dict]) -> bool:
    """
    检测并修复断裂的工具循环

    Args:
        messages: 消息列表 (会被原地修改)

    Returns:
        bool: 如果进行了修复返回 True

    修复逻辑:
    1. 分析对话状态
    2. 如果在工具循环中且最后的 assistant 消息没有 thinking 块
    3. 注入合成消息关闭循环:
       - Assistant: [System: Tool loop recovered. Previous tool execution accepted.]
       - User: [Proceed]
    """
    if not messages:
        logger.debug("Empty messages list, no recovery needed")
        return False

    try:
        # 分析对话状态
        state = analyze_conversation_state(messages)

        # 判断是否需要修复
        needs_recovery = (
            state.in_tool_loop and
            state.last_assistant_index >= 0 and
            not state.has_thinking
        )

        if not needs_recovery:
            logger.debug(
                f"No tool loop recovery needed: "
                f"in_tool_loop={state.in_tool_loop}, "
                f"has_thinking={state.has_thinking}"
            )
            return False

        # 检测是否真的是 thinking 被过滤
        if not detect_thinking_stripped(messages):
            logger.debug("Thinking block not stripped, no recovery needed")
            return False

        logger.info(
            f"Tool loop recovery triggered: {len(state.pending_tool_results)} "
            f"pending tool results"
        )

        # [FIX 2026-01-17] 关键修复：在原始 assistant 消息中注入 thinking 块
        # 这样可以确保 tool_use 块有签名上下文（Layer 2 能够命中）
        # 问题：之前只在末尾追加合成消息，但原始 assistant 消息中的 tool_use 块仍然没有 thinking 块
        # 解决：从缓存中恢复签名，在原始 assistant 消息的 content 开头插入 thinking 块
        assistant_msg = messages[state.last_assistant_index]

        # 从缓存中恢复签名
        try:
            from src.signature_cache import get_last_signature_with_text
            last_result = get_last_signature_with_text()

            if last_result:
                signature, thinking_text = last_result

                # 在 content 数组开头插入 thinking 块
                content = assistant_msg.get("content", [])
                if isinstance(content, list):
                    thinking_block = {
                        "type": "thinking",
                        "thinking": thinking_text,
                        "signature": signature
                    }
                    content.insert(0, thinking_block)
                    assistant_msg["content"] = content

                    logger.info(
                        f"Tool loop recovery: injected thinking block with signature "
                        f"(thinking_len={len(thinking_text)}, sig_len={len(signature)})"
                    )
            else:
                logger.warning(
                    "Tool loop recovery: no cached signature available, "
                    "tool_use blocks will rely on Layer 4-6 for signature recovery"
                )
        except ImportError:
            logger.warning("Tool loop recovery: signature_cache module not available")
        except Exception as e:
            logger.warning(f"Tool loop recovery: failed to inject thinking block: {e}")

        # [FIX 2026-01-17] 移除合成消息注入！
        # 问题：之前注入的 [Proceed] User 消息会被 Claude Code 当作新的用户输入
        # 导致模型认为这是一个新对话的开始，每次都重复自我介绍
        # 解决：只在原始 assistant 消息中注入 thinking 块就够了
        # 不再追加任何合成消息，避免干扰正常对话流程

        logger.info(
            f"Tool loop recovery completed: injected thinking block into assistant message "
            f"(no synthetic messages added to avoid conversation reset)"
        )

        return True

    except Exception as e:
        logger.error(f"Error during tool loop recovery: {e}", exc_info=True)
        return False


def get_recovery_stats(messages: List[Dict]) -> Dict[str, Any]:
    """
    获取恢复统计信息 (用于调试和监控)

    Args:
        messages: 消息列表

    Returns:
        Dict: 统计信息
    """
    try:
        state = analyze_conversation_state(messages)

        return {
            "total_messages": len(messages),
            "in_tool_loop": state.in_tool_loop,
            "has_thinking": state.has_thinking,
            "last_assistant_index": state.last_assistant_index,
            "pending_tool_results_count": len(state.pending_tool_results),
            "thinking_stripped": detect_thinking_stripped(messages),
            "needs_recovery": (
                state.in_tool_loop and
                state.last_assistant_index >= 0 and
                not state.has_thinking
            )
        }
    except Exception as e:
        logger.error(f"Error getting recovery stats: {e}", exc_info=True)
        return {
            "error": str(e),
            "total_messages": len(messages) if messages else 0
        }


# 导出公共接口
__all__ = [
    "ConversationState",
    "analyze_conversation_state",
    "detect_thinking_stripped",
    "close_tool_loop_for_thinking",
    "get_recovery_stats"
]
