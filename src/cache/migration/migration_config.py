"""
Migration Config - 迁移配置管理

管理缓存迁移的各项配置参数。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import os
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

log = logging.getLogger("gcli2api.cache.migration.migration_config")


@dataclass
class MigrationConfig:
    """
    迁移配置

    包含新旧缓存系统的配置参数。

    Usage:
        config = MigrationConfig()
        config.new_cache_db_path = "/path/to/new_cache.db"
    """

    # ==================== 旧缓存配置 ====================
    # 这些配置与现有 SignatureCache 保持一致

    legacy_max_size: int = 10000
    legacy_ttl_seconds: float = 3600.0
    legacy_key_prefix_length: int = 500

    # ==================== 新缓存配置 ====================
    # 这些配置用于新的分层缓存系统

    # L1 内存缓存配置
    new_l1_max_size: int = 5000
    new_l1_ttl_seconds: float = 1800.0  # 30 分钟

    # L2 SQLite 配置
    new_l2_db_path: Optional[str] = None  # None 表示使用默认路径
    new_l2_max_size: int = 100000
    new_l2_ttl_seconds: float = 86400.0  # 24 小时
    new_l2_wal_mode: bool = True

    # 异步写入队列配置
    async_queue_max_size: int = 1000
    async_queue_batch_size: int = 50
    async_queue_flush_interval: float = 1.0

    # ==================== 迁移行为配置 ====================

    # 双写失败策略
    # "ignore": 忽略新缓存写入失败
    # "log": 记录日志但不影响主流程
    # "raise": 抛出异常（仅用于测试）
    dual_write_failure_policy: str = "log"

    # 一致性验证采样率 (0.0 - 1.0)
    consistency_check_sample_rate: float = 0.1

    # 迁移统计上报间隔（秒）
    stats_report_interval: float = 60.0

    # ==================== 路径配置 ====================

    # 默认数据目录
    data_dir: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        # 设置默认数据目录
        if self.data_dir is None:
            self.data_dir = self._get_default_data_dir()

        # 设置默认 L2 数据库路径
        if self.new_l2_db_path is None:
            self.new_l2_db_path = str(
                Path(self.data_dir) / "signature_cache_v2.db"
            )

        # 从环境变量加载覆盖配置
        self._load_from_env()

    def _get_default_data_dir(self) -> str:
        """获取默认数据目录"""
        # 优先使用环境变量
        env_dir = os.environ.get("GCLI2API_DATA_DIR")
        if env_dir:
            return env_dir

        # 使用项目目录下的 data 文件夹
        # 假设当前工作目录是项目根目录
        project_root = Path(__file__).parent.parent.parent.parent
        data_dir = project_root / "data"

        # 确保目录存在
        data_dir.mkdir(parents=True, exist_ok=True)

        return str(data_dir)

    def _load_from_env(self):
        """从环境变量加载配置"""
        # 旧缓存配置
        if os.environ.get("CACHE_LEGACY_MAX_SIZE"):
            self.legacy_max_size = int(os.environ["CACHE_LEGACY_MAX_SIZE"])

        if os.environ.get("CACHE_LEGACY_TTL"):
            self.legacy_ttl_seconds = float(os.environ["CACHE_LEGACY_TTL"])

        # 新缓存配置
        if os.environ.get("CACHE_NEW_L1_MAX_SIZE"):
            self.new_l1_max_size = int(os.environ["CACHE_NEW_L1_MAX_SIZE"])

        if os.environ.get("CACHE_NEW_L1_TTL"):
            self.new_l1_ttl_seconds = float(os.environ["CACHE_NEW_L1_TTL"])

        if os.environ.get("CACHE_NEW_L2_DB_PATH"):
            self.new_l2_db_path = os.environ["CACHE_NEW_L2_DB_PATH"]

        if os.environ.get("CACHE_NEW_L2_MAX_SIZE"):
            self.new_l2_max_size = int(os.environ["CACHE_NEW_L2_MAX_SIZE"])

        if os.environ.get("CACHE_NEW_L2_TTL"):
            self.new_l2_ttl_seconds = float(os.environ["CACHE_NEW_L2_TTL"])

        # 迁移行为配置
        if os.environ.get("CACHE_DUAL_WRITE_FAILURE_POLICY"):
            policy = os.environ["CACHE_DUAL_WRITE_FAILURE_POLICY"]
            if policy in ("ignore", "log", "raise"):
                self.dual_write_failure_policy = policy

        if os.environ.get("CACHE_CONSISTENCY_SAMPLE_RATE"):
            self.consistency_check_sample_rate = float(
                os.environ["CACHE_CONSISTENCY_SAMPLE_RATE"]
            )

    def get_legacy_config(self) -> dict:
        """获取旧缓存配置（用于创建 SignatureCache）"""
        return {
            "max_size": self.legacy_max_size,
            "ttl_seconds": self.legacy_ttl_seconds,
            "key_prefix_length": self.legacy_key_prefix_length,
        }

    def get_new_l1_config(self) -> dict:
        """获取新缓存 L1 配置"""
        return {
            "max_size": self.new_l1_max_size,
            "ttl_seconds": self.new_l1_ttl_seconds,
        }

    def get_new_l2_config(self) -> dict:
        """获取新缓存 L2 配置"""
        return {
            "db_path": self.new_l2_db_path,
            "max_size": self.new_l2_max_size,
            "ttl_seconds": self.new_l2_ttl_seconds,
            "wal_mode": self.new_l2_wal_mode,
        }

    def get_async_queue_config(self) -> dict:
        """获取异步队列配置"""
        return {
            "max_size": self.async_queue_max_size,
            "batch_size": self.async_queue_batch_size,
            "flush_interval": self.async_queue_flush_interval,
        }

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "legacy": self.get_legacy_config(),
            "new_l1": self.get_new_l1_config(),
            "new_l2": self.get_new_l2_config(),
            "async_queue": self.get_async_queue_config(),
            "migration": {
                "dual_write_failure_policy": self.dual_write_failure_policy,
                "consistency_check_sample_rate": self.consistency_check_sample_rate,
                "stats_report_interval": self.stats_report_interval,
            },
            "paths": {
                "data_dir": self.data_dir,
            }
        }

    def __repr__(self) -> str:
        return (
            f"MigrationConfig("
            f"legacy_max={self.legacy_max_size}, "
            f"new_l1_max={self.new_l1_max_size}, "
            f"new_l2_path={self.new_l2_db_path})"
        )


# ==================== 全局实例 ====================

_global_config: Optional[MigrationConfig] = None
_global_config_lock = threading.Lock()


def get_migration_config() -> MigrationConfig:
    """
    获取全局迁移配置实例（线程安全的单例）

    Returns:
        全局 MigrationConfig 实例
    """
    global _global_config

    if _global_config is None:
        with _global_config_lock:
            if _global_config is None:
                _global_config = MigrationConfig()
                log.info(f"[MIGRATION_CONFIG] 创建全局配置实例: {_global_config}")

    return _global_config


def reset_migration_config() -> None:
    """重置全局迁移配置实例（主要用于测试）"""
    global _global_config

    with _global_config_lock:
        _global_config = None
        log.info("[MIGRATION_CONFIG] 重置全局配置实例")


def set_migration_config(config: MigrationConfig) -> None:
    """
    设置全局迁移配置实例

    Args:
        config: 新的配置实例
    """
    global _global_config

    with _global_config_lock:
        _global_config = config
        log.info(f"[MIGRATION_CONFIG] 设置全局配置实例: {config}")
