"""
Model Configuration - Model mapping and fallback logic
模型配置 - 模型名称映射和降级逻辑
"""

from typing import List

from log import log


# 模型名称映射
def model_mapping(model_name: str) -> str:
    """
    OpenAI 模型名映射到 Antigravity 实际模型名

    参考文档:
    - claude-sonnet-4-5-thinking -> claude-sonnet-4-5
    - claude-opus-4-5 -> claude-opus-4-5-thinking
    - gemini-2.5-flash-thinking -> gemini-2.5-flash
    """
    mapping = {
        "claude-sonnet-4-5-thinking": "claude-sonnet-4-5",
        "claude-opus-4-5": "claude-opus-4-5-thinking",
        "gemini-2.5-flash-thinking": "gemini-2.5-flash",
        # Cursor 客户端模型名映射
        "claude-4.5-opus-high-thinking": "claude-opus-4-5-thinking",
        "claude-4.5-opus-high": "claude-opus-4-5",
        "claude-4.5-opus": "claude-opus-4-5",
        "claude-4.5-opus-thinking": "claude-opus-4-5-thinking",
        "claude-4.5-sonnet-high-thinking": "claude-sonnet-4-5-thinking",
        "claude-4.5-sonnet-high": "claude-sonnet-4-5",
        "claude-4.5-sonnet": "claude-sonnet-4-5",
        "claude-4.5-sonnet-thinking": "claude-sonnet-4-5-thinking",
        "claude-opus-4-5-high-thinking": "claude-opus-4-5-thinking",
        "claude-opus-4-5-high": "claude-opus-4-5",
        "claude-sonnet-4-5-high-thinking": "claude-sonnet-4-5-thinking",
        "claude-sonnet-4-5-high": "claude-sonnet-4-5",
        # Gemini 模型名映射（修复 404 错误）
        "gemini-3-pro-preview": "gemini-3-pro-high",
        "gemini-3-pro": "gemini-3-pro-high",
        "gemini-3-flash-preview": "gemini-3-flash",
        # OpenAI/Anthropic 标准模型名映射
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
        "claude-3-opus-20240229": "claude-opus-4-5",
        "claude-3-5-sonnet": "claude-sonnet-4-5",
        "claude-3-opus": "claude-opus-4-5",
        "gpt-4": "claude-opus-4-5",
        "gpt-4-turbo": "claude-opus-4-5",
        "gpt-4o": "claude-sonnet-4-5",
    }
    return mapping.get(model_name, model_name)


def get_fallback_models(model_name: str) -> List[str]:
    """
    获取模型的降级链（使用新的跨池降级逻辑）

    注意：此函数用于预计算降级目标，使用 debug 级别日志避免噪音。
    实际降级时会使用 info 级别日志。

    [FIX 2026-01-21] Opus 模型降级策略调整：
    - Opus 模型不再自动跨池降级到 Gemini
    - 必须先尝试所有 Antigravity 凭证 -> Kiro -> AnyRouter -> Copilot
    - 只有所有后端都失败后，才在 Gateway 层进行跨模型降级

    Haiku 模型降级链：
    - Antigravity 不支持 Haiku，所以不在这里添加降级模型
    - Haiku 直接走 Gateway 层的 Kiro -> Copilot 链路
    - 只有所有后端都失败后，才最终降级到 gemini-3-flash

    Args:
        model_name: 当前模型名

    Returns:
        降级模型列表（按优先级排序）
    """
    from src.fallback_manager import get_cross_pool_fallback, is_haiku_model, is_opus_model, HAIKU_FINAL_FALLBACK

    fallback_list = []

    # Haiku 模型特殊处理
    # Antigravity 不支持 Haiku，所以这里只添加最终兜底模型
    # Haiku 会通过 Gateway 层直接路由到 Kiro/Copilot
    if is_haiku_model(model_name):
        # 只添加最终兜底模型，Kiro/Copilot 失败后使用
        fallback_list.append(HAIKU_FINAL_FALLBACK)  # gemini-3-flash (最终兜底)
        return fallback_list

    # [FIX 2026-01-21] Opus 模型特殊处理
    # Opus 不在 Antigravity 层进行跨池降级，而是让 Gateway 层处理
    # 降级顺序: AG (所有凭证) -> Kiro -> AnyRouter -> Copilot -> 跨模型
    if is_opus_model(model_name):
        log.debug(f"[FALLBACK] Opus 模型 {model_name} 不在 AG 层跨池降级，由 Gateway 层处理")
        return fallback_list  # 返回空列表，让 Gateway 层处理后端降级

    # 其他模型（如 Sonnet）仍然可以跨池降级
    # 获取跨池降级目标 - 预计算使用 debug 级别日志
    cross_pool_fallback = get_cross_pool_fallback(model_name, log_level="debug")
    if cross_pool_fallback:
        fallback_list.append(cross_pool_fallback)

    return fallback_list


def should_fallback_on_error(error_msg: str) -> bool:
    """
    判断是否应该触发模型降级

    只有额度用尽错误才触发降级，其他错误（400/429普通限流/5xx）应该重试

    Args:
        error_msg: 错误消息

    Returns:
        True 如果应该降级，False 如果应该重试或失败
    """
    from src.fallback_manager import is_quota_exhausted_error

    # 只有额度用尽才触发降级
    return is_quota_exhausted_error(error_msg)


def is_thinking_model(model_name: str) -> bool:
    """检测是否是思考模型"""
    # 检查是否包含 -thinking 后缀
    if "-thinking" in model_name:
        return True

    # 检查是否包含 pro 关键词
    if "pro" in model_name.lower():
        return True

    return False


# ==================== [FIX 2026-01-21] 跨模型 Thinking 隔离 ====================
#
# 问题描述：
# 当模型路由波动（Claude → Gemini → Claude）时，Gemini 返回的 thinking 块没有
# 有效的 signature，这会污染会话状态，导致后续 Claude 请求的 thinking 被禁用。
#
# 解决方案：
# 1. 检测目标模型类型（Claude / Gemini / 其他）
# 2. 根据模型类型过滤历史 thinking 块
# 3. 缓存层按模型 namespace 隔离
#
# Author: Claude Opus 4.5 (浮浮酱)
# Date: 2026-01-21
# ============================================================================


# Claude 模型标识符
CLAUDE_MODEL_PREFIXES = (
    "claude-",
    "anthropic.",
)

CLAUDE_MODEL_KEYWORDS = (
    "opus",
    "sonnet",
    "haiku",
)

# Gemini 模型标识符
GEMINI_MODEL_PREFIXES = (
    "gemini-",
    "models/gemini",
)

GEMINI_MODEL_KEYWORDS = (
    "gemini",
    "pro-high",
    "flash",
)


def is_claude_model(model_name: str) -> bool:
    """
    检测是否是 Claude 模型

    Claude 模型的 thinking 块需要有效的 signature。

    Args:
        model_name: 模型名称

    Returns:
        True 如果是 Claude 模型，否则 False

    Examples:
        >>> is_claude_model("claude-opus-4-5")
        True
        >>> is_claude_model("claude-sonnet-4-5-thinking")
        True
        >>> is_claude_model("gemini-3-pro-high")
        False
    """
    if not model_name:
        return False

    model_lower = model_name.lower()

    # 检查前缀
    for prefix in CLAUDE_MODEL_PREFIXES:
        if model_lower.startswith(prefix):
            return True

    # 检查关键词（更严格的匹配）
    for keyword in CLAUDE_MODEL_KEYWORDS:
        if keyword in model_lower and "gemini" not in model_lower:
            return True

    return False


def is_gemini_model(model_name: str) -> bool:
    """
    检测是否是 Gemini 模型

    Gemini 模型的 thinking 块（thought 字段）没有有效的 signature。

    Args:
        model_name: 模型名称

    Returns:
        True 如果是 Gemini 模型，否则 False

    Examples:
        >>> is_gemini_model("gemini-3-pro-high")
        True
        >>> is_gemini_model("gemini-3-flash")
        True
        >>> is_gemini_model("claude-opus-4-5")
        False
    """
    if not model_name:
        return False

    model_lower = model_name.lower()

    # 检查前缀
    for prefix in GEMINI_MODEL_PREFIXES:
        if model_lower.startswith(prefix):
            return True

    # 检查关键词
    for keyword in GEMINI_MODEL_KEYWORDS:
        if keyword in model_lower and "claude" not in model_lower:
            return True

    return False


def get_model_family(model_name: str) -> str:
    """
    获取模型家族/厂商

    用于 thinking 缓存的 namespace 隔离。

    Args:
        model_name: 模型名称

    Returns:
        模型家族名称: "claude", "gemini", 或 "other"

    Examples:
        >>> get_model_family("claude-opus-4-5")
        "claude"
        >>> get_model_family("gemini-3-pro-high")
        "gemini"
        >>> get_model_family("gpt-4")
        "other"
    """
    if is_claude_model(model_name):
        return "claude"
    elif is_gemini_model(model_name):
        return "gemini"
    else:
        return "other"


def should_preserve_thinking_for_model(source_model: str, target_model: str) -> bool:
    """
    判断是否应该在请求中保留来自源模型的 thinking 块

    关键规则：
    1. Claude → Claude: 保留（需要 signature 验证）
    2. Claude → Gemini: 移除（Gemini 不需要历史 thinking）
    3. Gemini → Claude: 移除（Gemini thinking 没有 signature，会导致 Claude 400）
    4. Gemini → Gemini: 移除（Gemini 也不使用历史 thinking）

    Args:
        source_model: 产生 thinking 的源模型
        target_model: 当前请求的目标模型

    Returns:
        True 如果应该保留 thinking，否则 False
    """
    source_family = get_model_family(source_model)
    target_family = get_model_family(target_model)

    # 只有 Claude → Claude 才保留 thinking
    if source_family == "claude" and target_family == "claude":
        return True

    # 其他所有情况都移除
    log.debug(
        f"[MODEL_CONFIG] thinking 块将被过滤: "
        f"source={source_model} ({source_family}) → target={target_model} ({target_family})"
    )
    return False
