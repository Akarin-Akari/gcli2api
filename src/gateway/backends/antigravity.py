"""
Antigravity 后端实现

实现 GatewayBackend Protocol，提供本地 Antigravity 服务的直调和代理功能。

作者: 浮浮酱 (Claude Sonnet 4.5)
创建日期: 2026-01-18
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional
import httpx
import json

from .interface import GatewayBackend, BackendConfig
from src.gateway.config import BACKENDS
from src.utils import log
from src.httpx_client import safe_close_client

__all__ = ["AntigravityBackend"]


class AntigravityBackend:
    """
    Antigravity 后端实现

    特性:
    1. 支持所有模型 (models: ["*"])
    2. /chat/completions 端点使用本地直调 (避免 127.0.0.1 回环)
    3. 其他端点使用 httpx 代理到 http://127.0.0.1:7861/antigravity/v1
    4. 实现健康检查
    """

    def __init__(self, config: Optional[BackendConfig] = None):
        """
        初始化 Antigravity 后端

        Args:
            config: 后端配置，如果为 None 则从 BACKENDS 加载
        """
        if config is None:
            backend_cfg = BACKENDS.get("antigravity", {})
            config = BackendConfig(
                name=backend_cfg.get("name", "Antigravity"),
                base_url=backend_cfg.get("base_url", "http://127.0.0.1:7861/antigravity/v1"),
                priority=backend_cfg.get("priority", 1),
                models=["*"],  # 支持所有模型
                enabled=backend_cfg.get("enabled", True),
                timeout=backend_cfg.get("timeout", 60.0),
                max_retries=backend_cfg.get("max_retries", 2),
            )

        self._config = config
        self._http_client: Optional[httpx.AsyncClient] = None
        self._local_handler: Optional[Any] = None

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
            log.debug(f"Antigravity backend health check failed: {e}", tag="GATEWAY")
            return False

    async def supports_model(self, model: str) -> bool:
        """
        检查是否支持指定模型

        Antigravity 支持所有模型 (models: ["*"])

        Args:
            model: 模型名称

        Returns:
            是否支持
        """
        return self._config.supports_model(model)

    async def handle_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool = False
    ) -> Any:
        """
        处理请求

        对于 /chat/completions 端点，使用本地直调
        对于其他端点，使用 httpx 代理

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        # /chat/completions 端点使用本地直调
        if endpoint == "/chat/completions":
            return await self._handle_local_chat_completions(body, headers, stream)

        # 其他端点使用 httpx 代理
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
        # /chat/completions 端点使用本地直调
        if endpoint == "/chat/completions":
            async for chunk in self._handle_local_streaming_chat_completions(body, headers):
                yield chunk
        else:
            # 其他端点使用 httpx 代理
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

    async def _get_local_handler(self) -> Optional[Any]:
        """
        获取本地处理器

        Returns:
            本地处理器函数或 None
        """
        if self._local_handler is None:
            try:
                from src.services.antigravity_service import handle_openai_chat_completions
                self._local_handler = handle_openai_chat_completions
            except ImportError as e:
                log.error(f"Failed to import antigravity_service: {e}", tag="GATEWAY")
                return None
        return self._local_handler

    async def _handle_local_chat_completions(
        self,
        body: dict,
        headers: dict,
        stream: bool
    ) -> Any:
        """
        使用本地直调处理 /chat/completions 请求

        Args:
            body: 请求体
            headers: 请求头
            stream: 是否流式响应

        Returns:
            响应对象
        """
        handler = await self._get_local_handler()
        if handler is None:
            # 降级到 httpx 代理
            log.warning("Local handler not available, falling back to proxy", tag="GATEWAY")
            return await self._handle_proxy_request("/chat/completions", body, headers, stream)

        try:
            from fastapi.responses import StreamingResponse as StarletteStreamingResponse
            from fastapi import HTTPException

            resp = await handler(body=body, headers=headers)
            status_code = getattr(resp, "status_code", 200)

            # 处理流式响应
            if stream:
                if status_code >= 400:
                    # 返回错误流
                    error_msg = json.dumps({"error": "Backend error", "status": status_code})

                    async def error_stream():
                        yield f"data: {error_msg}\n\n".encode("utf-8")

                    return error_stream()

                if isinstance(resp, StarletteStreamingResponse):
                    return resp.body_iterator

                # 非预期：流式请求返回了非 StreamingResponse
                raise ValueError(f"Expected StreamingResponse, got {type(resp)}")

            # 处理非流式响应
            if status_code >= 400:
                raise HTTPException(status_code=status_code, detail="Backend error")

            resp_body = getattr(resp, "body", b"")
            if isinstance(resp_body, bytes):
                return json.loads(resp_body.decode("utf-8", errors="ignore") or "{}")
            if isinstance(resp_body, str):
                return json.loads(resp_body or "{}")
            return resp_body

        except Exception as e:
            log.error(f"Local antigravity service call failed: {e}", tag="GATEWAY")
            # 降级到 httpx 代理
            return await self._handle_proxy_request("/chat/completions", body, headers, stream)

    async def _handle_local_streaming_chat_completions(
        self,
        body: dict,
        headers: dict
    ) -> AsyncIterator[bytes]:
        """
        使用本地直调处理流式 /chat/completions 请求

        Args:
            body: 请求体
            headers: 请求头

        Yields:
            响应数据块
        """
        handler = await self._get_local_handler()
        if handler is None:
            # 降级到 httpx 代理
            log.warning("Local handler not available, falling back to proxy", tag="GATEWAY")
            async for chunk in self._handle_proxy_streaming_request("/chat/completions", body, headers):
                yield chunk
            return

        try:
            from fastapi.responses import StreamingResponse as StarletteStreamingResponse
            from fastapi import HTTPException

            resp = await handler(body=body, headers=headers)
            status_code = getattr(resp, "status_code", 200)

            if status_code >= 400:
                # 返回错误流
                error_msg = json.dumps({"error": "Backend error", "status": status_code})
                yield f"data: {error_msg}\n\n".encode("utf-8")
                return

            if isinstance(resp, StarletteStreamingResponse):
                async for chunk in resp.body_iterator:
                    if isinstance(chunk, str):
                        yield chunk.encode("utf-8")
                    else:
                        yield chunk
                return

            # 非预期：流式请求返回了非 StreamingResponse
            error_msg = json.dumps({"error": f"Expected StreamingResponse, got {type(resp)}"})
            yield f"data: {error_msg}\n\n".encode("utf-8")

        except HTTPException as e:
            error_msg = json.dumps({"error": "Backend error", "status": e.status_code})
            yield f"data: {error_msg}\n\n".encode("utf-8")

        except Exception as e:
            log.error(f"Local antigravity streaming call failed: {e}", tag="GATEWAY")
            # 降级到 httpx 代理
            async for chunk in self._handle_proxy_streaming_request("/chat/completions", body, headers):
                yield chunk

    async def _handle_proxy_request(
        self,
        endpoint: str,
        body: dict,
        headers: dict,
        stream: bool
    ) -> Any:
        """
        使用 httpx 代理处理非流式请求

        Args:
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应（此方法忽略此参数，流式请求应使用 _handle_proxy_streaming_request）

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
            log.error(f"Antigravity proxy request failed: {e.response.status_code}", tag="GATEWAY")
            raise
        except Exception as e:
            log.error(f"Antigravity proxy request failed: {e}", tag="GATEWAY")
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
                timeout=self._config.timeout,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

        except httpx.HTTPStatusError as e:
            log.error(f"Antigravity proxy streaming request failed: {e.response.status_code}", tag="GATEWAY")
            error_msg = json.dumps({"error": f"Backend error: {e.response.status_code}"})
            yield f"data: {error_msg}\n\n".encode("utf-8")

        except Exception as e:
            log.error(f"Antigravity proxy streaming request failed: {e}", tag="GATEWAY")
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
