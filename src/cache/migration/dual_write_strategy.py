"""
Dual Write Strategy - 双写策略

实现同时写入新旧缓存系统的策略，确保数据一致性。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import logging
import threading
import time
import random
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .feature_flags import MigrationFeatureFlags
    from .migration_config import MigrationConfig

log = logging.getLogger("gcli2api.cache.migration.dual_write")


class WriteResult(Enum):
    """写入结果枚举"""
    SUCCESS = "success"
    LEGACY_ONLY = "legacy_only"
    NEW_ONLY = "new_only"
    BOTH_FAILED = "both_failed"
    SKIPPED = "skipped"


@dataclass
class WriteStats:
    """双写统计"""
    total_writes: int = 0
    legacy_writes: int = 0
    legacy_failures: int = 0
    new_writes: int = 0
    new_failures: int = 0
    dual_writes: int = 0
    consistency_checks: int = 0
    consistency_failures: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_write(
        self,
        legacy_success: bool,
        new_success: bool,
        is_dual: bool
    ) -> None:
        """记录写入结果"""
        with self._lock:
            self.total_writes += 1

            if legacy_success:
                self.legacy_writes += 1
            else:
                self.legacy_failures += 1

            if new_success:
                self.new_writes += 1
            else:
                self.new_failures += 1

            if is_dual:
                self.dual_writes += 1

    def record_consistency_check(self, passed: bool) -> None:
        """记录一致性检查结果"""
        with self._lock:
            self.consistency_checks += 1
            if not passed:
                self.consistency_failures += 1

    def to_dict(self) -> dict:
        """转换为字典"""
        with self._lock:
            return {
                "total_writes": self.total_writes,
                "legacy_writes": self.legacy_writes,
                "legacy_failures": self.legacy_failures,
                "new_writes": self.new_writes,
                "new_failures": self.new_failures,
                "dual_writes": self.dual_writes,
                "consistency_checks": self.consistency_checks,
                "consistency_failures": self.consistency_failures,
                "legacy_success_rate": (
                    self.legacy_writes / self.total_writes
                    if self.total_writes > 0 else 0.0
                ),
                "new_success_rate": (
                    self.new_writes / self.total_writes
                    if self.total_writes > 0 else 0.0
                ),
                "consistency_pass_rate": (
                    (self.consistency_checks - self.consistency_failures) /
                    self.consistency_checks
                    if self.consistency_checks > 0 else 1.0
                ),
            }


class DualWriteStrategy:
    """
    双写策略

    协调新旧缓存系统的写入操作，支持：
    - 同步双写
    - 异步写入新缓存
    - 写入失败降级
    - 一致性验证

    Usage:
        strategy = DualWriteStrategy(flags, config)

        # 写入
        result = strategy.write(
            key="hash123",
            value=cache_entry,
            legacy_writer=lambda k, v: legacy_cache.set(k, v),
            new_writer=lambda k, v: new_cache.set(k, v)
        )
    """

    def __init__(
        self,
        flags: "MigrationFeatureFlags",
        config: "MigrationConfig"
    ):
        """
        初始化双写策略

        Args:
            flags: 特性开关
            config: 迁移配置
        """
        self._flags = flags
        self._config = config
        self._stats = WriteStats()
        self._lock = threading.Lock()

        log.info("[DUAL_WRITE] 初始化双写策略")

    @property
    def stats(self) -> WriteStats:
        """获取统计信息"""
        return self._stats

    def write(
        self,
        thinking_text: str,
        signature: str,
        model: Optional[str],
        legacy_writer: Callable[[str, str, Optional[str]], bool],
        new_writer: Optional[Callable[[str, str, Optional[str]], bool]] = None,
        namespace: str = "default"
    ) -> WriteResult:
        """
        执行写入操作

        根据特性开关决定写入目标：
        - 只写旧缓存
        - 只写新缓存
        - 双写

        Args:
            thinking_text: thinking 文本
            signature: signature 值
            model: 模型名称
            legacy_writer: 旧缓存写入函数
            new_writer: 新缓存写入函数（可选）
            namespace: 命名空间

        Returns:
            写入结果
        """
        should_write_legacy = self._flags.should_write_to_legacy
        should_write_new = self._flags.should_write_to_new and new_writer is not None

        if not should_write_legacy and not should_write_new:
            log.warning("[DUAL_WRITE] 没有配置任何写入目标")
            return WriteResult.SKIPPED

        legacy_success = False
        new_success = False
        is_dual = should_write_legacy and should_write_new

        # 写入旧缓存
        if should_write_legacy:
            try:
                legacy_success = legacy_writer(thinking_text, signature, model)
                if legacy_success:
                    if self._flags.log_migration_events:
                        log.debug(
                            f"[DUAL_WRITE] 旧缓存写入成功: "
                            f"thinking_len={len(thinking_text)}"
                        )
                else:
                    log.warning("[DUAL_WRITE] 旧缓存写入返回 False")
            except Exception as e:
                log.error(f"[DUAL_WRITE] 旧缓存写入异常: {e}")
                legacy_success = False

        # 写入新缓存
        if should_write_new:
            try:
                if self._flags.async_write_to_new:
                    # 异步写入（不阻塞主流程）
                    self._async_write_new(
                        new_writer, thinking_text, signature, model, namespace
                    )
                    new_success = True  # 异步写入视为成功（实际结果稍后处理）
                else:
                    # 同步写入
                    new_success = new_writer(thinking_text, signature, model)

                if new_success and self._flags.log_migration_events:
                    log.debug(
                        f"[DUAL_WRITE] 新缓存写入{'已提交' if self._flags.async_write_to_new else '成功'}: "
                        f"thinking_len={len(thinking_text)}, namespace={namespace}"
                    )
            except Exception as e:
                log.error(f"[DUAL_WRITE] 新缓存写入异常: {e}")
                new_success = False
                self._handle_new_cache_failure(e)

        # 记录统计
        self._stats.record_write(legacy_success, new_success, is_dual)

        # 一致性验证（采样）
        if (
            is_dual and
            legacy_success and
            new_success and
            self._flags.validate_consistency and
            random.random() < self._config.consistency_check_sample_rate
        ):
            # 异步验证一致性
            self._schedule_consistency_check(thinking_text, namespace)

        # 确定返回结果
        if legacy_success and new_success:
            return WriteResult.SUCCESS
        elif legacy_success:
            return WriteResult.LEGACY_ONLY
        elif new_success:
            return WriteResult.NEW_ONLY
        else:
            return WriteResult.BOTH_FAILED

    def _async_write_new(
        self,
        writer: Callable,
        thinking_text: str,
        signature: str,
        model: Optional[str],
        namespace: str
    ) -> None:
        """异步写入新缓存"""
        def do_write():
            try:
                writer(thinking_text, signature, model)
            except Exception as e:
                log.error(f"[DUAL_WRITE] 异步写入新缓存失败: {e}")
                self._handle_new_cache_failure(e)

        # 使用线程执行异步写入
        thread = threading.Thread(target=do_write, daemon=True)
        thread.start()

    def _handle_new_cache_failure(self, error: Exception) -> None:
        """处理新缓存写入失败"""
        policy = self._config.dual_write_failure_policy

        if policy == "ignore":
            pass
        elif policy == "log":
            log.warning(f"[DUAL_WRITE] 新缓存写入失败（已记录）: {error}")
        elif policy == "raise":
            raise error

    def _schedule_consistency_check(
        self,
        thinking_text: str,
        namespace: str
    ) -> None:
        """调度一致性检查"""
        def do_check():
            # 等待一小段时间，确保写入完成
            time.sleep(0.1)
            # 实际的一致性检查在 LegacyAdapter 中实现
            # 这里只是占位
            log.debug(
                f"[DUAL_WRITE] 一致性检查已调度: "
                f"thinking_len={len(thinking_text)}, namespace={namespace}"
            )

        thread = threading.Thread(target=do_check, daemon=True)
        thread.start()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.to_dict()

    def reset_stats(self) -> None:
        """重置统计信息"""
        with self._lock:
            self._stats = WriteStats()
            log.info("[DUAL_WRITE] 统计信息已重置")


# ==================== 全局实例 ====================

_global_strategy: Optional[DualWriteStrategy] = None
_global_strategy_lock = threading.Lock()


def get_dual_write_strategy(
    flags: Optional["MigrationFeatureFlags"] = None,
    config: Optional["MigrationConfig"] = None
) -> DualWriteStrategy:
    """
    获取全局双写策略实例

    Args:
        flags: 特性开关（首次调用时必须提供）
        config: 迁移配置（首次调用时必须提供）

    Returns:
        全局 DualWriteStrategy 实例
    """
    global _global_strategy

    if _global_strategy is None:
        with _global_strategy_lock:
            if _global_strategy is None:
                if flags is None or config is None:
                    # 延迟导入避免循环依赖
                    from .feature_flags import get_feature_flags
                    from .migration_config import get_migration_config

                    flags = flags or get_feature_flags()
                    config = config or get_migration_config()

                _global_strategy = DualWriteStrategy(flags, config)
                log.info("[DUAL_WRITE] 创建全局双写策略实例")

    return _global_strategy


def reset_dual_write_strategy() -> None:
    """重置全局双写策略实例"""
    global _global_strategy

    with _global_strategy_lock:
        _global_strategy = None
        log.info("[DUAL_WRITE] 重置全局双写策略实例")
