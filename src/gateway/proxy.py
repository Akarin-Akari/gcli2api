"""
Gateway 代理请求模块

包含请求代理、流式响应处理、降级路由逻辑。

从 unified_gateway_router.py 抽取的代理函数。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, Optional, Tuple, Callable, Union, AsyncIterator
import json
import asyncio

import httpx
from fastapi import HTTPException
from starlette.responses import StreamingResponse as StarletteStreamingResponse

from .config import BACKENDS, RETRY_CONFIG, map_model_for_copilot
from .routing import (
    get_sorted_backends,
    get_backend_for_model,
    calculate_retry_delay,
    should_retry,
)

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# 延迟导入 http_client
try:
    from src.httpx_client import http_client, safe_close_client
except ImportError:
    http_client = None

__all__ = [
    "proxy_request_to_backend",
    "proxy_streaming_request",
    "proxy_streaming_request_with_timeout",
    "route_request_with_fallback",
    "ProxyHandler",
]


# ==================== 代理处理器类 ====================

class ProxyHandler:
    """
    代理处理器

    支持依赖注入本地处理器，避免硬编码直调。
    """

    def __init__(
        self,
        local_handler: Optional[Callable] = None,
        backends: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """
        初始化代理处理器

        Args:
            local_handler: 本地处理器函数 (用于 antigravity 后端直调)
            backends: 后端配置字典 (默认使用全局配置)
        """
        self._local_handler = local_handler
        self._backends = backends or BACKENDS

    async def proxy_request(
        self,
        backend_key: str,
        endpoint: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        stream: bool = False,
        method: str = "POST",
    ) -> Tuple[bool, Any]:
        """
        代理请求到后端

        Args:
            backend_key: 后端标识
            endpoint: API 端点
            body: 请求体
            headers: 请求头
            stream: 是否流式响应
            method: HTTP 方法

        Returns:
            Tuple[bool, Any]: (成功标志, 响应内容或错误信息)
        """
        return await proxy_request_to_backend(
            backend_key=backend_key,
            endpoint=endpoint,
            method=method,
            headers=headers,
            body=body,
            stream=stream,
            local_handler=self._local_handler,
            backends=self._backends,
        )


# ==================== 代理函数 ====================

async def proxy_request_to_backend(
    backend_key: str,
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    stream: bool = False,
    local_handler: Optional[Callable] = None,
    backends: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[bool, Any]:
    """
    代理请求到指定后端（带重试机制）

    Args:
        backend_key: 后端标识
        endpoint: API 端点
        method: HTTP 方法
        headers: 请求头
        body: 请求体
        stream: 是否流式响应
        local_handler: 本地处理器 (用于 antigravity 后端直调)
        backends: 后端配置字典

    Returns:
        Tuple[bool, Any]: (成功标志, 响应内容或错误信息)
    """
    if backends is None:
        backends = BACKENDS

    backend = backends.get(backend_key)
    if not backend:
        return False, f"Backend {backend_key} not found"

    # ==================== 本地 Antigravity：service 直调（避免 127.0.0.1 回环） ====================
    if backend_key == "antigravity" and endpoint == "/chat/completions" and method.upper() == "POST":
        # 尝试使用本地处理器
        handler = local_handler
        if handler is None:
            try:
                from src.services.antigravity_service import handle_openai_chat_completions
                handler = handle_openai_chat_completions
            except ImportError:
                pass

        if handler is not None:
            try:
                resp = await handler(body=body, headers=headers)

                status_code = getattr(resp, "status_code", 200)
                if stream:
                    if status_code >= 400:
                        async def error_stream():
                            error_msg = json.dumps({"error": "Backend error", "status": status_code})
                            yield f"data: {error_msg}\n\n"
                        return True, error_stream()

                    if isinstance(resp, StarletteStreamingResponse):
                        return True, resp.body_iterator

                    # 非预期：流式请求返回了非 StreamingResponse
                    return False, f"Backend error: {status_code}"

                # 非流式
                if status_code >= 400:
                    return False, f"Backend error: {status_code}"

                resp_body = getattr(resp, "body", b"")
                if isinstance(resp_body, bytes):
                    return True, json.loads(resp_body.decode("utf-8", errors="ignore") or "{}")
                if isinstance(resp_body, str):
                    return True, json.loads(resp_body or "{}")
                return True, resp_body

            except HTTPException as e:
                if stream:
                    status = int(getattr(e, "status_code", 500))

                    async def error_stream(status_code: int = status):
                        error_msg = json.dumps({"error": "Backend error", "status": status_code})
                        yield f"data: {error_msg}\n\n"
                    return True, error_stream()
                return False, f"Backend error: {e.status_code}"
            except Exception as e:
                log.error(f"Local antigravity service call failed: {e}", tag="GATEWAY")
                if stream:
                    msg = str(e)

                    async def error_stream(error_message: str = msg):
                        error_msg = json.dumps({"error": error_message})
                        yield f"data: {error_msg}\n\n"
                    return True, error_stream()
                return False, str(e)

    # 对 Copilot 后端应用模型名称映射
    if backend_key == "copilot" and body and isinstance(body, dict) and "model" in body:
        original_model = body.get("model", "")
        mapped_model = map_model_for_copilot(original_model)
        if mapped_model != original_model:
            if hasattr(log, 'route'):
                log.route(f"Model mapped: {original_model} -> {mapped_model}", tag="COPILOT")
            body = {**body, "model": mapped_model}

    url = f"{backend['base_url']}{endpoint}"

    # 根据请求类型选择超时时间
    if stream:
        timeout = backend.get("stream_timeout", backend.get("timeout", 300.0))
    else:
        timeout = backend.get("timeout", 60.0)

    # 获取最大重试次数
    max_retries = backend.get("max_retries", RETRY_CONFIG.get("max_retries", 3))

    # 构建请求头
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": headers.get("authorization", "Bearer dummy"),
    }
    # Preserve upstream client identity (important for backend routing/features)
    user_agent = headers.get("user-agent") or headers.get("User-Agent")
    if user_agent:
        request_headers["User-Agent"] = user_agent
        # Keep a copy even if a downstream client overwrites User-Agent
        request_headers["X-Forwarded-User-Agent"] = user_agent

    # Forward a small allowlist of gateway control headers
    for h in (
        "x-augment-client",
        "x-bugment-client",
        "x-augment-request",
        "x-bugment-request",
        # Augment signed-request headers (preserve for downstream logging/compat)
        "x-signature-version",
        "x-signature-timestamp",
        "x-signature-signature",
        "x-signature-vector",
        "x-disable-thinking-signature",
        "x-request-id",
    ):
        v = headers.get(h) or headers.get(h.lower()) or headers.get(h.upper())
        if v:
            request_headers[h] = v

    last_error = None
    last_status_code = None

    for attempt in range(max_retries + 1):  # +1 因为第一次不算重试
        try:
            if attempt > 0:
                delay = calculate_retry_delay(attempt - 1)
                log.warning(f"Retry {attempt}/{max_retries} for {backend_key} after {delay:.1f}s delay")
                await asyncio.sleep(delay)

            if stream:
                # 流式请求（带超时）
                return await proxy_streaming_request_with_timeout(
                    url, method, request_headers, body, timeout, backend_key
                )
            else:
                # 非流式请求
                if http_client is not None:
                    async with http_client.get_client(timeout=timeout) as client:
                        if method.upper() == "POST":
                            response = await client.post(url, json=body, headers=request_headers)
                        elif method.upper() == "GET":
                            response = await client.get(url, headers=request_headers)
                        else:
                            return False, f"Unsupported method: {method}"

                        last_status_code = response.status_code

                        if response.status_code >= 400:
                            error_text = response.text
                            log.warning(f"Backend {backend_key} returned error {response.status_code}: {error_text[:200]}")

                            # 检查是否应该重试
                            if should_retry(response.status_code, attempt, max_retries):
                                last_error = f"Backend error: {response.status_code}"
                                continue

                            return False, f"Backend error: {response.status_code}"

                        return True, response.json()
                else:
                    # 没有 http_client，使用 httpx 直接请求
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        if method.upper() == "POST":
                            response = await client.post(url, json=body, headers=request_headers)
                        elif method.upper() == "GET":
                            response = await client.get(url, headers=request_headers)
                        else:
                            return False, f"Unsupported method: {method}"

                        last_status_code = response.status_code

                        if response.status_code >= 400:
                            error_text = response.text
                            log.warning(f"Backend {backend_key} returned error {response.status_code}: {error_text[:200]}")

                            if should_retry(response.status_code, attempt, max_retries):
                                last_error = f"Backend error: {response.status_code}"
                                continue

                            return False, f"Backend error: {response.status_code}"

                        return True, response.json()

        except httpx.TimeoutException:
            log.warning(f"Backend {backend_key} timeout (attempt {attempt + 1}/{max_retries + 1})")
            last_error = "Request timeout"
            if attempt < max_retries:
                continue
        except httpx.ConnectError:
            log.warning(f"Backend {backend_key} connection failed (attempt {attempt + 1}/{max_retries + 1})")
            last_error = "Connection failed"
            if attempt < max_retries:
                continue
        except Exception as e:
            log.error(f"Backend {backend_key} request failed: {e}")
            last_error = str(e)
            # 对于未知错误，不重试
            break

    # 所有重试都失败
    log.error(f"Backend {backend_key} failed after {max_retries + 1} attempts. Last error: {last_error}")
    return False, last_error or "Unknown error"


async def proxy_streaming_request_with_timeout(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout: float,
    backend_key: str = "unknown",
) -> Tuple[bool, Any]:
    """
    处理流式代理请求（带超时和错误处理）

    Args:
        url: 请求URL
        method: HTTP方法
        headers: 请求头
        body: 请求体
        timeout: 超时时间（秒）
        backend_key: 后端标识（用于日志）

    Returns:
        Tuple[bool, Any]: (成功标志, 流生成器或错误信息)
    """
    try:
        # 创建带超时的客户端
        timeout_config = httpx.Timeout(
            connect=30.0,      # 连接超时
            read=timeout,      # 读取超时（流式数据）
            write=30.0,        # 写入超时
            pool=30.0,         # 连接池超时
        )
        client = httpx.AsyncClient(timeout=timeout_config)

        async def stream_generator():
            # 注意：chunk_timeout 检查已移除
            # 原因：之前的逻辑是在收到 chunk 后才检查时间差，这是错误的。
            # 当模型需要长时间思考（如 Claude 写长文档）时，两个 chunk 之间可能超过 120 秒，
            # 但只要最终收到了数据，就不应该超时。
            # httpx 的 read=timeout 配置已经处理了真正的读取超时。

            yielded_any = False
            saw_done = False

            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"Streaming request to {backend_key} failed: {response.status_code}")
                        error_msg = json.dumps({'error': 'Backend error', 'status': response.status_code})
                        yield f"data: {error_msg}\n\n"
                        return

                    if hasattr(log, 'success'):
                        log.success(f"Streaming started", tag=backend_key.upper())

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yielded_any = True
                            if b"[DONE]" in chunk:
                                saw_done = True
                            yield chunk.decode("utf-8", errors="ignore")

                    if hasattr(log, 'success'):
                        log.success(f"Streaming completed", tag=backend_key.upper())

            except httpx.ReadTimeout:
                log.warning(f"Read timeout from {backend_key} after {timeout}s")
                error_msg = json.dumps({
                    'error': {
                        'type': 'network',
                        'reason': 'timeout',
                        'message': 'Request timed out',
                        'retryable': True
                    }
                })
                yield f"data: {error_msg}\n\n"
            except httpx.ConnectTimeout:
                log.warning(f"Connect timeout to {backend_key}")
                error_msg = json.dumps({
                    'error': {
                        'type': 'network',
                        'reason': 'timeout',
                        'message': 'Request timed out',
                        'retryable': True
                    }
                })
                yield f"data: {error_msg}\n\n"
            except httpx.RemoteProtocolError as e:
                # Some upstreams (notably enterprise proxies) may close a chunked response
                # without a proper terminating chunk, even though the client has already
                # received the semantic end marker (e.g. SSE "[DONE]").
                #
                # If we already forwarded any bytes (or saw "[DONE]"), treat this as a
                # benign end-of-stream to avoid breaking Bugment parsers and spamming logs.
                if "incomplete chunked read" in str(e).lower():
                    if saw_done or yielded_any:
                        log.warning(
                            f"Ignoring benign upstream RemoteProtocolError after completion: {e}",
                            tag=backend_key.upper(),
                        )
                        return
                log.error(f"Streaming protocol error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            except asyncio.CancelledError:
                # Downstream client disconnected/cancelled (common for prompt enhancer or UI refresh).
                # Stop consuming the upstream stream quietly.
                return
            except Exception as e:
                log.error(f"Streaming error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            finally:
                try:
                    await safe_close_client(client)
                except Exception:
                    # Avoid noisy event-loop "connection_lost" traces on Windows Proactor when the
                    # peer has already reset the connection.
                    pass

        return True, stream_generator()

    except Exception as e:
        log.error(f"Failed to start streaming from {backend_key}: {e}")
        return False, str(e)


async def proxy_streaming_request(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout: float,
) -> Tuple[bool, Any]:
    """处理流式代理请求（兼容旧接口）"""
    try:
        client = httpx.AsyncClient(timeout=None)

        async def stream_generator():
            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"Streaming request failed: {response.status_code}")
                        error_msg = json.dumps({'error': 'Backend error', 'status': response.status_code})
                        yield f"data: {error_msg}\n\n"
                        return

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode("utf-8", errors="ignore")
            except httpx.RemoteProtocolError as e:
                # See proxy_streaming_request_with_timeout() for rationale.
                if "incomplete chunked read" in str(e).lower():
                    return
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\n\n"
            except asyncio.CancelledError:
                return
            finally:
                try:
                    await safe_close_client(client)
                except Exception:
                    pass

        return True, stream_generator()

    except Exception as e:
        log.error(f"Streaming request failed: {e}")
        return False, str(e)


async def route_request_with_fallback(
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    model: Optional[str] = None,
    stream: bool = False,
    local_handler: Optional[Callable] = None,
    backends: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    """
    带故障转移的请求路由

    优先使用指定后端，失败时自动切换到备用后端

    Args:
        endpoint: API 端点
        method: HTTP 方法
        headers: 请求头
        body: 请求体
        model: 模型名称 (用于选择后端)
        stream: 是否流式响应
        local_handler: 本地处理器
        backends: 后端配置字典

    Returns:
        响应内容

    Raises:
        HTTPException: 所有后端都失败时
    """
    if backends is None:
        backends = BACKENDS

    # 确定后端顺序
    specified_backend = get_backend_for_model(model) if model else None
    sorted_backends = get_sorted_backends()

    if specified_backend:
        # 将指定后端移到最前面
        sorted_backends = [(k, v) for k, v in sorted_backends if k == specified_backend] + \
                         [(k, v) for k, v in sorted_backends if k != specified_backend]

    last_error = None

    for backend_key, backend_config in sorted_backends:
        log.info(f"Trying backend: {backend_config['name']} for {endpoint}")

        success, result = await proxy_request_to_backend(
            backend_key=backend_key,
            endpoint=endpoint,
            method=method,
            headers=headers,
            body=body,
            stream=stream,
            local_handler=local_handler,
            backends=backends,
        )

        if success:
            if hasattr(log, 'success'):
                log.success(f"Request succeeded via {backend_config['name']}", tag="GATEWAY")
            return result

        last_error = result
        log.warning(f"Backend {backend_config['name']} failed: {result}, trying next...")

    # 所有后端都失败
    raise HTTPException(
        status_code=503,
        detail=f"All backends failed. Last error: {last_error}"
    )
