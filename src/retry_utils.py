"""
Retry Utilities - 429 重试策略工具模块

[FIX 2026-01-21] 对齐 Antigravity-Manager 的 retry.rs 实现：
- parse_duration_ms(): 解析 Duration 字符串 (e.g., "1.5s", "200ms", "1h16m0.667s")
- parse_retry_delay(): 从 429 错误 JSON 中提取精确的重试延迟

参考实现：Antigravity-Manager/src-tauri/src/proxy/upstream/retry.rs
"""

import json
import re
from typing import Optional

# Duration 解析正则表达式
# 匹配格式: 数字(可带小数) + 单位(ms/s/m/h)
DURATION_PATTERN = re.compile(r'([\d.]+)\s*(ms|s|m|h)')


def parse_duration_ms(duration_str: str) -> Optional[int]:
    """
    解析 Duration 字符串，返回毫秒数

    支持的格式:
    - "1.5s" -> 1500
    - "200ms" -> 200
    - "1h16m0.667s" -> 4560667
    - "30m" -> 1800000

    Args:
        duration_str: Duration 字符串

    Returns:
        毫秒数，解析失败返回 None

    Examples:
        >>> parse_duration_ms("1.5s")
        1500
        >>> parse_duration_ms("200ms")
        200
        >>> parse_duration_ms("1h16m0.667s")
        4560667
        >>> parse_duration_ms("invalid")
        None
    """
    if not duration_str or not isinstance(duration_str, str):
        return None

    total_ms: float = 0.0
    matched = False

    for match in DURATION_PATTERN.finditer(duration_str):
        matched = True
        try:
            value = float(match.group(1))
        except ValueError:
            continue

        unit = match.group(2)

        if unit == "ms":
            total_ms += value
        elif unit == "s":
            total_ms += value * 1000.0
        elif unit == "m":
            total_ms += value * 60.0 * 1000.0
        elif unit == "h":
            total_ms += value * 60.0 * 60.0 * 1000.0

    if not matched:
        return None

    return round(total_ms)


def parse_retry_delay(error_text: str) -> Optional[int]:
    """
    从 429 错误响应中提取精确的重试延迟（毫秒）

    解析优先级:
    1. RetryInfo.retryDelay - Google API 标准重试信息
    2. metadata.quotaResetDelay - 配额重置延迟

    Args:
        error_text: 错误响应文本（JSON 格式）

    Returns:
        重试延迟（毫秒），解析失败返回 None

    Example error JSON:
        {
            "error": {
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "1.203608125s"
                }]
            }
        }
    """
    if not error_text or not isinstance(error_text, str):
        return None

    try:
        data = json.loads(error_text)
    except json.JSONDecodeError:
        return None

    # 获取 error.details 数组
    error_obj = data.get("error")
    if not isinstance(error_obj, dict):
        return None

    details = error_obj.get("details")
    if not isinstance(details, list):
        return None

    # 方式1: 优先解析 RetryInfo.retryDelay
    for detail in details:
        if not isinstance(detail, dict):
            continue

        type_str = detail.get("@type")
        if isinstance(type_str, str) and "RetryInfo" in type_str:
            retry_delay = detail.get("retryDelay")
            if isinstance(retry_delay, str):
                delay_ms = parse_duration_ms(retry_delay)
                if delay_ms is not None:
                    return delay_ms

    # 方式2: 解析 metadata.quotaResetDelay
    for detail in details:
        if not isinstance(detail, dict):
            continue

        metadata = detail.get("metadata")
        if isinstance(metadata, dict):
            quota_delay = metadata.get("quotaResetDelay")
            if isinstance(quota_delay, str):
                delay_ms = parse_duration_ms(quota_delay)
                if delay_ms is not None:
                    return delay_ms

    return None


def parse_retry_delay_seconds(error_text: str) -> Optional[float]:
    """
    从 429 错误响应中提取精确的重试延迟（秒）

    这是 parse_retry_delay() 的便捷包装，返回秒而不是毫秒。

    Args:
        error_text: 错误响应文本（JSON 格式）

    Returns:
        重试延迟（秒），解析失败返回 None
    """
    delay_ms = parse_retry_delay(error_text)
    if delay_ms is None:
        return None
    return delay_ms / 1000.0
