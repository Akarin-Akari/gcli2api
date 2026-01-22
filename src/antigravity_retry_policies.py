"""
Retry Policies - 重试策略枚举与决策模块

[FIX 2026-01-21] 对齐 Antigravity-Manager 的重试策略：
- 统一枚举所有重试策略 (NoRetry, FixedDelay, LinearBackoff, ExponentialBackoff)
- 集中管理状态码 -> 策略的映射
- 提供 determine_retry_strategy() 统一决策函数

参考实现：Antigravity-Manager/src-tauri/src/proxy/handlers/claude.rs
"""

import random
from dataclasses import dataclass
from typing import Literal, Optional

from log import log
from .retry_utils import parse_retry_delay_seconds


@dataclass
class RetryStrategy:
    """
    重试策略数据结构

    Attributes:
        kind: 策略类型
            - "none": 不重试
            - "fixed": 固定延迟
            - "linear": 线性退避 (base * attempt)
            - "exponential": 指数退避 (base * 2^attempt)
        base_ms: 基础延迟（毫秒）
        max_ms: 最大延迟（毫秒），0 表示无限制
        jitter_ratio: 抖动比例 (0.0 - 1.0)，默认 0.2
    """
    kind: Literal["none", "fixed", "linear", "exponential"]
    base_ms: int = 0
    max_ms: int = 0
    jitter_ratio: float = 0.2

    def compute_delay(self, attempt: int, override_delay_ms: Optional[int] = None) -> float:
        """
        计算第 attempt 次重试的延迟（秒）

        Args:
            attempt: 当前重试次数（从 0 开始）
            override_delay_ms: 覆盖延迟（例如来自 Retry-After），如果提供则优先使用

        Returns:
            延迟时间（秒）
        """
        if self.kind == "none":
            return 0.0

        # 如果有覆盖延迟（如 Retry-After），优先使用
        if override_delay_ms is not None and override_delay_ms > 0:
            delay_ms = float(override_delay_ms)
        else:
            # 根据策略计算基础延迟
            if self.kind == "fixed":
                delay_ms = float(self.base_ms)
            elif self.kind == "linear":
                delay_ms = float(self.base_ms * (attempt + 1))
            elif self.kind == "exponential":
                delay_ms = float(self.base_ms * (2 ** attempt))
            else:
                delay_ms = 0.0

        # 应用最大延迟限制
        if self.max_ms > 0:
            delay_ms = min(delay_ms, float(self.max_ms))

        # 应用抖动
        if self.jitter_ratio > 0:
            jitter = random.uniform(1.0 - self.jitter_ratio, 1.0 + self.jitter_ratio)
            delay_ms *= jitter

        return max(0.0, delay_ms / 1000.0)


# 预定义策略
NO_RETRY = RetryStrategy(kind="none")
FIXED_DELAY_1S = RetryStrategy(kind="fixed", base_ms=1000)
EXPONENTIAL_BACKOFF_DEFAULT = RetryStrategy(
    kind="exponential",
    base_ms=1000,   # 1s
    max_ms=1800000  # 30min
)
LINEAR_BACKOFF_SHORT = RetryStrategy(
    kind="linear",
    base_ms=500,
    max_ms=5000
)


def determine_retry_strategy(
    status_code: int,
    error_text: str = "",
    retry_enabled: bool = True,
) -> RetryStrategy:
    """
    根据状态码和错误内容决定重试策略

    Args:
        status_code: HTTP 状态码
        error_text: 错误响应文本
        retry_enabled: 是否启用重试（全局开关）

    Returns:
        重试策略
    """
    if not retry_enabled:
        return NO_RETRY

    # 429 Too Many Requests
    if status_code == 429:
        # 检查是否是额度耗尽 (MODEL_CAPACITY_EXHAUSTED)
        # 这种情况下通常不建议立即重试，而是应该降级
        if "MODEL_CAPACITY_EXHAUSTED" in error_text:
            # 但为了保持兼容性，我们返回一个长延迟的指数退避
            # 让上层逻辑决定是否降级
            return RetryStrategy(
                kind="exponential",
                base_ms=5000,    # 5s 起步
                max_ms=3600000,  # 1h 上限
            )

        # 普通限流：指数退避
        return RetryStrategy(
            kind="exponential",
            base_ms=1000,    # 1s 起步
            max_ms=1800000,  # 30min 上限
        )

    # 5xx Server Errors
    # 500: Internal Server Error - 临时故障，指数退避
    # 502: Bad Gateway - 临时故障，指数退避
    # 503: Service Unavailable - 过载保护，指数退避
    # 504: Gateway Timeout - 超时，指数退避
    # 529: Site is overloaded - 过载，指数退避
    if status_code in (500, 502, 503, 504, 529):
        return RetryStrategy(
            kind="exponential",
            base_ms=1000,    # 1s 起步
            max_ms=60000,    # 60s 上限（服务端错误不宜重试太久）
        )

    # 400 Bad Request - 客户端错误，不重试
    if status_code == 400:
        return NO_RETRY

    # 401 Unauthorized / 403 Forbidden - 认证错误，不重试（需要换号）
    if status_code in (401, 403):
        return NO_RETRY

    # 其他错误默认不重试
    return NO_RETRY


def get_retry_delay_from_error(
    status_code: int,
    error_text: str,
    attempt: int,
    retry_enabled: bool = True,
) -> float:
    """
    便捷函数：直接计算重试延迟

    Args:
        status_code: HTTP 状态码
        error_text: 错误响应文本
        attempt: 当前重试次数
        retry_enabled: 是否启用重试

    Returns:
        延迟时间（秒）
    """
    # 1. 尝试解析 Retry-After 或 error details
    explicit_delay_ms = None

    # 尝试解析 Retry-After (通常在 headers 中，这里假设 error_text 可能包含相关信息或由调用者处理)
    # 这里主要解析 error_text 中的结构化延迟
    from .retry_utils import parse_retry_delay
    explicit_delay_ms = parse_retry_delay(error_text)

    # 2. 决定策略
    strategy = determine_retry_strategy(status_code, error_text, retry_enabled)

    # 3. 计算延迟
    return strategy.compute_delay(attempt, explicit_delay_ms)
