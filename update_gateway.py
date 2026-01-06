#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
更新 unified_gateway_router.py 添加重试逻辑
"""

import re

file_path = 'src/unified_gateway_router.py'

# 读取文件
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 更新 proxy_request_to_backend 函数
old_proxy_func = '''async def proxy_request_to_backend(
    backend_key: str,
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    stream: bool = False,
) -> Tuple[bool, Any]:
    """
    代理请求到指定后端

    Returns:
        Tuple[bool, Any]: (成功标志, 响应内容或错误信息)
    """
    backend = BACKENDS.get(backend_key)
    if not backend:
        return False, f"Backend {backend_key} not found"

    url = f"{backend['base_url']}{endpoint}"
    timeout = backend.get("timeout", 30.0)

    # 构建请求头
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": headers.get("authorization", "Bearer dummy"),
    }

    try:
        if stream:
            # 流式请求
            return await proxy_streaming_request(url, method, request_headers, body, timeout)
        else:
            # 非流式请求
            async with http_client.get_client(timeout=timeout) as client:
                if method.upper() == "POST":
                    response = await client.post(url, json=body, headers=request_headers)
                elif method.upper() == "GET":
                    response = await client.get(url, headers=request_headers)
                else:
                    return False, f"Unsupported method: {method}"

                if response.status_code >= 400:
                    error_text = response.text
                    log.warning(f"Backend {backend_key} returned error {response.status_code}: {error_text[:200]}")
                    return False, f"Backend error: {response.status_code}"

                return True, response.json()

    except httpx.TimeoutException:
        log.warning(f"Backend {backend_key} timeout")
        return False, "Request timeout"
    except httpx.ConnectError:
        log.warning(f"Backend {backend_key} connection failed")
        return False, "Connection failed"
    except Exception as e:
        log.error(f"Backend {backend_key} request failed: {e}")
        return False, str(e)'''

new_proxy_func = '''async def proxy_request_to_backend(
    backend_key: str,
    endpoint: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    stream: bool = False,
) -> Tuple[bool, Any]:
    """
    代理请求到指定后端（带重试机制）

    Returns:
        Tuple[bool, Any]: (成功标志, 响应内容或错误信息)
    """
    backend = BACKENDS.get(backend_key)
    if not backend:
        return False, f"Backend {backend_key} not found"

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

    last_error = None
    last_status_code = None

    for attempt in range(max_retries + 1):  # +1 因为第一次不算重试
        try:
            if attempt > 0:
                delay = calculate_retry_delay(attempt - 1)
                log.info(f"[Gateway] Retry {attempt}/{max_retries} for {backend_key} after {delay:.1f}s delay")
                await asyncio.sleep(delay)

            if stream:
                # 流式请求（带超时）
                return await proxy_streaming_request_with_timeout(
                    url, method, request_headers, body, timeout, backend_key
                )
            else:
                # 非流式请求
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
    return False, last_error or "Unknown error"'''

# 2. 添加带超时的流式请求函数
old_streaming = '''async def proxy_streaming_request(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout: float,
) -> Tuple[bool, Any]:
    """处理流式代理请求"""
    try:
        client = httpx.AsyncClient(timeout=None)

        async def stream_generator():
            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"Streaming request failed: {response.status_code}")
                        yield f"data: {json.dumps({'error': 'Backend error', 'status': response.status_code})}\\n\\n"
                        return

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode("utf-8", errors="ignore")
            finally:
                await client.aclose()

        return True, stream_generator()

    except Exception as e:
        log.error(f"Streaming request failed: {e}")
        return False, str(e)'''

new_streaming = '''async def proxy_streaming_request_with_timeout(
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
            last_data_time = time.time()
            chunk_timeout = 120.0  # 单个chunk的超时时间（2分钟）

            try:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        log.warning(f"[Gateway] Streaming request to {backend_key} failed: {response.status_code}")
                        error_msg = json.dumps({'error': 'Backend error', 'status': response.status_code})
                        yield f"data: {error_msg}\\n\\n"
                        return

                    log.info(f"[Gateway] Streaming started from {backend_key}")

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            current_time = time.time()
                            # 检查是否超过chunk超时
                            if current_time - last_data_time > chunk_timeout:
                                log.warning(f"[Gateway] Chunk timeout from {backend_key} after {chunk_timeout}s")
                                error_msg = json.dumps({'error': 'Chunk timeout', 'message': 'No data received for too long'})
                                yield f"data: {error_msg}\\n\\n"
                                break

                            last_data_time = current_time
                            yield chunk.decode("utf-8", errors="ignore")

                    log.info(f"[Gateway] Streaming completed from {backend_key}")

            except httpx.ReadTimeout:
                log.warning(f"[Gateway] Read timeout from {backend_key} after {timeout}s")
                error_msg = json.dumps({'error': 'Read timeout', 'message': f'No response within {timeout}s'})
                yield f"data: {error_msg}\\n\\n"
            except httpx.ConnectTimeout:
                log.warning(f"[Gateway] Connect timeout to {backend_key}")
                error_msg = json.dumps({'error': 'Connect timeout'})
                yield f"data: {error_msg}\\n\\n"
            except Exception as e:
                log.error(f"[Gateway] Streaming error from {backend_key}: {e}")
                error_msg = json.dumps({'error': str(e)})
                yield f"data: {error_msg}\\n\\n"
            finally:
                await client.aclose()

        return True, stream_generator()

    except Exception as e:
        log.error(f"[Gateway] Failed to start streaming from {backend_key}: {e}")
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
                        yield f"data: {error_msg}\\n\\n"
                        return

                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode("utf-8", errors="ignore")
            finally:
                await client.aclose()

        return True, stream_generator()

    except Exception as e:
        log.error(f"Streaming request failed: {e}")
        return False, str(e)'''

# 执行替换
modified = False

if old_proxy_func in content:
    content = content.replace(old_proxy_func, new_proxy_func)
    print("[OK] proxy_request_to_backend updated with retry logic")
    modified = True
else:
    print("[FAIL] Could not find old proxy_request_to_backend")

if old_streaming in content:
    content = content.replace(old_streaming, new_streaming)
    print("[OK] proxy_streaming_request_with_timeout added")
    modified = True
else:
    print("[FAIL] Could not find old streaming function")

if modified:
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("\n[SUCCESS] File updated!")
else:
    print("\n[INFO] No modifications made")
