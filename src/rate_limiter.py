"""
Rate Limiter - 请求最小间隔防抖模块

[FIX 2026-01-21] 对齐 Antigravity-Manager 的 rate_limiter.rs 实现：
- 确保对同一上游通道的调用至少间隔 N ms
- 主动削峰，避免瞬时并发过高触发限流

参考实现：Antigravity-Manager/src-tauri/src/proxy/common/rate_limiter.rs
"""

import asyncio
import time
from typing import Dict, Optional

from log import log


class RateLimiter:
    """
    请求速率限制器

    确保调用之间至少间隔指定的时间，用于：
    - 防止瞬时并发过高
    - 主动削峰，减少 429 错误
    - 保护上游服务

    Usage:
        limiter = RateLimiter(min_interval_ms=500)
        await limiter.wait()  # 第一次调用立即返回
        await limiter.wait()  # 第二次调用等待至少 500ms
    """

    def __init__(self, min_interval_ms: int = 500):
        """
        初始化速率限制器

        Args:
            min_interval_ms: 最小调用间隔（毫秒），默认 500ms
        """
        self._min_interval = max(0, min_interval_ms) / 1000.0  # 转换为秒
        self._last_call: Optional[float] = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """
        等待直到可以进行下一次调用

        如果距离上次调用未超过 min_interval，则 sleep 剩余时间。
        """
        if self._min_interval <= 0:
            return

        async with self._lock:
            now = time.monotonic()

            if self._last_call is not None:
                elapsed = now - self._last_call
                if elapsed < self._min_interval:
                    sleep_time = self._min_interval - elapsed
                    await asyncio.sleep(sleep_time)

            self._last_call = time.monotonic()

    def reset(self) -> None:
        """重置限制器状态（用于测试或特殊情况）"""
        self._last_call = None


class KeyedRateLimiter:
    """
    按键分组的速率限制器

    为不同的键（如账号、模型）维护独立的速率限制。

    Usage:
        limiter = KeyedRateLimiter(min_interval_ms=500)
        await limiter.wait("account1")  # account1 的第一次调用
        await limiter.wait("account2")  # account2 的第一次调用（不受 account1 影响）
        await limiter.wait("account1")  # account1 的第二次调用，需要等待
    """

    def __init__(self, min_interval_ms: int = 500):
        """
        初始化按键分组的速率限制器

        Args:
            min_interval_ms: 每个键的最小调用间隔（毫秒）
        """
        self._min_interval_ms = min_interval_ms
        self._limiters: Dict[str, RateLimiter] = {}
        self._lock = asyncio.Lock()

    async def wait(self, key: str) -> None:
        """
        等待直到指定键可以进行下一次调用

        Args:
            key: 限制器键（如账号名、模型名）
        """
        async with self._lock:
            if key not in self._limiters:
                self._limiters[key] = RateLimiter(self._min_interval_ms)
            limiter = self._limiters[key]

        await limiter.wait()

    async def cleanup_idle(self, idle_seconds: float = 300) -> int:
        """
        清理空闲的限制器（超过指定时间未使用）

        Args:
            idle_seconds: 空闲时间阈值（秒）

        Returns:
            清理的限制器数量
        """
        now = time.monotonic()
        threshold = now - idle_seconds

        async with self._lock:
            to_remove = []
            for key, limiter in self._limiters.items():
                if limiter._last_call is not None and limiter._last_call < threshold:
                    to_remove.append(key)

            for key in to_remove:
                del self._limiters[key]

            if to_remove:
                log.debug(f"[RATE_LIMITER] Cleaned up {len(to_remove)} idle limiters")

            return len(to_remove)


class AdaptiveRateLimiter:
    """
    自适应速率限制器

    根据错误率动态调整最小间隔：
    - 成功时逐渐减少间隔（加速）
    - 失败时增加间隔（减速）

    Usage:
        limiter = AdaptiveRateLimiter(
            base_interval_ms=500,
            min_interval_ms=100,
            max_interval_ms=5000
        )
        await limiter.wait()
        # 请求成功
        limiter.record_success()
        # 请求失败
        limiter.record_failure()
    """

    def __init__(
        self,
        base_interval_ms: int = 500,
        min_interval_ms: int = 100,
        max_interval_ms: int = 5000,
        acceleration_factor: float = 0.9,
        deceleration_factor: float = 2.0,
    ):
        """
        初始化自适应速率限制器

        Args:
            base_interval_ms: 基础间隔（毫秒）
            min_interval_ms: 最小间隔（毫秒）
            max_interval_ms: 最大间隔（毫秒）
            acceleration_factor: 成功时的加速因子（< 1.0）
            deceleration_factor: 失败时的减速因子（> 1.0）
        """
        self._base_interval = base_interval_ms / 1000.0
        self._min_interval = min_interval_ms / 1000.0
        self._max_interval = max_interval_ms / 1000.0
        self._current_interval = self._base_interval
        self._acceleration = acceleration_factor
        self._deceleration = deceleration_factor
        self._last_call: Optional[float] = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """等待直到可以进行下一次调用"""
        async with self._lock:
            now = time.monotonic()

            if self._last_call is not None:
                elapsed = now - self._last_call
                if elapsed < self._current_interval:
                    sleep_time = self._current_interval - elapsed
                    await asyncio.sleep(sleep_time)

            self._last_call = time.monotonic()

    def record_success(self) -> None:
        """记录成功，加速（减少间隔）"""
        self._current_interval = max(
            self._min_interval,
            self._current_interval * self._acceleration
        )
        log.debug(f"[ADAPTIVE_RATE_LIMITER] Success, interval reduced to {self._current_interval * 1000:.0f}ms")

    def record_failure(self) -> None:
        """记录失败，减速（增加间隔）"""
        self._current_interval = min(
            self._max_interval,
            self._current_interval * self._deceleration
        )
        log.debug(f"[ADAPTIVE_RATE_LIMITER] Failure, interval increased to {self._current_interval * 1000:.0f}ms")

    def reset(self) -> None:
        """重置到基础间隔"""
        self._current_interval = self._base_interval
        self._last_call = None

    @property
    def current_interval_ms(self) -> int:
        """当前间隔（毫秒）"""
        return int(self._current_interval * 1000)


# 全局限制器实例
_global_rate_limiter: Optional[RateLimiter] = None
_keyed_rate_limiter: Optional[KeyedRateLimiter] = None


def get_global_rate_limiter(min_interval_ms: int = 500) -> RateLimiter:
    """获取全局速率限制器"""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter(min_interval_ms)
    return _global_rate_limiter


def get_keyed_rate_limiter(min_interval_ms: int = 500) -> KeyedRateLimiter:
    """获取按键分组的速率限制器"""
    global _keyed_rate_limiter
    if _keyed_rate_limiter is None:
        _keyed_rate_limiter = KeyedRateLimiter(min_interval_ms)
    return _keyed_rate_limiter
