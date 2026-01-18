"""
Legacy Signature Cache Adapter - 旧接口适配器

提供与现有 SignatureCache 完全兼容的接口，内部根据配置
决定使用旧缓存、新缓存或双写模式。

这是渐进式迁移的核心组件，确保零侵入式切换。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-09
"""

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

from .feature_flags import (
    MigrationFeatureFlags,
    MigrationPhase,
    get_feature_flags,
)
from .migration_config import MigrationConfig, get_migration_config
from .dual_write_strategy import DualWriteStrategy, WriteResult, get_dual_write_strategy
from .read_strategy import ReadStrategy, ReadSource, get_read_strategy

log = logging.getLogger("gcli2api.cache.migration.legacy_adapter")


@dataclass
class AdapterCacheEntry:
    """
    适配器缓存条目

    与旧 SignatureCache 的 CacheEntry 保持兼容
    """
    signature: str
    thinking_text: str
    thinking_text_preview: str
    timestamp: float
    access_count: int = 0
    model: Optional[str] = None

    def is_expired(self, ttl_seconds: float) -> bool:
        """检查是否过期"""
        return time.time() - self.timestamp > ttl_seconds


@dataclass
class AdapterCacheStats:
    """
    适配器缓存统计

    与旧 SignatureCache 的 CacheStats 保持兼容
    """
    hits: int = 0
    misses: int = 0
    writes: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": f"{self.hit_rate:.2%}",
            "total_requests": self.hits + self.misses,
        }


class LegacySignatureCacheAdapter:
    """
    旧接口适配器

    提供与 SignatureCache 完全相同的接口，内部根据迁移阶段
    决定实际的缓存操作目标。

    Usage:
        # 作为 SignatureCache 的替代品使用
        adapter = LegacySignatureCacheAdapter()

        # 完全兼容的接口
        adapter.set(thinking_text="...", signature="...")
        signature = adapter.get(thinking_text="...")

        # 切换迁移阶段
        adapter.set_migration_phase(MigrationPhase.DUAL_WRITE)
    """

    # 默认配置（与 SignatureCache 保持一致）
    DEFAULT_MAX_SIZE = 10000
    DEFAULT_TTL_SECONDS = 3600
    DEFAULT_KEY_PREFIX_LENGTH = 500

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        key_prefix_length: int = DEFAULT_KEY_PREFIX_LENGTH,
        flags: Optional[MigrationFeatureFlags] = None,
        config: Optional[MigrationConfig] = None,
        namespace: str = "default"
    ):
        """
        初始化适配器

        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 缓存过期时间
            key_prefix_length: 用于生成哈希 key 的文本前缀长度
            flags: 特性开关（可选，默认使用全局实例）
            config: 迁移配置（可选，默认使用全局实例）
            namespace: 命名空间（用于新缓存的隔离）
        """
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._key_prefix_length = key_prefix_length
        self._namespace = namespace

        # 获取配置和策略
        self._flags = flags or get_feature_flags()
        self._config = config or get_migration_config()
        self._dual_write = get_dual_write_strategy(self._flags, self._config)
        self._read_strategy = get_read_strategy(self._flags, self._config)

        # 内部旧缓存（用于 Phase 1-3）
        self._legacy_cache: OrderedDict[str, AdapterCacheEntry] = OrderedDict()
        self._legacy_lock = threading.Lock()
        self._stats = AdapterCacheStats()

        # 新缓存管理器（延迟初始化）
        self._new_cache_manager = None
        self._new_cache_lock = threading.Lock()

        log.info(
            f"[LEGACY_ADAPTER] 初始化适配器: "
            f"max_size={max_size}, ttl={ttl_seconds}s, "
            f"phase={self._flags.phase.name}, namespace={namespace}"
        )

    def _get_new_cache_manager(self):
        """延迟获取新缓存管理器"""
        if self._new_cache_manager is None:
            with self._new_cache_lock:
                if self._new_cache_manager is None:
                    try:
                        # 延迟导入避免循环依赖
                        from ..signature_cache_manager import SignatureCacheManager, LayeredCacheConfig
                        from ..cache_interface import CacheConfig

                        l1_config = CacheConfig(
                            max_size=self._config.new_l1_max_size,
                            ttl_seconds=self._config.new_l1_ttl_seconds,
                        )
                        l2_config = CacheConfig(
                            db_path=self._config.new_l2_db_path,
                            max_size=self._config.new_l2_max_size,
                            ttl_seconds=self._config.new_l2_ttl_seconds,
                        )
                        layered_config = LayeredCacheConfig(
                            l1_config=l1_config,
                            l2_config=l2_config,
                        )

                        self._new_cache_manager = SignatureCacheManager(layered_config)
                        log.info("[LEGACY_ADAPTER] 新缓存管理器初始化成功")
                    except Exception as e:
                        log.error(f"[LEGACY_ADAPTER] 新缓存管理器初始化失败: {e}")
                        return None

        return self._new_cache_manager

    # ==================== 核心接口（与 SignatureCache 完全兼容）====================

    def set(
        self,
        thinking_text: str,
        signature: str,
        model: Optional[str] = None
    ) -> bool:
        """
        缓存 signature

        Args:
            thinking_text: thinking 块的文本内容
            signature: 对应的 signature 值
            model: 可选的模型名称

        Returns:
            是否成功缓存
        """
        if not thinking_text or not signature:
            log.debug("[LEGACY_ADAPTER] 跳过缓存：thinking_text 或 signature 为空")
            return False

        if not self._is_valid_signature(signature):
            log.warning("[LEGACY_ADAPTER] 跳过缓存：signature 格式无效")
            return False

        # 使用双写策略
        result = self._dual_write.write(
            thinking_text=thinking_text,
            signature=signature,
            model=model,
            legacy_writer=self._write_to_legacy,
            new_writer=self._write_to_new if self._flags.should_write_to_new else None,
            namespace=self._namespace,
        )

        success = result in (WriteResult.SUCCESS, WriteResult.LEGACY_ONLY, WriteResult.NEW_ONLY)

        if success:
            self._stats.writes += 1

        return success

    def get(self, thinking_text: str) -> Optional[str]:
        """
        获取缓存的 signature

        Args:
            thinking_text: thinking 块的文本内容

        Returns:
            缓存的 signature，如果未命中或已过期则返回 None
        """
        if not thinking_text:
            return None

        # 使用读取策略
        result, source = self._read_strategy.read(
            thinking_text=thinking_text,
            legacy_reader=self._read_from_legacy,
            new_reader=self._read_from_new if self._flags.should_read_from_new else None,
            namespace=self._namespace,
        )

        if result is not None:
            self._stats.hits += 1
        else:
            self._stats.misses += 1

        return result

    def invalidate(self, thinking_text: str) -> bool:
        """
        使指定的缓存条目失效

        Args:
            thinking_text: thinking 块的文本内容

        Returns:
            是否成功删除
        """
        if not thinking_text:
            return False

        key = self._generate_key(thinking_text)
        if not key:
            return False

        deleted = False

        # 从旧缓存删除
        if self._flags.should_write_to_legacy:
            with self._legacy_lock:
                if key in self._legacy_cache:
                    del self._legacy_cache[key]
                    deleted = True
                    log.info(f"[LEGACY_ADAPTER] 旧缓存失效: key={key[:16]}...")

        # 从新缓存删除
        if self._flags.should_write_to_new:
            new_cache = self._get_new_cache_manager()
            if new_cache:
                try:
                    # 新缓存的 invalidate 接口
                    # TODO: 需要在 SignatureCacheManager 中实现
                    pass
                except Exception as e:
                    log.error(f"[LEGACY_ADAPTER] 新缓存失效失败: {e}")

        return deleted

    def clear(self) -> int:
        """
        清空所有缓存

        Returns:
            清除的条目数量
        """
        count = 0

        # 清空旧缓存
        with self._legacy_lock:
            count = len(self._legacy_cache)
            self._legacy_cache.clear()
            log.info(f"[LEGACY_ADAPTER] 清空旧缓存: 删除 {count} 条")

        # 清空新缓存
        if self._flags.should_write_to_new:
            new_cache = self._get_new_cache_manager()
            if new_cache:
                try:
                    # TODO: 需要在 SignatureCacheManager 中实现 clear
                    pass
                except Exception as e:
                    log.error(f"[LEGACY_ADAPTER] 清空新缓存失败: {e}")

        return count

    def cleanup_expired(self) -> int:
        """
        清理所有过期的缓存条目

        Returns:
            清理的条目数量
        """
        count = 0

        with self._legacy_lock:
            expired_keys = [
                key for key, entry in self._legacy_cache.items()
                if entry.is_expired(self._ttl_seconds)
            ]

            for key in expired_keys:
                del self._legacy_cache[key]
                self._stats.expirations += 1
                count += 1

            if expired_keys:
                log.info(f"[LEGACY_ADAPTER] 清理过期缓存: {count} 条")

        return count

    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息

        Returns:
            包含命中率、写入次数等统计信息的字典
        """
        with self._legacy_lock:
            stats = self._stats.to_dict()
            stats["cache_size"] = len(self._legacy_cache)
            stats["max_size"] = self._max_size
            stats["ttl_seconds"] = self._ttl_seconds

            # 添加迁移相关统计
            stats["migration"] = {
                "phase": self._flags.phase.name,
                "dual_write": self._dual_write.get_stats(),
                "read_strategy": self._read_strategy.get_stats(),
            }

            return stats

    @property
    def size(self) -> int:
        """当前缓存大小"""
        with self._legacy_lock:
            return len(self._legacy_cache)

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return (
            f"LegacySignatureCacheAdapter("
            f"size={self.size}, max_size={self._max_size}, "
            f"ttl={self._ttl_seconds}s, phase={self._flags.phase.name}, "
            f"hit_rate={self._stats.hit_rate:.2%})"
        )

    # ==================== 扩展接口（用于 fallback）====================

    def get_last_signature(self) -> Optional[str]:
        """
        获取最近缓存的 signature（用于 fallback）

        Returns:
            最近缓存的有效 signature
        """
        with self._legacy_lock:
            if not self._legacy_cache:
                return None

            for key in reversed(self._legacy_cache.keys()):
                entry = self._legacy_cache[key]
                if not entry.is_expired(self._ttl_seconds):
                    log.info(
                        f"[LEGACY_ADAPTER] get_last_signature: 找到有效的最近 signature, "
                        f"key={key[:16]}..., age={time.time() - entry.timestamp:.1f}s"
                    )
                    return entry.signature

            return None

    def get_last_signature_with_text(self) -> Optional[Tuple[str, str]]:
        """
        获取最近缓存的 signature 及其对应的 thinking 文本

        Returns:
            (signature, thinking_text) 元组
        """
        with self._legacy_lock:
            if not self._legacy_cache:
                return None

            for key in reversed(self._legacy_cache.keys()):
                entry = self._legacy_cache[key]
                if not entry.is_expired(self._ttl_seconds):
                    log.info(
                        f"[LEGACY_ADAPTER] get_last_signature_with_text: 找到有效条目, "
                        f"key={key[:16]}..., thinking_len={len(entry.thinking_text)}"
                    )
                    return (entry.signature, entry.thinking_text)

            return None

    # ==================== 迁移控制接口 ====================

    def set_migration_phase(self, phase: MigrationPhase) -> None:
        """
        设置迁移阶段

        Args:
            phase: 新的迁移阶段
        """
        self._flags.set_phase(phase)
        log.info(f"[LEGACY_ADAPTER] 迁移阶段已切换: {phase.name}")

    def get_migration_phase(self) -> MigrationPhase:
        """获取当前迁移阶段"""
        return self._flags.phase

    def get_migration_status(self) -> dict:
        """获取迁移状态"""
        return {
            "phase": self._flags.phase.name,
            "flags": self._flags.get_status(),
            "dual_write_stats": self._dual_write.get_stats(),
            "read_stats": self._read_strategy.get_stats(),
        }

    # ==================== 内部方法 ====================

    def _normalize_thinking_text(self, thinking_text: str) -> str:
        """规范化 thinking 文本"""
        import re

        if not thinking_text:
            return ""

        text = thinking_text.strip()

        # 去除 <think>...</think> 标签
        match = re.match(
            r'^<think>\s*(.*?)\s*</think>\s*$',
            text, flags=re.DOTALL | re.IGNORECASE
        )
        if match:
            text = match.group(1).strip()

        # 去除 <reasoning>...</reasoning> 标签
        match = re.match(
            r'^<(?:redacted_)?reasoning>\s*(.*?)\s*</(?:redacted_)?reasoning>\s*$',
            text, flags=re.DOTALL | re.IGNORECASE
        )
        if match:
            text = match.group(1).strip()

        return text

    def _generate_key(self, thinking_text: str) -> str:
        """生成缓存 key"""
        import hashlib

        if not thinking_text:
            return ""

        normalized = self._normalize_thinking_text(thinking_text)
        if not normalized:
            return ""

        text_prefix = normalized[:self._key_prefix_length]
        return hashlib.md5(text_prefix.encode('utf-8')).hexdigest()

    def _is_valid_signature(self, signature: str) -> bool:
        """验证 signature 格式"""
        import re

        if not signature or not isinstance(signature, str):
            return False

        if len(signature) < 50:
            return False

        if signature == "skip_thought_signature_validator":
            return False

        if not re.match(r'^[A-Za-z0-9+/=_-]+$', signature):
            return False

        return True

    def _write_to_legacy(
        self,
        thinking_text: str,
        signature: str,
        model: Optional[str]
    ) -> bool:
        """写入旧缓存"""
        key = self._generate_key(thinking_text)
        if not key:
            return False

        with self._legacy_lock:
            entry = AdapterCacheEntry(
                signature=signature,
                thinking_text=thinking_text,
                thinking_text_preview=thinking_text[:200],
                timestamp=time.time(),
                model=model,
            )

            if key in self._legacy_cache:
                del self._legacy_cache[key]

            self._legacy_cache[key] = entry

            # LRU 淘汰
            while len(self._legacy_cache) > self._max_size:
                oldest_key, _ = self._legacy_cache.popitem(last=False)
                self._stats.evictions += 1
                log.debug(f"[LEGACY_ADAPTER] LRU 淘汰: key={oldest_key[:16]}...")

            return True

    def _write_to_new(
        self,
        thinking_text: str,
        signature: str,
        model: Optional[str]
    ) -> bool:
        """写入新缓存"""
        new_cache = self._get_new_cache_manager()
        if not new_cache:
            return False

        try:
            new_cache.cache_signature(
                thinking_text=thinking_text,
                signature=signature,
                model=model,
                namespace=self._namespace,
            )
            return True
        except Exception as e:
            log.error(f"[LEGACY_ADAPTER] 写入新缓存失败: {e}")
            return False

    def _read_from_legacy(self, thinking_text: str) -> Optional[str]:
        """从旧缓存读取"""
        key = self._generate_key(thinking_text)
        if not key:
            return None

        normalized_query = self._normalize_thinking_text(thinking_text)

        with self._legacy_lock:
            if key not in self._legacy_cache:
                return None

            entry = self._legacy_cache[key]

            if entry.is_expired(self._ttl_seconds):
                del self._legacy_cache[key]
                self._stats.expirations += 1
                return None

            # 验证完整文本匹配
            normalized_cached = self._normalize_thinking_text(entry.thinking_text)
            if normalized_query != normalized_cached:
                log.warning(
                    f"[LEGACY_ADAPTER] 哈希冲突检测到: key={key[:16]}..."
                )
                return None

            # 更新访问顺序
            self._legacy_cache.move_to_end(key)
            entry.access_count += 1

            return entry.signature

    def _read_from_new(
        self,
        thinking_text: str,
        namespace: str
    ) -> Optional[str]:
        """从新缓存读取"""
        new_cache = self._get_new_cache_manager()
        if not new_cache:
            return None

        try:
            return new_cache.get_cached_signature(
                thinking_text=thinking_text,
                namespace=namespace,
            )
        except Exception as e:
            log.error(f"[LEGACY_ADAPTER] 从新缓存读取失败: {e}")
            return None

    def shutdown(self) -> None:
        """关闭适配器"""
        if self._new_cache_manager:
            try:
                self._new_cache_manager.shutdown()
                log.info("[LEGACY_ADAPTER] 新缓存管理器已关闭")
            except Exception as e:
                log.error(f"[LEGACY_ADAPTER] 关闭新缓存管理器失败: {e}")


# ==================== 全局实例 ====================

_global_adapter: Optional[LegacySignatureCacheAdapter] = None
_global_adapter_lock = threading.Lock()


def get_legacy_adapter() -> LegacySignatureCacheAdapter:
    """
    获取全局适配器实例（线程安全的单例）

    Returns:
        全局 LegacySignatureCacheAdapter 实例
    """
    global _global_adapter

    if _global_adapter is None:
        with _global_adapter_lock:
            if _global_adapter is None:
                _global_adapter = LegacySignatureCacheAdapter()
                log.info("[LEGACY_ADAPTER] 创建全局适配器实例")

    return _global_adapter


def reset_legacy_adapter() -> None:
    """重置全局适配器实例"""
    global _global_adapter

    with _global_adapter_lock:
        if _global_adapter is not None:
            _global_adapter.shutdown()
            _global_adapter = None
            log.info("[LEGACY_ADAPTER] 重置全局适配器实例")


# ==================== 便捷函数（与 signature_cache.py 保持兼容）====================

def cache_signature_v2(
    thinking_text: str,
    signature: str,
    model: Optional[str] = None
) -> bool:
    """
    缓存 signature（便捷函数，v2 版本）

    Args:
        thinking_text: thinking 块的文本内容
        signature: 对应的 signature 值
        model: 可选的模型名称

    Returns:
        是否成功缓存
    """
    return get_legacy_adapter().set(thinking_text, signature, model)


def get_cached_signature_v2(thinking_text: str) -> Optional[str]:
    """
    获取缓存的 signature（便捷函数，v2 版本）

    Args:
        thinking_text: thinking 块的文本内容

    Returns:
        缓存的 signature
    """
    return get_legacy_adapter().get(thinking_text)


def get_cache_stats_v2() -> Dict[str, Any]:
    """获取缓存统计信息（v2 版本）"""
    return get_legacy_adapter().get_stats()


def get_last_signature_v2() -> Optional[str]:
    """获取最近缓存的 signature（v2 版本）"""
    return get_legacy_adapter().get_last_signature()


def get_last_signature_with_text_v2() -> Optional[Tuple[str, str]]:
    """获取最近缓存的 signature 及其 thinking 文本（v2 版本）"""
    return get_legacy_adapter().get_last_signature_with_text()
