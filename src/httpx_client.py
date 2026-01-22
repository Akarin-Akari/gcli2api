"""
通用的HTTP客户端模块 v2.0

为所有需要使用HTTP请求的模块提供统一的客户端配置和方法。
支持 TLS 指纹伪装（通过 curl_cffi）和优雅降级（回退到原生 httpx）。

特性:
- TLS 指纹伪装：使用 curl_cffi 模拟真实浏览器指纹
- 优雅降级：curl_cffi 不可用时自动回退到 httpx
- 统一接口：无论使用哪个后端，API 保持一致
- 代理支持：支持动态代理配置
- 流式请求：支持 SSE 流式响应

版本历史:
- v1.0: 原始版本，使用原生 httpx
- v2.0: 添加 TLS 指纹伪装支持

作者: 浮浮酱 (Claude Opus 4.5)
日期: 2026-01-21
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional, Union
import asyncio

import httpx

from config import get_proxy_config
from log import log

# 导入 TLS 伪装模块
from .tls_impersonate import (
    is_tls_impersonate_available,
    get_impersonate_target,
    get_go_style_headers,
    CURL_CFFI_AVAILABLE,
)

# 条件导入 curl_cffi
if CURL_CFFI_AVAILABLE:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
else:
    CurlAsyncSession = None


class HttpxClientManager:
    """
    通用HTTP客户端管理器 v2.0

    支持 TLS 指纹伪装和优雅降级。

    使用优先级:
    1. curl_cffi (如果可用且启用) - 提供 TLS 指纹伪装
    2. httpx (降级模式) - 原生 Python HTTP 客户端
    """

    def __init__(self):
        """初始化客户端管理器"""
        self._use_curl_cffi = is_tls_impersonate_available()
        self._logged_init = False

    def _log_init_once(self):
        """只在第一次使用时记录初始化日志"""
        if not self._logged_init:
            if self._use_curl_cffi:
                target = get_impersonate_target()
                log.info(f"[HttpxClient] TLS 伪装已启用，目标: {target}")
            else:
                log.debug("[HttpxClient] 使用原生 httpx（TLS 伪装不可用）")
            self._logged_init = True

    async def get_client_kwargs(self, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        """
        获取httpx客户端的通用配置参数

        Args:
            timeout: 请求超时时间（秒）
            **kwargs: 其他参数

        Returns:
            客户端配置字典
        """
        client_kwargs = {"timeout": timeout, **kwargs}

        # 动态读取代理配置，支持热更新
        current_proxy_config = await get_proxy_config()
        if current_proxy_config:
            client_kwargs["proxy"] = current_proxy_config

        return client_kwargs

    @asynccontextmanager
    async def get_client(
        self, timeout: float = 30.0, use_go_headers: bool = False, **kwargs
    ) -> AsyncGenerator[Union[httpx.AsyncClient, "CurlAsyncSession"], None]:
        """
        获取配置好的异步HTTP客户端

        Args:
            timeout: 请求超时时间（秒）
            use_go_headers: 是否使用 Go 客户端风格的请求头
            **kwargs: 其他参数

        Yields:
            HTTP 客户端实例（curl_cffi.AsyncSession 或 httpx.AsyncClient）
        """
        self._log_init_once()

        if self._use_curl_cffi:
            # 使用 curl_cffi 的 AsyncSession
            async for client in self._get_curl_client(timeout, use_go_headers, **kwargs):
                yield client
        else:
            # 降级到原生 httpx
            async for client in self._get_httpx_client(timeout, **kwargs):
                yield client

    async def _get_curl_client(
        self, timeout: float, use_go_headers: bool, **kwargs
    ) -> AsyncGenerator["CurlAsyncSession", None]:
        """获取 curl_cffi 客户端"""
        # 获取代理配置
        proxy_config = await get_proxy_config()

        # curl_cffi 的代理格式
        proxies = None
        if proxy_config:
            proxies = {"http": proxy_config, "https": proxy_config}

        # 准备请求头
        headers = kwargs.pop("headers", {})
        if use_go_headers:
            go_headers = get_go_style_headers()
            # Go 风格头部优先级较低，允许被覆盖
            headers = {**go_headers, **headers}

        async with CurlAsyncSession(
            impersonate=get_impersonate_target(),
            timeout=timeout,
            proxies=proxies,
            headers=headers,
            **kwargs
        ) as session:
            yield session

    async def _get_httpx_client(
        self, timeout: float, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取原生 httpx 客户端（降级模式）"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)

        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client

    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: float = 600.0, **kwargs
    ) -> AsyncGenerator[Union[httpx.AsyncClient, "CurlAsyncSession"], None]:
        """
        获取用于流式请求的HTTP客户端

        默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考。
        如果需要无限等待，可以显式传入 timeout=None

        Args:
            timeout: 请求超时时间（秒），默认 600 秒
            **kwargs: 其他参数

        Yields:
            HTTP 客户端实例
        """
        self._log_init_once()

        if self._use_curl_cffi:
            # curl_cffi 流式客户端
            proxy_config = await get_proxy_config()
            proxies = None
            if proxy_config:
                proxies = {"http": proxy_config, "https": proxy_config}

            session = CurlAsyncSession(
                impersonate=get_impersonate_target(),
                timeout=timeout,
                proxies=proxies,
                **kwargs
            )
            try:
                yield session
            finally:
                await session.close()
        else:
            # httpx 流式客户端
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            client = httpx.AsyncClient(**client_kwargs)
            try:
                yield client
            finally:
                await safe_close_client(client)


# 全局HTTP客户端管理器实例
http_client = HttpxClientManager()


# ====================== 通用的异步方法 ======================

async def get_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """
    通用异步GET请求

    注意：当使用 curl_cffi 时，返回的是 curl_cffi.Response，
    但 API 兼容 httpx.Response
    """
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.get(url, headers=headers)


async def post_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """通用异步POST请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.post(url, data=data, json=json, headers=headers)


async def put_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """通用异步PUT请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.put(url, data=data, json=json, headers=headers)


async def delete_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """通用异步DELETE请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.delete(url, headers=headers)


# ====================== 错误处理装饰器 ======================

def handle_http_errors(func):
    """HTTP错误处理装饰器"""

    async def wrapper(*args, **kwargs):
        try:
            response = await func(*args, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            log.error(f"HTTP错误: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            log.error(f"请求错误: {e}")
            raise
        except Exception as e:
            # curl_cffi 的错误处理
            error_msg = str(e)
            if "status_code" in error_msg or "HTTP" in error_msg:
                log.error(f"HTTP错误: {e}")
            else:
                log.error(f"未知错误: {e}")
            raise

    return wrapper


# ====================== 应用错误处理的安全方法 ======================

@handle_http_errors
async def safe_get_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """安全的异步GET请求（自动错误处理）"""
    return await get_async(url, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_post_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """安全的异步POST请求（自动错误处理）"""
    return await post_async(url, data=data, json=json, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_put_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Response:
    """安全的异步PUT请求（自动错误处理）"""
    return await put_async(url, data=data, json=json, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_delete_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """安全的异步DELETE请求（自动错误处理）"""
    return await delete_async(url, headers=headers, timeout=timeout, **kwargs)


# ====================== 流式请求支持 ======================

class StreamingContext:
    """流式请求上下文管理器"""

    def __init__(self, client: httpx.AsyncClient, stream_context):
        self.client = client
        self.stream_context = stream_context
        self.response = None

    async def __aenter__(self):
        self.response = await self.stream_context.__aenter__()
        return self.response

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.stream_context:
                await self.stream_context.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self.client:
                await safe_close_client(self.client)


@asynccontextmanager
async def get_streaming_post_context(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 600.0,
    **kwargs,
) -> AsyncGenerator[StreamingContext, None]:
    """
    获取流式POST请求的上下文管理器

    默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考

    注意：当使用 curl_cffi 时，流式处理方式略有不同
    """
    async with http_client.get_streaming_client(timeout=timeout, **kwargs) as client:
        stream_ctx = client.stream("POST", url, data=data, json=json, headers=headers)
        streaming_context = StreamingContext(client, stream_ctx)
        yield streaming_context


async def create_streaming_client_with_kwargs(**kwargs) -> Union[httpx.AsyncClient, "CurlAsyncSession"]:
    """
    创建用于流式处理的独立客户端实例（手动管理生命周期）

    警告：调用者必须确保调用 safe_close_client() 来释放资源
    建议使用 get_streaming_client() 上下文管理器代替此方法

    默认超时 600 秒（10分钟），适合 thinking 模型的长时间思考
    如果调用方需要无限等待，可以显式传入 timeout=None
    """
    timeout = kwargs.pop('timeout', 600.0)

    if http_client._use_curl_cffi:
        # curl_cffi 客户端
        proxy_config = await get_proxy_config()
        proxies = None
        if proxy_config:
            proxies = {"http": proxy_config, "https": proxy_config}

        return CurlAsyncSession(
            impersonate=get_impersonate_target(),
            timeout=timeout,
            proxies=proxies,
            **kwargs
        )
    else:
        # httpx 客户端
        client_kwargs = await http_client.get_client_kwargs(timeout=timeout, **kwargs)
        return httpx.AsyncClient(**client_kwargs)


async def safe_close_client(client: Union[httpx.AsyncClient, "CurlAsyncSession", Any]) -> None:
    """
    ✅ [FIX 2026-01-22] 安全地关闭 HTTP 客户端
    
    兼容两种客户端类型：
    - httpx.AsyncClient: 使用 aclose() 方法
    - curl_cffi.AsyncSession: 使用 close() 方法
    
    ✅ [FIX 2026-01-23] 增强错误处理：
    - 检查客户端状态，避免重复关闭
    - 捕获 curl_cffi 的 C 类型错误（客户端已关闭时）
    
    Args:
        client: HTTP 客户端实例（可能是 httpx.AsyncClient 或 CurlAsyncSession）
    """
    if client is None:
        return
    
    try:
        # 检查是否是 curl_cffi 的 AsyncSession
        if CURL_CFFI_AVAILABLE and isinstance(client, CurlAsyncSession):
            # curl_cffi 使用 close() 方法
            # ✅ [FIX 2026-01-23] 检查客户端是否已关闭
            if hasattr(client, "close"):
                # 检查客户端内部状态，避免关闭已关闭的客户端
                try:
                    # 尝试访问客户端属性来判断是否已关闭
                    # curl_cffi 客户端关闭后，某些内部属性会变为 None
                    if hasattr(client, "_session") and client._session is None:
                        return  # 客户端已关闭，无需再次关闭
                except (AttributeError, TypeError):
                    pass  # 无法检查状态，继续尝试关闭
                
                try:
                    await client.close()
                except (TypeError, AttributeError, ValueError) as e:
                    # ✅ [FIX 2026-01-23] 捕获 curl_cffi 的 C 类型错误
                    # 错误信息通常包含 "cdata pointer" 或 "NoneType" 或 "initializer"
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ["cdata", "nonetype", "initializer", "void *"]):
                        # 客户端已关闭或处于无效状态，忽略错误
                        log.debug(f"[HttpxClient] Client already closed or invalid: {e}")
                        return
                    raise  # 其他错误继续抛出
                except Exception as e:
                    # ✅ [FIX 2026-01-23] 捕获其他可能的 curl_cffi 错误
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ["cdata", "nonetype", "initializer", "void *", "closed"]):
                        log.debug(f"[HttpxClient] Client already closed or invalid: {e}")
                        return
                    # 其他未知错误也忽略，避免影响主流程
                    log.debug(f"[HttpxClient] Ignoring error during client close: {e}")
                    return
        # 检查是否是 httpx 的 AsyncClient
        elif isinstance(client, httpx.AsyncClient):
            # httpx 使用 aclose() 方法
            if hasattr(client, "aclose"):
                # ✅ [FIX 2026-01-23] 检查 httpx 客户端是否已关闭
                try:
                    # httpx 客户端关闭后，_transport 会变为 None
                    if hasattr(client, "_transport") and client._transport is None:
                        return  # 客户端已关闭，无需再次关闭
                except (AttributeError, TypeError):
                    pass  # 无法检查状态，继续尝试关闭
                
                try:
                    await client.aclose()
                except (RuntimeError, AttributeError) as e:
                    # httpx 客户端已关闭时会抛出 RuntimeError
                    error_str = str(e).lower()
                    if "closed" in error_str or "not open" in error_str:
                        log.debug(f"[HttpxClient] Client already closed: {e}")
                        return
                    raise  # 其他错误继续抛出
        else:
            # 尝试通用方法：先尝试 aclose，再尝试 close
            if hasattr(client, "aclose"):
                try:
                    await client.aclose()
                except Exception:
                    # 如果 aclose 失败，尝试 close
                    if hasattr(client, "close"):
                        await client.close()
            elif hasattr(client, "close"):
                await client.close()
    except Exception as e:
        # ✅ [FIX 2026-01-23] 更详细的错误处理
        error_str = str(e).lower()
        # 忽略常见的"已关闭"错误，包括 curl_cffi 的 C 类型错误
        if any(keyword in error_str for keyword in ["closed", "cdata", "nonetype", "initializer", "void *", "not open"]):
            log.debug(f"[HttpxClient] Client already closed or invalid: {e}")
        else:
            # ✅ [FIX 2026-01-23] 将警告降级为调试，避免日志噪音
            # 这些错误通常是客户端已关闭或处于无效状态，不影响主流程
            log.debug(f"[HttpxClient] Error closing client (ignored): {e}")


# ====================== TLS 状态查询 ======================

def get_http_client_status() -> Dict[str, Any]:
    """
    获取 HTTP 客户端状态

    Returns:
        状态信息字典
    """
    from .tls_impersonate import get_tls_status
    tls_status = get_tls_status()

    return {
        "backend": "curl_cffi" if http_client._use_curl_cffi else "httpx",
        "tls_impersonate": tls_status,
    }
