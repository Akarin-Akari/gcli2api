"""
Gateway 模块 - 统一网关路由

该模块提供多后端网关路由功能，支持:
- 多后端配置和优先级路由
- 请求规范化和代理
- Augment/Bugment 兼容
- SSE 流式响应转换
- 工具循环处理

目录结构:
- config.py: 后端配置 (BACKENDS, KIRO_GATEWAY_MODELS, RETRY_CONFIG)
- routing.py: 路由决策 (get_backend_for_model, get_sorted_backends)
- proxy.py: 代理请求 (proxy_request_to_backend, route_request_with_fallback)
- normalization.py: 请求规范化 (normalize_request_body, normalize_tools)
- tool_loop.py: 工具循环 (stream_openai_with_tool_loop)
- endpoints/: API 端点定义
- augment/: Augment/Bugment 兼容层
- sse/: SSE 流转换
- backends/: 后端接口和实现 (Phase 2)

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import TYPE_CHECKING

# 版本信息
__version__ = "1.0.0"
__author__ = "浮浮酱"

# 延迟导入，避免循环依赖
if TYPE_CHECKING:
    from .config import BACKENDS, KIRO_GATEWAY_MODELS, RETRY_CONFIG, ROUTABLE_MODELS
    from .routing import get_backend_for_model, get_sorted_backends
    from .proxy import proxy_request_to_backend, route_request_with_fallback
    from .normalization import normalize_request_body, normalize_tools, normalize_tool_choice

__all__ = [
    # 配置
    "BACKENDS",
    "KIRO_GATEWAY_MODELS",
    "RETRY_CONFIG",
    "ROUTABLE_MODELS",
    # 路由
    "get_backend_for_model",
    "get_sorted_backends",
    # 代理
    "proxy_request_to_backend",
    "route_request_with_fallback",
    # 规范化
    "normalize_request_body",
    "normalize_tools",
    "normalize_tool_choice",
    # 路由器工厂
    "get_gateway_router",
    "get_augment_router",
    # 适配器（渐进迁移）
    "get_adapter_router",
    "get_adapter_augment_router",
]


def get_adapter_router():
    """
    获取适配器路由器（支持渐进迁移）

    通过环境变量 USE_NEW_GATEWAY 控制使用新/旧模块
    """
    from .adapter import get_router
    return get_router()


def get_adapter_augment_router():
    """
    获取适配器 Augment 路由器（支持渐进迁移）

    通过环境变量 USE_NEW_GATEWAY 控制使用新/旧模块
    """
    from .adapter import get_augment_router
    return get_augment_router()


def get_gateway_router():
    """获取网关路由器 (延迟导入)"""
    from .endpoints import create_gateway_router
    return create_gateway_router()


def get_augment_router():
    """获取 Augment 路由器 (延迟导入)"""
    from .augment.endpoints import create_augment_router
    return create_augment_router()
