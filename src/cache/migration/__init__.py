"""
Migration Module - 缓存迁移适配层

提供从旧 SignatureCache 到新分层缓存系统的渐进式迁移支持。

核心组件：
- MigrationFeatureFlags: 特性开关，控制迁移阶段
- MigrationConfig: 迁移配置
- DualWriteStrategy: 双写策略
- ReadStrategy: 读取策略
- LegacySignatureCacheAdapter: 旧接口适配器

迁移阶段：
- Phase 1 (LEGACY_ONLY): 只使用旧缓存（默认，零风险）
- Phase 2 (DUAL_WRITE): 双写模式（写入新旧，读取旧优先）
- Phase 3 (NEW_PREFERRED): 新缓存优先（写入新旧，读取新优先）
- Phase 4 (NEW_ONLY): 只使用新缓存（迁移完成）

Usage:
    from cache.migration import (
        LegacySignatureCacheAdapter,
        MigrationPhase,
        set_migration_phase,
    )

    # 创建适配器（与 SignatureCache 接口完全兼容）
    adapter = LegacySignatureCacheAdapter()

    # 使用与旧接口相同的方式
    adapter.set(thinking_text="...", signature="...")
    signature = adapter.get(thinking_text="...")

    # 切换迁移阶段
    set_migration_phase(MigrationPhase.DUAL_WRITE)

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
Version: 2.0.0
"""

__version__ = "2.0.0"

# Feature Flags
from .feature_flags import (
    MigrationPhase,
    MigrationFeatureFlags,
    get_feature_flags,
    reset_feature_flags,
    set_migration_phase,
    get_migration_phase,
)

# Migration Config
from .migration_config import (
    MigrationConfig,
    get_migration_config,
    reset_migration_config,
    set_migration_config,
)

# Dual Write Strategy
from .dual_write_strategy import (
    WriteResult,
    WriteStats,
    DualWriteStrategy,
    get_dual_write_strategy,
    reset_dual_write_strategy,
)

# Read Strategy
from .read_strategy import (
    ReadSource,
    ReadStats,
    ReadStrategy,
    get_read_strategy,
    reset_read_strategy,
)

# Legacy Adapter
from .legacy_adapter import (
    AdapterCacheEntry,
    AdapterCacheStats,
    LegacySignatureCacheAdapter,
    get_legacy_adapter,
    reset_legacy_adapter,
    # 便捷函数
    cache_signature_v2,
    get_cached_signature_v2,
    get_cache_stats_v2,
    get_last_signature_v2,
    get_last_signature_with_text_v2,
)

__all__ = [
    # Version
    "__version__",

    # Feature Flags
    "MigrationPhase",
    "MigrationFeatureFlags",
    "get_feature_flags",
    "reset_feature_flags",
    "set_migration_phase",
    "get_migration_phase",

    # Migration Config
    "MigrationConfig",
    "get_migration_config",
    "reset_migration_config",
    "set_migration_config",

    # Dual Write Strategy
    "WriteResult",
    "WriteStats",
    "DualWriteStrategy",
    "get_dual_write_strategy",
    "reset_dual_write_strategy",

    # Read Strategy
    "ReadSource",
    "ReadStats",
    "ReadStrategy",
    "get_read_strategy",
    "reset_read_strategy",

    # Legacy Adapter
    "AdapterCacheEntry",
    "AdapterCacheStats",
    "LegacySignatureCacheAdapter",
    "get_legacy_adapter",
    "reset_legacy_adapter",
    "cache_signature_v2",
    "get_cached_signature_v2",
    "get_cache_stats_v2",
    "get_last_signature_v2",
    "get_last_signature_with_text_v2",
]
