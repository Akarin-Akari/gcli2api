"""
Gateway 后端接口定义

定义 GatewayBackend Protocol 和 BackendConfig 数据类。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Protocol, Any, AsyncIterator, Optional, List
from dataclasses import dataclass, field

__all__ = ["GatewayBackend", "BackendConfig"]


@dataclass
class BackendConfig:
    """后端配置数据类"""

    name: str
    """后端名称"""

    base_url: str
    """后端基础 URL"""

    priority: int
    """优先级 (数字越小优先级越高)"""

    models: List[str] = field(default_factory=list)
    """支持的模型列表 (["*"] 表示支持所有模型)"""

    enabled: bool = True
    """是否启用"""

    timeout: float = 30.0
    """请求超时时间 (秒)"""

    max_retries: int = 3
    """最大重试次数"""

    def supports_model(self, model: str) -> bool:
        """
        检查是否支持指定模型

        Args:
            model: 模型名称

        Returns:
            是否支持
        """
        if "*" in self.models:
            return True
        return model in self.models


class GatewayBackend(Protocol):
    """
    网关后端接口协议

    所有后端实现必须遵循此协议。
    """

    @property
    def name(self) -> str:
        """后端名称"""
        ...

    @property
    def config(self) -> BackendConfig:
        """后端配置"""
        ...

    async def is_available(self) -> bool:
        """
        检查后端是否可用

        Returns:
            是否可用
        """
        ...

    async def supports_model(self, model: str) -> bool:
        """
        检查是否支持指定模型

        Args:
            model: 模型名称

        Returns:
            是否支持
        """
        ...

    async def handle_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool = False
    ) -> Any:
        """
        处理请求

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        ...

    async def handle_streaming_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict
    ) -> AsyncIterator[bytes]:
        """
        处理流式请求

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头

        Yields:
            响应数据块
        """
        ...
