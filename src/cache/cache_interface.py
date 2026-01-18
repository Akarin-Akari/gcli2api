"""
Cache Interface - Abstract interface for cache layers
缓存层抽象接口 - 定义 L1/L2 缓存层的通用接口

This module provides:
    - CacheEntry: Data class for cache entries
    - CacheConfig: Configuration class for cache layers
    - ICacheLayer: Abstract interface for cache layer implementations
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import hashlib


@dataclass
class CacheEntry:
    """
    Cache entry data class
    缓存条目数据类

    Attributes:
        signature: The signature string (encrypted thinking content)
        thinking_hash: Hash of the thinking text (used as cache key)
        thinking_prefix: First N characters of thinking text (for debugging)
        model: Model name that generated this signature
        namespace: Namespace for isolation (e.g., "antigravity", "anthropic")
        conversation_id: Optional conversation ID for session isolation
        created_at: Timestamp when entry was created
        expires_at: Timestamp when entry expires (None = never expires)
        access_count: Number of times this entry was accessed
        last_accessed_at: Timestamp of last access
        metadata: Optional additional metadata
    """
    signature: str
    thinking_hash: str
    thinking_prefix: str = ""
    model: str = "unknown"
    namespace: str = "default"
    conversation_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if entry has expired"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def touch(self) -> None:
        """Update access statistics"""
        self.access_count += 1
        self.last_accessed_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "signature": self.signature,
            "thinking_hash": self.thinking_hash,
            "thinking_prefix": self.thinking_prefix,
            "model": self.model,
            "namespace": self.namespace,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        """Create from dictionary"""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        expires_at = data.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        last_accessed_at = data.get("last_accessed_at")
        if isinstance(last_accessed_at, str):
            last_accessed_at = datetime.fromisoformat(last_accessed_at)

        return cls(
            signature=data.get("signature", ""),
            thinking_hash=data.get("thinking_hash", ""),
            thinking_prefix=data.get("thinking_prefix", ""),
            model=data.get("model", "unknown"),
            namespace=data.get("namespace", "default"),
            conversation_id=data.get("conversation_id"),
            created_at=created_at,
            expires_at=expires_at,
            access_count=data.get("access_count", 0),
            last_accessed_at=last_accessed_at,
            metadata=data.get("metadata", {}),
        )


@dataclass
class CacheConfig:
    """
    Configuration class for cache layers
    缓存层配置类

    Attributes:
        max_size: Maximum number of entries in cache (0 = unlimited)
        ttl_seconds: Time-to-live in seconds (0 = never expires)
        key_prefix_length: Length of thinking text prefix to store for debugging
        namespace: Default namespace for this cache instance
        enable_stats: Whether to track access statistics
        eviction_policy: Eviction policy ("lru", "lfu", "fifo")
        write_through: Whether to write through to next layer immediately
        batch_size: Batch size for async write operations
        batch_timeout_ms: Timeout in milliseconds before flushing batch
    """
    max_size: int = 10000
    ttl_seconds: int = 3600  # 1 hour default
    key_prefix_length: int = 500
    namespace: str = "default"
    enable_stats: bool = True
    eviction_policy: str = "lru"  # lru, lfu, fifo
    write_through: bool = True
    batch_size: int = 100
    batch_timeout_ms: int = 1000  # 1 second

    # SQLite specific configs
    db_path: Optional[str] = None
    wal_mode: bool = True
    busy_timeout_ms: int = 5000

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors"""
        errors = []

        if self.max_size < 0:
            errors.append("max_size must be >= 0")

        if self.ttl_seconds < 0:
            errors.append("ttl_seconds must be >= 0")

        if self.key_prefix_length < 0:
            errors.append("key_prefix_length must be >= 0")

        if self.eviction_policy not in ("lru", "lfu", "fifo"):
            errors.append(f"eviction_policy must be one of: lru, lfu, fifo")

        if self.batch_size < 1:
            errors.append("batch_size must be >= 1")

        if self.batch_timeout_ms < 0:
            errors.append("batch_timeout_ms must be >= 0")

        return errors


@dataclass
class CacheStats:
    """
    Cache statistics
    缓存统计信息
    """
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    current_size: int = 0
    max_size: int = 0
    total_writes: int = 0
    total_deletes: int = 0
    oldest_entry_age_seconds: float = 0.0
    newest_entry_age_seconds: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate"""
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "current_size": self.current_size,
            "max_size": self.max_size,
            "total_writes": self.total_writes,
            "total_deletes": self.total_deletes,
            "hit_rate": self.hit_rate,
            "oldest_entry_age_seconds": self.oldest_entry_age_seconds,
            "newest_entry_age_seconds": self.newest_entry_age_seconds,
        }


class ICacheLayer(ABC):
    """
    Abstract interface for cache layer implementations
    缓存层抽象接口

    This interface defines the contract for all cache layer implementations:
    - L1 Memory Cache (fast, volatile)
    - L2 SQLite Database (persistent, slower)

    All implementations must be thread-safe.
    """

    @abstractmethod
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
            conversation_id: Optional conversation ID for session isolation

        Returns:
            CacheEntry if found and not expired, None otherwise
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    def get_stats(self) -> CacheStats:
        """
        Get cache statistics

        Returns:
            CacheStats object with current statistics
        """
        pass

    @abstractmethod
    def cleanup_expired(self) -> int:
        """
        Remove expired entries

        Returns:
            Number of entries removed
        """
        pass

    @abstractmethod
    def size(self) -> int:
        """
        Get current number of entries

        Returns:
            Number of entries in cache
        """
        pass

    # Optional methods with default implementations

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
        # Default implementation returns empty list
        # Subclasses can override for actual implementation
        return []

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
        # Default implementation returns empty list
        return []

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
        # Default implementation calls set() for each entry
        count = 0
        for entry in entries:
            if self.set(entry, update_if_exists):
                count += 1
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
        # Default implementation calls delete() for each hash
        count = 0
        for h in thinking_hashes:
            if self.delete(h, namespace):
                count += 1
        return count


def generate_thinking_hash(thinking_text: str, prefix_length: int = 500) -> Tuple[str, str]:
    """
    Generate hash and prefix from thinking text

    Args:
        thinking_text: The thinking text to hash
        prefix_length: Length of prefix to extract

    Returns:
        Tuple of (hash, prefix)
    """
    # Normalize text
    normalized = thinking_text.strip()

    # Generate SHA-256 hash
    hash_value = hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    # Extract prefix (first N characters)
    prefix = normalized[:prefix_length] if len(normalized) > prefix_length else normalized

    return hash_value, prefix


def build_cache_key(
    thinking_hash: str,
    namespace: str = "default",
    conversation_id: Optional[str] = None
) -> str:
    """
    Build composite cache key

    Format: namespace:conversation_id:thinking_hash
    If conversation_id is None, format is: namespace:_:thinking_hash

    Args:
        thinking_hash: Hash of thinking text
        namespace: Namespace for isolation
        conversation_id: Optional conversation ID

    Returns:
        Composite cache key string
    """
    conv_id = conversation_id if conversation_id else "_"
    return f"{namespace}:{conv_id}:{thinking_hash}"


def parse_cache_key(cache_key: str) -> Tuple[str, Optional[str], str]:
    """
    Parse composite cache key

    Args:
        cache_key: Composite cache key string

    Returns:
        Tuple of (namespace, conversation_id, thinking_hash)
        conversation_id is None if it was "_"
    """
    parts = cache_key.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid cache key format: {cache_key}")

    namespace = parts[0]
    conversation_id = parts[1] if parts[1] != "_" else None
    thinking_hash = parts[2]

    return namespace, conversation_id, thinking_hash
