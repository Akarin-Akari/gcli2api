"""
Gateway 兼容性包装器

为 web.py 提供与 unified_gateway_router.py 相同的导入接口，
实现无缝切换。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18

使用方法:
在 web.py 中，可以将:
    from src.unified_gateway_router import router as gateway_router, augment_router

替换为:
    from src.gateway.compat import router as gateway_router, augment_router

或者使用适配器模式（推荐）:
    from src.gateway import get_adapter_router, get_adapter_augment_router
    gateway_router = get_adapter_router()
    augment_router = get_adapter_augment_router()
"""

import os
from log import log

# 迁移开关
USE_NEW_GATEWAY = os.getenv("USE_NEW_GATEWAY", "false").lower() in ("true", "1", "yes")

if USE_NEW_GATEWAY:
    log.info("Gateway 兼容层: 使用新模块", tag="COMPAT")
    from .endpoints import create_gateway_router
    from .augment import create_augment_router

    # 创建路由器实例
    router = create_gateway_router()
    augment_router = create_augment_router()
else:
    log.info("Gateway 兼容层: 使用旧模块", tag="COMPAT")
    try:
        from src.unified_gateway_router import router, augment_router
    except ImportError as e:
        log.warning(f"旧模块导入失败，回退到新模块: {e}", tag="COMPAT")
        from .endpoints import create_gateway_router
        from .augment import create_augment_router
        router = create_gateway_router()
        augment_router = create_augment_router()

# 导出与旧模块相同的接口
__all__ = [
    "router",
    "augment_router",
]
