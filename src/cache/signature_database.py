"""
Signature Database - SQLite persistent storage layer (L2 Cache)
SQLite 持久化存储层 - 作为 L2 缓存提供持久化能力

This module provides:
    - SQLite-based persistent storage with WAL mode
    - Thread-safe read/write operations
    - TTL-based expiration
    - Namespace and conversation isolation
    - Batch operation optimization

Architecture:
    - Uses WAL (Write-Ahead Logging) for better concurrency
    - Implements ICacheLayer interface
    - Designed to be used as L2 cache behind memory cache
"""

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
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


# Default database path
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "signature_cache.db"
)


class SignatureDatabase(ICacheLayer):
    """
    SQLite-based persistent storage layer for signature cache
    基于 SQLite 的签名缓存持久化存储层

    Features:
        - WAL mode for better read/write concurrency
        - Thread-safe with connection pooling
        - TTL-based automatic expiration
        - Namespace and conversation isolation
        - Optimized batch operations

    Usage:
        config = CacheConfig(
            db_path="path/to/cache.db",
            ttl_seconds=3600,
            wal_mode=True
        )
        db = SignatureDatabase(config)

        # Store entry
        entry = CacheEntry(signature="...", thinking_hash="...")
        db.set(entry)

        # Retrieve entry
        result = db.get(thinking_hash, namespace="default")
    """

    # SQL statements
    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS signature_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            thinking_hash TEXT NOT NULL,
            signature TEXT NOT NULL,
            thinking_prefix TEXT DEFAULT '',
            model TEXT DEFAULT 'unknown',
            namespace TEXT DEFAULT 'default',
            conversation_id TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            access_count INTEGER DEFAULT 0,
            last_accessed_at TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """

    # [FIX 2026-01-17] Tool Cache 表 - 存储工具ID与签名的映射
    CREATE_TOOL_CACHE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS tool_signature_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id TEXT UNIQUE NOT NULL,
            signature TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            access_count INTEGER DEFAULT 0,
            last_accessed_at TEXT
        )
    """

    # [FIX 2026-01-17] Session Cache 表 - 存储会话级别的签名
    CREATE_SESSION_CACHE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS session_signature_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            signature TEXT NOT NULL,
            thinking_text TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            access_count INTEGER DEFAULT 0,
            last_accessed_at TEXT
        )
    """

    # [FIX 2026-01-17] Conversation State 表 - 存储会话状态机数据
    CREATE_CONVERSATION_STATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS conversation_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scid TEXT UNIQUE NOT NULL,
            client_type TEXT NOT NULL,
            authoritative_history TEXT NOT NULL,
            last_signature TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            access_count INTEGER DEFAULT 0
        )
    """

    CREATE_INDEXES_SQL = [
        "CREATE INDEX IF NOT EXISTS idx_thinking_hash ON signature_cache(thinking_hash)",
        "CREATE INDEX IF NOT EXISTS idx_namespace ON signature_cache(namespace)",
        "CREATE INDEX IF NOT EXISTS idx_conversation_id ON signature_cache(conversation_id)",
        "CREATE INDEX IF NOT EXISTS idx_expires_at ON signature_cache(expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_cache_key ON signature_cache(cache_key)",
        "CREATE INDEX IF NOT EXISTS idx_created_at ON signature_cache(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_last_accessed ON signature_cache(last_accessed_at)",
        # [FIX 2026-01-17] Tool Cache 索引
        "CREATE INDEX IF NOT EXISTS idx_tool_id ON tool_signature_cache(tool_id)",
        "CREATE INDEX IF NOT EXISTS idx_tool_expires_at ON tool_signature_cache(expires_at)",
        # [FIX 2026-01-17] Session Cache 索引
        "CREATE INDEX IF NOT EXISTS idx_session_id ON session_signature_cache(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_expires_at ON session_signature_cache(expires_at)",
        # [FIX 2026-01-17] Conversation State 索引
        "CREATE INDEX IF NOT EXISTS idx_conversation_state_scid ON conversation_state(scid)",
        "CREATE INDEX IF NOT EXISTS idx_conversation_state_expires ON conversation_state(expires_at)",
    ]

    def __init__(self, config: Optional[CacheConfig] = None):
        """
        Initialize SignatureDatabase

        Args:
            config: Cache configuration. If None, uses default config.
        """
        self.config = config or CacheConfig()

        # Set database path
        self.db_path = self.config.db_path or DEFAULT_DB_PATH

        # Ensure directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            log.info(f"[SIGNATURE_DB] Created database directory: {db_dir}")

        # Thread-local storage for connections
        self._local = threading.local()

        # Lock for initialization
        self._init_lock = threading.Lock()
        self._initialized = False

        # Statistics
        self._stats = CacheStats()
        self._stats_lock = threading.Lock()

        # Initialize database
        self._initialize_database()

        log.info(f"[SIGNATURE_DB] Initialized with db_path={self.db_path}, "
                f"wal_mode={self.config.wal_mode}, ttl={self.config.ttl_seconds}s")

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection

        Returns:
            SQLite connection for current thread
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=self.config.busy_timeout_ms / 1000.0,
                check_same_thread=False
            )

            # Enable WAL mode if configured
            if self.config.wal_mode:
                conn.execute("PRAGMA journal_mode=WAL")

            # Performance optimizations
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA temp_store=MEMORY")

            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")

            # Row factory for dict-like access
            conn.row_factory = sqlite3.Row

            self._local.connection = conn

        return self._local.connection

    @contextmanager
    def _get_cursor(self, commit: bool = True):
        """
        Context manager for database cursor

        Args:
            commit: Whether to commit after operations

        Yields:
            SQLite cursor
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"[SIGNATURE_DB] Database error: {e}")
            raise
        finally:
            cursor.close()

    def _initialize_database(self) -> None:
        """Initialize database schema"""
        with self._init_lock:
            if self._initialized:
                return

            try:
                with self._get_cursor() as cursor:
                    # Create main signature cache table
                    cursor.execute(self.CREATE_TABLE_SQL)

                    # [FIX 2026-01-17] Create Tool Cache, Session Cache, and Conversation State tables
                    cursor.execute(self.CREATE_TOOL_CACHE_TABLE_SQL)
                    cursor.execute(self.CREATE_SESSION_CACHE_TABLE_SQL)
                    cursor.execute(self.CREATE_CONVERSATION_STATE_TABLE_SQL)

                    # Create indexes
                    for index_sql in self.CREATE_INDEXES_SQL:
                        cursor.execute(index_sql)

                self._initialized = True
                log.info("[SIGNATURE_DB] Database schema initialized (including tool_cache, session_cache, and conversation_state)")

            except Exception as e:
                log.error(f"[SIGNATURE_DB] Failed to initialize database: {e}")
                raise

    def _entry_to_row(self, entry: CacheEntry) -> Dict[str, Any]:
        """
        Convert CacheEntry to database row

        Args:
            entry: Cache entry

        Returns:
            Dictionary for database insertion
        """
        import json

        cache_key = build_cache_key(
            entry.thinking_hash,
            entry.namespace,
            entry.conversation_id
        )

        return {
            "cache_key": cache_key,
            "thinking_hash": entry.thinking_hash,
            "signature": entry.signature,
            "thinking_prefix": entry.thinking_prefix,
            "model": entry.model,
            "namespace": entry.namespace,
            "conversation_id": entry.conversation_id,
            "created_at": entry.created_at.isoformat() if entry.created_at else datetime.now().isoformat(),
            "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
            "access_count": entry.access_count,
            "last_accessed_at": entry.last_accessed_at.isoformat() if entry.last_accessed_at else None,
            "metadata": json.dumps(entry.metadata) if entry.metadata else "{}",
        }

    def _row_to_entry(self, row: sqlite3.Row) -> CacheEntry:
        """
        Convert database row to CacheEntry

        Args:
            row: Database row

        Returns:
            CacheEntry object
        """
        import json

        # Parse datetime fields
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now()
        expires_at = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
        last_accessed_at = datetime.fromisoformat(row["last_accessed_at"]) if row["last_accessed_at"] else None

        # Parse metadata
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except json.JSONDecodeError:
            metadata = {}

        return CacheEntry(
            signature=row["signature"],
            thinking_hash=row["thinking_hash"],
            thinking_prefix=row["thinking_prefix"] or "",
            model=row["model"] or "unknown",
            namespace=row["namespace"] or "default",
            conversation_id=row["conversation_id"],
            created_at=created_at,
            expires_at=expires_at,
            access_count=row["access_count"] or 0,
            last_accessed_at=last_accessed_at,
            metadata=metadata,
        )

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

        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM signature_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,)
                )
                row = cursor.fetchone()

                if not row:
                    with self._stats_lock:
                        self._stats.misses += 1
                    return None

                entry = self._row_to_entry(row)

                # Check expiration
                if entry.is_expired():
                    with self._stats_lock:
                        self._stats.misses += 1
                        self._stats.expirations += 1
                    # Delete expired entry
                    self.delete(thinking_hash, namespace, conversation_id)
                    return None

                # Update access statistics
                self._update_access_stats(cache_key)

                with self._stats_lock:
                    self._stats.hits += 1

                log.debug(f"[SIGNATURE_DB] Cache hit: key={cache_key[:50]}...")
                return entry

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting entry: {e}")
            with self._stats_lock:
                self._stats.misses += 1
            return None

    def _update_access_stats(self, cache_key: str) -> None:
        """Update access count and timestamp for an entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE signature_cache
                    SET access_count = access_count + 1,
                        last_accessed_at = ?
                    WHERE cache_key = ?
                    """,
                    (datetime.now().isoformat(), cache_key)
                )
        except Exception as e:
            log.debug(f"[SIGNATURE_DB] Error updating access stats: {e}")

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

        row_data = self._entry_to_row(entry)

        try:
            with self._get_cursor() as cursor:
                if update_if_exists:
                    # UPSERT: Insert or update
                    cursor.execute(
                        """
                        INSERT INTO signature_cache (
                            cache_key, thinking_hash, signature, thinking_prefix,
                            model, namespace, conversation_id, created_at,
                            expires_at, access_count, last_accessed_at, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            signature = excluded.signature,
                            thinking_prefix = excluded.thinking_prefix,
                            model = excluded.model,
                            expires_at = excluded.expires_at,
                            metadata = excluded.metadata
                        """,
                        (
                            row_data["cache_key"],
                            row_data["thinking_hash"],
                            row_data["signature"],
                            row_data["thinking_prefix"],
                            row_data["model"],
                            row_data["namespace"],
                            row_data["conversation_id"],
                            row_data["created_at"],
                            row_data["expires_at"],
                            row_data["access_count"],
                            row_data["last_accessed_at"],
                            row_data["metadata"],
                        )
                    )
                else:
                    # Insert only if not exists
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO signature_cache (
                            cache_key, thinking_hash, signature, thinking_prefix,
                            model, namespace, conversation_id, created_at,
                            expires_at, access_count, last_accessed_at, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row_data["cache_key"],
                            row_data["thinking_hash"],
                            row_data["signature"],
                            row_data["thinking_prefix"],
                            row_data["model"],
                            row_data["namespace"],
                            row_data["conversation_id"],
                            row_data["created_at"],
                            row_data["expires_at"],
                            row_data["access_count"],
                            row_data["last_accessed_at"],
                            row_data["metadata"],
                        )
                    )

                if cursor.rowcount > 0:
                    with self._stats_lock:
                        self._stats.total_writes += 1
                    log.debug(f"[SIGNATURE_DB] Entry stored: hash={entry.thinking_hash[:16]}...")
                    return True

                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error setting entry: {e}")
            return False

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

        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM signature_cache WHERE cache_key = ?",
                    (cache_key,)
                )

                if cursor.rowcount > 0:
                    with self._stats_lock:
                        self._stats.total_deletes += 1
                    log.debug(f"[SIGNATURE_DB] Entry deleted: key={cache_key[:50]}...")
                    return True

                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error deleting entry: {e}")
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
            Number of entries cleared
        """
        try:
            with self._get_cursor() as cursor:
                if namespace is None and conversation_id is None:
                    # Clear all
                    cursor.execute("DELETE FROM signature_cache")
                elif namespace is not None and conversation_id is not None:
                    # Clear by namespace and conversation
                    cursor.execute(
                        "DELETE FROM signature_cache WHERE namespace = ? AND conversation_id = ?",
                        (namespace, conversation_id)
                    )
                elif namespace is not None:
                    # Clear by namespace
                    cursor.execute(
                        "DELETE FROM signature_cache WHERE namespace = ?",
                        (namespace,)
                    )
                else:
                    # Clear by conversation
                    cursor.execute(
                        "DELETE FROM signature_cache WHERE conversation_id = ?",
                        (conversation_id,)
                    )

                count = cursor.rowcount
                with self._stats_lock:
                    self._stats.total_deletes += count

                log.info(f"[SIGNATURE_DB] Cleared {count} entries "
                        f"(namespace={namespace}, conversation_id={conversation_id})")
                return count

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error clearing entries: {e}")
            return 0

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

        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT expires_at FROM signature_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,)
                )
                row = cursor.fetchone()

                if not row:
                    return False

                # Check expiration
                if row["expires_at"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now() > expires_at:
                        return False

                return True

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error checking existence: {e}")
            return False

    def get_stats(self) -> CacheStats:
        """
        Get cache statistics

        Returns:
            CacheStats object with current statistics
        """
        try:
            with self._get_cursor(commit=False) as cursor:
                # Get current size
                cursor.execute("SELECT COUNT(*) FROM signature_cache")
                current_size = cursor.fetchone()[0]

                # Get oldest and newest entries
                cursor.execute(
                    """
                    SELECT
                        MIN(created_at) as oldest,
                        MAX(created_at) as newest
                    FROM signature_cache
                    """
                )
                row = cursor.fetchone()

                oldest_age = 0.0
                newest_age = 0.0
                now = datetime.now()

                if row["oldest"]:
                    oldest = datetime.fromisoformat(row["oldest"])
                    oldest_age = (now - oldest).total_seconds()

                if row["newest"]:
                    newest = datetime.fromisoformat(row["newest"])
                    newest_age = (now - newest).total_seconds()

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

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting stats: {e}")
            with self._stats_lock:
                return CacheStats(
                    hits=self._stats.hits,
                    misses=self._stats.misses,
                    evictions=self._stats.evictions,
                    expirations=self._stats.expirations,
                )

    def cleanup_expired(self) -> int:
        """
        Remove expired entries

        Returns:
            Number of entries removed
        """
        try:
            now = datetime.now().isoformat()

            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM signature_cache
                    WHERE expires_at IS NOT NULL AND expires_at < ?
                    """,
                    (now,)
                )

                count = cursor.rowcount
                with self._stats_lock:
                    self._stats.expirations += count
                    self._stats.total_deletes += count

                if count > 0:
                    log.info(f"[SIGNATURE_DB] Cleaned up {count} expired entries")

                return count

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error cleaning up expired entries: {e}")
            return 0

    def size(self) -> int:
        """
        Get current number of entries

        Returns:
            Number of entries in cache
        """
        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute("SELECT COUNT(*) FROM signature_cache")
                return cursor.fetchone()[0]

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting size: {e}")
            return 0

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
        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM signature_cache
                    WHERE namespace = ? AND thinking_prefix LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (namespace, f"{thinking_prefix}%", limit)
                )

                entries = []
                for row in cursor.fetchall():
                    entry = self._row_to_entry(row)
                    if not entry.is_expired():
                        entries.append(entry)

                return entries

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting by prefix: {e}")
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
        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM signature_cache
                    WHERE namespace = ?
                    ORDER BY last_accessed_at DESC NULLS LAST, created_at DESC
                    LIMIT ?
                    """,
                    (namespace, limit)
                )

                entries = []
                for row in cursor.fetchall():
                    entry = self._row_to_entry(row)
                    if not entry.is_expired():
                        entries.append(entry)

                return entries

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting recent entries: {e}")
            return []

    def bulk_set(
        self,
        entries: List[CacheEntry],
        update_if_exists: bool = True
    ) -> int:
        """
        Bulk set multiple entries (optimized batch operation)

        Args:
            entries: List of cache entries to store
            update_if_exists: If True, update existing entries

        Returns:
            Number of entries successfully stored
        """
        if not entries:
            return 0

        count = 0
        try:
            with self._get_cursor() as cursor:
                for entry in entries:
                    # Calculate expiration if TTL is configured
                    if self.config.ttl_seconds > 0 and entry.expires_at is None:
                        entry.expires_at = datetime.now() + timedelta(seconds=self.config.ttl_seconds)

                    row_data = self._entry_to_row(entry)

                    if update_if_exists:
                        cursor.execute(
                            """
                            INSERT INTO signature_cache (
                                cache_key, thinking_hash, signature, thinking_prefix,
                                model, namespace, conversation_id, created_at,
                                expires_at, access_count, last_accessed_at, metadata
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(cache_key) DO UPDATE SET
                                signature = excluded.signature,
                                thinking_prefix = excluded.thinking_prefix,
                                model = excluded.model,
                                expires_at = excluded.expires_at,
                                metadata = excluded.metadata
                            """,
                            (
                                row_data["cache_key"],
                                row_data["thinking_hash"],
                                row_data["signature"],
                                row_data["thinking_prefix"],
                                row_data["model"],
                                row_data["namespace"],
                                row_data["conversation_id"],
                                row_data["created_at"],
                                row_data["expires_at"],
                                row_data["access_count"],
                                row_data["last_accessed_at"],
                                row_data["metadata"],
                            )
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT OR IGNORE INTO signature_cache (
                                cache_key, thinking_hash, signature, thinking_prefix,
                                model, namespace, conversation_id, created_at,
                                expires_at, access_count, last_accessed_at, metadata
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row_data["cache_key"],
                                row_data["thinking_hash"],
                                row_data["signature"],
                                row_data["thinking_prefix"],
                                row_data["model"],
                                row_data["namespace"],
                                row_data["conversation_id"],
                                row_data["created_at"],
                                row_data["expires_at"],
                                row_data["access_count"],
                                row_data["last_accessed_at"],
                                row_data["metadata"],
                            )
                        )

                    if cursor.rowcount > 0:
                        count += 1

            with self._stats_lock:
                self._stats.total_writes += count

            log.debug(f"[SIGNATURE_DB] Bulk set {count}/{len(entries)} entries")
            return count

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error in bulk set: {e}")
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

        try:
            cache_keys = [
                build_cache_key(h, namespace, None)
                for h in thinking_hashes
            ]

            placeholders = ",".join(["?" for _ in cache_keys])

            with self._get_cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM signature_cache WHERE cache_key IN ({placeholders})",
                    cache_keys
                )

                count = cursor.rowcount
                with self._stats_lock:
                    self._stats.total_deletes += count

                log.debug(f"[SIGNATURE_DB] Bulk deleted {count}/{len(thinking_hashes)} entries")
                return count

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error in bulk delete: {e}")
            return 0

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
        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM signature_cache
                    WHERE thinking_hash = ?
                    ORDER BY last_accessed_at DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """,
                    (thinking_hash,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                entry = self._row_to_entry(row)

                # Check expiration
                if entry.is_expired():
                    return None

                log.debug(f"[SIGNATURE_DB] Fallback cache hit: hash={thinking_hash[:16]}...")
                return entry

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error in fallback lookup: {e}")
            return None

    def vacuum(self) -> None:
        """
        Optimize database by reclaiming space

        Should be called periodically during low-traffic periods
        """
        try:
            conn = self._get_connection()
            conn.execute("VACUUM")
            log.info("[SIGNATURE_DB] Database vacuumed successfully")
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error vacuuming database: {e}")

    # ==================== Tool Cache Methods ====================
    # [FIX 2026-01-17] Tool Cache CRUD - 存储工具ID与签名的映射

    def set_tool_signature(
        self,
        tool_id: str,
        signature: str,
        ttl_seconds: Optional[int] = None
    ) -> bool:
        """
        Store tool ID to signature mapping

        Args:
            tool_id: The tool call ID
            signature: The thinking signature
            ttl_seconds: Optional TTL, defaults to config.ttl_seconds

        Returns:
            True if stored successfully
        """
        if not tool_id or not signature:
            return False

        ttl = ttl_seconds if ttl_seconds is not None else self.config.ttl_seconds
        now = datetime.now()
        expires_at = (now + timedelta(seconds=ttl)).isoformat() if ttl > 0 else None

        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tool_signature_cache (
                        tool_id, signature, created_at, expires_at, access_count, last_accessed_at
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(tool_id) DO UPDATE SET
                        signature = excluded.signature,
                        expires_at = excluded.expires_at,
                        last_accessed_at = excluded.last_accessed_at
                    """,
                    (tool_id, signature, now.isoformat(), expires_at, now.isoformat())
                )

                if cursor.rowcount > 0:
                    log.debug(f"[SIGNATURE_DB] Tool signature stored: tool_id={tool_id[:20]}...")
                    return True
                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error storing tool signature: {e}")
            return False

    def get_tool_signature(self, tool_id: str) -> Optional[str]:
        """
        Get signature by tool ID

        Args:
            tool_id: The tool call ID

        Returns:
            Signature if found and not expired, None otherwise
        """
        if not tool_id:
            return None

        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT signature, expires_at FROM tool_signature_cache
                    WHERE tool_id = ?
                    """,
                    (tool_id,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                # Check expiration
                if row["expires_at"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now() > expires_at:
                        # Delete expired entry
                        self.delete_tool_signature(tool_id)
                        return None

                # Update access stats
                self._update_tool_access_stats(tool_id)

                log.debug(f"[SIGNATURE_DB] Tool signature hit: tool_id={tool_id[:20]}...")
                return row["signature"]

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting tool signature: {e}")
            return None

    def delete_tool_signature(self, tool_id: str) -> bool:
        """Delete tool signature entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM tool_signature_cache WHERE tool_id = ?",
                    (tool_id,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error deleting tool signature: {e}")
            return False

    def _update_tool_access_stats(self, tool_id: str) -> None:
        """Update access count and timestamp for tool cache entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE tool_signature_cache
                    SET access_count = access_count + 1,
                        last_accessed_at = ?
                    WHERE tool_id = ?
                    """,
                    (datetime.now().isoformat(), tool_id)
                )
        except Exception as e:
            log.debug(f"[SIGNATURE_DB] Error updating tool access stats: {e}")

    def cleanup_expired_tool_cache(self) -> int:
        """Remove expired tool cache entries"""
        try:
            now = datetime.now().isoformat()
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM tool_signature_cache
                    WHERE expires_at IS NOT NULL AND expires_at < ?
                    """,
                    (now,)
                )
                count = cursor.rowcount
                if count > 0:
                    log.info(f"[SIGNATURE_DB] Cleaned up {count} expired tool cache entries")
                return count
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error cleaning up tool cache: {e}")
            return 0

    # ==================== Session Cache Methods ====================
    # [FIX 2026-01-17] Session Cache CRUD - 存储会话级别的签名

    def set_session_signature(
        self,
        session_id: str,
        signature: str,
        thinking_text: str = "",
        ttl_seconds: Optional[int] = None
    ) -> bool:
        """
        Store session signature with optional thinking text

        Args:
            session_id: The session ID
            signature: The thinking signature
            thinking_text: Optional thinking text for recovery
            ttl_seconds: Optional TTL, defaults to config.ttl_seconds

        Returns:
            True if stored successfully
        """
        if not session_id or not signature:
            return False

        ttl = ttl_seconds if ttl_seconds is not None else self.config.ttl_seconds
        now = datetime.now()
        expires_at = (now + timedelta(seconds=ttl)).isoformat() if ttl > 0 else None

        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO session_signature_cache (
                        session_id, signature, thinking_text, created_at, expires_at,
                        access_count, last_accessed_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        signature = excluded.signature,
                        thinking_text = excluded.thinking_text,
                        expires_at = excluded.expires_at,
                        last_accessed_at = excluded.last_accessed_at
                    """,
                    (session_id, signature, thinking_text, now.isoformat(), expires_at, now.isoformat())
                )

                if cursor.rowcount > 0:
                    log.debug(f"[SIGNATURE_DB] Session signature stored: session_id={session_id[:20]}...")
                    return True
                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error storing session signature: {e}")
            return False

    def get_session_signature(self, session_id: str) -> Optional[Tuple[str, str]]:
        """
        Get signature and thinking text by session ID

        Args:
            session_id: The session ID

        Returns:
            Tuple of (signature, thinking_text) if found and not expired, None otherwise
        """
        if not session_id:
            return None

        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT signature, thinking_text, expires_at FROM session_signature_cache
                    WHERE session_id = ?
                    """,
                    (session_id,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                # Check expiration
                if row["expires_at"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now() > expires_at:
                        # Delete expired entry
                        self.delete_session_signature(session_id)
                        return None

                # Update access stats
                self._update_session_access_stats(session_id)

                log.debug(f"[SIGNATURE_DB] Session signature hit: session_id={session_id[:20]}...")
                return (row["signature"], row["thinking_text"] or "")

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting session signature: {e}")
            return None

    def delete_session_signature(self, session_id: str) -> bool:
        """Delete session signature entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM session_signature_cache WHERE session_id = ?",
                    (session_id,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error deleting session signature: {e}")
            return False

    def _update_session_access_stats(self, session_id: str) -> None:
        """Update access count and timestamp for session cache entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE session_signature_cache
                    SET access_count = access_count + 1,
                        last_accessed_at = ?
                    WHERE session_id = ?
                    """,
                    (datetime.now().isoformat(), session_id)
                )
        except Exception as e:
            log.debug(f"[SIGNATURE_DB] Error updating session access stats: {e}")

    def cleanup_expired_session_cache(self) -> int:
        """Remove expired session cache entries"""
        try:
            now = datetime.now().isoformat()
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM session_signature_cache
                    WHERE expires_at IS NOT NULL AND expires_at < ?
                    """,
                    (now,)
                )
                count = cursor.rowcount
                if count > 0:
                    log.info(f"[SIGNATURE_DB] Cleaned up {count} expired session cache entries")
                return count
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error cleaning up session cache: {e}")
            return 0

    def get_last_session_signature(self) -> Optional[Tuple[str, str]]:
        """
        Get the most recently accessed session signature

        Returns:
            Tuple of (signature, thinking_text) if found, None otherwise
        """
        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT signature, thinking_text, expires_at FROM session_signature_cache
                    ORDER BY last_accessed_at DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()

                if not row:
                    return None

                # Check expiration
                if row["expires_at"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now() > expires_at:
                        return None

                log.debug("[SIGNATURE_DB] Last session signature retrieved")
                return (row["signature"], row["thinking_text"] or "")

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting last session signature: {e}")
            return None

    # ==================== Conversation State Methods ====================
    # [FIX 2026-01-17] Conversation State CRUD - 存储会话状态机数据

    def store_conversation_state(
        self,
        scid: str,
        client_type: str,
        history: str,
        signature: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ) -> bool:
        """
        Store conversation state for SCID-based state machine

        Args:
            scid: Server Conversation ID
            client_type: Client type ('cursor' | 'augment' | 'claude_code' | 'unknown')
            history: JSON-formatted authoritative history
            signature: Optional last valid signature
            ttl_seconds: Optional TTL, defaults to config.ttl_seconds

        Returns:
            True if stored successfully
        """
        if not scid or not client_type or not history:
            return False

        ttl = ttl_seconds if ttl_seconds is not None else self.config.ttl_seconds
        now = datetime.now()
        expires_at = (now + timedelta(seconds=ttl)).isoformat() if ttl > 0 else None

        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation_state (
                        scid, client_type, authoritative_history, last_signature,
                        created_at, updated_at, expires_at, access_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(scid) DO UPDATE SET
                        client_type = excluded.client_type,
                        authoritative_history = excluded.authoritative_history,
                        last_signature = excluded.last_signature,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at,
                        access_count = access_count + 1
                    """,
                    (scid, client_type, history, signature, now.isoformat(), now.isoformat(), expires_at)
                )

                if cursor.rowcount > 0:
                    log.debug(f"[SIGNATURE_DB] Conversation state stored: scid={scid[:20]}...")
                    return True
                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error storing conversation state: {e}")
            return False

    def get_conversation_state(self, scid: str) -> Optional[Dict[str, Any]]:
        """
        Get conversation state by SCID

        Args:
            scid: Server Conversation ID

        Returns:
            Dictionary with state data if found and not expired, None otherwise
        """
        if not scid:
            return None

        try:
            with self._get_cursor(commit=False) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM conversation_state
                    WHERE scid = ?
                    """,
                    (scid,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                # Check expiration
                if row["expires_at"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now() > expires_at:
                        # Delete expired entry
                        self.delete_conversation_state(scid)
                        return None

                # Update access stats
                self._update_conversation_state_access_stats(scid)

                log.debug(f"[SIGNATURE_DB] Conversation state hit: scid={scid[:20]}...")

                import json
                return {
                    "scid": row["scid"],
                    "client_type": row["client_type"],
                    "authoritative_history": json.loads(row["authoritative_history"]) if row["authoritative_history"] else [],
                    "last_signature": row["last_signature"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "expires_at": row["expires_at"],
                    "access_count": row["access_count"]
                }

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error getting conversation state: {e}")
            return None

    def update_conversation_state(
        self,
        scid: str,
        history: str,
        signature: Optional[str] = None
    ) -> bool:
        """
        Update existing conversation state

        Args:
            scid: Server Conversation ID
            history: Updated JSON-formatted authoritative history
            signature: Updated last valid signature

        Returns:
            True if updated successfully
        """
        if not scid or not history:
            return False

        try:
            now = datetime.now()
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE conversation_state
                    SET authoritative_history = ?,
                        last_signature = ?,
                        updated_at = ?,
                        access_count = access_count + 1
                    WHERE scid = ?
                    """,
                    (history, signature, now.isoformat(), scid)
                )

                if cursor.rowcount > 0:
                    log.debug(f"[SIGNATURE_DB] Conversation state updated: scid={scid[:20]}...")
                    return True
                return False

        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error updating conversation state: {e}")
            return False

    def delete_conversation_state(self, scid: str) -> bool:
        """
        Delete conversation state entry

        Args:
            scid: Server Conversation ID

        Returns:
            True if deleted successfully
        """
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM conversation_state WHERE scid = ?",
                    (scid,)
                )
                if cursor.rowcount > 0:
                    log.debug(f"[SIGNATURE_DB] Conversation state deleted: scid={scid[:20]}...")
                    return True
                return False
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error deleting conversation state: {e}")
            return False

    def cleanup_expired_states(self) -> int:
        """
        Remove expired conversation state entries

        Returns:
            Number of entries removed
        """
        try:
            now = datetime.now().isoformat()
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM conversation_state
                    WHERE expires_at IS NOT NULL AND expires_at < ?
                    """,
                    (now,)
                )
                count = cursor.rowcount
                if count > 0:
                    log.info(f"[SIGNATURE_DB] Cleaned up {count} expired conversation state entries")
                return count
        except Exception as e:
            log.error(f"[SIGNATURE_DB] Error cleaning up conversation states: {e}")
            return 0

    def _update_conversation_state_access_stats(self, scid: str) -> None:
        """Update access count for conversation state entry"""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE conversation_state
                    SET access_count = access_count + 1
                    WHERE scid = ?
                    """,
                    (scid,)
                )
        except Exception as e:
            log.debug(f"[SIGNATURE_DB] Error updating conversation state access stats: {e}")

    def close(self) -> None:
        """Close database connection for current thread"""
        if hasattr(self._local, 'connection') and self._local.connection:
            try:
                self._local.connection.close()
                self._local.connection = None
                log.debug("[SIGNATURE_DB] Connection closed")
            except Exception as e:
                log.error(f"[SIGNATURE_DB] Error closing connection: {e}")

    def __del__(self):
        """Cleanup on deletion"""
        self.close()
