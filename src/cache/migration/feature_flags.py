"""
Migration Feature Flags - 迁移特性开关

控制缓存系统迁移的各个阶段行为，支持渐进式切换。

迁移阶段：
- Phase 1: 只使用旧缓存（默认，零风险）
- Phase 2: 双写模式（写入新旧，读取旧优先）
- Phase 3: 新缓存优先（写入新旧，读取新优先）
- Phase 4: 只使用新缓存（迁移完成）

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import os
import logging
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable, List

log = logging.getLogger("gcli2api.cache.migration.feature_flags")


class MigrationPhase(IntEnum):
    """迁移阶段枚举"""
    LEGACY_ONLY = 1      # 只使用旧缓存
    DUAL_WRITE = 2       # 双写模式
    NEW_PREFERRED = 3    # 新缓存优先
    NEW_ONLY = 4         # 只使用新缓存


@dataclass
class MigrationFeatureFlags:
    """
    迁移特性开关配置

    控制缓存迁移的各个方面，支持动态更新。

    Usage:
        flags = MigrationFeatureFlags()
        flags.set_phase(MigrationPhase.DUAL_WRITE)

        if flags.should_write_to_new:
            new_cache.set(...)
    """

    # 当前迁移阶段
    _phase: MigrationPhase = field(default=MigrationPhase.LEGACY_ONLY)

    # 写入控制（根据 phase 自动计算，也可手动覆盖）
    _write_to_legacy_override: Optional[bool] = None
    _write_to_new_override: Optional[bool] = None

    # 读取控制（根据 phase 自动计算，也可手动覆盖）
    _read_from_legacy_override: Optional[bool] = None
    _read_from_new_override: Optional[bool] = None
    _prefer_new_on_read_override: Optional[bool] = None

    # 验证和调试
    validate_consistency: bool = False  # 验证双写一致性
    log_migration_events: bool = True   # 记录迁移事件
    log_read_source: bool = True        # 记录读取来源

    # 性能控制
    async_write_to_new: bool = True     # 异步写入新缓存（不阻塞主流程）

    # 回调函数（用于监控）
    _on_phase_change_callbacks: List[Callable] = field(default_factory=list)

    # 线程锁
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        """初始化后处理"""
        # 从环境变量加载配置
        self._load_from_env()

    def _load_from_env(self):
        """从环境变量加载配置"""
        # CACHE_MIGRATION_PHASE: 1-4
        phase_str = os.environ.get("CACHE_MIGRATION_PHASE", "")
        if phase_str.isdigit():
            phase_int = int(phase_str)
            if 1 <= phase_int <= 4:
                self._phase = MigrationPhase(phase_int)
                log.info(f"[FEATURE_FLAGS] 从环境变量加载迁移阶段: {self._phase.name}")

        # CACHE_VALIDATE_CONSISTENCY: true/false
        validate_str = os.environ.get("CACHE_VALIDATE_CONSISTENCY", "").lower()
        if validate_str in ("true", "1", "yes"):
            self.validate_consistency = True
        elif validate_str in ("false", "0", "no"):
            self.validate_consistency = False

        # CACHE_LOG_MIGRATION: true/false
        log_str = os.environ.get("CACHE_LOG_MIGRATION", "").lower()
        if log_str in ("true", "1", "yes"):
            self.log_migration_events = True
        elif log_str in ("false", "0", "no"):
            self.log_migration_events = False

    @property
    def phase(self) -> MigrationPhase:
        """获取当前迁移阶段"""
        return self._phase

    def set_phase(self, phase: MigrationPhase) -> None:
        """
        设置迁移阶段

        Args:
            phase: 新的迁移阶段
        """
        with self._lock:
            old_phase = self._phase
            self._phase = phase

            if old_phase != phase:
                log.info(f"[FEATURE_FLAGS] 迁移阶段变更: {old_phase.name} -> {phase.name}")

                # 触发回调
                for callback in self._on_phase_change_callbacks:
                    try:
                        callback(old_phase, phase)
                    except Exception as e:
                        log.error(f"[FEATURE_FLAGS] 阶段变更回调失败: {e}")

    def on_phase_change(self, callback: Callable) -> None:
        """
        注册阶段变更回调

        Args:
            callback: 回调函数，签名为 (old_phase, new_phase)
        """
        with self._lock:
            self._on_phase_change_callbacks.append(callback)

    # ==================== 写入控制 ====================

    @property
    def should_write_to_legacy(self) -> bool:
        """是否应该写入旧缓存"""
        if self._write_to_legacy_override is not None:
            return self._write_to_legacy_override

        # 根据阶段自动判断
        # Phase 1-3: 写入旧缓存
        # Phase 4: 不写入旧缓存
        return self._phase in (
            MigrationPhase.LEGACY_ONLY,
            MigrationPhase.DUAL_WRITE,
            MigrationPhase.NEW_PREFERRED
        )

    @property
    def should_write_to_new(self) -> bool:
        """是否应该写入新缓存"""
        if self._write_to_new_override is not None:
            return self._write_to_new_override

        # 根据阶段自动判断
        # Phase 1: 不写入新缓存
        # Phase 2-4: 写入新缓存
        return self._phase in (
            MigrationPhase.DUAL_WRITE,
            MigrationPhase.NEW_PREFERRED,
            MigrationPhase.NEW_ONLY
        )

    @property
    def is_dual_write_enabled(self) -> bool:
        """是否启用双写"""
        return self.should_write_to_legacy and self.should_write_to_new

    # ==================== 读取控制 ====================

    @property
    def should_read_from_legacy(self) -> bool:
        """是否应该从旧缓存读取"""
        if self._read_from_legacy_override is not None:
            return self._read_from_legacy_override

        # 根据阶段自动判断
        # Phase 1-3: 可以从旧缓存读取
        # Phase 4: 不从旧缓存读取
        return self._phase in (
            MigrationPhase.LEGACY_ONLY,
            MigrationPhase.DUAL_WRITE,
            MigrationPhase.NEW_PREFERRED
        )

    @property
    def should_read_from_new(self) -> bool:
        """是否应该从新缓存读取"""
        if self._read_from_new_override is not None:
            return self._read_from_new_override

        # 根据阶段自动判断
        # Phase 1: 不从新缓存读取
        # Phase 2-4: 可以从新缓存读取
        return self._phase in (
            MigrationPhase.DUAL_WRITE,
            MigrationPhase.NEW_PREFERRED,
            MigrationPhase.NEW_ONLY
        )

    @property
    def prefer_new_on_read(self) -> bool:
        """读取时是否优先使用新缓存"""
        if self._prefer_new_on_read_override is not None:
            return self._prefer_new_on_read_override

        # 根据阶段自动判断
        # Phase 1-2: 优先旧缓存
        # Phase 3-4: 优先新缓存
        return self._phase in (
            MigrationPhase.NEW_PREFERRED,
            MigrationPhase.NEW_ONLY
        )

    # ==================== 手动覆盖 ====================

    def override_write_to_legacy(self, value: Optional[bool]) -> None:
        """手动覆盖是否写入旧缓存"""
        with self._lock:
            self._write_to_legacy_override = value
            if value is not None:
                log.info(f"[FEATURE_FLAGS] 手动覆盖 write_to_legacy: {value}")

    def override_write_to_new(self, value: Optional[bool]) -> None:
        """手动覆盖是否写入新缓存"""
        with self._lock:
            self._write_to_new_override = value
            if value is not None:
                log.info(f"[FEATURE_FLAGS] 手动覆盖 write_to_new: {value}")

    def override_read_from_legacy(self, value: Optional[bool]) -> None:
        """手动覆盖是否从旧缓存读取"""
        with self._lock:
            self._read_from_legacy_override = value
            if value is not None:
                log.info(f"[FEATURE_FLAGS] 手动覆盖 read_from_legacy: {value}")

    def override_read_from_new(self, value: Optional[bool]) -> None:
        """手动覆盖是否从新缓存读取"""
        with self._lock:
            self._read_from_new_override = value
            if value is not None:
                log.info(f"[FEATURE_FLAGS] 手动覆盖 read_from_new: {value}")

    def override_prefer_new_on_read(self, value: Optional[bool]) -> None:
        """手动覆盖读取时是否优先新缓存"""
        with self._lock:
            self._prefer_new_on_read_override = value
            if value is not None:
                log.info(f"[FEATURE_FLAGS] 手动覆盖 prefer_new_on_read: {value}")

    def clear_overrides(self) -> None:
        """清除所有手动覆盖"""
        with self._lock:
            self._write_to_legacy_override = None
            self._write_to_new_override = None
            self._read_from_legacy_override = None
            self._read_from_new_override = None
            self._prefer_new_on_read_override = None
            log.info("[FEATURE_FLAGS] 已清除所有手动覆盖")

    # ==================== 状态查询 ====================

    def get_status(self) -> dict:
        """获取当前状态"""
        return {
            "phase": self._phase.name,
            "phase_value": self._phase.value,
            "write": {
                "to_legacy": self.should_write_to_legacy,
                "to_new": self.should_write_to_new,
                "dual_write": self.is_dual_write_enabled,
                "async_to_new": self.async_write_to_new,
            },
            "read": {
                "from_legacy": self.should_read_from_legacy,
                "from_new": self.should_read_from_new,
                "prefer_new": self.prefer_new_on_read,
            },
            "debug": {
                "validate_consistency": self.validate_consistency,
                "log_migration_events": self.log_migration_events,
                "log_read_source": self.log_read_source,
            },
            "overrides": {
                "write_to_legacy": self._write_to_legacy_override,
                "write_to_new": self._write_to_new_override,
                "read_from_legacy": self._read_from_legacy_override,
                "read_from_new": self._read_from_new_override,
                "prefer_new_on_read": self._prefer_new_on_read_override,
            }
        }

    def __repr__(self) -> str:
        return (
            f"MigrationFeatureFlags("
            f"phase={self._phase.name}, "
            f"write_legacy={self.should_write_to_legacy}, "
            f"write_new={self.should_write_to_new}, "
            f"read_legacy={self.should_read_from_legacy}, "
            f"read_new={self.should_read_from_new}, "
            f"prefer_new={self.prefer_new_on_read})"
        )


# ==================== 全局实例 ====================

_global_flags: Optional[MigrationFeatureFlags] = None
_global_flags_lock = threading.Lock()


def get_feature_flags() -> MigrationFeatureFlags:
    """
    获取全局特性开关实例（线程安全的单例）

    Returns:
        全局 MigrationFeatureFlags 实例
    """
    global _global_flags

    if _global_flags is None:
        with _global_flags_lock:
            if _global_flags is None:
                _global_flags = MigrationFeatureFlags()
                log.info(f"[FEATURE_FLAGS] 创建全局特性开关实例: {_global_flags}")

    return _global_flags


def reset_feature_flags() -> None:
    """重置全局特性开关实例（主要用于测试）"""
    global _global_flags

    with _global_flags_lock:
        _global_flags = None
        log.info("[FEATURE_FLAGS] 重置全局特性开关实例")


def set_migration_phase(phase: MigrationPhase) -> None:
    """
    设置迁移阶段（便捷函数）

    Args:
        phase: 新的迁移阶段
    """
    get_feature_flags().set_phase(phase)


def get_migration_phase() -> MigrationPhase:
    """
    获取当前迁移阶段（便捷函数）

    Returns:
        当前迁移阶段
    """
    return get_feature_flags().phase
