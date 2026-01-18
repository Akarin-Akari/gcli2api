"""
Memory Cache - High-performance L1 memory cache layer
高性能 L1 内存缓存层 - 提供快速内存访问和 LRU 驱逐

This module provides:
    - Fast in-memory caching with OrderedDict
    - LRU eviction policy
    - Thread-safe operations with RWLock
    - TTL-based expiration
    - Namespace and conversation isolation

Architecture:
    - Uses OrderedDict for O(1) access and LRU ordering
    - Implements read-write lock separation for better concurrency
    - Designed to be used as L1 cache in front of SQLite L2 cache
"""

import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
    parse_cache_key,
)


class RWLock:
    """
    Read-Write Lock implementation
    读写锁实现 - 允许多个读取者或单个写入者

    Features:
        - Multiple readers can hold the lock simultaneously
        - Writers have exclusive access
        - Writer preference to prevent starvation
    """

    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    def acquire_read(self) -> None:
        """Acquire read lock"""
        with self._read_ready:
            # Wait if there's an active writer or writers waiting
            while self._writer_active or self._writers_waiting > 0:
                self._read_ready.wait()
            self._readers += 1

    def release_read(self) -> None:
        """Release read lock"""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self) -> None:
        """Acquire write lock"""
        with self._read_ready:
            self._writers_waiting += 1
            try:
                while self._readers > 0 or self._writer_active:
                    self._read_ready.wait()
                self._writer_active = True
            finally:
                self._writers_waiting -= 1

    def release_write(self) -> None:
        """Release write lock"""
        with self._read_ready:
            self._writer_active = False
            self._read_ready.notify_all()

    def read_lock(self):
        """Context manager for read lock"""
        return _ReadLockContext(self)

    def write_lock(self):
        """Context manager for write lock"""
        return _WriteLockContext(self)


class _ReadLockContext:
    """Context manager for read lock"""

    def __init__(self, rwlock: RWLock):
        self._rwlock = rwlock

    def __enter__(self):
        self._rwlock.acquire_read()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._rwlock.release_read()
        return False


class _WriteLockContext:
    """Context manager for write lock"""

    def __init__(self, rwlock: RWLock):
        self._rwlock = rwlock

    def __enter__(self):
        self._rwlock.acquire_write()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._rwlock.release_write()
        return False


class MemoryCache(ICacheLayer):
    """
    High-performance in-memory cache layer (L1 Cache)
    高性能内存缓存层 - 作为 L1 缓存提供快速访问

    Features:
        - O(1) get/set operations with OrderedDict
        - LRU eviction when max_size is reached
        - TTL-based automatic expiration
        - Thread-safe with read-write lock separation
        - Namespace and conversation isolation

    Usage:
        config = CacheConfig(
            max_size=10000,
            ttl_seconds=3600,
            eviction_policy="lru"
        )
        cache = MemoryCache(config)

        # Store entry
        entry = CacheEntry(signature="...", thinking_hash="...")
        cache.set(entry)

        # Retrieve entry
        result = cache.get(thinking_hash, namespace="default")
    """

    def __init__(self, config: Optional[CacheConfig] = None):
        """
        Initialize MemoryCache

        Args:
            config: Cache configuration. If None, uses default config.
        """
        self.config = config or CacheConfig()

        # Main storage: OrderedDict for LRU ordering
        # Key: cache_key (namespace:conversation_id:thinking_hash)
        # Value: CacheEntry
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()

        # Read-write lock for thread safety
        self._rwlock = RWLock()

        # Statistics
        self._stats = CacheStats()
        self._stats_lock = threading.Lock()

        # Last accessed entry (for quick fallback)
        self._last_entry: Optional[CacheEntry] = None
        self._last_entry_lock = threading.Lock()

        log.info(f"[MEMORY_CACHE] Initialized with max_size={self.config.max_size}, "
                f"ttl={self.config.ttl_seconds}s, eviction={self.config.eviction_policy}")

    def get(
        self,
        thinking_hash: str,
        namespace: str = "default",
        conversation_id: Optional[str] = None
    ) -> Optional[CacheEntry]:
        """
        Get cache entry by thinking hash

        Args:
            thinking_hash: Hash of the thinking text
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID

        Returns:
            CacheEntry if found and not expired, None otherwise
        """
        cache_key = build_cache_key(thinking_hash, namespace, conversation_id)

        with self._rwlock.read_lock():
            if cache_key not in self._cache:
                with self._stats_lock:
                    self._stats.misses += 1
                return None

            entry = self._cache[cache_key]

            # Check expiration
            if entry.is_expired():
                with self._stats_lock:
                    self._stats.misses += 1
                    self._stats.expirations += 1
                # Schedule deletion (will be done on next write or cleanup)
                return None

        # Move to end for LRU (requires write lock)
        with self._rwlock.write_lock():
            if cache_key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(cache_key)

                # Update access statistics
                entry = self._cache[cache_key]
                entry.touch()

                # Update last entry
                with self._last_entry_lock:
                    self._last_entry = entry

        with self._stats_lock:
            self._stats.hits += 1

        log.debug(f"[MEMORY_CACHE] Cache hit: key={cache_key[:50]}...")
        return entry

    def set(
        self,
        entry: CacheEntry,
        update_if_exists: bool = True
    ) -> bool:
        """
        Set cache entry

        Args:
            entry: Cache entry to store
            update_if_exists: If True, update existing entry; if False, skip if exists

        Returns:
            True if entry was stored successfully
        """
        # Calculate expiration if TTL is configured
        if self.config.ttl_seconds > 0 and entry.expires_at is None:
            entry.expires_at = datetime.now() + timedelta(seconds=self.config.ttl_seconds)

        cache_key = build_cache_key(
            entry.thinking_hash,
            entry.namespace,
            entry.conversation_id
        )

        with self._rwlock.write_lock():
            # Check if entry exists
            if cache_key in self._cache:
                if not update_if_exists:
                    return False
                # Update existing entry
                self._cache[cache_key] = entry
                # Move to end for LRU
                self._cache.move_to_end(cache_key)
            else:
                # Check capacity and evict if necessary
                while len(self._cache) >= self.config.max_size > 0:
                    self._evict_one()

                # Add new entry
                self._cache[cache_key] = entry

            # Update last entry
            with self._last_entry_lock:
                self._last_entry = entry

        with self._stats_lock:
            self._stats.total_writes += 1

        log.debug(f"[MEMORY_CACHE] Entry stored: hash={entry.thinking_hash[:16]}...")
        return True

    def _evict_one(self) -> Optional[CacheEntry]:
        """
        Evict one entry based on eviction policy

        Must be called with write lock held.

        Returns:
            Evicted entry or None
        """
        if not self._cache:
            return None

        if self.config.eviction_policy == "lru":
            # Remove least recently used (first item)
            cache_key, entry = self._cache.popitem(last=False)
        elif self.config.eviction_policy == "fifo":
            # Remove first inserted (first item)
            cache_key, entry = self._cache.popitem(last=False)
        elif self.config.eviction_policy == "lfu":
            # Remove least frequently used
            min_access = float('inf')
            lfu_key = None
            for key, e in self._cache.items():
                if e.access_count < min_access:
                    min_access = e.access_count
                    lfu_key = key
            if lfu_key:
                entry = self._cache.pop(lfu_key)
                cache_key = lfu_key
            else:
                return None
        else:
            # Default to LRU
            cache_key, entry = self._cache.popitem(last=False)

        with self._stats_lock:
            self._stats.evictions += 1

        log.debug(f"[MEMORY_CACHE] Evicted entry: key={cache_key[:50]}...")
        return entry

    def delete(
        self,
        thinking_hash: str,
        namespace: str = "default",
        conversation_id: Optional[str] = None
    ) -> bool:
        """
        Delete cache entry

        Args:
            thinking_hash: Hash of the thinking text
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID

        Returns:
            True if entry was deleted, False if not found
        """
        cache_key = build_cache_key(thinking_hash, namespace, conversation_id)

        with self._rwlock.write_lock():
            if cache_key not in self._cache:
                return False

            del self._cache[cache_key]

        with self._stats_lock:
            self._stats.total_deletes += 1

        log.debug(f"[MEMORY_CACHE] Entry deleted: key={cache_key[:50]}...")
        return True

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
            Number of entries cleared
        """
        with self._rwlock.write_lock():
            if namespace is None and conversation_id is None:
                # Clear all
                count = len(self._cache)
                self._cache.clear()
                with self._last_entry_lock:
                    self._last_entry = None
            else:
                # Clear by filter
                keys_to_delete = []
                for cache_key in self._cache:
                    try:
                        ns, conv_id, _ = parse_cache_key(cache_key)
                        if namespace is not None and ns != namespace:
                            continue
                        if conversation_id is not None and conv_id != conversation_id:
                            continue
                        keys_to_delete.append(cache_key)
                    except ValueError:
                        continue

                for key in keys_to_delete:
                    del self._cache[key]
                count = len(keys_to_delete)

        with self._stats_lock:
            self._stats.total_deletes += count

        log.info(f"[MEMORY_CACHE] Cleared {count} entries "
                f"(namespace={namespace}, conversation_id={conversation_id})")
        return count

    def exists(
        self,
        thinking_hash: str,
        namespace: str = "default",
        conversation_id: Optional[str] = None
    ) -> bool:
        """
        Check if entry exists (without updating access stats)

        Args:
            thinking_hash: Hash of the thinking text
            namespace: Namespace for isolation
            conversation_id: Optional conversation ID

        Returns:
            True if entry exists and is not expired
        """
        cache_key = build_cache_key(thinking_hash, namespace, conversation_id)

        with self._rwlock.read_lock():
            if cache_key not in self._cache:
                return False

            entry = self._cache[cache_key]
            return not entry.is_expired()

    def get_stats(self) -> CacheStats:
        """
        Get cache statistics

        Returns:
            CacheStats object with current statistics
        """
        with self._rwlock.read_lock():
            current_size = len(self._cache)

            # Calculate age of oldest and newest entries
            oldest_age = 0.0
            newest_age = 0.0
            now = datetime.now()

            if self._cache:
                # First item is oldest (due to OrderedDict ordering)
                first_key = next(iter(self._cache))
                oldest_entry = self._cache[first_key]
                if oldest_entry.created_at:
                    oldest_age = (now - oldest_entry.created_at).total_seconds()

                # Last item is newest
                last_key = next(reversed(self._cache))
                newest_entry = self._cache[last_key]
                if newest_entry.created_at:
                    newest_age = (now - newest_entry.created_at).total_seconds()

        with self._stats_lock:
            stats = CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                expirations=self._stats.expirations,
                current_size=current_size,
                max_size=self.config.max_size,
                total_writes=self._stats.total_writes,
                total_deletes=self._stats.total_deletes,
                oldest_entry_age_seconds=oldest_age,
                newest_entry_age_seconds=newest_age,
            )

        return stats

    def cleanup_expired(self) -> int:
        """
        Remove expired entries

        Returns:
            Number of entries removed
        """
        keys_to_delete = []

        with self._rwlock.read_lock():
            for cache_key, entry in self._cache.items():
                if entry.is_expired():
                    keys_to_delete.append(cache_key)

        if not keys_to_delete:
            return 0

        with self._rwlock.write_lock():
            count = 0
            for cache_key in keys_to_delete:
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    count += 1

        with self._stats_lock:
            self._stats.expirations += count
            self._stats.total_deletes += count

        if count > 0:
            log.info(f"[MEMORY_CACHE] Cleaned up {count} expired entries")

        return count

    def size(self) -> int:
        """
        Get current number of entries

        Returns:
            Number of entries in cache
        """
        with self._rwlock.read_lock():
            return len(self._cache)

    # ==================== Extended Methods ====================

    def get_by_prefix(
        self,
        thinking_prefix: str,
        namespace: str = "default",
        limit: int = 10
    ) -> List[CacheEntry]:
        """
        Get entries by thinking text prefix (for debugging)

        Args:
            thinking_prefix: Prefix to search for
            namespace: Namespace for isolation
            limit: Maximum number of entries to return

        Returns:
            List of matching cache entries
        """
        results = []

        with self._rwlock.read_lock():
            for cache_key, entry in self._cache.items():
                if entry.namespace != namespace:
                    continue
                if not entry.thinking_prefix.startswith(thinking_prefix):
                    continue
                if entry.is_expired():
                    continue

                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    def get_recent(
        self,
        namespace: str = "default",
        limit: int = 10
    ) -> List[CacheEntry]:
        """
        Get most recently accessed entries

        Args:
            namespace: Namespace for isolation
            limit: Maximum number of entries to return

        Returns:
            List of recent cache entries
        """
        results = []

        with self._rwlock.read_lock():
            # Iterate in reverse order (most recent first)
            for cache_key in reversed(self._cache):
                entry = self._cache[cache_key]
                if entry.namespace != namespace:
                    continue
                if entry.is_expired():
                    continue

                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    def bulk_set(
        self,
        entries: List[CacheEntry],
        update_if_exists: bool = True
    ) -> int:
        """
        Bulk set multiple entries

        Args:
            entries: List of cache entries to store
            update_if_exists: If True, update existing entries

        Returns:
            Number of entries successfully stored
        """
        if not entries:
            return 0

        count = 0

        with self._rwlock.write_lock():
            for entry in entries:
                # Calculate expiration if TTL is configured
                if self.config.ttl_seconds > 0 and entry.expires_at is None:
                    entry.expires_at = datetime.now() + timedelta(seconds=self.config.ttl_seconds)

                cache_key = build_cache_key(
                    entry.thinking_hash,
                    entry.namespace,
                    entry.conversation_id
                )

                if cache_key in self._cache:
                    if not update_if_exists:
                        continue
                    self._cache[cache_key] = entry
                    self._cache.move_to_end(cache_key)
                else:
                    # Check capacity
                    while len(self._cache) >= self.config.max_size > 0:
                        self._evict_one()

                    self._cache[cache_key] = entry

                count += 1

            # Update last entry with the most recent one
            if entries:
                with self._last_entry_lock:
                    self._last_entry = entries[-1]

        with self._stats_lock:
            self._stats.total_writes += count

        log.debug(f"[MEMORY_CACHE] Bulk set {count}/{len(entries)} entries")
        return count

    def bulk_delete(
        self,
        thinking_hashes: List[str],
        namespace: str = "default"
    ) -> int:
        """
        Bulk delete multiple entries

        Args:
            thinking_hashes: List of hashes to delete
            namespace: Namespace for isolation

        Returns:
            Number of entries deleted
        """
        if not thinking_hashes:
            return 0

        cache_keys = [
            build_cache_key(h, namespace, None)
            for h in thinking_hashes
        ]

        count = 0

        with self._rwlock.write_lock():
            for cache_key in cache_keys:
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    count += 1

        with self._stats_lock:
            self._stats.total_deletes += count

        log.debug(f"[MEMORY_CACHE] Bulk deleted {count}/{len(thinking_hashes)} entries")
        return count

    def get_last_entry(self) -> Optional[CacheEntry]:
        """
        Get the last accessed/stored entry

        This is useful for fallback when exact match fails.

        Returns:
            Last entry or None
        """
        with self._last_entry_lock:
            return self._last_entry

    def get_by_thinking_hash_any_namespace(
        self,
        thinking_hash: str
    ) -> Optional[CacheEntry]:
        """
        Get entry by thinking hash, ignoring namespace and conversation_id.
        This is useful for fallback lookup when exact namespace match fails.

        Args:
            thinking_hash: Hash of the thinking text

        Returns:
            CacheEntry if found and not expired, None otherwise
        """
        with self._rwlock.read_lock():
            for cache_key, entry in reversed(self._cache.items()):
                if entry.thinking_hash == thinking_hash:
                    if not entry.is_expired():
                        log.debug(f"[MEMORY_CACHE] Fallback cache hit: hash={thinking_hash[:16]}...")
                        return entry

        return None

    def warm_up(self, entries: List[CacheEntry]) -> int:
        """
        Warm up cache with pre-existing entries (e.g., from L2 cache)

        Args:
            entries: List of entries to load into cache

        Returns:
            Number of entries loaded
        """
        return self.bulk_set(entries, update_if_exists=False)

    def get_all_entries(self, namespace: Optional[str] = None) -> List[CacheEntry]:
        """
        Get all entries, optionally filtered by namespace

        Args:
            namespace: If provided, only return entries in this namespace

        Returns:
            List of all (non-expired) cache entries
        """
        results = []

        with self._rwlock.read_lock():
            for entry in self._cache.values():
                if namespace is not None and entry.namespace != namespace:
                    continue
                if entry.is_expired():
                    continue
                results.append(entry)

        return results

    def reset_stats(self) -> None:
        """Reset cache statistics"""
        with self._stats_lock:
            self._stats = CacheStats()
        log.info("[MEMORY_CACHE] Statistics reset")

