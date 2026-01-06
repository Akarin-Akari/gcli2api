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

    Args:
        model_name: 当前模型名

    Returns:
        降级模型列表（按优先级排序）
    """
    from src.fallback_manager import get_cross_pool_fallback, is_haiku_model, HAIKU_FALLBACK_TARGET

    fallback_list = []

    # Haiku 模型特殊处理
    if is_haiku_model(model_name):
        fallback_list.append(HAIKU_FALLBACK_TARGET)
        return fallback_list

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
