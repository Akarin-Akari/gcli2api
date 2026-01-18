"""
Signature Cache Manager - Layered cache orchestrator
分层缓存管理器 - 协调 L1 内存缓存和 L2 SQLite 持久化层

This module provides:
    - Unified cache access interface
    - L1/L2 layered caching strategy
    - Write-through to L2 with optional async queue
    - Cache warm-up from L2 to L1
    - Fallback lookup across namespaces
    - Statistics aggregation

Architecture:
    Read Path:  L1 (Memory) -> L2 (SQLite) -> Miss
    Write Path: L1 (Memory) -> L2 (SQLite) [sync/async]
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# 支持多种导入方式 - log.py 在 gcli2api/ 目录下
import sys
import os as _os
_cache_dir = _os.path.dirname(_os.path.abspath(__file__))  # cache/
_src_dir = _os.path.dirname(_cache_dir)  # src/
_project_dir = _os.path.dirname(_src_dir)  # gcli2api/
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)
from log import log

from .cache_interface import (
    CacheConfig,
    CacheEntry,
    CacheStats,
    ICacheLayer,
    build_cache_key,
    generate_thinking_hash,
)
from .memory_cache import MemoryCache
from .signature_database import SignatureDatabase


@dataclass
class LayeredCacheConfig:
    """
    Configuration for layered cache manager
    分层缓存管理器配置

    Attributes:
        l1_config: Configuration for L1 memory cache
        l2_config: Configuration for L2 SQLite database
        enable_l2: Whether to enable L2 persistent cache
        async_write: Whether to use async write queue for L2
        write_through: Whether to write through to L2 on every set
        warm_up_on_start: Whether to warm up L1 from L2 on startup
        warm_up_limit: Maximum entries to load during warm-up
        fallback_any_namespace: Try fallback lookup ignoring namespace
        namespace: Default namespace for this cache instance
    """
    l1_config: Optional[CacheConfig] = None
    l2_config: Optional[CacheConfig] = None
    enable_l2: bool = True
    async_write: bool = True
    write_through: bool = True
    warm_up_on_start: bool = False
    warm_up_limit: int = 1000
    fallback_any_namespace: bool = True
    namespace: str = "default"


@dataclass
class AggregatedStats:
    """
    Aggregated statistics from all cache layers
    聚合统计信息
    """
    l1_stats: CacheStats = field(default_factory=CacheStats)
    l2_stats: CacheStats = field(default_factory=CacheStats)
    l1_to_l2_promotions: int = 0
    l2_fallback_hits: int = 0
    total_requests: int = 0
    async_queue_size: int = 0
    async_queue_pending: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "l1": self.l1_stats.to_dict(),
            "l2": self.l2_stats.to_dict() if self.l2_stats else None,
            "l1_to_l2_promotions": self.l1_to_l2_promotions,
            "l2_fallback_hits": self.l2_fallback_hits,
            "total_requests": self.total_requests,
            "async_queue_size": self.async_queue_size,
            "async_queue_pending": self.async_queue_pending,
            "overall_hit_rate": self._calculate_overall_hit_rate(),
        }

    def _calculate_overall_hit_rate(self) -> float:
        """Calculate overall hit rate across all layers"""
        total_hits = self.l1_stats.hits + self.l2_fallback_hits
        if self.total_requests == 0:
            return 0.0
        return total_hits / self.total_requests


class SignatureCacheManager:
    """
    Layered Cache Manager - Coordinates L1 Memory and L2 SQLite caches
    分层缓存管理器 - 协调内存缓存和SQLite持久化层

    Features:
        - Unified interface for cache operations
        - L1 (fast memory) + L2 (persistent SQLite) architecture
        - Write-through with optional async queue
        - Automatic L1 warm-up from L2
        - Fallback lookup across namespaces
        - Thread-safe operations

    Read Strategy:
        1. Try L1 (memory cache)
        2. If miss, try L2 (SQLite)
        3. If L2 hit, promote to L1
        4. If both miss, try fallback lookup (any namespace)

    Write Strategy:
        1. Always write to L1
        2. Write-through to L2 (sync or async)

    Usage:
        config = LayeredCacheConfig(
            l1_config=CacheConfig(max_size=10000),
            l2_config=CacheConfig(db_path="cache.db"),
            enable_l2=True,
            async_write=True
        )
        manager = SignatureCacheManager(config)

        # Store signature
        manager.cache_signature(
            thinking_text="...",
            signature="...",
            model="claude-3",
            namespace="anthropic"
        )

        # Retrieve signature
        result = manager.get_cached_signature(
            thinking_text="...",
            namespace="anthropic"
        )
    """

    # Singleton instance
    _instance: Optional["SignatureCacheManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, config: Optional[LayeredCacheConfig] = None):
        """Singleton pattern implementation"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, config: Optional[LayeredCacheConfig] = None):
        """
        Initialize SignatureCacheManager

        Args:
            config: Layered cache configuration
        """
        # Prevent re-initialization
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.config = config or LayeredCacheConfig()

        # Initialize L1 memory cache
        l1_config = self.config.l1_config or CacheConfig(
            max_size=10000,
            ttl_seconds=3600,
            eviction_policy="lru"
        )
        self._l1_cache = MemoryCache(l1_config)

        # Initialize L2 SQLite cache if enabled
        self._l2_cache: Optional[SignatureDatabase] = None
        if self.config.enable_l2:
            l2_config = self.config.l2_config or CacheConfig(
                ttl_seconds=86400 * 7,  # 7 days default
                wal_mode=True
            )
            self._l2_cache = SignatureDatabase(l2_config)

        # Async write queue (will be set by external module)
        self._async_queue: Optional[Any] = None

        # Statistics
        self._stats = AggregatedStats()
        self._stats_lock = threading.Lock()

        # Default namespace
        self._default_namespace = self.config.namespace

        # Mark as initialized
        self._initialized = True

        log.info(f"[CACHE_MANAGER] Initialized with L1={True}, L2={self.config.enable_l2}, "
                f"async_write={self.config.async_write}, namespace={self._default_namespace}")

        # Warm up L1 from L2 if configured
        if self.config.warm_up_on_start and self._l2_cache:
            self._warm_up_l1()

    @classmethod
    def get_instance(cls, config: Optional[LayeredCacheConfig] = None) -> "SignatureCacheManager":
        """
        Get singleton instance

        Args:
            config: Configuration (only used on first call)

        Returns:
            SignatureCacheManager instance
        """
        return cls(config)

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)"""
        with cls._instance_lock:
            if cls._instance is not None:
                # Close L2 connection if exists
                if hasattr(cls._instance, '_l2_cache') and cls._instance._l2_cache:
                    cls._instance._l2_cache.close()
                cls._instance = None

    def set_async_queue(self, queue: Any) -> None:
        """
        Set async write queue

        Args:
            queue: AsyncWriteQueue instance
        """
        self._async_queue = queue
        log.info("[CACHE_MANAGER] Async write queue configured")

    # ==================== Main API ====================

    def cache_signature(
        self,
        thinking_text: str,
        signature: str,
        model: str = "unknown",
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Cache a signature for thinking text

        This is the main write API. It writes to L1 immediately and
        schedules/performs L2 write based on configuration.

        Args:
            thinking_text: The thinking text to cache
            signature: The signature to store
            model: Model name that generated this signature
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID
            metadata: Optional additional metadata

        Returns:
            True if caching was successful (at least to L1)
        """
        namespace = namespace or self._default_namespace

        # Generate hash and prefix
        thinking_hash, thinking_prefix = generate_thinking_hash(
            thinking_text,
            prefix_length=self.config.l1_config.key_prefix_length if self.config.l1_config else 500
        )

        # Create cache entry
        entry = CacheEntry(
            signature=signature,
            thinking_hash=thinking_hash,
            thinking_prefix=thinking_prefix,
            model=model,
            namespace=namespace,
            conversation_id=conversation_id,
            created_at=datetime.now(),
            metadata=metadata or {}
        )

        # Write to L1 (always synchronous)
        l1_success = self._l1_cache.set(entry)

        if not l1_success:
            log.warning(f"[CACHE_MANAGER] Failed to write to L1: hash={thinking_hash[:16]}...")
            return False

        # Write to L2 if enabled
        if self.config.enable_l2 and self._l2_cache:
            if self.config.async_write and self._async_queue:
                # Async write via queue
                self._async_queue.enqueue(entry)
            elif self.config.write_through:
                # Synchronous write-through
                l2_success = self._l2_cache.set(entry)
                if not l2_success:
                    log.warning(f"[CACHE_MANAGER] Failed to write-through to L2: hash={thinking_hash[:16]}...")

        log.debug(f"[CACHE_MANAGER] Signature cached: hash={thinking_hash[:16]}..., "
                 f"model={model}, namespace={namespace}")
        return True

    def get_cached_signature(
        self,
        thinking_text: str,
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None,
        include_metadata: bool = False
    ) -> Optional[str | Tuple[str, Dict[str, Any]]]:
        """
        Get cached signature for thinking text

        This is the main read API. It follows the layered lookup strategy:
        L1 -> L2 -> Fallback (any namespace)

        Args:
            thinking_text: The thinking text to look up
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID
            include_metadata: If True, return (signature, metadata) tuple

        Returns:
            Signature string, or (signature, metadata) tuple if include_metadata=True,
            or None if not found
        """
        namespace = namespace or self._default_namespace

        # Generate hash
        thinking_hash, _ = generate_thinking_hash(thinking_text)

        with self._stats_lock:
            self._stats.total_requests += 1

        # Try L1 first
        entry = self._l1_cache.get(thinking_hash, namespace, conversation_id)

        if entry:
            log.debug(f"[CACHE_MANAGER] L1 hit: hash={thinking_hash[:16]}...")
            return self._format_result(entry, include_metadata)

        # Try L2 if enabled
        if self.config.enable_l2 and self._l2_cache:
            entry = self._l2_cache.get(thinking_hash, namespace, conversation_id)

            if entry:
                # Promote to L1
                self._l1_cache.set(entry, update_if_exists=False)
                with self._stats_lock:
                    self._stats.l1_to_l2_promotions += 1
                    self._stats.l2_fallback_hits += 1

                log.debug(f"[CACHE_MANAGER] L2 hit (promoted to L1): hash={thinking_hash[:16]}...")
                return self._format_result(entry, include_metadata)

        # Try fallback lookup (any namespace) if enabled
        if self.config.fallback_any_namespace:
            entry = self._fallback_lookup(thinking_hash)

            if entry:
                with self._stats_lock:
                    self._stats.l2_fallback_hits += 1

                log.debug(f"[CACHE_MANAGER] Fallback hit: hash={thinking_hash[:16]}..., "
                         f"original_namespace={entry.namespace}")
                return self._format_result(entry, include_metadata)

        log.debug(f"[CACHE_MANAGER] Cache miss: hash={thinking_hash[:16]}...")
        return None

    def get_last_signature(
        self,
        include_metadata: bool = False
    ) -> Optional[str | Tuple[str, Dict[str, Any]]]:
        """
        Get the last cached signature

        Useful for fallback when exact match fails.

        Args:
            include_metadata: If True, return (signature, metadata) tuple

        Returns:
            Signature string or tuple, or None if no entries
        """
        entry = self._l1_cache.get_last_entry()

        if entry:
            return self._format_result(entry, include_metadata)

        return None

    def _format_result(
        self,
        entry: CacheEntry,
        include_metadata: bool
    ) -> str | Tuple[str, Dict[str, Any]]:
        """Format result based on include_metadata flag"""
        if include_metadata:
            return entry.signature, entry.metadata
        return entry.signature

    def _fallback_lookup(self, thinking_hash: str) -> Optional[CacheEntry]:
        """
        Fallback lookup ignoring namespace

        Tries L1 first, then L2.

        Args:
            thinking_hash: Hash to look up

        Returns:
            CacheEntry if found, None otherwise
        """
        # Try L1 fallback
        entry = self._l1_cache.get_by_thinking_hash_any_namespace(thinking_hash)
        if entry:
            return entry

        # Try L2 fallback if enabled
        if self.config.enable_l2 and self._l2_cache:
            entry = self._l2_cache.get_by_thinking_hash_any_namespace(thinking_hash)
            if entry:
                # Promote to L1
                self._l1_cache.set(entry, update_if_exists=False)
                return entry

        return None

    # ==================== Cache Management ====================

    def delete(
        self,
        thinking_text: str,
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None
    ) -> bool:
        """
        Delete cache entry

        Args:
            thinking_text: The thinking text to delete
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID

        Returns:
            True if entry was deleted from at least one layer
        """
        namespace = namespace or self._default_namespace
        thinking_hash, _ = generate_thinking_hash(thinking_text)

        l1_deleted = self._l1_cache.delete(thinking_hash, namespace, conversation_id)

        l2_deleted = False
        if self.config.enable_l2 and self._l2_cache:
            l2_deleted = self._l2_cache.delete(thinking_hash, namespace, conversation_id)

        if l1_deleted or l2_deleted:
            log.debug(f"[CACHE_MANAGER] Entry deleted: hash={thinking_hash[:16]}..., "
                     f"L1={l1_deleted}, L2={l2_deleted}")
            return True

        return False

    def clear(
        self,
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None
    ) -> int:
        """
        Clear cache entries

        Args:
            namespace: If provided, only clear entries in this namespace
            conversation_id: If provided, only clear entries for this conversation

        Returns:
            Total number of entries cleared
        """
        l1_count = self._l1_cache.clear(namespace, conversation_id)

        l2_count = 0
        if self.config.enable_l2 and self._l2_cache:
            l2_count = self._l2_cache.clear(namespace, conversation_id)

        total = l1_count + l2_count
        log.info(f"[CACHE_MANAGER] Cleared {total} entries (L1={l1_count}, L2={l2_count})")
        return total

    def exists(
        self,
        thinking_text: str,
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None
    ) -> bool:
        """
        Check if entry exists in cache

        Args:
            thinking_text: The thinking text to check
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID

        Returns:
            True if entry exists in any layer
        """
        namespace = namespace or self._default_namespace
        thinking_hash, _ = generate_thinking_hash(thinking_text)

        if self._l1_cache.exists(thinking_hash, namespace, conversation_id):
            return True

        if self.config.enable_l2 and self._l2_cache:
            return self._l2_cache.exists(thinking_hash, namespace, conversation_id)

        return False

    def cleanup_expired(self) -> int:
        """
        Remove expired entries from all layers

        Returns:
            Total number of entries removed
        """
        l1_count = self._l1_cache.cleanup_expired()

        l2_count = 0
        if self.config.enable_l2 and self._l2_cache:
            l2_count = self._l2_cache.cleanup_expired()

        total = l1_count + l2_count
        if total > 0:
            log.info(f"[CACHE_MANAGER] Cleaned up {total} expired entries (L1={l1_count}, L2={l2_count})")

        return total

    # ==================== Statistics ====================

    def get_stats(self) -> AggregatedStats:
        """
        Get aggregated statistics from all layers

        Returns:
            AggregatedStats object
        """
        l1_stats = self._l1_cache.get_stats()

        l2_stats = None
        if self.config.enable_l2 and self._l2_cache:
            l2_stats = self._l2_cache.get_stats()

        async_queue_size = 0
        async_queue_pending = 0
        if self._async_queue:
            async_queue_size = getattr(self._async_queue, 'queue_size', 0)
            async_queue_pending = getattr(self._async_queue, 'pending_count', 0)

        with self._stats_lock:
            stats = AggregatedStats(
                l1_stats=l1_stats,
                l2_stats=l2_stats or CacheStats(),
                l1_to_l2_promotions=self._stats.l1_to_l2_promotions,
                l2_fallback_hits=self._stats.l2_fallback_hits,
                total_requests=self._stats.total_requests,
                async_queue_size=async_queue_size,
                async_queue_pending=async_queue_pending,
            )

        return stats

    def reset_stats(self) -> None:
        """Reset all statistics"""
        self._l1_cache.reset_stats()

        with self._stats_lock:
            self._stats = AggregatedStats()

        log.info("[CACHE_MANAGER] Statistics reset")

    # ==================== Size Information ====================

    def size(self) -> Dict[str, int]:
        """
        Get current size of each layer

        Returns:
            Dict with l1_size, l2_size, and total
        """
        l1_size = self._l1_cache.size()

        l2_size = 0
        if self.config.enable_l2 and self._l2_cache:
            l2_size = self._l2_cache.size()

        return {
            "l1_size": l1_size,
            "l2_size": l2_size,
            "total": l1_size + l2_size,
        }

    # ==================== Warm-up and Maintenance ====================

    def _warm_up_l1(self) -> int:
        """
        Warm up L1 cache from L2

        Returns:
            Number of entries loaded
        """
        if not self._l2_cache:
            return 0

        try:
            # Get recent entries from L2
            entries = self._l2_cache.get_recent(
                namespace=self._default_namespace,
                limit=self.config.warm_up_limit
            )

            if not entries:
                log.info("[CACHE_MANAGER] No entries to warm up from L2")
                return 0

            # Load into L1
            count = self._l1_cache.warm_up(entries)

            log.info(f"[CACHE_MANAGER] Warmed up L1 with {count} entries from L2")
            return count

        except Exception as e:
            log.error(f"[CACHE_MANAGER] Error during warm-up: {e}")
            return 0

    def sync_l1_to_l2(self) -> int:
        """
        Sync all L1 entries to L2

        Useful for ensuring persistence before shutdown.

        Returns:
            Number of entries synced
        """
        if not self.config.enable_l2 or not self._l2_cache:
            return 0

        try:
            entries = self._l1_cache.get_all_entries()

            if not entries:
                return 0

            count = self._l2_cache.bulk_set(entries, update_if_exists=True)

            log.info(f"[CACHE_MANAGER] Synced {count} entries from L1 to L2")
            return count

        except Exception as e:
            log.error(f"[CACHE_MANAGER] Error during L1->L2 sync: {e}")
            return 0

    def vacuum_l2(self) -> None:
        """
        Optimize L2 database by reclaiming space

        Should be called during maintenance windows.
        """
        if self.config.enable_l2 and self._l2_cache:
            self._l2_cache.vacuum()

    # ==================== Lifecycle ====================

    def shutdown(self, sync_before_close: bool = True) -> None:
        """
        Gracefully shutdown the cache manager

        Args:
            sync_before_close: If True, sync L1 to L2 before closing
        """
        log.info("[CACHE_MANAGER] Shutting down...")

        # Stop async queue if exists
        if self._async_queue:
            try:
                self._async_queue.stop(wait=True)
            except Exception as e:
                log.error(f"[CACHE_MANAGER] Error stopping async queue: {e}")

        # Sync L1 to L2 if requested
        if sync_before_close:
            self.sync_l1_to_l2()

        # Close L2 connection
        if self._l2_cache:
            self._l2_cache.close()

        log.info("[CACHE_MANAGER] Shutdown complete")

    def __del__(self):
        """Cleanup on deletion"""
        # Note: Don't call shutdown here as it may cause issues
        # during interpreter shutdown
        pass


# ==================== Convenience Functions ====================

def get_cache_manager(
    config: Optional[LayeredCacheConfig] = None
) -> SignatureCacheManager:
    """
    Get the singleton cache manager instance

    Args:
        config: Configuration (only used on first call)

    Returns:
        SignatureCacheManager singleton instance
    """
    return SignatureCacheManager.get_instance(config)


def cache_signature(
    thinking_text: str,
    signature: str,
    model: str = "unknown",
    namespace: str = "default",
    conversation_id: Optional[str] = None
) -> bool:
    """
    Convenience function to cache a signature

    Args:
        thinking_text: The thinking text to cache
        signature: The signature to store
        model: Model name
        namespace: Namespace for isolation
        conversation_id: Optional conversation ID

    Returns:
        True if successful
    """
    manager = get_cache_manager()
    return manager.cache_signature(
        thinking_text=thinking_text,
        signature=signature,
        model=model,
        namespace=namespace,
        conversation_id=conversation_id
    )


def get_cached_signature(
    thinking_text: str,
    namespace: str = "default",
    conversation_id: Optional[str] = None
) -> Optional[str]:
    """
    Convenience function to get a cached signature

    Args:
        thinking_text: The thinking text to look up
        namespace: Namespace for isolation
        conversation_id: Optional conversation ID

    Returns:
        Signature string or None
    """
    manager = get_cache_manager()
    return manager.get_cached_signature(
        thinking_text=thinking_text,
        namespace=namespace,
        conversation_id=conversation_id
    )


def get_last_signature() -> Optional[str]:
    """
    Convenience function to get the last cached signature

    Returns:
        Last signature or None
    """
    manager = get_cache_manager()
    return manager.get_last_signature()
