"""
Gateway 适配器层

桥接新模块化架构和现有 unified_gateway_router.py，
实现渐进式迁移，确保服务不中断。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18

迁移策略:
1. 首先导出与旧模块兼容的接口
2. 内部逐步切换到新模块实现
3. 完成迁移后，旧模块可以安全删除
"""

import os
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from log import log

# 迁移开关：通过环境变量控制是否使用新模块
# 默认 False，保持使用旧模块，确保稳定性
USE_NEW_GATEWAY = os.getenv("USE_NEW_GATEWAY", "false").lower() in ("true", "1", "yes")

__all__ = [
    # 配置相关
    "BACKENDS",
    "KIRO_GATEWAY_MODELS",
    "RETRY_CONFIG",
    # 路由相关
    "get_sorted_backends",
    "get_backend_for_model",
    # 代理相关
    "proxy_request_to_backend",
    "route_request_with_fallback",
    # 规范化相关
    "normalize_request_body",
    "normalize_tools",
    "normalize_tool_choice",
    "normalize_messages",
    # 路由器
    "get_router",
    "get_augment_router",
]


def _log_adapter_mode():
    """记录适配器模式"""
    mode = "新模块" if USE_NEW_GATEWAY else "旧模块"
    log.debug(f"Gateway 适配器模式: {mode}", tag="ADAPTER")


# ============== 配置适配 ==============

if USE_NEW_GATEWAY:
    # 使用新模块
    from .config import (
        BACKENDS,
        KIRO_GATEWAY_MODELS,
        RETRY_CONFIG,
    )
else:
    # 使用旧模块（保持兼容）
    try:
        from src.unified_gateway_router import (
            BACKENDS,
            KIRO_GATEWAY_MODELS,
        )
        # 旧模块可能没有 RETRY_CONFIG，提供默认值
        RETRY_CONFIG = {
            "base_delay": 1.0,
            "max_delay": 30.0,
            "exponential_base": 2,
        }
    except ImportError as e:
        log.error(f"无法导入旧模块配置: {e}", tag="ADAPTER")
        # 使用新模块作为后备
        from .config import (
            BACKENDS,
            KIRO_GATEWAY_MODELS,
            RETRY_CONFIG,
        )


# ============== 路由函数适配 ==============

def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表"""
    if USE_NEW_GATEWAY:
        from .routing import get_sorted_backends as _get_sorted_backends
        return _get_sorted_backends()
    else:
        try:
            from src.unified_gateway_router import get_sorted_backends as _get_sorted_backends
            return _get_sorted_backends()
        except ImportError:
            from .routing import get_sorted_backends as _get_sorted_backends
            return _get_sorted_backends()


def get_backend_for_model(model: str) -> Optional[str]:
    """根据模型名称获取后端"""
    if USE_NEW_GATEWAY:
        from .routing import get_backend_for_model as _get_backend_for_model
        return _get_backend_for_model(model)
    else:
        try:
            from src.unified_gateway_router import get_backend_for_model as _get_backend_for_model
            return _get_backend_for_model(model)
        except ImportError:
            from .routing import get_backend_for_model as _get_backend_for_model
            return _get_backend_for_model(model)


# ============== 代理函数适配 ==============

async def proxy_request_to_backend(
    backend_key: str,
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    stream: bool = False,
) -> Tuple[bool, Any]:
    """代理请求到指定后端"""
    if USE_NEW_GATEWAY:
        from .proxy import proxy_request_to_backend as _proxy_request
        return await _proxy_request(backend_key, endpoint, method, headers, body, stream)
    else:
        try:
            from src.unified_gateway_router import proxy_request_to_backend as _proxy_request
            return await _proxy_request(backend_key, endpoint, method, headers, body, stream)
        except ImportError:
            from .proxy import proxy_request_to_backend as _proxy_request
            return await _proxy_request(backend_key, endpoint, method, headers, body, stream)


async def route_request_with_fallback(
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    model: Optional[str] = None,
    stream: bool = False,
) -> Any:
    """带故障转移的请求路由"""
    if USE_NEW_GATEWAY:
        from .proxy import route_request_with_fallback as _route_request
        return await _route_request(endpoint, method, headers, body, model, stream)
    else:
        try:
            from src.unified_gateway_router import route_request_with_fallback as _route_request
            return await _route_request(endpoint, method, headers, body, model, stream)
        except ImportError:
            from .proxy import route_request_with_fallback as _route_request
            return await _route_request(endpoint, method, headers, body, model, stream)


# ============== 规范化函数适配 ==============

def normalize_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """规范化请求体"""
    if USE_NEW_GATEWAY:
        from .normalization import normalize_request_body as _normalize
        return _normalize(body)
    else:
        # 旧模块可能没有这个函数，直接使用新模块
        from .normalization import normalize_request_body as _normalize
        return _normalize(body)


def normalize_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规范化工具定义"""
    if USE_NEW_GATEWAY:
        from .normalization import normalize_tools as _normalize
        return _normalize(tools)
    else:
        from .normalization import normalize_tools as _normalize
        return _normalize(tools)


def normalize_tool_choice(tool_choice: Any) -> Any:
    """规范化工具选择"""
    if USE_NEW_GATEWAY:
        from .normalization import normalize_tool_choice as _normalize
        return _normalize(tool_choice)
    else:
        from .normalization import normalize_tool_choice as _normalize
        return _normalize(tool_choice)


def normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规范化消息列表"""
    if USE_NEW_GATEWAY:
        from .normalization import normalize_messages as _normalize
        return _normalize(messages)
    else:
        from .normalization import normalize_messages as _normalize
        return _normalize(messages)


# ============== 路由器适配 ==============

def get_router():
    """
    获取 Gateway 路由器

    Returns:
        FastAPI APIRouter 实例
    """
    if USE_NEW_GATEWAY:
        log.info("使用新版 Gateway 路由器", tag="ADAPTER")
        from .endpoints import create_gateway_router
        return create_gateway_router()
    else:
        log.info("使用旧版 Gateway 路由器", tag="ADAPTER")
        try:
            from src.unified_gateway_router import router
            return router
        except ImportError:
            log.warning("旧路由器导入失败，回退到新路由器", tag="ADAPTER")
            from .endpoints import create_gateway_router
            return create_gateway_router()


def get_augment_router():
    """
    获取 Augment 兼容路由器

    Returns:
        FastAPI APIRouter 实例
    """
    if USE_NEW_GATEWAY:
        log.info("使用新版 Augment 路由器", tag="ADAPTER")
        from .augment.endpoints import create_augment_router
        return create_augment_router()
    else:
        log.info("使用旧版 Augment 路由器", tag="ADAPTER")
        try:
            from src.unified_gateway_router import augment_router
            return augment_router
        except ImportError:
            log.warning("旧 Augment 路由器导入失败，回退到新路由器", tag="ADAPTER")
            from .augment.endpoints import create_augment_router
            return create_augment_router()


# ============== 初始化日志 ==============

_log_adapter_mode()
