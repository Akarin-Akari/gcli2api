"""
AnthropicSanitizer 集成示例

展示如何在 anthropic_converter.py 中集成 AnthropicSanitizer
"""

from typing import Any, Dict, List, Optional
from src.ide_compat.sanitizer import sanitize_anthropic_messages


# ============================================================================
# 示例 1: 在消息转换前净化
# ============================================================================

def convert_anthropic_to_gemini_with_sanitizer(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    转换 Anthropic 请求为 Gemini 格式 (带消息净化)

    Args:
        payload: Anthropic 请求 payload

    Returns:
        Gemini 格式的请求
    """
    messages = payload.get("messages", [])
    thinking_config = payload.get("thinking")

    # 判断是否启用 thinking
    thinking_enabled = thinking_config is not None

    # 获取 session_id (如果有)
    session_id = payload.get("metadata", {}).get("session_id")

    # 净化消息
    sanitized_messages, final_thinking_enabled = sanitize_anthropic_messages(
        messages=messages,
        thinking_enabled=thinking_enabled,
        session_id=session_id
    )

    # 更新 payload
    payload["messages"] = sanitized_messages

    # 如果 thinking 被禁用,移除 thinking 配置
    if not final_thinking_enabled:
        payload.pop("thinking", None)

    # 继续正常的转换流程...
    # (这里省略具体的转换逻辑)

    return payload


# ============================================================================
# 示例 2: 在路由层集成
# ============================================================================

async def handle_anthropic_messages_request(request):
    """
    处理 Anthropic Messages API 请求 (带消息净化)

    Args:
        request: FastAPI Request 对象

    Returns:
        响应
    """
    from fastapi import Request
    from src.ide_compat.sanitizer import get_sanitizer

    # 解析请求
    payload = await request.json()

    # 获取 sanitizer 实例
    sanitizer = get_sanitizer()

    # 提取参数
    messages = payload.get("messages", [])
    thinking_config = payload.get("thinking")
    thinking_enabled = thinking_config is not None

    # 净化消息
    sanitized_messages, final_thinking_enabled = sanitizer.sanitize_messages(
        messages=messages,
        thinking_enabled=thinking_enabled,
        session_id=request.headers.get("X-Session-ID")
    )

    # 更新 payload
    payload["messages"] = sanitized_messages
    if not final_thinking_enabled:
        payload.pop("thinking", None)

    # 记录统计信息
    stats = sanitizer.get_stats()
    print(f"[SANITIZER] Stats: {stats}")

    # 继续处理请求...
    # (这里省略具体的处理逻辑)


# ============================================================================
# 示例 3: 批量消息净化
# ============================================================================

def batch_sanitize_messages(
    message_batches: List[List[Dict]],
    thinking_enabled: bool = True
) -> List[List[Dict]]:
    """
    批量净化多个消息列表

    Args:
        message_batches: 消息列表的列表
        thinking_enabled: 是否启用 thinking

    Returns:
        净化后的消息列表的列表
    """
    from src.ide_compat.sanitizer import get_sanitizer

    sanitizer = get_sanitizer()
    sanitized_batches = []

    for messages in message_batches:
        sanitized, _ = sanitizer.sanitize_messages(
            messages=messages,
            thinking_enabled=thinking_enabled
        )
        sanitized_batches.append(sanitized)

    return sanitized_batches


# ============================================================================
# 示例 4: 带错误处理的集成
# ============================================================================

def safe_convert_with_sanitizer(
    payload: Dict[str, Any],
    fallback_on_error: bool = True
) -> Dict[str, Any]:
    """
    安全的消息转换 (带净化和错误处理)

    Args:
        payload: 原始 payload
        fallback_on_error: 错误时是否使用 fallback

    Returns:
        转换后的 payload
    """
    from src.ide_compat.sanitizer import sanitize_anthropic_messages
    import logging

    log = logging.getLogger(__name__)

    try:
        messages = payload.get("messages", [])
        thinking_enabled = payload.get("thinking") is not None

        # 净化消息
        sanitized_messages, final_thinking_enabled = sanitize_anthropic_messages(
            messages=messages,
            thinking_enabled=thinking_enabled
        )

        # 更新 payload
        payload["messages"] = sanitized_messages
        if not final_thinking_enabled:
            payload.pop("thinking", None)

        log.info(f"[SANITIZER] 消息净化成功: messages={len(sanitized_messages)}")

    except Exception as e:
        log.error(f"[SANITIZER] 消息净化失败: {e}", exc_info=True)

        if fallback_on_error:
            # 使用原始消息
            log.warning("[SANITIZER] 使用原始消息作为 fallback")
        else:
            raise

    return payload


# ============================================================================
# 示例 5: 自定义 sanitizer 配置
# ============================================================================

def create_custom_sanitizer():
    """
    创建自定义配置的 sanitizer

    Returns:
        AnthropicSanitizer 实例
    """
    from src.ide_compat.sanitizer import AnthropicSanitizer
    from src.signature_cache import get_signature_cache

    # 获取签名缓存实例
    signature_cache = get_signature_cache()

    # 创建自定义 sanitizer
    sanitizer = AnthropicSanitizer(
        signature_cache=signature_cache,
        state_manager=None  # 可以传入自定义的 state_manager
    )

    return sanitizer


# ============================================================================
# 示例 6: 监控和统计
# ============================================================================

def monitor_sanitizer_stats():
    """
    监控 sanitizer 统计信息

    Returns:
        统计信息字典
    """
    from src.ide_compat.sanitizer import get_sanitizer

    sanitizer = get_sanitizer()
    stats = sanitizer.get_stats()

    # 计算降级率
    total_validated = stats["thinking_blocks_validated"]
    total_downgraded = stats["thinking_blocks_downgraded"]

    if total_validated > 0:
        downgrade_rate = (total_downgraded / total_validated) * 100
    else:
        downgrade_rate = 0

    # 计算恢复成功率
    total_recovered = stats["thinking_blocks_recovered"]
    if total_validated > 0:
        recovery_rate = (total_recovered / total_validated) * 100
    else:
        recovery_rate = 0

    return {
        **stats,
        "downgrade_rate": f"{downgrade_rate:.2f}%",
        "recovery_rate": f"{recovery_rate:.2f}%"
    }


# ============================================================================
# 示例 7: 集成到现有的 anthropic_converter.py
# ============================================================================

def integrate_into_anthropic_converter():
    """
    展示如何集成到现有的 anthropic_converter.py

    在 convert_anthropic_to_gemini() 函数中添加以下代码:
    """

    # 在函数开始处添加:
    """
    from src.ide_compat.sanitizer import sanitize_anthropic_messages

    # ... 现有代码 ...

    # 在处理 messages 之前添加净化步骤:
    messages = payload.get("messages", [])
    thinking_config = payload.get("thinking")
    thinking_enabled = thinking_config is not None

    # 净化消息
    sanitized_messages, final_thinking_enabled = sanitize_anthropic_messages(
        messages=messages,
        thinking_enabled=thinking_enabled,
        session_id=payload.get("metadata", {}).get("session_id")
    )

    # 更新 payload
    payload["messages"] = sanitized_messages
    if not final_thinking_enabled:
        payload.pop("thinking", None)

    # 继续现有的转换逻辑...
    """


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    # 示例 payload
    example_payload = {
        "model": "claude-opus-4.5",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me think...",
                        "thoughtSignature": "invalid_sig"  # 无效签名
                    },
                    {
                        "type": "text",
                        "text": "Here is my answer."
                    }
                ]
            }
        ],
        "thinking": {"type": "enabled", "budget_tokens": 10000}
    }

    # 使用 sanitizer
    result = convert_anthropic_to_gemini_with_sanitizer(example_payload)
    print(f"Sanitized payload: {result}")

    # 查看统计信息
    stats = monitor_sanitizer_stats()
    print(f"Sanitizer stats: {stats}")
