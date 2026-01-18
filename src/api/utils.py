"""
Base API Utils - 复用的 API 客户端基础功能

对齐 gcli2api_official/src/api/utils.py 的职责边界：
- 错误处理与重试决策
- 自动封禁
- 成功/失败记录（模型级 cooldown）
- 429 冷却时间解析

说明：本模块先提供最小子集，逐步替换自研版中分散的实现，保持行为可控与可回滚。
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from config import (
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from src.utils import parse_quota_reset_timestamp


def _is_antigravity_mode(mode: str) -> bool:
    return (mode or "").lower() == "antigravity"


async def check_should_auto_ban(status_code: int) -> bool:
    return (await get_auto_ban_enabled()) and status_code in await get_auto_ban_error_codes()


async def handle_auto_ban(credential_manager, status_code: int, credential_name: str, mode: str = "geminicli") -> None:
    if not credential_manager or not credential_name:
        return
    log.warning(f"[{mode.upper()} AUTO_BAN] Status {status_code} triggers auto-ban for credential: {credential_name}")
    await credential_manager.set_cred_disabled(
        credential_name,
        True,
        is_antigravity=_is_antigravity_mode(mode),
    )


async def handle_error_with_retry(
    credential_manager,
    status_code: int,
    credential_name: str,
    retry_enabled: bool,
    attempt: int,
    max_retries: int,
    retry_interval: float,
    mode: str = "geminicli",
) -> bool:
    should_auto_ban = await check_should_auto_ban(status_code)
    if should_auto_ban:
        await handle_auto_ban(credential_manager, status_code, credential_name, mode)
        if retry_enabled and attempt < max_retries:
            log.info(
                f"[{mode.upper()} RETRY] Retrying with next credential after auto-ban "
                f"(status {status_code}, attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_interval)
            return True
        return False

    if status_code == 429 and retry_enabled and attempt < max_retries:
        log.info(
            f"[{mode.upper()} RETRY] 429 rate limit encountered, retrying "
            f"(attempt {attempt + 1}/{max_retries})"
        )
        await asyncio.sleep(retry_interval)
        return True

    return False


async def get_retry_config() -> dict:
    return {
        "retry_enabled": await get_retry_429_enabled(),
        "max_retries": await get_retry_429_max_retries(),
        "retry_interval": await get_retry_429_interval(),
    }


async def record_api_call_success(credential_manager, credential_name: str, mode: str = "geminicli", model_key: Optional[str] = None) -> None:
    if credential_manager and credential_name:
        await credential_manager.record_api_call_result(
            credential_name,
            True,
            is_antigravity=_is_antigravity_mode(mode),
            model_key=model_key,
        )


async def record_api_call_error(
    credential_manager,
    credential_name: str,
    status_code: int,
    cooldown_until: Optional[float] = None,
    mode: str = "geminicli",
    model_key: Optional[str] = None,
) -> None:
    if credential_manager and credential_name:
        await credential_manager.record_api_call_result(
            credential_name,
            False,
            status_code,
            cooldown_until=cooldown_until,
            is_antigravity=_is_antigravity_mode(mode),
            model_key=model_key,
        )


async def parse_and_log_cooldown(
    error_text: str,
    mode: str = "geminicli",
    response_headers: Optional[dict] = None,
) -> Optional[float]:
    """
    解析并记录冷却时间

    Args:
        error_text: 错误响应文本（JSON 字符串）
        mode: 模式标识（用于日志）
        response_headers: HTTP 响应头（可选，用于解析 Retry-After）

    Returns:
        Unix 时间戳（秒），如果无法解析则返回 None
    """
    try:
        error_data = json.loads(error_text)
        # ✅ [FIX 2026-01-17] 传入响应头和错误消息以支持完整的 Retry-After 解析
        cooldown_until = parse_quota_reset_timestamp(
            error_data,
            response_headers=response_headers,
            error_message=error_text,
        )
        if cooldown_until:
            log.info(
                f"[{mode.upper()}] 检测到quota冷却时间: "
                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
            )
            return cooldown_until
    except Exception as parse_err:
        log.debug(f"[{mode.upper()}] Failed to parse cooldown time: {parse_err}")
    return None

