"""
Rate Limit Registry - 统一的限流状态池模块

[FIX 2026-01-21] 对齐 Antigravity-Manager 的限流状态管理：
- 统一记录账号/模型级别的限流状态
- 提供 mark_rate_limited() 统一入口
- 支持 Web 控制台查询限流状态

参考实现：Antigravity-Manager/src-tauri/src/proxy/handlers/openai.rs
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from log import log


@dataclass
class RateLimitState:
    """
    单个凭证/模型的限流状态

    Attributes:
        last_status: 最后一次错误的 HTTP 状态码
        last_error: 最后一次错误的简短描述
        cooldown_until: 冷却截止时间戳（None 表示无冷却）
        last_retry_after: 最后一次 Retry-After 值（秒）
        consecutive_failures: 连续失败次数
        last_updated: 最后更新时间戳
        reason: 限流原因分类
    """
    last_status: int = 0
    last_error: str = ""
    cooldown_until: Optional[float] = None
    last_retry_after: Optional[float] = None
    consecutive_failures: int = 0
    last_updated: float = field(default_factory=time.time)
    reason: Literal["rate_limit", "quota_exhausted", "server_error", "auth_error", "unknown"] = "unknown"

    def is_in_cooldown(self) -> bool:
        """检查是否仍在冷却中"""
        if self.cooldown_until is None:
            return False
        return time.time() < self.cooldown_until

    def remaining_cooldown_seconds(self) -> float:
        """返回剩余冷却时间（秒），已过期返回 0"""
        if self.cooldown_until is None:
            return 0.0
        remaining = self.cooldown_until - time.time()
        return max(0.0, remaining)

    def to_dict(self) -> Dict:
        """转换为字典格式（用于 API 响应）"""
        return {
            "last_status": self.last_status,
            "last_error": self.last_error,
            "cooldown_until": self.cooldown_until,
            "remaining_cooldown_seconds": self.remaining_cooldown_seconds(),
            "is_in_cooldown": self.is_in_cooldown(),
            "consecutive_failures": self.consecutive_failures,
            "last_updated": self.last_updated,
            "reason": self.reason,
        }


class RateLimitRegistry:
    """
    限流状态注册表

    统一管理所有凭证/模型的限流状态，提供：
    - mark_rate_limited(): 标记限流状态
    - clear_rate_limit(): 清除限流状态（请求成功时）
    - get_state(): 获取单个状态
    - get_all_states(): 获取所有状态（用于 Web 控制台）
    - get_cooldown_entries(): 获取当前在冷却中的条目
    """

    def __init__(self):
        # 键格式: (credential_name, model_name)
        # model_name 可以是 "*" 表示凭证级别的限流
        self._registry: Dict[Tuple[str, str], RateLimitState] = {}
        self._lock = asyncio.Lock()

    async def mark_rate_limited(
        self,
        credential_name: str,
        model_name: str,
        *,
        status_code: int,
        error_text: str = "",
        retry_after: Optional[float] = None,
        cooldown_until: Optional[float] = None,
        reason: Literal["rate_limit", "quota_exhausted", "server_error", "auth_error", "unknown"] = "unknown",
    ) -> None:
        """
        标记凭证/模型为限流状态

        Args:
            credential_name: 凭证名称
            model_name: 模型名称（"*" 表示凭证级别）
            status_code: HTTP 状态码
            error_text: 错误描述（会被截断到 200 字符）
            retry_after: Retry-After 值（秒）
            cooldown_until: 冷却截止时间戳
            reason: 限流原因分类
        """
        key = (credential_name, model_name)
        now = time.time()

        # 如果没有明确的 cooldown_until，根据 retry_after 计算
        if cooldown_until is None and retry_after is not None and retry_after > 0:
            cooldown_until = now + retry_after

        async with self._lock:
            existing = self._registry.get(key)

            if existing:
                # 更新现有状态
                existing.last_status = status_code
                existing.last_error = error_text[:200] if error_text else ""
                existing.cooldown_until = cooldown_until
                existing.last_retry_after = retry_after
                existing.consecutive_failures += 1
                existing.last_updated = now
                existing.reason = reason
            else:
                # 创建新状态
                self._registry[key] = RateLimitState(
                    last_status=status_code,
                    last_error=error_text[:200] if error_text else "",
                    cooldown_until=cooldown_until,
                    last_retry_after=retry_after,
                    consecutive_failures=1,
                    last_updated=now,
                    reason=reason,
                )

        log.info(
            f"[RATE_LIMIT_REGISTRY] Marked rate limited: "
            f"cred={credential_name}, model={model_name}, "
            f"status={status_code}, reason={reason}, "
            f"cooldown={cooldown_until - now if cooldown_until else 0:.1f}s"
        )

    async def clear_rate_limit(
        self,
        credential_name: str,
        model_name: str,
    ) -> None:
        """
        清除凭证/模型的限流状态（请求成功时调用）

        Args:
            credential_name: 凭证名称
            model_name: 模型名称
        """
        key = (credential_name, model_name)

        async with self._lock:
            if key in self._registry:
                # 重置状态但保留记录
                state = self._registry[key]
                state.cooldown_until = None
                state.consecutive_failures = 0
                state.last_updated = time.time()
                log.debug(
                    f"[RATE_LIMIT_REGISTRY] Cleared rate limit: "
                    f"cred={credential_name}, model={model_name}"
                )

    async def get_state(
        self,
        credential_name: str,
        model_name: str,
    ) -> Optional[RateLimitState]:
        """
        获取凭证/模型的限流状态

        Args:
            credential_name: 凭证名称
            model_name: 模型名称

        Returns:
            限流状态，不存在返回 None
        """
        key = (credential_name, model_name)
        async with self._lock:
            return self._registry.get(key)

    async def is_rate_limited(
        self,
        credential_name: str,
        model_name: str,
    ) -> bool:
        """
        检查凭证/模型是否在限流中

        Args:
            credential_name: 凭证名称
            model_name: 模型名称

        Returns:
            True 表示在限流中
        """
        state = await self.get_state(credential_name, model_name)
        if state is None:
            return False
        return state.is_in_cooldown()

    async def get_all_states(self) -> Dict[str, Dict]:
        """
        获取所有限流状态（用于 Web 控制台）

        Returns:
            格式: {"credential:model": {...state...}}
        """
        async with self._lock:
            result = {}
            for (cred, model), state in self._registry.items():
                key = f"{cred}:{model}"
                result[key] = state.to_dict()
            return result

    async def get_cooldown_entries(self) -> List[Dict]:
        """
        获取当前在冷却中的条目

        Returns:
            当前在冷却中的条目列表
        """
        async with self._lock:
            result = []
            for (cred, model), state in self._registry.items():
                if state.is_in_cooldown():
                    entry = state.to_dict()
                    entry["credential"] = cred
                    entry["model"] = model
                    result.append(entry)
            return result

    async def cleanup_expired(self) -> int:
        """
        清理过期的限流记录（冷却已结束且超过 1 小时未更新）

        Returns:
            清理的记录数
        """
        now = time.time()
        one_hour_ago = now - 3600

        async with self._lock:
            to_remove = []
            for key, state in self._registry.items():
                if not state.is_in_cooldown() and state.last_updated < one_hour_ago:
                    to_remove.append(key)

            for key in to_remove:
                del self._registry[key]

            if to_remove:
                log.debug(f"[RATE_LIMIT_REGISTRY] Cleaned up {len(to_remove)} expired entries")

            return len(to_remove)

    async def clear_rate_limit_for_credential(self, credential_name: str) -> int:
        """
        清除指定凭证的所有限流状态

        [FIX 2026-01-22] 用于凭证检验/projectID切换后，完全重置限流状态。
        解决偶发限流问题：检验功能切换projectID后，内部限流状态未清除导致的问题。

        Args:
            credential_name: 凭证名称

        Returns:
            清除的记录数
        """
        async with self._lock:
            to_remove = []
            for key in self._registry.keys():
                cred, _model = key
                if cred == credential_name:
                    to_remove.append(key)

            for key in to_remove:
                del self._registry[key]

            if to_remove:
                log.info(
                    f"[RATE_LIMIT_REGISTRY] Cleared all rate limits for credential: "
                    f"{credential_name} (removed {len(to_remove)} entries)"
                )

            return len(to_remove)

    async def get_stats(self) -> Dict:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        async with self._lock:
            total = len(self._registry)
            in_cooldown = sum(1 for s in self._registry.values() if s.is_in_cooldown())

            by_reason = {}
            for state in self._registry.values():
                by_reason[state.reason] = by_reason.get(state.reason, 0) + 1

            return {
                "total_entries": total,
                "in_cooldown": in_cooldown,
                "by_reason": by_reason,
            }


# 全局单例
_rate_limit_registry: Optional[RateLimitRegistry] = None


def get_rate_limit_registry() -> RateLimitRegistry:
    """获取全局限流状态注册表实例"""
    global _rate_limit_registry
    if _rate_limit_registry is None:
        _rate_limit_registry = RateLimitRegistry()
    return _rate_limit_registry


# 便捷函数
async def mark_rate_limited(
    credential_name: str,
    model_name: str,
    *,
    status_code: int,
    error_text: str = "",
    retry_after: Optional[float] = None,
    cooldown_until: Optional[float] = None,
    reason: Literal["rate_limit", "quota_exhausted", "server_error", "auth_error", "unknown"] = "unknown",
) -> None:
    """
    标记凭证/模型为限流状态（便捷函数）

    这是 RateLimitRegistry.mark_rate_limited() 的便捷包装。
    """
    registry = get_rate_limit_registry()
    await registry.mark_rate_limited(
        credential_name,
        model_name,
        status_code=status_code,
        error_text=error_text,
        retry_after=retry_after,
        cooldown_until=cooldown_until,
        reason=reason,
    )


async def clear_rate_limit(credential_name: str, model_name: str) -> None:
    """
    清除凭证/模型的限流状态（便捷函数）

    这是 RateLimitRegistry.clear_rate_limit() 的便捷包装。
    """
    registry = get_rate_limit_registry()
    await registry.clear_rate_limit(credential_name, model_name)


async def is_rate_limited(credential_name: str, model_name: str) -> bool:
    """
    检查凭证/模型是否在限流中（便捷函数）

    这是 RateLimitRegistry.is_rate_limited() 的便捷包装。
    """
    registry = get_rate_limit_registry()
    return await registry.is_rate_limited(credential_name, model_name)


async def clear_rate_limit_for_credential(credential_name: str) -> int:
    """
    清除指定凭证的所有限流状态（便捷函数）

    [FIX 2026-01-22] 用于凭证检验/projectID切换后，完全重置限流状态。
    解决偶发限流问题：检验功能切换projectID后，内部限流状态未清除导致的问题。

    Args:
        credential_name: 凭证名称

    Returns:
        清除的记录数
    """
    registry = get_rate_limit_registry()
    return await registry.clear_rate_limit_for_credential(credential_name)
