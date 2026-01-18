"""
Cache Module - Layered caching architecture for SignatureCache
分层缓存架构模块 - 提供 L1 内存缓存 + L2 SQLite 持久化

Architecture:
    L1: Memory Cache (fast, volatile, limited size)
    L2: SQLite Database (persistent, WAL mode, unlimited size)

Key Features:
    - Namespace isolation (namespace:conversation_id:thinking_hash)
    - Async write-through to L2
    - Read-write lock separation
    - Batch commit optimization

Usage:
    from cache import SignatureCacheManager, LayeredCacheConfig

    # Initialize with default config
    manager = SignatureCacheManager.get_instance()

    # Cache a signature
    manager.cache_signature(
        thinking_text="...",
        signature="...",
        model="claude-3",
        namespace="anthropic"
    )

    # Retrieve a signature
    sig = manager.get_cached_signature(
        thinking_text="...",
        namespace="anthropic"
    )

    # Or use convenience functions
    from cache import cache_signature, get_cached_signature
    cache_signature("thinking", "signature", model="claude-3")
    sig = get_cached_signature("thinking")
"""

from .cache_interface import (
    ICacheLayer,
    CacheEntry,
    CacheConfig,
    CacheStats,
    generate_thinking_hash,
    build_cache_key,
    parse_cache_key,
)
from .memory_cache import MemoryCache, RWLock
from .signature_database import SignatureDatabase
from .signature_cache_manager import (
    SignatureCacheManager,
    LayeredCacheConfig,
    AggregatedStats,
    get_cache_manager,
    cache_signature,
    get_cached_signature,
    get_last_signature,
)
from .async_write_queue import (
    AsyncWriteQueue,
    AsyncWriteConfig,
    QueueStats,
    QueueState,
    WriteTask,
    create_async_queue,
)

__all__ = [
    # Core interfaces
    "ICacheLayer",
    "CacheEntry",
    "CacheConfig",
    "CacheStats",
    # L1 Memory Cache
    "MemoryCache",
    "RWLock",
    # L2 SQLite Database
    "SignatureDatabase",
    # Layered Cache Manager
    "SignatureCacheManager",
    "LayeredCacheConfig",
    "AggregatedStats",
    # Async Write Queue
    "AsyncWriteQueue",
    "AsyncWriteConfig",
    "QueueStats",
    "QueueState",
    "WriteTask",
    # Convenience functions
    "get_cache_manager",
    "cache_signature",
    "get_cached_signature",
    "get_last_signature",
    "create_async_queue",
    # Utility functions
    "generate_thinking_hash",
    "build_cache_key",
    "parse_cache_key",
]

__version__ = "2.0.0"
