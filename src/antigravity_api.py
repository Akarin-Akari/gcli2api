"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import (
    get_antigravity_api_url,
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_return_thoughts_to_frontend,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from .fallback_manager import get_cross_pool_fallback, get_model_pool

from .credential_manager import CredentialManager
from .httpx_client import create_streaming_client_with_kwargs, http_client
from .models import Model, model_to_dict
from .utils import ANTIGRAVITY_USER_AGENT, parse_quota_reset_timestamp


class NonRetryableError(Exception):
    """不可重试的错误，用于 400 等客户端错误，避免外层 except 继续重试"""
    pass


async def _check_should_auto_ban(status_code: int) -> bool:
    """检查是否应该触发自动封禁"""
    return (
        await get_auto_ban_enabled()
        and status_code in await get_auto_ban_error_codes()
    )


async def _handle_auto_ban(
    credential_manager: CredentialManager,
    status_code: int,
    credential_name: str
) -> None:
    """处理自动封禁：直接禁用凭证"""
    if credential_manager and credential_name:
        log.warning(
            f"[ANTIGRAVITY AUTO_BAN] Status {status_code} triggers auto-ban for credential: {credential_name}"
        )
        await credential_manager.set_cred_disabled(credential_name, True, is_antigravity=True)


def build_antigravity_headers(access_token: str) -> Dict[str, str]:
    """构建 Antigravity API 请求头"""
    return {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }


def generate_request_id() -> str:
    """生成请求 ID"""
    import uuid
    return f"req-{uuid.uuid4()}"


def build_antigravity_request_body(
    contents: List[Dict[str, Any]],
    model: str,
    project_id: str,
    session_id: str,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    generation_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 Antigravity 请求体

    Args:
        contents: 消息内容列表
        model: 模型名称
        project_id: 项目 ID
        session_id: 会话 ID
        system_instruction: 系统指令
        tools: 工具定义列表
        generation_config: 生成配置

    Returns:
        Antigravity 格式的请求体
    Raises:
        ValueError: 如果 contents 为空或包含空 parts
    """
    # ✅ 新增：验证 contents 不为空（防御性检查，最后一道防线）
    if not contents:
        raise ValueError("contents cannot be empty. At least one message with valid content is required. "
                        "This is a defensive check - the issue should be caught earlier in chat_completions.")

    # 验证每个 content 的 parts 不为空
    for i, content_item in enumerate(contents):
        parts = content_item.get("parts", [])
        if not parts:
            raise ValueError(f"Content at index {i} (role={content_item.get('role')}) has empty parts. "
                           f"Each content must have at least one part (text, image, or functionCall).")

    request_body = {
        "project": project_id,
        "requestId": generate_request_id(),
        "model": model,
        "userAgent": "antigravity",
        "request": {
            "contents": contents,
            "session_id": session_id,
        }
    }

    # 添加系统指令
    if system_instruction:
        request_body["request"]["systemInstruction"] = system_instruction

    # 添加工具定义
    if tools:
        request_body["request"]["tools"] = tools
        request_body["request"]["toolConfig"] = {
            "functionCallingConfig": {"mode": "VALIDATED"}
        }

    # 添加生成配置
    if generation_config:
        request_body["request"]["generationConfig"] = generation_config

    return request_body


async def _filter_thinking_from_stream(lines, return_thoughts: bool):
    """过滤流式响应中的思维链（如果配置禁用）"""
    async for line in lines:
        if not line or not line.startswith("data: "):
            yield line
            continue

        raw = line[6:].strip()
        if raw == "[DONE]":
            yield line
            continue

        if not return_thoughts:
            try:
                data = json.loads(raw)
                response = data.get("response", {}) or {}
                candidate = (response.get("candidates", []) or [{}])[0] or {}
                parts = (candidate.get("content", {}) or {}).get("parts", []) or []

                # 过滤掉思维链部分
                filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]

                # 如果过滤后为空，跳过这一行
                if not filtered_parts and parts:
                    continue

                # 更新parts
                if filtered_parts != parts:
                    candidate["content"]["parts"] = filtered_parts
                    yield f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n"
                    continue
            except Exception:
                pass

        yield line


async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    发送 Antigravity 流式请求

    重试策略优化：
    - 5xx 错误：用同一个凭证重试（服务端临时问题，切换凭证没意义）
    - 429 限流：切换凭证重试，但最多 3 次（避免快速消耗所有凭证额度）
    - 400 错误：不重试（客户端参数错误）
    - 额度用尽：不重试（让上层处理降级）

    Returns:
        (response, credential_name, credential_data)
    """
    retry_enabled = await get_retry_429_enabled()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # 429 限流时切换凭证的最大次数（避免快速消耗所有凭证额度）
    max_credential_switches = 3
    credential_switch_count = 0

    # 5xx 错误时用同一凭证重试的最大次数
    max_same_cred_retries = 2

    # 当前使用的凭证（5xx 错误时复用）
    current_cred_result = None

    while True:
        # 决定是否需要获取新凭证
        need_new_credential = current_cred_result is None

        if need_new_credential:
            # 获取可用凭证（传递模型名称）
            cred_result = await credential_manager.get_valid_credential(
                is_antigravity=True, model_key=model_name
            )

            # 根据 enable_cross_pool_fallback 参数决定降级策略
            if not cred_result:
                if enable_cross_pool_fallback:
                    # Claude Code 模式：尝试跨池降级
                    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                    if fallback_model:
                        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=fallback_model
                        )
                        if cred_result:
                            # 更新请求体中的模型名
                            request_body["model"] = fallback_model
                            model_name = fallback_model  # 更新本地变量
                            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

                # 如果仍然没有凭证，报错
                if not cred_result:
                    log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
                    raise Exception(f"No valid antigravity credentials available for model {model_name}")

            current_cred_result = cred_result
            same_cred_retry_count = 0  # 重置同凭证重试计数

        current_file, credential_data = current_cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            current_cred_result = None  # 强制获取新凭证
            continue

        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, cred_switches={credential_switch_count}/{max_credential_switches})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送流式请求
            client = await create_streaming_client_with_kwargs()
            antigravity_url = await get_antigravity_api_url()

            try:
                # 使用stream方法但不在async with块中消费数据
                stream_ctx = client.stream(
                    "POST",
                    f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse",
                    json=request_body,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    # 获取配置并包装响应流，在源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    filtered_lines = _filter_thinking_from_stream(response.aiter_lines(), return_thoughts)
                    # 返回过滤后的行生成器和资源管理对象,让调用者管理资源生命周期
                    return (filtered_lines, stream_ctx, client), current_file, credential_data

                # 处理错误
                error_body = await response.aread()
                error_text = error_body.decode('utf-8', errors='ignore')
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_text[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    try:
                        error_data = json.loads(error_text)
                        cooldown_until = parse_quota_reset_timestamp(error_data)
                        if cooldown_until:
                            log.info(
                                f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                            )
                    except Exception as parse_err:
                        log.debug(f"[ANTIGRAVITY] Failed to parse cooldown time: {parse_err}")

                await credential_manager.record_api_call_result(
                    current_file,
                    False,
                    response.status_code,
                    cooldown_until=cooldown_until,
                    is_antigravity=True,
                    model_key=model_name
                )

                # 检查自动封禁
                if await _check_should_auto_ban(response.status_code):
                    await _handle_auto_ban(credential_manager, response.status_code, current_file)

                # 清理资源
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await client.aclose()

                # 400 错误 - 客户端参数错误，不重试
                if response.status_code == 400:
                    log.warning(f"[ANTIGRAVITY] 400 客户端错误，不重试 (model={model_name})")
                    raise NonRetryableError(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

                # 429 错误处理
                if response.status_code == 429:
                    # 检查是否是额度用尽
                    is_capacity_exhausted = False
                    try:
                        error_data = json.loads(error_text)
                        details = error_data.get("error", {}).get("details", [])
                        for detail in details:
                            if detail.get("reason") == "MODEL_CAPACITY_EXHAUSTED":
                                is_capacity_exhausted = True
                                log.warning(f"[ANTIGRAVITY] MODEL_CAPACITY_EXHAUSTED detected, not retrying (model={model_name})")
                                break
                    except Exception:
                        pass

                    if is_capacity_exhausted:
                        # 额度用尽，不重试，让上层处理降级
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

                    # 普通 429 限流 - 切换凭证重试，但有次数限制
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        credential_switch_count += 1
                        current_cred_result = None  # 强制获取新凭证
                        log.warning(f"[ANTIGRAVITY] 429 限流，切换凭证重试 ({credential_switch_count}/{max_credential_switches})")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 429 限流，已达到最大凭证切换次数 ({max_credential_switches})，不再重试")
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

                # 5xx 错误 - 用同一凭证重试
                if response.status_code >= 500:
                    same_cred_retry_count += 1
                    if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，已达到最大重试次数 ({max_same_cred_retries})，不再重试")
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

                # 其他错误，直接抛出
                raise Exception(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

            except NonRetryableError:
                # 不可重试的错误，确保清理资源后直接向上抛出
                try:
                    await client.aclose()
                except Exception:
                    pass
                raise
            except Exception as stream_error:
                # 确保在异常情况下也清理资源
                try:
                    await client.aclose()
                except Exception:
                    pass
                raise stream_error

        except NonRetryableError:
            # 不可重试的错误，直接向上抛出
            raise
        except Exception as e:
            # 网络错误等 - 用同一凭证重试
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            same_cred_retry_count = getattr(locals(), 'same_cred_retry_count', 0) + 1
            if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                log.warning(f"[ANTIGRAVITY] 网络错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                await asyncio.sleep(retry_interval)
                continue
            raise


async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    发送 Antigravity 非流式请求

    重试策略优化（与流式请求保持一致）：
    - 5xx 错误：用同一个凭证重试（服务端临时问题，切换凭证没意义）
    - 429 限流：切换凭证重试，但最多 3 次（避免快速消耗所有凭证额度）
    - 400 错误：不重试（客户端参数错误）
    - 额度用尽：不重试（让上层处理降级）

    Returns:
        (response_data, credential_name, credential_data)
    """
    retry_enabled = await get_retry_429_enabled()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # 429 限流时切换凭证的最大次数（避免快速消耗所有凭证额度）
    max_credential_switches = 3
    credential_switch_count = 0

    # 5xx 错误时用同一凭证重试的最大次数
    max_same_cred_retries = 2

    # 当前使用的凭证（5xx 错误时复用）
    current_cred_result = None
    same_cred_retry_count = 0

    while True:
        # 决定是否需要获取新凭证
        need_new_credential = current_cred_result is None

        if need_new_credential:
            # 获取可用凭证（传递模型名称）
            cred_result = await credential_manager.get_valid_credential(
                is_antigravity=True, model_key=model_name
            )

            # 根据 enable_cross_pool_fallback 参数决定降级策略
            if not cred_result:
                if enable_cross_pool_fallback:
                    # Claude Code 模式：尝试跨池降级
                    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                    if fallback_model:
                        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=fallback_model
                        )
                        if cred_result:
                            # 更新请求体中的模型名
                            request_body["model"] = fallback_model
                            model_name = fallback_model  # 更新本地变量
                            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

                # 如果仍然没有凭证，报错
                if not cred_result:
                    log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
                    raise Exception(f"No valid antigravity credentials available for model {model_name}")

            current_cred_result = cred_result
            same_cred_retry_count = 0  # 重置同凭证重试计数

        current_file, credential_data = current_cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            current_cred_result = None  # 强制获取新凭证
            continue

        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, cred_switches={credential_switch_count}/{max_credential_switches})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送非流式请求
            antigravity_url = await get_antigravity_api_url()

            # 使用上下文管理器确保正确的资源管理
            async with http_client.get_client(timeout=300.0) as client:
                response = await client.post(
                    f"{antigravity_url}/v1internal:generateContent",
                    json=request_body,
                    headers=headers,
                )

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    await credential_manager.record_api_call_result(
                        current_file, True, is_antigravity=True, model_key=model_name
                    )
                    response_data = response.json()

                    # 从源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    if not return_thoughts:
                        try:
                            candidate = (response_data.get("response", {}) or {}).get("candidates", [{}])[0] or {}
                            parts = (candidate.get("content", {}) or {}).get("parts", []) or []
                            # 过滤掉思维链部分
                            filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]
                            if filtered_parts != parts:
                                candidate["content"]["parts"] = filtered_parts
                        except Exception as e:
                            log.debug(f"[ANTIGRAVITY] Failed to filter thinking from response: {e}")

                    return response_data, current_file, credential_data

                # 处理错误
                error_body = response.text
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_body[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    try:
                        error_data = json.loads(error_body)
                        cooldown_until = parse_quota_reset_timestamp(error_data)
                        if cooldown_until:
                            log.info(
                                f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                            )
                    except Exception as parse_err:
                        log.debug(f"[ANTIGRAVITY] Failed to parse cooldown time: {parse_err}")

                await credential_manager.record_api_call_result(
                    current_file,
                    False,
                    response.status_code,
                    cooldown_until=cooldown_until,
                    is_antigravity=True,
                    model_key=model_name
                )

                # 检查自动封禁
                if await _check_should_auto_ban(response.status_code):
                    await _handle_auto_ban(credential_manager, response.status_code, current_file)

                # 400 错误 - 客户端参数错误，不重试
                if response.status_code == 400:
                    log.warning(f"[ANTIGRAVITY] 400 客户端错误，不重试 (model={model_name})")
                    raise NonRetryableError(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

                # 429 错误处理
                if response.status_code == 429:
                    # 检查是否是额度用尽
                    is_capacity_exhausted = False
                    try:
                        error_data = json.loads(error_body)
                        details = error_data.get("error", {}).get("details", [])
                        for detail in details:
                            if detail.get("reason") == "MODEL_CAPACITY_EXHAUSTED":
                                is_capacity_exhausted = True
                                log.warning(f"[ANTIGRAVITY] MODEL_CAPACITY_EXHAUSTED detected, not retrying (model={model_name})")
                                break
                    except Exception:
                        pass

                    if is_capacity_exhausted:
                        # 额度用尽，不重试，让上层处理降级
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

                    # 普通 429 限流 - 切换凭证重试，但有次数限制
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        credential_switch_count += 1
                        current_cred_result = None  # 强制获取新凭证
                        log.warning(f"[ANTIGRAVITY] 429 限流，切换凭证重试 ({credential_switch_count}/{max_credential_switches})")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 429 限流，已达到最大凭证切换次数 ({max_credential_switches})，不再重试")
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

                # 5xx 错误 - 用同一凭证重试
                if response.status_code >= 500:
                    same_cred_retry_count += 1
                    if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，已达到最大重试次数 ({max_same_cred_retries})，不再重试")
                        raise Exception(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

                # 其他错误，直接抛出
                raise Exception(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

        except NonRetryableError:
            # 不可重试的错误，直接向上抛出
            raise
        except Exception as e:
            # 网络错误等 - 用同一凭证重试
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            same_cred_retry_count += 1
            if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                log.warning(f"[ANTIGRAVITY] 网络错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                await asyncio.sleep(retry_interval)
                continue
            raise


async def fetch_available_models(
    credential_manager: CredentialManager,
) -> List[Dict[str, Any]]:
    """
    获取可用模型列表，返回符合 OpenAI API 规范的格式

    Returns:
        模型列表，格式为字典列表（用于兼容现有代码）
    """
    # 获取可用凭证
    cred_result = await credential_manager.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("[ANTIGRAVITY] No valid credentials available for fetching models")
        return []

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return []

    # 构建请求头
    headers = build_antigravity_headers(access_token)

    try:
        # 使用 POST 请求获取模型列表（根据 buildAxiosConfig，method 是 POST）
        antigravity_url = await get_antigravity_api_url()

        # 使用上下文管理器确保正确的资源管理
        async with http_client.get_client(timeout=30.0) as client:
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},  # 空的请求体
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY] Raw models response: {json.dumps(data, ensure_ascii=False)[:500]}")

                # 转换为 OpenAI 格式的模型列表，使用 Model 类
                model_list = []
                current_timestamp = int(datetime.now(timezone.utc).timestamp())

                if 'models' in data and isinstance(data['models'], dict):
                    # 遍历模型字典
                    for model_id in data['models'].keys():
                        model = Model(
                            id=model_id,
                            object='model',
                            created=current_timestamp,
                            owned_by='google'
                        )
                        model_list.append(model_to_dict(model))

                # 添加额外的 claude-opus-4-5 模型
                claude_opus_model = Model(
                    id='claude-opus-4-5',
                    object='model',
                    created=current_timestamp,
                    owned_by='google'
                )
                model_list.append(model_to_dict(claude_opus_model))

                log.info(f"[ANTIGRAVITY] Fetched {len(model_list)} available models")
                return model_list
            else:
                log.error(f"[ANTIGRAVITY] Failed to fetch models ({response.status_code}): {response.text[:500]}")
                return []

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY] Failed to fetch models: {e}")
        log.error(f"[ANTIGRAVITY] Traceback: {traceback.format_exc()}")
        return []


async def fetch_quota_info(access_token: str) -> Dict[str, Any]:
    """
    获取指定凭证的额度信息

    Args:
        access_token: Antigravity 访问令牌

    Returns:
        包含额度信息的字典，格式为：
        {
            "success": True,
            "models": {
                "gemini-2.0-flash-exp": {
                    "remaining": 0.95,
                    "resetTime": "12-20 10:30",
                    "resetTimeRaw": "2025-12-20T02:30:00Z"
                }
            }
        }
    """

    headers = build_antigravity_headers(access_token)

    try:
        antigravity_url = await get_antigravity_api_url()

        async with http_client.get_client(timeout=30.0) as client:
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY QUOTA] Raw response: {json.dumps(data, ensure_ascii=False)[:500]}")

                quota_info = {}

                if 'models' in data and isinstance(data['models'], dict):
                    for model_id, model_data in data['models'].items():
                        if isinstance(model_data, dict) and 'quotaInfo' in model_data:
                            quota = model_data['quotaInfo']
                            remaining = quota.get('remainingFraction', 0)
                            reset_time_raw = quota.get('resetTime', '')

                            # 转换为北京时间
                            reset_time_beijing = 'N/A'
                            if reset_time_raw:
                                try:
                                    utc_date = datetime.fromisoformat(reset_time_raw.replace('Z', '+00:00'))
                                    # 转换为北京时间 (UTC+8)
                                    from datetime import timedelta
                                    beijing_date = utc_date + timedelta(hours=8)
                                    reset_time_beijing = beijing_date.strftime('%m-%d %H:%M')
                                except Exception as e:
                                    log.warning(f"[ANTIGRAVITY QUOTA] Failed to parse reset time: {e}")

                            quota_info[model_id] = {
                                "remaining": remaining,
                                "resetTime": reset_time_beijing,
                                "resetTimeRaw": reset_time_raw
                            }

                return {
                    "success": True,
                    "models": quota_info
                }
            else:
                log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota ({response.status_code}): {response.text[:500]}")
                return {
                    "success": False,
                    "error": f"API返回错误: {response.status_code}"
                }

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota: {e}")
        log.error(f"[ANTIGRAVITY QUOTA] Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e)
        }