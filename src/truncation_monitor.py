"""
Truncation Monitor - 上下文截断监控模块
用于收集截断统计、缓存命中率、MAX_TOKENS 频率等指标

这是自定义功能模块，原版 gcli2api 不包含此功能
[FIX 2026-01-10] 用于诊断长对话导致的工具调用失败问题
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import deque
from threading import Lock

from log import log


@dataclass
class TruncationEvent:
    """单次截断事件记录"""
    timestamp: float
    model: str
    original_tokens: int
    final_tokens: int
    truncated: bool
    strategy: str  # "smart", "aggressive", "none"
    messages_removed: int
    tool_chars_saved: int
    dynamic_limit: int


@dataclass
class MaxTokensEvent:
    """MAX_TOKENS 事件记录"""
    timestamp: float
    model: str
    prompt_tokens: int
    output_tokens: int
    cached_tokens: int
    actual_processed_tokens: int
    finish_reason: str


@dataclass
class CacheHitEvent:
    """缓存命中事件记录"""
    timestamp: float
    model: str
    prompt_tokens: int
    cached_tokens: int
    hit_ratio: float  # cached_tokens / prompt_tokens


class TruncationMonitor:
    """
    上下文截断监控器

    功能：
    1. 收集截断事件统计
    2. 跟踪 MAX_TOKENS 错误频率
    3. 监控缓存命中率
    4. 定期输出统计摘要
    """

    # 单例实例
    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # 事件队列（保留最近 1000 条）
        self._truncation_events: deque = deque(maxlen=1000)
        self._max_tokens_events: deque = deque(maxlen=1000)
        self._cache_hit_events: deque = deque(maxlen=1000)

        # 汇总统计
        self._total_requests = 0
        self._total_truncations = 0
        self._total_max_tokens = 0
        self._total_cached_tokens = 0
        self._total_prompt_tokens = 0

        # 按模型统计
        self._model_stats: Dict[str, Dict] = {}

        # 上次摘要时间
        self._last_summary_time = time.time()
        self._summary_interval = 300  # 5 分钟输出一次摘要

        # 线程锁
        self._event_lock = Lock()

        self._initialized = True
        log.info("[TRUNCATION MONITOR] 监控器已初始化")

    def record_truncation(
        self,
        model: str,
        original_tokens: int,
        final_tokens: int,
        truncated: bool,
        strategy: str = "smart",
        messages_removed: int = 0,
        tool_chars_saved: int = 0,
        dynamic_limit: int = 0,
    ) -> None:
        """记录截断事件"""
        with self._event_lock:
            event = TruncationEvent(
                timestamp=time.time(),
                model=model,
                original_tokens=original_tokens,
                final_tokens=final_tokens,
                truncated=truncated,
                strategy=strategy,
                messages_removed=messages_removed,
                tool_chars_saved=tool_chars_saved,
                dynamic_limit=dynamic_limit,
            )
            self._truncation_events.append(event)

            self._total_requests += 1
            if truncated:
                self._total_truncations += 1

            # 更新模型统计
            if model not in self._model_stats:
                self._model_stats[model] = {
                    "requests": 0,
                    "truncations": 0,
                    "max_tokens_errors": 0,
                    "total_tokens_saved": 0,
                }
            self._model_stats[model]["requests"] += 1
            if truncated:
                self._model_stats[model]["truncations"] += 1
                self._model_stats[model]["total_tokens_saved"] += (original_tokens - final_tokens)

        # 输出详细日志
        if truncated:
            log.info(
                f"[TRUNCATION MONITOR] 截断事件: model={model}, "
                f"tokens={original_tokens:,}->{final_tokens:,} "
                f"(节省 {original_tokens - final_tokens:,}), "
                f"strategy={strategy}, messages_removed={messages_removed}, "
                f"limit={dynamic_limit:,}"
            )

        # 检查是否需要输出摘要
        self._maybe_output_summary()

    def record_max_tokens(
        self,
        model: str,
        prompt_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        finish_reason: str = "MAX_TOKENS",
    ) -> None:
        """记录 MAX_TOKENS 事件"""
        with self._event_lock:
            actual_processed = prompt_tokens - cached_tokens
            event = MaxTokensEvent(
                timestamp=time.time(),
                model=model,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                actual_processed_tokens=actual_processed,
                finish_reason=finish_reason,
            )
            self._max_tokens_events.append(event)
            self._total_max_tokens += 1

            # 更新模型统计
            if model not in self._model_stats:
                self._model_stats[model] = {
                    "requests": 0,
                    "truncations": 0,
                    "max_tokens_errors": 0,
                    "total_tokens_saved": 0,
                }
            self._model_stats[model]["max_tokens_errors"] += 1

        # 输出警告日志
        log.warning(
            f"[TRUNCATION MONITOR] MAX_TOKENS 事件: model={model}, "
            f"prompt={prompt_tokens:,}, output={output_tokens:,}, "
            f"cached={cached_tokens:,}, actual_processed={actual_processed:,}, "
            f"finish_reason={finish_reason}"
        )

        self._maybe_output_summary()

    def record_cache_hit(
        self,
        model: str,
        prompt_tokens: int,
        cached_tokens: int,
    ) -> None:
        """记录缓存命中事件"""
        if cached_tokens <= 0 or prompt_tokens <= 0:
            return

        with self._event_lock:
            hit_ratio = cached_tokens / prompt_tokens
            event = CacheHitEvent(
                timestamp=time.time(),
                model=model,
                prompt_tokens=prompt_tokens,
                cached_tokens=cached_tokens,
                hit_ratio=hit_ratio,
            )
            self._cache_hit_events.append(event)
            self._total_cached_tokens += cached_tokens
            self._total_prompt_tokens += prompt_tokens

        # 高缓存命中率时输出 debug 日志
        if hit_ratio > 0.5:
            log.debug(
                f"[TRUNCATION MONITOR] 高缓存命中: model={model}, "
                f"hit_ratio={hit_ratio:.1%}, "
                f"cached={cached_tokens:,}/{prompt_tokens:,}"
            )

    def _maybe_output_summary(self) -> None:
        """检查是否需要输出统计摘要"""
        now = time.time()
        if now - self._last_summary_time < self._summary_interval:
            return

        self._last_summary_time = now
        self._output_summary()

    def _output_summary(self) -> None:
        """输出统计摘要"""
        with self._event_lock:
            if self._total_requests == 0:
                return

            truncation_rate = self._total_truncations / self._total_requests * 100
            max_tokens_rate = self._total_max_tokens / self._total_requests * 100
            cache_hit_rate = (
                self._total_cached_tokens / self._total_prompt_tokens * 100
                if self._total_prompt_tokens > 0 else 0
            )

            log.info(
                f"[TRUNCATION MONITOR] === 统计摘要 === \n"
                f"  总请求数: {self._total_requests:,}\n"
                f"  截断次数: {self._total_truncations:,} ({truncation_rate:.1f}%)\n"
                f"  MAX_TOKENS 次数: {self._total_max_tokens:,} ({max_tokens_rate:.1f}%)\n"
                f"  缓存命中率: {cache_hit_rate:.1f}%\n"
                f"  模型统计: {self._format_model_stats()}"
            )

    def _format_model_stats(self) -> str:
        """格式化模型统计"""
        if not self._model_stats:
            return "无数据"

        parts = []
        for model, stats in self._model_stats.items():
            truncation_pct = (
                stats["truncations"] / stats["requests"] * 100
                if stats["requests"] > 0 else 0
            )
            parts.append(
                f"{model}: {stats['requests']}req, "
                f"{stats['truncations']}trunc({truncation_pct:.0f}%), "
                f"{stats['max_tokens_errors']}maxtkn"
            )
        return " | ".join(parts)

    def get_stats(self) -> Dict:
        """获取当前统计数据"""
        with self._event_lock:
            return {
                "total_requests": self._total_requests,
                "total_truncations": self._total_truncations,
                "total_max_tokens": self._total_max_tokens,
                "truncation_rate": (
                    self._total_truncations / self._total_requests * 100
                    if self._total_requests > 0 else 0
                ),
                "max_tokens_rate": (
                    self._total_max_tokens / self._total_requests * 100
                    if self._total_requests > 0 else 0
                ),
                "cache_hit_rate": (
                    self._total_cached_tokens / self._total_prompt_tokens * 100
                    if self._total_prompt_tokens > 0 else 0
                ),
                "model_stats": dict(self._model_stats),
                "recent_truncations": list(self._truncation_events)[-10:],
                "recent_max_tokens": list(self._max_tokens_events)[-10:],
            }

    def force_summary(self) -> None:
        """强制输出统计摘要"""
        self._output_summary()


# 全局监控器实例
_monitor: Optional[TruncationMonitor] = None


def get_monitor() -> TruncationMonitor:
    """获取全局监控器实例"""
    global _monitor
    if _monitor is None:
        _monitor = TruncationMonitor()
    return _monitor


# 便捷函数
def record_truncation(**kwargs) -> None:
    """记录截断事件"""
    get_monitor().record_truncation(**kwargs)


def record_max_tokens(**kwargs) -> None:
    """记录 MAX_TOKENS 事件"""
    get_monitor().record_max_tokens(**kwargs)


def record_cache_hit(**kwargs) -> None:
    """记录缓存命中事件"""
    get_monitor().record_cache_hit(**kwargs)


def get_truncation_stats() -> Dict:
    """获取截断统计"""
    return get_monitor().get_stats()


def force_summary() -> None:
    """强制输出统计摘要"""
    get_monitor().force_summary()
