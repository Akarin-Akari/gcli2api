"""
Gateway 后端模块

包含后端接口定义和具体实现。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

__all__ = [
    "GatewayBackend",
    "BackendConfig",
    "GatewayRegistry",
    "AntigravityBackend",
    "CopilotBackend",
]


# 延迟导入避免循环依赖
def __getattr__(name: str):
    if name == "GatewayBackend":
        from .interface import GatewayBackend
        return GatewayBackend
    elif name == "BackendConfig":
        from .interface import BackendConfig
        return BackendConfig
    elif name == "GatewayRegistry":
        from .registry import GatewayRegistry
        return GatewayRegistry
    elif name == "AntigravityBackend":
        from .antigravity import AntigravityBackend
        return AntigravityBackend
    elif name == "CopilotBackend":
        from .copilot import CopilotBackend
        return CopilotBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
