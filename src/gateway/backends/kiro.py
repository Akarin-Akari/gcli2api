"""
Kiro Gateway 后端实现

实现 GatewayBackend Protocol，提供 Kiro Gateway 服务的代理功能。

Kiro Gateway 专门用于 Claude 模型的降级：
- 支持模型：claude-sonnet-4.5, claude-opus-4.5, claude-haiku-4.5, claude-sonnet-4
- 优先级：2（次于 Antigravity，高于 Copilot）
- 端口：9876

作者: 浮浮酱 (Claude Sonnet 4.5)
创建日期: 2026-01-18
更新日期: 2026-01-19
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional
import httpx
import json

from .interface import GatewayBackend, BackendConfig
from src.gateway.config import BACKENDS, KIRO_GATEWAY_MODELS
from src.gateway.routing import is_kiro_gateway_supported, KIRO_GATEWAY_SUPPORTED_MODELS
from src.utils import log
from src.httpx_client import safe_close_client

__all__ = ["KiroGatewayBackend"]


class KiroGatewayBackend:
    """
    Kiro Gateway 后端实现

    特性:
    1. 专门用于 Claude 模型的降级
    2. 支持模型：claude-sonnet-4.5, claude-opus-4.5, claude-haiku-4.5, claude-sonnet-4
    3. 优先级：2（次于 Antigravity，高于 Copilot）
    4. 端口：9876
    5. 使用 httpx 代理到配置的 base_url
    6. 超时时间最长 (timeout: 120s, stream_timeout: 600s)
    7. 实现健康检查
    8. 从环境变量读取 KIRO_GATEWAY_ENABLED 判断是否启用
    """

    def __init__(self, config: Optional[BackendConfig] = None):
        """
        初始化 Kiro Gateway 后端

        Args:
            config: 后端配置，如果为 None 则从 BACKENDS 加载
        """
        if config is None:
            backend_cfg = BACKENDS.get("kiro-gateway", {})
            config = BackendConfig(
                name=backend_cfg.get("name", "Kiro Gateway"),
                base_url=backend_cfg.get("base_url", "http://127.0.0.1:9876/v1"),
                priority=backend_cfg.get("priority", 2),  # 优先级调整为 2
                models=list(KIRO_GATEWAY_SUPPORTED_MODELS),  # 使用新的支持模型列表
                enabled=backend_cfg.get("enabled", True),
                timeout=backend_cfg.get("timeout", 120.0),
                max_retries=backend_cfg.get("max_retries", 2),
            )

        self._config = config
        self._http_client: Optional[httpx.AsyncClient] = None
        self._stream_timeout = BACKENDS.get("kiro-gateway", {}).get("stream_timeout", 600.0)

    @property
    def name(self) -> str:
        """后端名称"""
        return self._config.name

    @property
    def config(self) -> BackendConfig:
        """后端配置"""
        return self._config

    async def is_available(self) -> bool:
        """
        检查后端是否可用

        通过健康检查端点验证服务状态

        Returns:
            是否可用
        """
        if not self._config.enabled:
            return False

        # 如果没有配置任何模型，则认为不可用
        if not self._config.models:
            log.debug("Kiro Gateway has no models configured", tag="GATEWAY")
            return False

        try:
            client = await self._get_http_client()
            # 尝试访问健康检查端点
            # Kiro Gateway 可能提供 /health 或 /v1/models 端点
            health_url = f"{self._config.base_url.rstrip('/v1')}/health"
            response = await client.get(health_url, timeout=5.0)
            return response.status_code == 200
        except Exception:
            # 如果 /health 端点不存在，尝试 /v1/models
            try:
                client = await self._get_http_client()
                models_url = f"{self._config.base_url}/models"
                response = await client.get(models_url, timeout=5.0)
                return response.status_code == 200
            except Exception as e:
                log.debug(f"Kiro Gateway backend health check failed: {e}", tag="GATEWAY")
                return False

    async def supports_model(self, model: str) -> bool:
        """
        检查是否支持指定模型

        使用 is_kiro_gateway_supported 函数进行智能匹配

        Args:
            model: 模型名称

        Returns:
            是否支持
        """
        return is_kiro_gateway_supported(model)

    async def handle_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool = False
    ) -> Any:
        """
        处理请求

        使用 httpx 代理到 Kiro Gateway

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        return await self._handle_proxy_request(endpoint, body, headers, stream)

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
        async for chunk in self._handle_proxy_streaming_request(endpoint, body, headers):
            yield chunk

    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        获取或创建 HTTP 客户端

        Returns:
            httpx.AsyncClient 实例
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout),
                follow_redirects=True,
            )
        return self._http_client

    async def _handle_proxy_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool
    ) -> Any:
        """
        使用 httpx 代理处理请求

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        # 流式请求应该调用 handle_streaming_request
        if stream:
            raise ValueError("Stream requests should use handle_streaming_request")

        client = await self._get_http_client()
        url = f"{self._config.base_url}{endpoint}"

        # 构建请求头
        request_headers = {
            "Content-Type": "application/json",
            "Authorization": headers.get("authorization") or headers.get("Authorization", "Bearer dummy"),
        }

        # 保留 User-Agent
        user_agent = headers.get("user-agent") or headers.get("User-Agent")
        if user_agent:
            request_headers["User-Agent"] = user_agent
            request_headers["X-Forwarded-User-Agent"] = user_agent

        # 转发特定的控制头
        for h in (
            "x-augment-client",
            "x-bugment-client",
            "x-augment-request",
            "x-bugment-request",
            "x-signature-version",
            "x-signature-timestamp",
            "x-signature-signature",
            "x-signature-vector",
            "x-disable-thinking-signature",
            "x-request-id",
        ):
            value = headers.get(h) or headers.get(h.title())
            if value:
                request_headers[h] = value

        try:
            # 非流式请求
            response = await client.post(
                url,
                json=body,
                headers=request_headers,
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            log.error(f"Kiro Gateway proxy request failed: {e.response.status_code}", tag="GATEWAY")
            raise
        except Exception as e:
            log.error(f"Kiro Gateway proxy request failed: {e}", tag="GATEWAY")
            raise

    async def _handle_proxy_streaming_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict
    ) -> AsyncIterator[bytes]:
        """
        使用 httpx 代理处理流式请求

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头

        Yields:
            响应数据块
        """
        client = await self._get_http_client()
        url = f"{self._config.base_url}{endpoint}"

        # 构建请求头
        request_headers = {
            "Content-Type": "application/json",
            "Authorization": headers.get("authorization") or headers.get("Authorization", "Bearer dummy"),
        }

        # 保留 User-Agent
        user_agent = headers.get("user-agent") or headers.get("User-Agent")
        if user_agent:
            request_headers["User-Agent"] = user_agent
            request_headers["X-Forwarded-User-Agent"] = user_agent

        # 转发特定的控制头
        for h in (
            "x-augment-client",
            "x-bugment-client",
            "x-augment-request",
            "x-bugment-request",
            "x-signature-version",
            "x-signature-timestamp",
            "x-signature-signature",
            "x-signature-vector",
            "x-disable-thinking-signature",
            "x-request-id",
        ):
            value = headers.get(h) or headers.get(h.title())
            if value:
                request_headers[h] = value

        try:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=request_headers,
                timeout=self._stream_timeout,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

        except httpx.HTTPStatusError as e:
            log.error(f"Kiro Gateway proxy streaming request failed: {e.response.status_code}", tag="GATEWAY")
            error_msg = json.dumps({"error": f"Backend error: {e.response.status_code}"})
            yield f"data: {error_msg}\n\n".encode("utf-8")

        except Exception as e:
            log.error(f"Kiro Gateway proxy streaming request failed: {e}", tag="GATEWAY")
            error_msg = json.dumps({"error": str(e)})
            yield f"data: {error_msg}\n\n".encode("utf-8")

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client is not None:
            await safe_close_client(self._http_client)
            self._http_client = None

    def __del__(self):
        """析构函数"""
        if self._http_client is not None:
            # 注意：在析构函数中无法使用 await
            # 建议显式调用 close() 方法
            pass
