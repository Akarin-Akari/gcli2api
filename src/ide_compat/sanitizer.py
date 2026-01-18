"""
AnthropicSanitizer - IDE 兼容层核心兜底组件

该模块提供了 Anthropic 消息净化器,用于处理各种 IDE 客户端的特殊行为:
- 验证 thinking block 的签名有效性
- 无效签名时降级为 text block (而非报错)
- 确保 thinkingConfig 与消息内容一致
- 处理 tool_use/tool_result 链条完整性

核心设计原则:
1. 兜底保护 - 绝不抛出异常,所有错误都转换为降级处理
2. 签名恢复 - 6层签名恢复策略,最大化恢复成功率
3. 优雅降级 - 无法恢复时降级为 text block,保留内容
4. 详细日志 - 所有操作都有详细日志,便于问题追踪

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

# 导入签名处理相关模块
from src.converters.thoughtSignature_fix import (
    has_valid_thoughtsignature,
    sanitize_thinking_block,
    decode_tool_id_and_signature,
    MIN_SIGNATURE_LENGTH,
    SKIP_SIGNATURE_VALIDATOR,
)
from src.converters.signature_recovery import (
    recover_signature_for_thinking,
    recover_signature_for_tool_use,
    RecoverySource,
    is_valid_signature,
)

log = logging.getLogger("gcli2api.ide_compat.sanitizer")


class AnthropicSanitizer:
    """
    Anthropic 消息净化器 - IDE 兼容层核心组件

    职责:
    1. 验证 thinking block 的签名有效性
    2. 无效签名时降级为 text block (而非报错)
    3. 确保 thinkingConfig 与消息内容一致
    4. 处理 tool_use/tool_result 链条完整性

    使用示例:
        sanitizer = AnthropicSanitizer(signature_cache, state_manager)
        sanitized_messages, should_enable_thinking = sanitizer.sanitize_messages(
            messages, thinking_enabled=True
        )
    """

    def __init__(self, signature_cache=None, state_manager=None):
        """
        初始化净化器

        Args:
            signature_cache: 签名缓存实例 (可选,如果为 None 则自动获取全局实例)
            state_manager: 状态管理器实例 (可选,用于获取 session_id)
        """
        self.signature_cache = signature_cache
        self.state_manager = state_manager

        # 统计信息
        self.stats = {
            "total_messages": 0,
            "thinking_blocks_validated": 0,
            "thinking_blocks_recovered": 0,
            "thinking_blocks_downgraded": 0,
            "tool_use_blocks_recovered": 0,
            "tool_chains_fixed": 0,
        }

    def sanitize_messages(
        self,
        messages: List[Dict],
        thinking_enabled: bool,
        session_id: Optional[str] = None,
        last_thought_signature: Optional[str] = None
    ) -> Tuple[List[Dict], bool]:
        """
        净化消息列表

        Args:
            messages: 原始消息列表
            thinking_enabled: 是否启用 thinking 模式
            session_id: 会话ID (可选,用于 Session Cache)
            last_thought_signature: 上下文中的最后一个 thinking 签名 (可选)

        Returns:
            (sanitized_messages, should_enable_thinking)
            - sanitized_messages: 净化后的消息
            - should_enable_thinking: 是否应该启用 thinking (可能因验证失败而降级)
        """
        if not messages:
            return messages, thinking_enabled

        self.stats["total_messages"] += len(messages)

        try:
            # 1. 验证和恢复 thinking blocks
            sanitized_messages = self._validate_and_recover_thinking_blocks(
                messages,
                thinking_enabled,
                session_id,
                last_thought_signature
            )

            # 2. 确保 tool_use/tool_result 链条完整性
            sanitized_messages = self._ensure_tool_chain_integrity(sanitized_messages)

            # 3. 同步 thinkingConfig 与消息内容
            final_thinking_enabled = self._sync_thinking_config(
                sanitized_messages,
                thinking_enabled
            )

            log.info(
                f"[SANITIZER] 消息净化完成: "
                f"messages={len(sanitized_messages)}, "
                f"thinking_enabled={thinking_enabled}->{final_thinking_enabled}, "
                f"recovered={self.stats['thinking_blocks_recovered']}, "
                f"downgraded={self.stats['thinking_blocks_downgraded']}"
            )

            return sanitized_messages, final_thinking_enabled

        except Exception as e:
            # 兜底保护: 任何异常都不应该影响主流程
            log.error(f"[SANITIZER] 消息净化失败,返回原始消息: {e}", exc_info=True)
            return messages, thinking_enabled

    def _validate_and_recover_thinking_blocks(
        self,
        messages: List[Dict],
        thinking_enabled: bool,
        session_id: Optional[str] = None,
        last_thought_signature: Optional[str] = None
    ) -> List[Dict]:
        """
        验证和恢复 thinking blocks

        对每个消息中的 thinking block:
        1. 检查是否有有效签名
        2. 如果无效,尝试 6层签名恢复策略
        3. 如果恢复失败,降级为 text block

        Args:
            messages: 消息列表
            thinking_enabled: 是否启用 thinking
            session_id: 会话ID
            last_thought_signature: 上下文签名

        Returns:
            处理后的消息列表
        """
        if not thinking_enabled:
            # thinking 未启用,不需要验证
            return messages

        sanitized_messages = []
        current_thinking_signature = last_thought_signature

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role != "assistant" or not isinstance(content, list):
                # 非 assistant 消息或非列表内容,直接保留
                sanitized_messages.append(msg)
                continue

            # 处理 assistant 消息的 content blocks
            new_content = []
            for block in content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue

                block_type = block.get("type")

                # 处理 thinking block
                if block_type in ("thinking", "redacted_thinking"):
                    self.stats["thinking_blocks_validated"] += 1

                    # 验证签名
                    is_valid, recovered_signature = self._validate_thinking_block(
                        block,
                        session_id=session_id,
                        context_signature=current_thinking_signature
                    )

                    if is_valid:
                        # 签名有效,清理额外字段后保留
                        sanitized_block = sanitize_thinking_block(block)
                        new_content.append(sanitized_block)

                        # 更新当前 thinking 签名
                        if recovered_signature:
                            current_thinking_signature = recovered_signature
                    else:
                        # 签名无效且无法恢复,降级为 text block
                        downgraded_block = self._downgrade_thinking_to_text(block)
                        if downgraded_block:
                            new_content.append(downgraded_block)
                        self.stats["thinking_blocks_downgraded"] += 1

                # 处理 tool_use block
                elif block_type == "tool_use":
                    # tool_use 也需要签名恢复
                    recovered_block = self._recover_tool_use_signature(
                        block,
                        session_id=session_id,
                        context_signature=current_thinking_signature
                    )
                    new_content.append(recovered_block)

                else:
                    # 其他类型的 block 直接保留
                    new_content.append(block)

            # 更新消息的 content
            sanitized_msg = msg.copy()
            sanitized_msg["content"] = new_content
            sanitized_messages.append(sanitized_msg)

        return sanitized_messages

    def _validate_thinking_block(
        self,
        block: Dict,
        session_id: Optional[str] = None,
        context_signature: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        验证单个 thinking block

        验证流程:
        1. 检查是否已有有效签名
        2. 如果无效,尝试 6层签名恢复策略
        3. 返回验证结果和恢复的签名

        Args:
            block: thinking block 字典
            session_id: 会话ID (可选)
            context_signature: 上下文签名 (可选)

        Returns:
            (is_valid, recovered_signature)
            - is_valid: 是否有效 (已有有效签名或成功恢复)
            - recovered_signature: 恢复的签名 (如果有)
        """
        # 1. 检查是否已有有效签名
        if has_valid_thoughtsignature(block):
            signature = block.get("thoughtSignature")
            log.debug(f"[SANITIZER] Thinking block 已有有效签名: sig_len={len(signature) if signature else 0}")
            return True, signature

        # 2. 尝试恢复签名
        thinking_text = block.get("thinking", "")
        client_signature = block.get("thoughtSignature")

        try:
            recovery_result = recover_signature_for_thinking(
                thinking_text=thinking_text,
                client_signature=client_signature,
                context_signature=context_signature,
                session_id=session_id,
                use_placeholder_fallback=False  # 不使用占位符,降级为 text
            )

            if recovery_result.signature and is_valid_signature(recovery_result.signature):
                log.info(
                    f"[SANITIZER] Thinking block 签名恢复成功: "
                    f"source={recovery_result.source.value}, "
                    f"thinking_len={len(thinking_text)}"
                )
                self.stats["thinking_blocks_recovered"] += 1

                # 更新 block 的签名
                block["thoughtSignature"] = recovery_result.signature

                # 缓存恢复的签名 (仅缓存非 fallback 来源)
                if recovery_result.source not in (RecoverySource.LAST_SIGNATURE, RecoverySource.PLACEHOLDER):
                    self._cache_recovered_signature(thinking_text, recovery_result.signature, session_id)

                return True, recovery_result.signature
            else:
                log.warning(
                    f"[SANITIZER] Thinking block 签名恢复失败: "
                    f"thinking_len={len(thinking_text)}, "
                    f"将降级为 text block"
                )
                return False, None

        except Exception as e:
            log.error(f"[SANITIZER] Thinking block 签名恢复异常: {e}", exc_info=True)
            return False, None

    def _downgrade_thinking_to_text(self, block: Dict) -> Optional[Dict]:
        """
        将无效的 thinking block 降级为 text block

        Args:
            block: thinking block 字典

        Returns:
            text block 字典,如果内容为空则返回 None
        """
        thinking_text = block.get("thinking", "")

        # 只有非空内容才降级为 text
        if thinking_text and str(thinking_text).strip():
            log.info(
                f"[SANITIZER] 降级 thinking block 为 text block: "
                f"content_len={len(thinking_text)}"
            )
            return {
                "type": "text",
                "text": str(thinking_text)
            }
        else:
            log.debug("[SANITIZER] 丢弃空 thinking block")
            return None

    def _recover_tool_use_signature(
        self,
        block: Dict,
        session_id: Optional[str] = None,
        context_signature: Optional[str] = None
    ) -> Dict:
        """
        恢复 tool_use block 的签名

        Args:
            block: tool_use block 字典
            session_id: 会话ID
            context_signature: 上下文签名

        Returns:
            处理后的 tool_use block
        """
        tool_id = block.get("id", "")
        client_signature = block.get("thoughtSignature")

        # 解码 tool_id (可能包含编码的签名)
        original_id, encoded_signature = decode_tool_id_and_signature(tool_id)

        try:
            recovery_result = recover_signature_for_tool_use(
                tool_id=original_id,
                encoded_tool_id=tool_id,
                client_signature=client_signature,
                context_signature=context_signature,
                session_id=session_id,
                use_placeholder_fallback=True  # tool_use 可以使用占位符
            )

            if recovery_result.signature:
                # 更新 block 的签名
                recovered_block = block.copy()
                recovered_block["thoughtSignature"] = recovery_result.signature

                if recovery_result.source != RecoverySource.PLACEHOLDER:
                    log.debug(
                        f"[SANITIZER] Tool use 签名恢复成功: "
                        f"tool_id={original_id}, source={recovery_result.source.value}"
                    )
                    self.stats["tool_use_blocks_recovered"] += 1

                return recovered_block
            else:
                # 恢复失败,使用占位符
                log.warning(f"[SANITIZER] Tool use 签名恢复失败,使用占位符: tool_id={original_id}")
                recovered_block = block.copy()
                recovered_block["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR
                return recovered_block

        except Exception as e:
            log.error(f"[SANITIZER] Tool use 签名恢复异常: {e}", exc_info=True)
            # 异常时使用占位符
            recovered_block = block.copy()
            recovered_block["thoughtSignature"] = SKIP_SIGNATURE_VALIDATOR
            return recovered_block

    def _ensure_tool_chain_integrity(self, messages: List[Dict]) -> List[Dict]:
        """
        确保 tool_use/tool_result 链条完整

        检查规则:
        1. 每个 tool_use 必须有对应的 tool_result
        2. tool_result 必须紧跟在对应的 tool_use 之后
        3. 如果链条断裂,记录警告但不修复 (修复逻辑由其他模块负责)

        Args:
            messages: 消息列表

        Returns:
            消息列表 (当前版本不修改,仅验证)
        """
        # 收集所有 tool_use 和 tool_result
        tool_uses = {}  # tool_id -> message_index
        tool_results = {}  # tool_id -> message_index

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
                        tool_uses[tool_id] = msg_idx
                elif block_type == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id:
                        tool_results[tool_use_id] = msg_idx

        # 检查完整性
        broken_chains = []
        for tool_id in tool_uses:
            if tool_id not in tool_results:
                broken_chains.append(tool_id)

        if broken_chains:
            log.warning(
                f"[SANITIZER] 检测到断裂的工具调用链: "
                f"broken_count={len(broken_chains)}, "
                f"tool_ids={broken_chains[:3]}..."  # 只显示前3个
            )
            self.stats["tool_chains_fixed"] += len(broken_chains)

        # 当前版本不修复,仅记录
        # 修复逻辑由 tool_loop_recovery 模块负责
        return messages

    def _sync_thinking_config(
        self,
        messages: List[Dict],
        thinking_enabled: bool
    ) -> bool:
        """
        同步 thinkingConfig 与消息内容

        规则:
        - 如果消息中没有有效的 thinking block,则应禁用 thinking
        - 如果 thinking_enabled=False,确保消息中没有 thinking block

        Args:
            messages: 消息列表
            thinking_enabled: 当前 thinking 配置

        Returns:
            最终的 thinking_enabled 状态
        """
        # 检查消息中是否有有效的 thinking block
        has_valid_thinking = False

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type in ("thinking", "redacted_thinking"):
                    if has_valid_thoughtsignature(block):
                        has_valid_thinking = True
                        break

            if has_valid_thinking:
                break

        # 同步逻辑
        if thinking_enabled and not has_valid_thinking:
            log.info(
                "[SANITIZER] 消息中没有有效的 thinking block,禁用 thinking 模式"
            )
            return False
        elif not thinking_enabled and has_valid_thinking:
            log.warning(
                "[SANITIZER] thinking 已禁用但消息中仍有 thinking block,保持禁用状态"
            )
            return False

        return thinking_enabled

    def _cache_recovered_signature(
        self,
        thinking_text: str,
        signature: str,
        session_id: Optional[str] = None
    ) -> None:
        """
        缓存恢复的签名

        Args:
            thinking_text: thinking 文本
            signature: 签名
            session_id: 会话ID
        """
        try:
            # 导入缓存函数 (延迟导入避免循环依赖)
            from src.signature_cache import cache_signature, cache_session_signature

            # 缓存到内容Hash缓存
            cache_signature(thinking_text, signature)

            # 如果有 session_id,同时缓存到 Session Cache
            if session_id:
                cache_session_signature(session_id, signature, thinking_text)

            log.debug(
                f"[SANITIZER] 缓存恢复的签名: "
                f"thinking_len={len(thinking_text)}, "
                f"session_id={session_id[:16] if session_id else 'N/A'}..."
            )

        except Exception as e:
            log.warning(f"[SANITIZER] 缓存签名失败: {e}")

    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return self.stats.copy()

    def reset_stats(self) -> None:
        """重置统计信息"""
        for key in self.stats:
            self.stats[key] = 0


# ============================================================================
# 便捷函数
# ============================================================================

_global_sanitizer: Optional[AnthropicSanitizer] = None


def get_sanitizer() -> AnthropicSanitizer:
    """
    获取全局 AnthropicSanitizer 实例 (单例模式)

    Returns:
        全局 AnthropicSanitizer 实例
    """
    global _global_sanitizer

    if _global_sanitizer is None:
        _global_sanitizer = AnthropicSanitizer()
        log.info("[SANITIZER] 创建全局 AnthropicSanitizer 实例")

    return _global_sanitizer


def sanitize_anthropic_messages(
    messages: List[Dict],
    thinking_enabled: bool,
    session_id: Optional[str] = None,
    last_thought_signature: Optional[str] = None
) -> Tuple[List[Dict], bool]:
    """
    净化 Anthropic 消息 (便捷函数)

    Args:
        messages: 原始消息列表
        thinking_enabled: 是否启用 thinking 模式
        session_id: 会话ID (可选)
        last_thought_signature: 上下文签名 (可选)

    Returns:
        (sanitized_messages, should_enable_thinking)
    """
    sanitizer = get_sanitizer()
    return sanitizer.sanitize_messages(
        messages,
        thinking_enabled,
        session_id=session_id,
        last_thought_signature=last_thought_signature
    )


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "AnthropicSanitizer",
    "get_sanitizer",
    "sanitize_anthropic_messages",
]
