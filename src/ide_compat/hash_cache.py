"""
Content Hash Cache - IDE compatibility layer for signature caching
内容哈希缓存 - 用于 IDE 兼容性的签名缓存层

This module provides:
    - Content-based hash caching for thinking blocks
    - Normalization to handle IDE text transformations
    - Prefix matching for truncated content
    - Fast in-memory lookup with LRU eviction

Design Purpose:
    - IDEs may transform thinking text (add spaces, newlines, etc.)
    - Through normalize + hash, we can match transformed content
    - Prefix matching handles truncated thinking blocks

Architecture:
    - L1: In-memory cache (fast access)
    - Optional L2: SQLite persistence (future enhancement)

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

import hashlib
import re
import threading
import time
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List

log = logging.getLogger("gcli2api.hash_cache")


@dataclass
class HashCacheEntry:
    """
    Hash cache entry data structure
    哈希缓存条目数据结构

    Attributes:
        content_hash: SHA256 hash of the thinking text (exact)
        normalized_hash: SHA256 hash of normalized thinking text
        signature: The signature string
        thinking_text: Original thinking text (for validation)
        thinking_prefix: First 200 chars (for debugging)
        created_at: Timestamp when entry was created
        expires_at: Timestamp when entry expires
        access_count: Number of times accessed
        last_accessed_at: Last access timestamp
    """
    content_hash: str
    normalized_hash: str
    signature: str
    thinking_text: str
    thinking_prefix: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        """Check if entry has expired"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def touch(self) -> None:
        """Update access statistics"""
        self.access_count += 1
        self.last_accessed_at = datetime.now()


@dataclass
class HashCacheStats:
    """
    Hash cache statistics
    哈希缓存统计信息
    """
    exact_hits: int = 0
    normalized_hits: int = 0
    prefix_hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    total_writes: int = 0
    current_size: int = 0
    max_size: int = 0

    @property
    def total_hits(self) -> int:
        """Total cache hits"""
        return self.exact_hits + self.normalized_hits + self.prefix_hits

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate"""
        total = self.total_hits + self.misses
        if total == 0:
            return 0.0
        return self.total_hits / total

    def to_dict(self) -> Dict[str, any]:
        """Convert to dictionary"""
        return {
            "exact_hits": self.exact_hits,
            "normalized_hits": self.normalized_hits,
            "prefix_hits": self.prefix_hits,
            "total_hits": self.total_hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "total_writes": self.total_writes,
            "current_size": self.current_size,
            "max_size": self.max_size,
            "hit_rate": self.hit_rate,
        }


class ContentHashCache:
    """
    Content Hash Cache - Fast signature lookup by thinking text hash
    内容哈希缓存 - 通过 thinking 文本哈希快速查找签名

    Design Purpose:
        - IDE may transform thinking text (add spaces, newlines, etc.)
        - Through normalize + hash, we can match transformed content
        - Prefix matching handles truncated thinking blocks

    Architecture:
        - L1: In-memory cache (fast access)
        - Dual hash strategy: exact + normalized
        - Prefix matching as fallback
        - LRU eviction policy

    Thread Safety:
        - All operations are thread-safe with threading.Lock

    Usage:
        cache = ContentHashCache(max_size=10000, ttl_seconds=3600)

        # Store signature
        cache.set(thinking_text="Let me think...", signature="EqQBCg...")

        # Retrieve signature (exact match)
        sig = cache.get(thinking_text="Let me think...")

        # Retrieve signature (normalized match)
        sig = cache.get(thinking_text="Let  me   think...")

        # Retrieve signature (prefix match)
        sig = cache.get_with_prefix_match(thinking_text="Let me think", min_prefix_len=10)
    """

    # Default configuration
    DEFAULT_MAX_SIZE = 10000
    DEFAULT_TTL_SECONDS = 3600  # 1 hour
    DEFAULT_MIN_PREFIX_LENGTH = 100  # Minimum prefix length for prefix matching

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        min_prefix_length: int = DEFAULT_MIN_PREFIX_LENGTH
    ):
        """
        Initialize ContentHashCache

        Args:
            max_size: Maximum number of cache entries (LRU eviction when exceeded)
            ttl_seconds: Time-to-live in seconds (0 = never expires)
            min_prefix_length: Minimum prefix length for prefix matching
        """
        # Main cache: hash -> entry
        # Uses OrderedDict for LRU ordering
        self._exact_cache: OrderedDict[str, HashCacheEntry] = OrderedDict()
        self._normalized_cache: OrderedDict[str, HashCacheEntry] = OrderedDict()

        # Prefix index: prefix_hash -> list of entries
        # For fast prefix matching
        self._prefix_index: Dict[str, List[HashCacheEntry]] = {}

        # Configuration
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._min_prefix_length = min_prefix_length

        # Thread safety
        self._lock = threading.Lock()

        # Statistics
        self._stats = HashCacheStats()
        self._stats.max_size = max_size

        log.info(
            f"[HASH_CACHE] Initialized: max_size={max_size}, "
            f"ttl_seconds={ttl_seconds}, min_prefix_length={min_prefix_length}"
        )

    @staticmethod
    def compute_hash(text: str, normalize: bool = False) -> str:
        """
        Compute SHA256 hash of text

        Args:
            text: Text to hash
            normalize: Whether to normalize text before hashing

        Returns:
            SHA256 hash (full 64 characters)
        """
        if normalize:
            text = ContentHashCache.normalize_text(text)
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    @staticmethod
    def normalize_text(text: str) -> str:
        """
        Normalize text for hash matching

        Normalization steps:
            1. Strip leading/trailing whitespace
            2. Collapse consecutive whitespace to single space
            3. Normalize line endings to \n

        Args:
            text: Original text

        Returns:
            Normalized text
        """
        # Strip leading/trailing whitespace
        text = text.strip()

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Collapse consecutive whitespace (including newlines) to single space
        text = re.sub(r'\s+', ' ', text)

        return text

    def get(self, thinking_text: str) -> Optional[str]:
        """
        Get signature by thinking text

        Tries multiple matching strategies:
            1. Exact hash match
            2. Normalized hash match

        Args:
            thinking_text: Thinking text to lookup

        Returns:
            Signature if found, None otherwise
        """
        if not thinking_text:
            return None

        # Compute hashes
        exact_hash = self.compute_hash(thinking_text, normalize=False)
        normalized_hash = self.compute_hash(thinking_text, normalize=True)

        with self._lock:
            # Try exact match first
            if exact_hash in self._exact_cache:
                entry = self._exact_cache[exact_hash]

                # Check expiration
                if entry.is_expired():
                    self._remove_entry(entry)
                    self._stats.expirations += 1
                    self._stats.misses += 1
                    log.debug(f"[HASH_CACHE] Exact match expired: hash={exact_hash[:16]}...")
                    return None

                # Update access stats
                entry.touch()
                self._exact_cache.move_to_end(exact_hash)
                self._stats.exact_hits += 1

                log.debug(
                    f"[HASH_CACHE] Exact hit: hash={exact_hash[:16]}..., "
                    f"access_count={entry.access_count}"
                )
                return entry.signature

            # Try normalized match
            # First check normalized_cache, then check exact_cache (in case normalized == exact)
            if normalized_hash in self._normalized_cache:
                entry = self._normalized_cache[normalized_hash]

                # Check expiration
                if entry.is_expired():
                    self._remove_entry(entry)
                    self._stats.expirations += 1
                    self._stats.misses += 1
                    log.debug(f"[HASH_CACHE] Normalized match expired: hash={normalized_hash[:16]}...")
                    return None

                # Update access stats
                entry.touch()
                self._normalized_cache.move_to_end(normalized_hash)
                self._stats.normalized_hits += 1

                log.debug(
                    f"[HASH_CACHE] Normalized hit: hash={normalized_hash[:16]}..., "
                    f"access_count={entry.access_count}"
                )
                return entry.signature
            elif normalized_hash in self._exact_cache:
                # Normalized hash might be in exact_cache if original text was already normalized
                entry = self._exact_cache[normalized_hash]

                # Check expiration
                if entry.is_expired():
                    self._remove_entry(entry)
                    self._stats.expirations += 1
                    self._stats.misses += 1
                    log.debug(f"[HASH_CACHE] Normalized match (via exact) expired: hash={normalized_hash[:16]}...")
                    return None

                # Update access stats
                entry.touch()
                self._exact_cache.move_to_end(normalized_hash)
                self._stats.normalized_hits += 1

                log.debug(
                    f"[HASH_CACHE] Normalized hit (via exact): hash={normalized_hash[:16]}..., "
                    f"access_count={entry.access_count}"
                )
                return entry.signature

            # Cache miss
            self._stats.misses += 1
            log.debug(f"[HASH_CACHE] Miss: exact={exact_hash[:16]}..., normalized={normalized_hash[:16]}...")
            return None

    def set(self, thinking_text: str, signature: str) -> bool:
        """
        Cache thinking text and signature mapping

        Stores both exact and normalized hashes for flexible matching.

        Args:
            thinking_text: Thinking text
            signature: Signature to cache

        Returns:
            True if successfully cached
        """
        if not thinking_text or not signature:
            return False

        # Compute hashes
        exact_hash = self.compute_hash(thinking_text, normalize=False)
        normalized_hash = self.compute_hash(thinking_text, normalize=True)

        # Calculate expiration
        expires_at = None
        if self._ttl_seconds > 0:
            expires_at = datetime.now() + timedelta(seconds=self._ttl_seconds)

        # Create entry
        entry = HashCacheEntry(
            content_hash=exact_hash,
            normalized_hash=normalized_hash,
            signature=signature,
            thinking_text=thinking_text,
            thinking_prefix=thinking_text[:200],
            created_at=datetime.now(),
            expires_at=expires_at,
            access_count=0
        )

        with self._lock:
            # Store in exact cache
            if exact_hash in self._exact_cache:
                del self._exact_cache[exact_hash]
            self._exact_cache[exact_hash] = entry
            self._exact_cache.move_to_end(exact_hash)

            # Store in normalized cache (if different from exact)
            if normalized_hash != exact_hash:
                if normalized_hash in self._normalized_cache:
                    del self._normalized_cache[normalized_hash]
                self._normalized_cache[normalized_hash] = entry
                self._normalized_cache.move_to_end(normalized_hash)

            # Update prefix index
            self._update_prefix_index(entry)

            # Update stats
            self._stats.total_writes += 1
            self._stats.current_size = len(self._exact_cache)

            # LRU eviction
            self._evict_if_needed()

            log.debug(
                f"[HASH_CACHE] Cached: exact={exact_hash[:16]}..., "
                f"normalized={normalized_hash[:16]}..., "
                f"cache_size={len(self._exact_cache)}"
            )

            return True

    def get_with_prefix_match(
        self,
        thinking_text: str,
        min_prefix_len: Optional[int] = None
    ) -> Optional[str]:
        """
        Get signature with prefix matching

        This is a fallback strategy when exact/normalized matching fails.
        Useful for truncated thinking text.

        Args:
            thinking_text: Thinking text (possibly truncated)
            min_prefix_len: Minimum prefix length (default: self._min_prefix_length)

        Returns:
            Signature if prefix match found, None otherwise
        """
        if not thinking_text:
            return None

        min_len = min_prefix_len or self._min_prefix_length

        # Check if text is long enough
        if len(thinking_text) < min_len:
            log.debug(
                f"[HASH_CACHE] Prefix match skipped: text too short "
                f"({len(thinking_text)} < {min_len})"
            )
            return None

        # Normalize for consistent matching
        normalized_text = self.normalize_text(thinking_text)
        prefix = normalized_text[:min_len]
        prefix_hash = self.compute_hash(prefix, normalize=False)

        with self._lock:
            # Check prefix index
            if prefix_hash not in self._prefix_index:
                self._stats.misses += 1
                log.debug(f"[HASH_CACHE] Prefix miss: hash={prefix_hash[:16]}...")
                return None

            # Find matching entry
            candidates = self._prefix_index[prefix_hash]
            for entry in candidates:
                # Check expiration
                if entry.is_expired():
                    continue

                # Check if prefix matches
                entry_normalized = self.normalize_text(entry.thinking_text)
                if entry_normalized.startswith(prefix):
                    # Update access stats
                    entry.touch()
                    self._stats.prefix_hits += 1

                    log.debug(
                        f"[HASH_CACHE] Prefix hit: hash={prefix_hash[:16]}..., "
                        f"access_count={entry.access_count}"
                    )
                    return entry.signature

            # No match found
            self._stats.misses += 1
            log.debug(f"[HASH_CACHE] Prefix miss: no matching entry for hash={prefix_hash[:16]}...")
            return None

    def cleanup_expired(self) -> int:
        """
        Clean up expired entries

        Returns:
            Number of entries removed
        """
        if self._ttl_seconds <= 0:
            return 0

        with self._lock:
            expired_entries = []

            # Find expired entries
            for entry in self._exact_cache.values():
                if entry.is_expired():
                    expired_entries.append(entry)

            # Remove expired entries
            for entry in expired_entries:
                self._remove_entry(entry)
                self._stats.expirations += 1

            count = len(expired_entries)
            if count > 0:
                self._stats.current_size = len(self._exact_cache)
                log.info(f"[HASH_CACHE] Cleaned up {count} expired entries")

            return count

    def get_stats(self) -> Dict[str, any]:
        """
        Get cache statistics

        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            self._stats.current_size = len(self._exact_cache)
            return self._stats.to_dict()

    def clear(self) -> int:
        """
        Clear all cache entries

        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = len(self._exact_cache)
            self._exact_cache.clear()
            self._normalized_cache.clear()
            self._prefix_index.clear()
            self._stats.current_size = 0

            log.info(f"[HASH_CACHE] Cleared {count} entries")
            return count

    # Private methods

    def _update_prefix_index(self, entry: HashCacheEntry) -> None:
        """
        Update prefix index for an entry

        Args:
            entry: Cache entry to index
        """
        if len(entry.thinking_text) < self._min_prefix_length:
            return

        # Compute prefix hash
        normalized_text = self.normalize_text(entry.thinking_text)
        prefix = normalized_text[:self._min_prefix_length]
        prefix_hash = self.compute_hash(prefix, normalize=False)

        # Add to index
        if prefix_hash not in self._prefix_index:
            self._prefix_index[prefix_hash] = []

        # Avoid duplicates
        if entry not in self._prefix_index[prefix_hash]:
            self._prefix_index[prefix_hash].append(entry)

    def _remove_entry(self, entry: HashCacheEntry) -> None:
        """
        Remove an entry from all caches

        Args:
            entry: Entry to remove
        """
        # Remove from exact cache
        if entry.content_hash in self._exact_cache:
            del self._exact_cache[entry.content_hash]

        # Remove from normalized cache
        if entry.normalized_hash in self._normalized_cache:
            del self._normalized_cache[entry.normalized_hash]

        # Remove from prefix index
        if len(entry.thinking_text) >= self._min_prefix_length:
            normalized_text = self.normalize_text(entry.thinking_text)
            prefix = normalized_text[:self._min_prefix_length]
            prefix_hash = self.compute_hash(prefix, normalize=False)

            if prefix_hash in self._prefix_index:
                try:
                    self._prefix_index[prefix_hash].remove(entry)
                    if not self._prefix_index[prefix_hash]:
                        del self._prefix_index[prefix_hash]
                except ValueError:
                    pass

    def _evict_if_needed(self) -> None:
        """
        Evict entries if cache size exceeds max_size (LRU policy)
        """
        if self._max_size <= 0:
            return

        while len(self._exact_cache) > self._max_size:
            # Remove least recently used (first item in OrderedDict)
            exact_hash, entry = self._exact_cache.popitem(last=False)

            # Remove from normalized cache
            if entry.normalized_hash in self._normalized_cache:
                del self._normalized_cache[entry.normalized_hash]

            # Remove from prefix index
            if len(entry.thinking_text) >= self._min_prefix_length:
                normalized_text = self.normalize_text(entry.thinking_text)
                prefix = normalized_text[:self._min_prefix_length]
                prefix_hash = self.compute_hash(prefix, normalize=False)

                if prefix_hash in self._prefix_index:
                    try:
                        self._prefix_index[prefix_hash].remove(entry)
                        if not self._prefix_index[prefix_hash]:
                            del self._prefix_index[prefix_hash]
                    except ValueError:
                        pass

            self._stats.evictions += 1
            log.debug(f"[HASH_CACHE] LRU evicted: hash={exact_hash[:16]}...")
