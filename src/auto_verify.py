"""
Auto Verify Module - 自动校验凭证模块

[FIX 2026-01-22] 解决偶发限流问题：
当检测到 429 限流且所有凭证轮换用尽后，自动触发 projectID 刷新。

触发条件（严格）：
1. 429 错误导致所有凭证轮换用尽（全部凭证都被限流）
2. 上述情况连续发生 5 次
3. 20 分钟冷却期内只允许校验一次

失败处理：
- 自动校验失败后，返回信号让路由降级到其他后端
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from log import log


@dataclass
class AutoVerifyState:
    """单个凭证的自动校验状态"""
    all_creds_exhausted_count: int = 0      # "所有凭证用尽"的连续次数
    last_verify_time: float = 0.0           # 上次自动校验时间
    last_verify_success: bool = False       # 上次校验是否成功
    total_verify_count: int = 0             # 总校验次数
    last_exhausted_time: float = 0.0        # 上次"所有凭证用尽"时间
    should_fallback: bool = False           # 是否应该降级到其他后端


class AutoVerifyManager:
    """
    自动校验管理器

    触发条件（严格）：
    - 429 错误导致所有凭证轮换用尽
    - 上述情况连续发生 N 次（默认 5 次）
    - 冷却期内不重复触发（默认 20 分钟）

    失败处理：
    - 自动校验失败后，标记 should_fallback = True
    - 上层可根据此标记降级到其他后端
    """

    def __init__(
        self,
        cooldown_seconds: float = 1200.0,       # 凭证级冷却：20 分钟
        exhausted_threshold: int = 5,            # "所有凭证用尽"阈值：5 次
        global_hourly_limit: int = 6,            # 全局每小时限制：6 次（20分钟1次 * 3 = 1小时3次，留余量）
        enabled: bool = True,                    # 是否启用
        exhausted_count_reset_seconds: float = 3600.0,  # 1小时内无"用尽"事件则重置计数
    ):
        self.cooldown_seconds = cooldown_seconds
        self.exhausted_threshold = exhausted_threshold
        self.global_hourly_limit = global_hourly_limit
        self.enabled = enabled
        self.exhausted_count_reset_seconds = exhausted_count_reset_seconds

        # 凭证状态: {credential_name: AutoVerifyState}
        self._states: Dict[str, AutoVerifyState] = {}

        # 全局速率限制: 记录过去一小时的校验时间戳
        self._global_verify_timestamps: list[float] = []

        # 正在校验中的凭证（防止并发重复触发）
        self._verifying: Set[str] = set()

        self._lock = asyncio.Lock()

    def _get_state(self, credential_name: str) -> AutoVerifyState:
        """获取或创建凭证状态"""
        if credential_name not in self._states:
            self._states[credential_name] = AutoVerifyState()
        return self._states[credential_name]

    def _cleanup_global_timestamps(self) -> None:
        """清理超过 1 小时的全局时间戳"""
        one_hour_ago = time.time() - 3600
        self._global_verify_timestamps = [
            ts for ts in self._global_verify_timestamps if ts > one_hour_ago
        ]

    def _can_auto_verify(self, credential_name: str) -> tuple[bool, str]:
        """
        检查是否可以对该凭证进行自动校验

        Returns:
            (can_verify, reason)
        """
        if not self.enabled:
            return False, "auto_verify_disabled"

        state = self._get_state(credential_name)
        now = time.time()

        # 1. 检查是否正在校验中
        if credential_name in self._verifying:
            return False, "already_verifying"

        # 2. 检查凭证级冷却（20 分钟）
        if state.last_verify_time > 0:
            elapsed = now - state.last_verify_time
            if elapsed < self.cooldown_seconds:
                remaining = self.cooldown_seconds - elapsed
                return False, f"cooldown_active ({remaining:.0f}s remaining)"

        # 3. 检查"所有凭证用尽"次数阈值（5 次）
        if state.all_creds_exhausted_count < self.exhausted_threshold:
            return False, f"threshold_not_met ({state.all_creds_exhausted_count}/{self.exhausted_threshold})"

        # 4. 检查全局速率限制
        self._cleanup_global_timestamps()
        if len(self._global_verify_timestamps) >= self.global_hourly_limit:
            return False, f"global_limit_reached ({len(self._global_verify_timestamps)}/{self.global_hourly_limit}/hour)"

        return True, "ok"

    async def record_all_credentials_exhausted(
        self,
        credential_name: str,
        model_name: str,
        error_text: str = "",
        is_antigravity: bool = True,
    ) -> tuple[Optional[bool], bool]:
        """
        记录"所有凭证轮换用尽仍然 429"事件

        只有在所有凭证都尝试过且仍然 429 时调用此函数。

        Args:
            credential_name: 最后一个尝试的凭证名称
            model_name: 模型名称
            error_text: 错误文本
            is_antigravity: 是否为 Antigravity 凭证

        Returns:
            (verify_result, should_fallback)
            - verify_result: None=未触发, True=校验成功, False=校验失败
            - should_fallback: 是否应该降级到其他后端
        """
        async with self._lock:
            state = self._get_state(credential_name)
            now = time.time()

            # 检查是否需要重置计数（超过 1 小时未发生"用尽"事件）
            if state.last_exhausted_time > 0:
                elapsed = now - state.last_exhausted_time
                if elapsed > self.exhausted_count_reset_seconds:
                    log.debug(
                        f"[AUTO_VERIFY] Resetting exhausted count (no events for {elapsed:.0f}s): "
                        f"cred={credential_name}"
                    )
                    state.all_creds_exhausted_count = 0
                    state.should_fallback = False

            # 更新"所有凭证用尽"计数
            state.all_creds_exhausted_count += 1
            state.last_exhausted_time = now

            log.warning(
                f"[AUTO_VERIFY] All credentials exhausted: cred={credential_name}, model={model_name}, "
                f"exhausted_count={state.all_creds_exhausted_count}/{self.exhausted_threshold}"
            )

            # 如果已经标记为需要降级，直接返回
            if state.should_fallback:
                log.info(f"[AUTO_VERIFY] Already marked for fallback: cred={credential_name}")
                return None, True

            # 检查是否可以自动校验
            can_verify, reason = self._can_auto_verify(credential_name)
            if not can_verify:
                log.debug(f"[AUTO_VERIFY] Skip auto verify: {reason}")
                return None, False

            # 标记为正在校验
            self._verifying.add(credential_name)

        # 在锁外执行校验（避免阻塞其他请求）
        try:
            log.info(
                f"[AUTO_VERIFY] Triggering auto verify: cred={credential_name}, "
                f"exhausted_count={state.all_creds_exhausted_count}"
            )
            success = await self._do_verify(credential_name, is_antigravity)

            # 更新状态
            async with self._lock:
                state = self._get_state(credential_name)
                state.last_verify_time = time.time()
                state.last_verify_success = success
                state.total_verify_count += 1

                if success:
                    # 校验成功，重置计数
                    state.all_creds_exhausted_count = 0
                    state.should_fallback = False
                    self._global_verify_timestamps.append(time.time())
                    log.info(f"[AUTO_VERIFY] Auto verify SUCCESS: cred={credential_name}")
                    return True, False
                else:
                    # 校验失败，标记为需要降级
                    state.should_fallback = True
                    log.warning(
                        f"[AUTO_VERIFY] Auto verify FAILED, marking for fallback: cred={credential_name}"
                    )
                    return False, True

        except Exception as e:
            log.error(f"[AUTO_VERIFY] Auto verify ERROR: cred={credential_name}, error={e}")
            async with self._lock:
                state = self._get_state(credential_name)
                state.should_fallback = True
            return False, True

        finally:
            async with self._lock:
                self._verifying.discard(credential_name)

    async def _do_verify(self, credential_name: str, is_antigravity: bool) -> bool:
        """
        执行实际的校验操作（调用 web_routes 中的函数）

        Args:
            credential_name: 凭证名称
            is_antigravity: 是否为 Antigravity 凭证

        Returns:
            是否成功
        """
        try:
            # 导入并调用 web_routes 中的校验函数
            from src.web_routes import verify_credential_project_common

            response = await verify_credential_project_common(
                filename=credential_name,
                is_antigravity=is_antigravity,
            )

            # 检查响应状态
            if hasattr(response, 'status_code'):
                return response.status_code == 200

            # JSONResponse 的 body 是 bytes
            if hasattr(response, 'body'):
                import json
                body = json.loads(response.body)
                return body.get("success", False)

            return False

        except Exception as e:
            log.error(f"[AUTO_VERIFY] _do_verify error: {e}")
            return False

    def should_fallback_to_other_backend(self, credential_name: str) -> bool:
        """
        检查是否应该降级到其他后端

        Args:
            credential_name: 凭证名称

        Returns:
            是否应该降级
        """
        if credential_name not in self._states:
            return False
        return self._states[credential_name].should_fallback

    def clear_fallback_flag(self, credential_name: str) -> None:
        """清除降级标记（成功请求后调用）"""
        if credential_name in self._states:
            self._states[credential_name].should_fallback = False
            self._states[credential_name].all_creds_exhausted_count = 0

    def record_success(self, credential_name: str) -> None:
        """
        记录请求成功，重置计数

        Args:
            credential_name: 凭证名称
        """
        if credential_name in self._states:
            state = self._states[credential_name]
            state.all_creds_exhausted_count = 0
            state.should_fallback = False

    async def get_stats(self) -> Dict:
        """获取统计信息"""
        async with self._lock:
            self._cleanup_global_timestamps()

            return {
                "enabled": self.enabled,
                "cooldown_seconds": self.cooldown_seconds,
                "exhausted_threshold": self.exhausted_threshold,
                "global_hourly_limit": self.global_hourly_limit,
                "global_verify_count_last_hour": len(self._global_verify_timestamps),
                "tracked_credentials": len(self._states),
                "currently_verifying": list(self._verifying),
                "states": {
                    cred: {
                        "all_creds_exhausted_count": state.all_creds_exhausted_count,
                        "last_verify_time": state.last_verify_time,
                        "last_verify_success": state.last_verify_success,
                        "total_verify_count": state.total_verify_count,
                        "should_fallback": state.should_fallback,
                    }
                    for cred, state in self._states.items()
                },
            }

    def update_config(
        self,
        enabled: Optional[bool] = None,
        cooldown_seconds: Optional[float] = None,
        exhausted_threshold: Optional[int] = None,
        global_hourly_limit: Optional[int] = None,
    ) -> None:
        """动态更新配置"""
        if enabled is not None:
            self.enabled = enabled
        if cooldown_seconds is not None:
            self.cooldown_seconds = cooldown_seconds
        if exhausted_threshold is not None:
            self.exhausted_threshold = exhausted_threshold
        if global_hourly_limit is not None:
            self.global_hourly_limit = global_hourly_limit

        log.info(
            f"[AUTO_VERIFY] Config updated: enabled={self.enabled}, "
            f"cooldown={self.cooldown_seconds}s, threshold={self.exhausted_threshold}, "
            f"hourly_limit={self.global_hourly_limit}"
        )


# ============ 全局单例 ============

_auto_verify_manager: Optional[AutoVerifyManager] = None


def get_auto_verify_manager() -> AutoVerifyManager:
    """获取全局自动校验管理器实例"""
    global _auto_verify_manager
    if _auto_verify_manager is None:
        # 从环境变量读取配置
        enabled = os.getenv("AUTO_VERIFY_ENABLED", "true").lower() in ("true", "1", "yes")
        cooldown = float(os.getenv("AUTO_VERIFY_COOLDOWN_SECONDS", "1200"))  # 默认 20 分钟
        threshold = int(os.getenv("AUTO_VERIFY_EXHAUSTED_THRESHOLD", "5"))   # 默认 5 次
        hourly_limit = int(os.getenv("AUTO_VERIFY_GLOBAL_HOURLY_LIMIT", "6"))

        _auto_verify_manager = AutoVerifyManager(
            enabled=enabled,
            cooldown_seconds=cooldown,
            exhausted_threshold=threshold,
            global_hourly_limit=hourly_limit,
        )

        log.info(
            f"[AUTO_VERIFY] Initialized: enabled={enabled}, "
            f"cooldown={cooldown}s, exhausted_threshold={threshold}, hourly_limit={hourly_limit}"
        )

    return _auto_verify_manager


# ============ 便捷函数 ============

async def record_all_credentials_exhausted(
    credential_name: str,
    model_name: str,
    error_text: str = "",
    is_antigravity: bool = True,
) -> tuple[Optional[bool], bool]:
    """
    记录"所有凭证轮换用尽仍然 429"事件（便捷函数）

    只有在所有凭证都尝试过且仍然 429 时调用此函数。

    Returns:
        (verify_result, should_fallback)
        - verify_result: None=未触发, True=校验成功, False=校验失败
        - should_fallback: 是否应该降级到其他后端
    """
    manager = get_auto_verify_manager()
    return await manager.record_all_credentials_exhausted(
        credential_name, model_name, error_text, is_antigravity
    )


def should_fallback_to_other_backend(credential_name: str) -> bool:
    """检查是否应该降级到其他后端（便捷函数）"""
    manager = get_auto_verify_manager()
    return manager.should_fallback_to_other_backend(credential_name)


def record_request_success(credential_name: str) -> None:
    """记录请求成功，重置计数（便捷函数）"""
    manager = get_auto_verify_manager()
    manager.record_success(credential_name)


async def get_auto_verify_stats() -> Dict:
    """获取自动校验统计信息（便捷函数）"""
    manager = get_auto_verify_manager()
    return await manager.get_stats()

