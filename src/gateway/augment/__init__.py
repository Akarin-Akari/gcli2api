"""
Gateway Augment 兼容模块

包含 Augment/Bugment 协议支持。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from fastapi import APIRouter

__all__ = [
    "create_augment_router",
    "BugmentStateManager",
    # 状态管理函数
    "bugment_conversation_state_put",
    "bugment_conversation_state_get",
    "bugment_tool_state_put",
    "bugment_tool_state_get",
    # Nodes Bridge 函数
    "stream_openai_with_nodes_bridge",
    "augment_chat_history_to_messages",
    "extract_tool_result_nodes",
    "build_openai_messages_from_bugment",
    "prepend_bugment_guidance_system_message",
]


def create_augment_router() -> APIRouter:
    """
    创建 Augment 路由器 (无前缀)

    Returns:
        配置好的 APIRouter 实例
    """
    from .endpoints import router
    return router


# 延迟导入避免循环依赖
def __getattr__(name: str):
    if name == "BugmentStateManager":
        from .state import BugmentStateManager
        return BugmentStateManager
    if name in ("bugment_conversation_state_put", "bugment_conversation_state_get",
                "bugment_tool_state_put", "bugment_tool_state_get"):
        from . import state
        return getattr(state, name)
    if name in ("stream_openai_with_nodes_bridge", "augment_chat_history_to_messages",
                "extract_tool_result_nodes", "build_openai_messages_from_bugment",
                "prepend_bugment_guidance_system_message"):
        from . import nodes_bridge
        return getattr(nodes_bridge, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
