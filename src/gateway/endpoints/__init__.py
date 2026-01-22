"""
Gateway 端点模块

包含 OpenAI、Anthropic、模型列表等 API 端点。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from fastapi import APIRouter

__all__ = [
    "create_gateway_router",
    "openai_router",
    "anthropic_router",
    "models_router",
    "admin_router",
]


def create_gateway_router() -> APIRouter:
    """
    创建网关路由器

    Returns:
        配置好的 APIRouter 实例
    """
    from .openai import router as openai_router
    from .anthropic import router as anthropic_router
    from .models import router as models_router
    from .admin import router as admin_router

    router = APIRouter(prefix="/gateway")
    router.include_router(models_router, tags=["models"])
    router.include_router(openai_router, tags=["openai"])
    router.include_router(anthropic_router, tags=["anthropic"])
    router.include_router(admin_router, tags=["admin"])
    return router


# 延迟导入避免循环依赖
def __getattr__(name: str):
    if name == "openai_router":
        from .openai import router
        return router
    elif name == "anthropic_router":
        from .anthropic import router
        return router
    elif name == "models_router":
        from .models import router
        return router
    elif name == "admin_router":
        from .admin import router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
