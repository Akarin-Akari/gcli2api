"""
Gateway 路由决策模块

包含后端选择和优先级排序逻辑。

从 unified_gateway_router.py 抽取的路由决策函数。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

import re
from typing import List, Tuple, Optional, Dict, Any

from .config import (
    BACKENDS,
    KIRO_GATEWAY_MODELS,
    RETRY_CONFIG,
    normalize_model_name,
    is_antigravity_supported,
    MODEL_ROUTING,
    get_model_routing_rule,
    ModelRoutingRule,
)

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

__all__ = [
    "get_sorted_backends",
    "get_backend_for_model",
    "get_backend_base_url",
    "calculate_retry_delay",
    "should_retry",
    "check_backend_health",
    "is_kiro_gateway_supported",
    "KIRO_GATEWAY_SUPPORTED_MODELS",
    "is_anyrouter_supported",
    "ANYROUTER_SUPPORTED_MODELS",
    # 新增：模型路由链
    "get_backend_chain_for_model",
    "get_fallback_backend",
    "should_fallback_to_next",
]

# Kiro Gateway 支持的模型列表（Claude 4.5 全家桶 + Claude Sonnet 4）
KIRO_GATEWAY_SUPPORTED_MODELS = {
    "claude-sonnet-4.5", "claude-opus-4.5", "claude-haiku-4.5", "claude-sonnet-4",
}


def get_backend_base_url(backend_config: Dict[str, Any]) -> Optional[str]:
    """
    获取后端的 base_url

    处理两种配置格式：
    1. base_url: 单个 URL（如 antigravity, copilot, kiro-gateway）
    2. base_urls: URL 列表（如 anyrouter）

    Args:
        backend_config: 后端配置字典

    Returns:
        base_url 字符串，如果都不存在则返回 None
    """
    # 优先使用 base_url（单数）
    if "base_url" in backend_config:
        return backend_config["base_url"]

    # 然后尝试 base_urls（复数），取第一个
    if "base_urls" in backend_config:
        base_urls = backend_config["base_urls"]
        if base_urls and len(base_urls) > 0:
            return base_urls[0]

    return None


def get_sorted_backends() -> List[Tuple[str, Dict[str, Any]]]:
    """
    获取按优先级排序的后端列表

    Returns:
        List[Tuple[str, dict]]: [(backend_key, backend_config), ...]
            按 priority 升序排列（数字越小优先级越高）
    """
    enabled_backends = [
        (k, v) for k, v in BACKENDS.items()
        if v.get("enabled", True)
    ]
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])


def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端

    路由策略（按优先级）：
    1. 检查是否有模型特定路由规则（model_routing 配置）
    2. 检查是否配置了 Kiro Gateway 路由（环境变量）
    3. 检查是否在 Antigravity 支持列表中
    4. 支持 -> Antigravity（按 token 计费，更经济）
    5. 不支持 -> Copilot（按次计费，但支持更多模型）

    Args:
        model: 模型名称

    Returns:
        后端标识 ("antigravity", "copilot", "kiro-gateway") 或 None
    """
    if not model:
        model = ""

    model_lower = model.lower()

    # 0. 优先检查模型特定路由规则
    routing_rule = get_model_routing_rule(model)
    if routing_rule and routing_rule.enabled and routing_rule.backends:
        first_backend = routing_rule.backends[0]
        # 检查第一个后端是否启用
        backend_config = BACKENDS.get(first_backend, {})
        if backend_config.get("enabled", True):
            if hasattr(log, 'route'):
                log.route(
                    f"Model {model} -> {first_backend} (model_routing rule, chain: {routing_rule.backends})",
                    tag="GATEWAY"
                )
            return first_backend
        else:
            # 第一个后端未启用，尝试下一个
            for backend in routing_rule.backends[1:]:
                backend_config = BACKENDS.get(backend, {})
                if backend_config.get("enabled", True):
                    if hasattr(log, 'route'):
                        log.route(
                            f"Model {model} -> {backend} (model_routing fallback, first backend disabled)",
                            tag="GATEWAY"
                        )
                    return backend

    # 1. 优先检查 Kiro Gateway 路由配置（环境变量）
    if KIRO_GATEWAY_MODELS:
        # 精确匹配
        if model_lower in KIRO_GATEWAY_MODELS:
            if hasattr(log, 'route'):
                log.route(f"Model {model} -> Kiro Gateway (configured)", tag="GATEWAY")
            return "kiro-gateway"

        # 模糊匹配（检查模型名是否包含配置的模式）
        normalized_model = normalize_model_name(model)
        for kiro_model in KIRO_GATEWAY_MODELS:
            if normalized_model == kiro_model.lower() or normalized_model.startswith(kiro_model.lower()):
                if hasattr(log, 'route'):
                    log.route(f"Model {model} -> Kiro Gateway (pattern match: {kiro_model})", tag="GATEWAY")
                return "kiro-gateway"

    # 2. 检查 Antigravity 支持
    if is_antigravity_supported(model):
        if hasattr(log, 'route'):
            log.route(f"Model {model} -> Antigravity", tag="GATEWAY")
        return "antigravity"
    else:
        if hasattr(log, 'route'):
            log.route(f"Model {model} -> Copilot (not in AG list)", tag="GATEWAY")
        return "copilot"


def calculate_retry_delay(attempt: int, config: Dict[str, Any] = None) -> float:
    """
    计算重试延迟时间（指数退避）

    Args:
        attempt: 当前重试次数（从0开始）
        config: 重试配置

    Returns:
        延迟时间（秒）
    """
    if config is None:
        config = RETRY_CONFIG

    base_delay = config.get("base_delay", 1.0)
    max_delay = config.get("max_delay", 10.0)
    exponential_base = config.get("exponential_base", 2)

    delay = base_delay * (exponential_base ** attempt)
    return min(delay, max_delay)


def should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    """
    判断是否应该重试

    Args:
        status_code: HTTP 状态码
        attempt: 当前重试次数
        max_retries: 最大重试次数

    Returns:
        是否应该重试
    """
    if attempt >= max_retries:
        return False

    retry_on_status = RETRY_CONFIG.get("retry_on_status", [500, 502, 503, 504])
    return status_code in retry_on_status


async def check_backend_health(backend_key: str) -> bool:
    """
    检查后端服务健康状态

    Args:
        backend_key: 后端标识

    Returns:
        是否健康
    """
    backend = BACKENDS.get(backend_key)
    if not backend or not backend.get("enabled", True):
        return False

    # TODO: 实现实际的健康检查逻辑
    # 目前只检查配置是否启用
    return True


def get_backend_config(backend_key: str) -> Optional[Dict[str, Any]]:
    """
    获取后端配置

    Args:
        backend_key: 后端标识

    Returns:
        后端配置字典或 None
    """
    return BACKENDS.get(backend_key)


def is_backend_enabled(backend_key: str) -> bool:
    """
    检查后端是否启用

    Args:
        backend_key: 后端标识

    Returns:
        是否启用
    """
    backend = BACKENDS.get(backend_key)
    if not backend:
        return False
    return backend.get("enabled", True)


def is_kiro_gateway_supported(model: str) -> bool:
    """
    检查模型是否被 Kiro Gateway 支持

    Kiro Gateway 支持的模型：
    - claude-sonnet-4.5 (含 thinking 变体)
    - claude-opus-4.5 (含 thinking 变体)
    - claude-haiku-4.5
    - claude-sonnet-4

    Args:
        model: 模型名称

    Returns:
        是否被 Kiro Gateway 支持
    """
    if not model:
        return False

    model_lower = model.lower()

    # 必须是 Claude 模型
    if "claude" not in model_lower:
        return False

    # 规范化模型名称（移除 -thinking 等后缀）
    normalized = normalize_model_name(model)

    # 精确匹配
    if normalized in KIRO_GATEWAY_SUPPORTED_MODELS:
        return True

    # 模糊匹配 Claude 4.5 系列
    if "claude" in normalized:
        # 检查版本号 4.5 / 4-5
        has_45 = bool(re.search(r'4[.\-]5', normalized))
        has_sonnet = "sonnet" in normalized
        has_opus = "opus" in normalized
        has_haiku = "haiku" in normalized

        if has_45 and (has_sonnet or has_opus or has_haiku):
            return True

        # 检查 claude-sonnet-4（不是 4.5）
        has_4 = bool(re.search(r'sonnet[.\-]?4(?![.\-]5)', normalized)) or \
                bool(re.search(r'4[.\-]?sonnet(?![.\-]5)', normalized))
        if has_4 and has_sonnet:
            return True

    return False


# AnyRouter 支持的模型列表（来自官方）
ANYROUTER_SUPPORTED_MODELS = {
    # Claude 4.5 系列
    "claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001",
    "claude-opus-4.5", "claude-sonnet-4.5", "claude-haiku-4.5",
    # Claude 4 系列
    "claude-opus-4-20250514", "claude-opus-4-1-20250805", "claude-sonnet-4-20250514",
    "claude-opus-4", "claude-sonnet-4",
    # Claude 3.7 系列
    "claude-3-7-sonnet-20250219", "claude-3.7-sonnet",
    # Claude 3.5 系列
    "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
    "claude-3.5-sonnet", "claude-3.5-haiku",
    # 其他模型
    "gemini-2.5-pro", "gpt-5-codex",
}


def is_anyrouter_supported(model: str) -> bool:
    """
    检查模型是否被 AnyRouter 支持

    AnyRouter 支持：
    - 所有 Claude 模型系列（3.5, 3.7, 4, 4.5）
    - Gemini 2.5 Pro
    - GPT-5 Codex

    Args:
        model: 模型名称

    Returns:
        是否被 AnyRouter 支持
    """
    if not model:
        return False

    model_lower = model.lower()

    # 规范化模型名称（移除 -thinking 等后缀）
    normalized = normalize_model_name(model)

    # 精确匹配
    if normalized in ANYROUTER_SUPPORTED_MODELS:
        return True

    # 检查 Claude 模型
    if "claude" in model_lower:
        return True

    # 检查 Gemini 2.5
    if "gemini" in model_lower and "2.5" in model_lower:
        return True

    # 检查 GPT-5 Codex
    if "gpt-5" in model_lower and "codex" in model_lower:
        return True

    return False


# ==================== 模型路由链函数（新增） ====================

def get_backend_chain_for_model(model: str) -> List[str]:
    """
    获取模型的后端优先级链

    如果模型配置了特定路由规则，返回配置的后端链；
    否则返回默认的单后端列表。

    Args:
        model: 模型名称

    Returns:
        后端名称列表，按优先级排序

    Example:
        >>> get_backend_chain_for_model("claude-sonnet-4.5")
        ['kiro-gateway', 'antigravity']
        >>> get_backend_chain_for_model("gpt-4o")
        ['copilot']
    """
    routing_rule = get_model_routing_rule(model)
    if routing_rule and routing_rule.enabled and routing_rule.backends:
        # 过滤掉未启用的后端
        enabled_backends = []
        for backend in routing_rule.backends:
            backend_config = BACKENDS.get(backend, {})
            if backend_config.get("enabled", True):
                enabled_backends.append(backend)
        if enabled_backends:
            return enabled_backends

    # 没有特定规则，返回默认后端
    default_backend = get_backend_for_model(model)
    return [default_backend] if default_backend else []


def get_fallback_backend(
    model: str,
    current_backend: str,
    status_code: int = None,
    error_type: str = None,
    visited_backends: Optional[set] = None  # ✅ [FIX 2026-01-22] 防止循环降级
) -> Optional[str]:
    """
    获取降级后端

    当当前后端请求失败时，根据配置的降级条件返回下一个后端。

    Args:
        model: 模型名称
        current_backend: 当前失败的后端
        status_code: HTTP 状态码（如 429, 503）
        error_type: 错误类型（timeout, connection_error, unavailable）
        visited_backends: 已访问的后端集合（用于防止循环降级）

    Returns:
        下一个后端名称，如果没有可用的降级后端则返回 None

    Example:
        >>> get_fallback_backend("claude-sonnet-4.5", "kiro-gateway", status_code=429)
        'antigravity'
        >>> get_fallback_backend("claude-sonnet-4.5", "antigravity", status_code=429)
        None  # 已经是最后一个后端
    """
    # ✅ [FIX 2026-01-22] 初始化已访问后端集合
    if visited_backends is None:
        visited_backends = set()
    
    # ✅ [FIX 2026-01-22] 防止循环降级
    if current_backend in visited_backends:
        if hasattr(log, 'error'):
            log.error(
                f"[FALLBACK] 检测到循环降级: {current_backend} 已在访问链中 "
                f"(visited: {visited_backends})",
                tag="GATEWAY"
            )
        return None
    
    visited_backends.add(current_backend)
    
    routing_rule = get_model_routing_rule(model)
    if not routing_rule or not routing_rule.enabled:
        return None

    # 检查是否应该降级
    if not routing_rule.should_fallback(status_code, error_type):
        if hasattr(log, 'debug'):
            log.debug(
                f"No fallback for {model}: status={status_code}, error={error_type} not in fallback_on",
                tag="GATEWAY"
            )
        return None

    # 获取下一个后端
    next_backend = routing_rule.get_next_backend(current_backend)
    if next_backend:
        # ✅ [FIX 2026-01-22] 检查下一个后端是否已在访问链中
        if next_backend in visited_backends:
            if hasattr(log, 'error'):
                log.error(
                    f"[FALLBACK] 下一个后端 {next_backend} 已在访问链中，避免循环",
                    tag="GATEWAY"
                )
            return None
        
        # 检查下一个后端是否启用
        backend_config = BACKENDS.get(next_backend, {})
        if backend_config.get("enabled", True):
            if hasattr(log, 'route'):
                log.route(
                    f"Fallback: {model} {current_backend} -> {next_backend} "
                    f"(status={status_code}, error={error_type})",
                    tag="GATEWAY"
                )
            return next_backend
        else:
            # ✅ [FIX 2026-01-22] 递归查找下一个启用的后端，传递 visited_backends
            return get_fallback_backend(
                model, next_backend, status_code, error_type, visited_backends
            )

    return None


def should_fallback_to_next(
    model: str,
    current_backend: str,
    status_code: int = None,
    error_type: str = None
) -> bool:
    """
    判断是否应该降级到下一个后端

    Args:
        model: 模型名称
        current_backend: 当前后端
        status_code: HTTP 状态码
        error_type: 错误类型

    Returns:
        是否应该降级
    """
    routing_rule = get_model_routing_rule(model)
    if not routing_rule or not routing_rule.enabled:
        return False

    # 检查当前后端是否在路由链中
    if current_backend not in routing_rule.backends:
        return False

    # 检查是否有下一个后端
    idx = routing_rule.backends.index(current_backend)
    if idx + 1 >= len(routing_rule.backends):
        return False

    # 检查降级条件
    return routing_rule.should_fallback(status_code, error_type)
