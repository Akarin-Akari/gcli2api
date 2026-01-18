"""
Read Strategy - 读取策略

实现从新旧缓存系统读取的策略，支持渐进式切换。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, Tuple, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .feature_flags import MigrationFeatureFlags
    from .migration_config import MigrationConfig

log = logging.getLogger("gcli2api.cache.migration.read_strategy")


class ReadSource(Enum):
    """读取来源枚举"""
    LEGACY = "legacy"
    NEW = "new"
    NONE = "none"


@dataclass
class ReadStats:
    """读取统计"""
    total_reads: int = 0
    legacy_hits: int = 0
    legacy_misses: int = 0
    new_hits: int = 0
    new_misses: int = 0
    fallback_hits: int = 0  # 主源未命中，fallback 命中

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_read(
        self,
        source: ReadSource,
        is_fallback: bool = False
    ) -> None:
        """记录读取结果"""
        with self._lock:
            self.total_reads += 1

            if source == ReadSource.LEGACY:
                self.legacy_hits += 1
                if is_fallback:
                    self.fallback_hits += 1
            elif source == ReadSource.NEW:
                self.new_hits += 1
                if is_fallback:
                    self.fallback_hits += 1
            else:
                # 两个都没命中
                pass

    def record_miss(self, source: str) -> None:
        """记录未命中"""
        with self._lock:
            if source == "legacy":
                self.legacy_misses += 1
            elif source == "new":
                self.new_misses += 1

    def to_dict(self) -> dict:
        """转换为字典"""
        with self._lock:
            total_hits = self.legacy_hits + self.new_hits
            return {
                "total_reads": self.total_reads,
                "legacy_hits": self.legacy_hits,
                "legacy_misses": self.legacy_misses,
                "new_hits": self.new_hits,
                "new_misses": self.new_misses,
                "fallback_hits": self.fallback_hits,
                "hit_rate": (
                    total_hits / self.total_reads
                    if self.total_reads > 0 else 0.0
                ),
                "new_hit_ratio": (
                    self.new_hits / total_hits
                    if total_hits > 0 else 0.0
                ),
            }


class ReadStrategy:
    """
    读取策略

    协调从新旧缓存系统读取，支持：
    - 优先读取配置
    - Fallback 机制
    - 读取来源追踪

    Usage:
        strategy = ReadStrategy(flags, config)

        # 读取
        result, source = strategy.read(
            key="hash123",
            legacy_reader=lambda k: legacy_cache.get(k),
            new_reader=lambda k: new_cache.get(k)
        )
    """

    def __init__(
        self,
        flags: "MigrationFeatureFlags",
        config: "MigrationConfig"
    ):
        """
        初始化读取策略

        Args:
            flags: 特性开关
            config: 迁移配置
        """
        self._flags = flags
        self._config = config
        self._stats = ReadStats()
        self._lock = threading.Lock()

        log.info("[READ_STRATEGY] 初始化读取策略")

    @property
    def stats(self) -> ReadStats:
        """获取统计信息"""
        return self._stats

    def read(
        self,
        thinking_text: str,
        legacy_reader: Callable[[str], Optional[str]],
        new_reader: Optional[Callable[[str, str], Optional[str]]] = None,
        namespace: str = "default"
    ) -> Tuple[Optional[str], ReadSource]:
        """
        执行读取操作

        根据特性开关决定读取顺序：
        - 只读旧缓存
        - 只读新缓存
        - 优先旧缓存，fallback 到新缓存
        - 优先新缓存，fallback 到旧缓存

        Args:
            thinking_text: thinking 文本
            legacy_reader: 旧缓存读取函数
            new_reader: 新缓存读取函数（可选）
            namespace: 命名空间

        Returns:
            (读取结果, 读取来源) 元组
        """
        should_read_legacy = self._flags.should_read_from_legacy
        should_read_new = self._flags.should_read_from_new and new_reader is not None
        prefer_new = self._flags.prefer_new_on_read

        if not should_read_legacy and not should_read_new:
            log.warning("[READ_STRATEGY] 没有配置任何读取源")
            return None, ReadSource.NONE

        # 确定读取顺序
        if prefer_new and should_read_new:
            # 优先新缓存
            primary_reader = lambda: self._read_new(new_reader, thinking_text, namespace)
            primary_source = ReadSource.NEW
            fallback_reader = lambda: self._read_legacy(legacy_reader, thinking_text) if should_read_legacy else None
            fallback_source = ReadSource.LEGACY
        else:
            # 优先旧缓存（默认）
            primary_reader = lambda: self._read_legacy(legacy_reader, thinking_text)
            primary_source = ReadSource.LEGACY
            fallback_reader = lambda: self._read_new(new_reader, thinking_text, namespace) if should_read_new else None
            fallback_source = ReadSource.NEW

        # 尝试主源
        result = primary_reader()
        if result is not None:
            self._stats.record_read(primary_source, is_fallback=False)
            if self._flags.log_read_source:
                log.debug(
                    f"[READ_STRATEGY] 命中 {primary_source.value}: "
                    f"thinking_len={len(thinking_text)}"
                )
            return result, primary_source

        # 主源未命中，记录
        self._stats.record_miss(primary_source.value)

        # 尝试 fallback
        if fallback_reader:
            result = fallback_reader()
            if result is not None:
                self._stats.record_read(fallback_source, is_fallback=True)
                if self._flags.log_read_source:
                    log.debug(
                        f"[READ_STRATEGY] Fallback 命中 {fallback_source.value}: "
                        f"thinking_len={len(thinking_text)}"
                    )
                return result, fallback_source

            # Fallback 也未命中
            self._stats.record_miss(fallback_source.value)

        # 都未命中
        if self._flags.log_read_source:
            log.debug(
                f"[READ_STRATEGY] 未命中: thinking_len={len(thinking_text)}"
            )
        return None, ReadSource.NONE

    def _read_legacy(
        self,
        reader: Callable[[str], Optional[str]],
        thinking_text: str
    ) -> Optional[str]:
        """从旧缓存读取"""
        try:
            return reader(thinking_text)
        except Exception as e:
            log.error(f"[READ_STRATEGY] 旧缓存读取异常: {e}")
            return None

    def _read_new(
        self,
        reader: Callable[[str, str], Optional[str]],
        thinking_text: str,
        namespace: str
    ) -> Optional[str]:
        """从新缓存读取"""
        try:
            return reader(thinking_text, namespace)
        except Exception as e:
            log.error(f"[READ_STRATEGY] 新缓存读取异常: {e}")
            return None

    def read_with_text(
        self,
        legacy_reader: Callable[[], Optional[Tuple[str, str]]],
        new_reader: Optional[Callable[[str], Optional[Tuple[str, str]]]] = None,
        namespace: str = "default"
    ) -> Tuple[Optional[Tuple[str, str]], ReadSource]:
        """
        读取 signature 和 thinking_text（用于 fallback 场景）

        Args:
            legacy_reader: 旧缓存读取函数，返回 (signature, thinking_text)
            new_reader: 新缓存读取函数，返回 (signature, thinking_text)
            namespace: 命名空间

        Returns:
            ((signature, thinking_text), 读取来源) 元组
        """
        should_read_legacy = self._flags.should_read_from_legacy
        should_read_new = self._flags.should_read_from_new and new_reader is not None
        prefer_new = self._flags.prefer_new_on_read

        if not should_read_legacy and not should_read_new:
            return None, ReadSource.NONE

        # 确定读取顺序
        if prefer_new and should_read_new:
            primary_reader = lambda: self._safe_call(new_reader, namespace)
            primary_source = ReadSource.NEW
            fallback_reader = lambda: self._safe_call(legacy_reader) if should_read_legacy else None
            fallback_source = ReadSource.LEGACY
        else:
            primary_reader = lambda: self._safe_call(legacy_reader)
            primary_source = ReadSource.LEGACY
            fallback_reader = lambda: self._safe_call(new_reader, namespace) if should_read_new else None
            fallback_source = ReadSource.NEW

        # 尝试主源
        result = primary_reader()
        if result is not None:
            self._stats.record_read(primary_source, is_fallback=False)
            return result, primary_source

        self._stats.record_miss(primary_source.value)

        # 尝试 fallback
        if fallback_reader:
            result = fallback_reader()
            if result is not None:
                self._stats.record_read(fallback_source, is_fallback=True)
                return result, fallback_source

            self._stats.record_miss(fallback_source.value)

        return None, ReadSource.NONE

    def _safe_call(self, func: Callable, *args) -> Optional[Any]:
        """安全调用函数"""
        try:
            return func(*args) if args else func()
        except Exception as e:
            log.error(f"[READ_STRATEGY] 读取异常: {e}")
            return None

    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.to_dict()

    def reset_stats(self) -> None:
        """重置统计信息"""
        with self._lock:
            self._stats = ReadStats()
            log.info("[READ_STRATEGY] 统计信息已重置")


# ==================== 全局实例 ====================

_global_strategy: Optional[ReadStrategy] = None
_global_strategy_lock = threading.Lock()


def get_read_strategy(
    flags: Optional["MigrationFeatureFlags"] = None,
    config: Optional["MigrationConfig"] = None
) -> ReadStrategy:
    """
    获取全局读取策略实例

    Args:
        flags: 特性开关（首次调用时必须提供）
        config: 迁移配置（首次调用时必须提供）

    Returns:
        全局 ReadStrategy 实例
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

                _global_strategy = ReadStrategy(flags, config)
                log.info("[READ_STRATEGY] 创建全局读取策略实例")

    return _global_strategy


def reset_read_strategy() -> None:
    """重置全局读取策略实例"""
    global _global_strategy

    with _global_strategy_lock:
        _global_strategy = None
        log.info("[READ_STRATEGY] 重置全局读取策略实例")
