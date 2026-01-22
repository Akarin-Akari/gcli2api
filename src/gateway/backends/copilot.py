"""
Copilot 后端实现

实现 GatewayBackend Protocol，提供 Copilot 服务的代理功能。

作者: 浮浮酱 (Claude Sonnet 4.5)
创建日期: 2026-01-18
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional
import httpx
import json

from .interface import GatewayBackend, BackendConfig
from src.gateway.config import BACKENDS, map_model_for_copilot
from src.utils import log
from src.httpx_client import safe_close_client

__all__ = ["CopilotBackend"]


class CopilotBackend:
    """
    Copilot 后端实现

    特性:
    1. 支持 GPT 系列模型 (gpt-4, gpt-4-turbo, gpt-4o, gpt-5.2 等)
    2. 使用 httpx 代理到 http://127.0.0.1:8141/v1
    3. 超时时间较长 (timeout: 120s, stream_timeout: 600s) 因为是思考模型
    4. 实现健康检查
    """

    # 支持的 GPT 模型列表
    SUPPORTED_MODELS = {
        # GPT-4 系列
        "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
        "gpt-4-0125-preview", "gpt-4-turbo-preview",
        # GPT-4.1 系列
        "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        # GPT-5 系列
        "gpt-5", "gpt-5.1", "gpt-5.2",
        # O 系列
        "o1", "o1-mini", "o1-pro", "o3", "o3-mini",
        # GPT-3.5
        "gpt-3.5-turbo", "gpt-3.5-turbo-16k",
    }

    def __init__(self, config: Optional[BackendConfig] = None):
        """
        初始化 Copilot 后端

        Args:
            config: 后端配置，如果为 None 则从 BACKENDS 加载
        """
        if config is None:
            backend_cfg = BACKENDS.get("copilot", {})
            config = BackendConfig(
                name=backend_cfg.get("name", "Copilot"),
                base_url=backend_cfg.get("base_url", "http://127.0.0.1:8141/v1"),
                priority=backend_cfg.get("priority", 2),
                models=list(self.SUPPORTED_MODELS),
                enabled=backend_cfg.get("enabled", True),
                timeout=backend_cfg.get("timeout", 120.0),
                max_retries=backend_cfg.get("max_retries", 3),
            )

        self._config = config
        self._http_client: Optional[httpx.AsyncClient] = None

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

        try:
            client = await self._get_http_client()
            # 尝试访问健康检查端点
            health_url = f"{self._config.base_url.rstrip('/v1')}/health"
            response = await client.get(health_url, timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            log.debug(f"Copilot backend health check failed: {e}", tag="GATEWAY")
            return False

    async def supports_model(self, model: str) -> bool:
        """
        检查是否支持指定模型

        Copilot 支持 GPT 系列模型

        Args:
            model: 模型名称

        Returns:
            是否支持
        """
        # 使用 map_model_for_copilot 映射模型名称
        mapped_model = map_model_for_copilot(model)

        # 检查映射后的模型是否在支持列表中
        if mapped_model.lower() in {m.lower() for m in self.SUPPORTED_MODELS}:
            return True

        # 模糊匹配：检查是否包含 gpt/o1/o3
        model_lower = model.lower()
        if any(prefix in model_lower for prefix in ["gpt", "o1", "o3"]):
            return True

        return False

    async def handle_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool = False
    ) -> Any:
        """
        处理请求

        使用 httpx 代理到 Copilot 服务

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        # 映射模型名称
        if "model" in body:
            body["model"] = map_model_for_copilot(body["model"])

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
        # 映射模型名称
        if "model" in body:
            body["model"] = map_model_for_copilot(body["model"])

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
            log.error(f"Copilot proxy request failed: {e.response.status_code}", tag="GATEWAY")
            raise
        except Exception as e:
            log.error(f"Copilot proxy request failed: {e}", tag="GATEWAY")
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
                timeout=self._config.timeout,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

        except httpx.HTTPStatusError as e:
            log.error(f"Copilot proxy streaming request failed: {e.response.status_code}", tag="GATEWAY")
            error_msg = json.dumps({"error": f"Backend error: {e.response.status_code}"})
            yield f"data: {error_msg}\n\n".encode("utf-8")

        except Exception as e:
            log.error(f"Copilot proxy streaming request failed: {e}", tag="GATEWAY")
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
