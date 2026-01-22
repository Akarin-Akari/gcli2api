"""
Gateway 后端注册中心

管理所有后端的注册和查询。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, List, Optional, TYPE_CHECKING
import threading

if TYPE_CHECKING:
    from .interface import GatewayBackend

__all__ = ["GatewayRegistry"]


class GatewayRegistry:
    """
    网关后端注册中心 (单例模式)

    管理所有后端的注册、注销和查询。
    """

    _instance: Optional["GatewayRegistry"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "GatewayRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._backends: Dict[str, "GatewayBackend"] = {}

    @classmethod
    def get_instance(cls) -> "GatewayRegistry":
        """获取单例实例"""
        return cls()

    def register(self, backend: "GatewayBackend") -> None:
        """
        注册后端

        Args:
            backend: 后端实例
        """
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        """
        注销后端

        Args:
            name: 后端名称
        """
        self._backends.pop(name, None)

    def get(self, name: str) -> Optional["GatewayBackend"]:
        """
        获取后端

        Args:
            name: 后端名称

        Returns:
            后端实例或 None
        """
        return self._backends.get(name)

    def get_all(self) -> List["GatewayBackend"]:
        """
        获取所有后端

        Returns:
            后端实例列表
        """
        return list(self._backends.values())

    def get_sorted_by_priority(self) -> List["GatewayBackend"]:
        """
        按优先级排序获取后端

        Returns:
            按优先级排序的后端实例列表
        """
        return sorted(
            self._backends.values(),
            key=lambda b: b.config.priority
        )

    async def get_backend_for_model(self, model: str) -> Optional["GatewayBackend"]:
        """
        根据模型选择后端

        Args:
            model: 模型名称

        Returns:
            最佳后端实例或 None
        """
        for backend in self.get_sorted_by_priority():
            if await backend.is_available() and await backend.supports_model(model):
                return backend
        return None

    def clear(self) -> None:
        """清除所有后端"""
        self._backends.clear()

    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "total_backends": len(self._backends),
            "enabled_backends": sum(
                1 for b in self._backends.values()
                if b.config.enabled
            ),
        }
