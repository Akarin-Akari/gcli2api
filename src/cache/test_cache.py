"""
Unit Tests for Cache Module
缓存模块单元测试

Tests cover:
    - CacheEntry and CacheConfig data classes
    - MemoryCache (L1) operations
    - SignatureDatabase (L2) operations
    - SignatureCacheManager layered operations
    - AsyncWriteQueue background processing
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from typing import List

# Add src and gcli2api to path for imports
try:
    _current_file = os.path.abspath(__file__)
except NameError:
    _current_file = os.path.abspath("src/cache/test_cache.py")

_src_dir = os.path.dirname(os.path.dirname(_current_file))
_gcli2api_dir = os.path.dirname(_src_dir)

if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _gcli2api_dir not in sys.path:
    sys.path.insert(0, _gcli2api_dir)

from cache.cache_interface import (
    CacheConfig,
    CacheEntry,
    CacheStats,
    build_cache_key,
    generate_thinking_hash,
    parse_cache_key,
)
from cache.memory_cache import MemoryCache, RWLock
from cache.signature_database import SignatureDatabase
from cache.signature_cache_manager import (
    LayeredCacheConfig,
    SignatureCacheManager,
)
from cache.async_write_queue import (
    AsyncWriteConfig,
    AsyncWriteQueue,
    QueueState,
)


class TestCacheInterface(unittest.TestCase):
    """Test cache interface data classes and utilities"""

    def test_cache_entry_creation(self):
        """Test CacheEntry creation with defaults"""
        entry = CacheEntry(
            signature="test_signature",
            thinking_hash="abc123"
        )
        self.assertEqual(entry.signature, "test_signature")
        self.assertEqual(entry.thinking_hash, "abc123")
        self.assertEqual(entry.namespace, "default")
        self.assertEqual(entry.access_count, 0)
        self.assertIsNotNone(entry.created_at)

    def test_cache_entry_expiration(self):
        """Test CacheEntry expiration check"""
        # Not expired (no expiration set)
        entry = CacheEntry(signature="sig", thinking_hash="hash")
        self.assertFalse(entry.is_expired())

        # Not expired (future expiration)
        entry.expires_at = datetime.now() + timedelta(hours=1)
        self.assertFalse(entry.is_expired())

        # Expired (past expiration)
        entry.expires_at = datetime.now() - timedelta(hours=1)
        self.assertTrue(entry.is_expired())

    def test_cache_entry_touch(self):
        """Test CacheEntry touch method"""
        entry = CacheEntry(signature="sig", thinking_hash="hash")
        self.assertEqual(entry.access_count, 0)
        self.assertIsNone(entry.last_accessed_at)

        entry.touch()
        self.assertEqual(entry.access_count, 1)
        self.assertIsNotNone(entry.last_accessed_at)

        entry.touch()
        self.assertEqual(entry.access_count, 2)

    def test_cache_entry_serialization(self):
        """Test CacheEntry to_dict and from_dict"""
        entry = CacheEntry(
            signature="test_sig",
            thinking_hash="test_hash",
            thinking_prefix="prefix",
            model="claude-3",
            namespace="test_ns",
            metadata={"key": "value"}
        )

        # Serialize
        data = entry.to_dict()
        self.assertEqual(data["signature"], "test_sig")
        self.assertEqual(data["model"], "claude-3")

        # Deserialize
        restored = CacheEntry.from_dict(data)
        self.assertEqual(restored.signature, entry.signature)
        self.assertEqual(restored.thinking_hash, entry.thinking_hash)
        self.assertEqual(restored.model, entry.model)

    def test_cache_config_validation(self):
        """Test CacheConfig validation"""
        # Valid config
        config = CacheConfig(max_size=1000, ttl_seconds=3600)
        errors = config.validate()
        self.assertEqual(len(errors), 0)

        # Invalid config
        config = CacheConfig(max_size=-1, ttl_seconds=-1, eviction_policy="invalid")
        errors = config.validate()
        self.assertGreater(len(errors), 0)

    def test_generate_thinking_hash(self):
        """Test thinking hash generation"""
        text = "This is a test thinking text"
        hash_val, prefix = generate_thinking_hash(text, prefix_length=10)

        self.assertEqual(len(hash_val), 64)  # SHA-256 hex
        self.assertEqual(prefix, "This is a ")

        # Same text should produce same hash
        hash_val2, _ = generate_thinking_hash(text)
        self.assertEqual(hash_val, hash_val2)

        # Different text should produce different hash
        hash_val3, _ = generate_thinking_hash("Different text")
        self.assertNotEqual(hash_val, hash_val3)

    def test_build_and_parse_cache_key(self):
        """Test cache key building and parsing"""
        # With conversation_id
        key = build_cache_key("hash123", "namespace1", "conv456")
        self.assertEqual(key, "namespace1:conv456:hash123")

        ns, conv, h = parse_cache_key(key)
        self.assertEqual(ns, "namespace1")
        self.assertEqual(conv, "conv456")
        self.assertEqual(h, "hash123")

        # Without conversation_id
        key = build_cache_key("hash123", "namespace1", None)
        self.assertEqual(key, "namespace1:_:hash123")

        ns, conv, h = parse_cache_key(key)
        self.assertEqual(ns, "namespace1")
        self.assertIsNone(conv)
        self.assertEqual(h, "hash123")


class TestRWLock(unittest.TestCase):
    """Test read-write lock implementation"""

    def test_multiple_readers(self):
        """Test multiple readers can hold lock simultaneously"""
        lock = RWLock()
        results = []

        def reader(reader_id):
            with lock.read_lock():
                results.append(f"reader_{reader_id}_start")
                time.sleep(0.1)
                results.append(f"reader_{reader_id}_end")

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All readers should overlap
        self.assertEqual(len(results), 6)

    def test_writer_exclusive(self):
        """Test writer has exclusive access"""
        lock = RWLock()
        results = []

        def writer():
            with lock.write_lock():
                results.append("writer_start")
                time.sleep(0.1)
                results.append("writer_end")

        def reader():
            time.sleep(0.05)  # Start slightly after writer
            with lock.read_lock():
                results.append("reader")

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Reader should wait for writer
        self.assertEqual(results[0], "writer_start")
        self.assertEqual(results[1], "writer_end")


class TestMemoryCache(unittest.TestCase):
    """Test L1 memory cache"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = CacheConfig(max_size=100, ttl_seconds=3600)
        self.cache = MemoryCache(self.config)

    def tearDown(self):
        """Clean up after tests"""
        self.cache.clear()

    def test_set_and_get(self):
        """Test basic set and get operations"""
        entry = CacheEntry(
            signature="test_sig",
            thinking_hash="test_hash",
            namespace="default"
        )

        # Set
        result = self.cache.set(entry)
        self.assertTrue(result)

        # Get
        retrieved = self.cache.get("test_hash", "default")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.signature, "test_sig")

    def test_get_miss(self):
        """Test cache miss"""
        result = self.cache.get("nonexistent", "default")
        self.assertIsNone(result)

    def test_delete(self):
        """Test delete operation"""
        entry = CacheEntry(signature="sig", thinking_hash="hash")
        self.cache.set(entry)

        # Delete
        result = self.cache.delete("hash", "default")
        self.assertTrue(result)

        # Verify deleted
        retrieved = self.cache.get("hash", "default")
        self.assertIsNone(retrieved)

    def test_clear(self):
        """Test clear operation"""
        for i in range(10):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            self.cache.set(entry)

        self.assertEqual(self.cache.size(), 10)

        # Clear all
        count = self.cache.clear()
        self.assertEqual(count, 10)
        self.assertEqual(self.cache.size(), 0)

    def test_lru_eviction(self):
        """Test LRU eviction when max_size is reached"""
        config = CacheConfig(max_size=5, eviction_policy="lru")
        cache = MemoryCache(config)

        # Fill cache
        for i in range(5):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            cache.set(entry)

        # Access first entry to make it recently used
        cache.get("hash_0", "default")

        # Add one more to trigger eviction
        entry = CacheEntry(signature="sig_new", thinking_hash="hash_new")
        cache.set(entry)

        # hash_1 should be evicted (least recently used)
        self.assertIsNone(cache.get("hash_1", "default"))
        # hash_0 should still exist (was accessed)
        self.assertIsNotNone(cache.get("hash_0", "default"))
        # New entry should exist
        self.assertIsNotNone(cache.get("hash_new", "default"))

    def test_ttl_expiration(self):
        """Test TTL-based expiration"""
        config = CacheConfig(ttl_seconds=1)  # 1 second TTL
        cache = MemoryCache(config)

        entry = CacheEntry(signature="sig", thinking_hash="hash")
        cache.set(entry)

        # Should exist immediately
        self.assertIsNotNone(cache.get("hash", "default"))

        # Wait for expiration
        time.sleep(1.5)

        # Should be expired
        self.assertIsNone(cache.get("hash", "default"))

    def test_namespace_isolation(self):
        """Test namespace isolation"""
        entry1 = CacheEntry(signature="sig1", thinking_hash="hash", namespace="ns1")
        entry2 = CacheEntry(signature="sig2", thinking_hash="hash", namespace="ns2")

        self.cache.set(entry1)
        self.cache.set(entry2)

        # Same hash, different namespaces
        result1 = self.cache.get("hash", "ns1")
        result2 = self.cache.get("hash", "ns2")

        self.assertEqual(result1.signature, "sig1")
        self.assertEqual(result2.signature, "sig2")

    def test_bulk_operations(self):
        """Test bulk set and delete"""
        entries = [
            CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            for i in range(10)
        ]

        # Bulk set
        count = self.cache.bulk_set(entries)
        self.assertEqual(count, 10)
        self.assertEqual(self.cache.size(), 10)

        # Bulk delete
        hashes = [f"hash_{i}" for i in range(5)]
        deleted = self.cache.bulk_delete(hashes)
        self.assertEqual(deleted, 5)
        self.assertEqual(self.cache.size(), 5)

    def test_get_stats(self):
        """Test statistics collection"""
        entry = CacheEntry(signature="sig", thinking_hash="hash")
        self.cache.set(entry)

        # Hit
        self.cache.get("hash", "default")
        # Miss
        self.cache.get("nonexistent", "default")

        stats = self.cache.get_stats()
        self.assertEqual(stats.hits, 1)
        self.assertEqual(stats.misses, 1)
        self.assertEqual(stats.total_writes, 1)


class TestSignatureDatabase(unittest.TestCase):
    """Test L2 SQLite database"""

    def setUp(self):
        """Set up test fixtures"""
        # Use temporary database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cache.db")
        self.config = CacheConfig(db_path=self.db_path, ttl_seconds=3600)
        self.db = SignatureDatabase(self.config)

    def tearDown(self):
        """Clean up after tests"""
        self.db.close()
        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_and_get(self):
        """Test basic set and get operations"""
        entry = CacheEntry(
            signature="test_sig",
            thinking_hash="test_hash",
            namespace="default"
        )

        # Set
        result = self.db.set(entry)
        self.assertTrue(result)

        # Get
        retrieved = self.db.get("test_hash", "default")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.signature, "test_sig")

    def test_persistence(self):
        """Test data persistence across connections"""
        entry = CacheEntry(signature="persistent_sig", thinking_hash="persistent_hash")
        self.db.set(entry)
        self.db.close()

        # Reopen database
        db2 = SignatureDatabase(self.config)
        retrieved = db2.get("persistent_hash", "default")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.signature, "persistent_sig")
        db2.close()

    def test_bulk_set(self):
        """Test bulk set operation"""
        entries = [
            CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            for i in range(100)
        ]

        count = self.db.bulk_set(entries)
        self.assertEqual(count, 100)
        self.assertEqual(self.db.size(), 100)

    def test_cleanup_expired(self):
        """Test expired entry cleanup"""
        # Add expired entry
        entry = CacheEntry(
            signature="expired_sig",
            thinking_hash="expired_hash",
            expires_at=datetime.now() - timedelta(hours=1)
        )
        self.db.set(entry)

        # Add valid entry
        valid_entry = CacheEntry(
            signature="valid_sig",
            thinking_hash="valid_hash",
            expires_at=datetime.now() + timedelta(hours=1)
        )
        self.db.set(valid_entry)

        # Cleanup
        count = self.db.cleanup_expired()
        self.assertEqual(count, 1)

        # Verify
        self.assertIsNone(self.db.get("expired_hash", "default"))
        self.assertIsNotNone(self.db.get("valid_hash", "default"))


class TestSignatureCacheManager(unittest.TestCase):
    """Test layered cache manager"""

    def setUp(self):
        """Set up test fixtures"""
        # Reset singleton
        SignatureCacheManager.reset_instance()

        # Use temporary database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cache.db")

        self.config = LayeredCacheConfig(
            l1_config=CacheConfig(max_size=100, ttl_seconds=3600),
            l2_config=CacheConfig(db_path=self.db_path, ttl_seconds=86400),
            enable_l2=True,
            async_write=False,  # Sync for testing
            fallback_any_namespace=True
        )
        self.manager = SignatureCacheManager(self.config)

    def tearDown(self):
        """Clean up after tests"""
        self.manager.shutdown(sync_before_close=False)
        SignatureCacheManager.reset_instance()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cache_and_retrieve(self):
        """Test basic cache and retrieve"""
        thinking = "This is test thinking text"
        signature = "test_signature_value"

        # Cache
        result = self.manager.cache_signature(
            thinking_text=thinking,
            signature=signature,
            model="claude-3",
            namespace="test"
        )
        self.assertTrue(result)

        # Retrieve
        retrieved = self.manager.get_cached_signature(
            thinking_text=thinking,
            namespace="test"
        )
        self.assertEqual(retrieved, signature)

    def test_l1_to_l2_promotion(self):
        """Test L2 hit promotes to L1"""
        thinking = "Promotion test thinking"
        signature = "promotion_signature"

        # Cache (writes to both L1 and L2)
        self.manager.cache_signature(thinking, signature)

        # Clear L1 only
        self.manager._l1_cache.clear()

        # Retrieve (should hit L2 and promote to L1)
        retrieved = self.manager.get_cached_signature(thinking)
        self.assertEqual(retrieved, signature)

        # Verify promoted to L1
        thinking_hash, _ = generate_thinking_hash(thinking)
        l1_entry = self.manager._l1_cache.get(thinking_hash, "default")
        self.assertIsNotNone(l1_entry)

    def test_fallback_any_namespace(self):
        """Test fallback lookup across namespaces"""
        thinking = "Fallback test thinking"
        signature = "fallback_signature"

        # Cache in namespace1
        self.manager.cache_signature(thinking, signature, namespace="namespace1")

        # Try to retrieve from namespace2 (should fallback to namespace1)
        retrieved = self.manager.get_cached_signature(thinking, namespace="namespace2")
        self.assertEqual(retrieved, signature)

    def test_delete(self):
        """Test delete operation"""
        thinking = "Delete test thinking"
        signature = "delete_signature"

        self.manager.cache_signature(thinking, signature)
        self.assertTrue(self.manager.exists(thinking))

        self.manager.delete(thinking)
        self.assertFalse(self.manager.exists(thinking))

    def test_clear(self):
        """Test clear operation"""
        for i in range(10):
            self.manager.cache_signature(f"thinking_{i}", f"sig_{i}")

        sizes = self.manager.size()
        self.assertEqual(sizes["l1_size"], 10)

        self.manager.clear()

        sizes = self.manager.size()
        self.assertEqual(sizes["l1_size"], 0)

    def test_get_stats(self):
        """Test statistics aggregation"""
        self.manager.cache_signature("thinking1", "sig1")
        self.manager.get_cached_signature("thinking1")  # Hit
        self.manager.get_cached_signature("nonexistent")  # Miss

        stats = self.manager.get_stats()
        self.assertEqual(stats.l1_stats.hits, 1)
        self.assertEqual(stats.l1_stats.misses, 1)
        self.assertEqual(stats.total_requests, 2)


class TestAsyncWriteQueue(unittest.TestCase):
    """Test async write queue"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_async.db")
        self.db = SignatureDatabase(CacheConfig(db_path=self.db_path))

        self.config = AsyncWriteConfig(
            batch_size=5,
            batch_timeout_ms=100,
            max_retries=2
        )
        self.queue = AsyncWriteQueue(self.db, self.config)

    def tearDown(self):
        """Clean up after tests"""
        if self.queue.state == QueueState.RUNNING:
            self.queue.stop(wait=True)
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_start_stop(self):
        """Test queue start and stop"""
        self.assertEqual(self.queue.state, QueueState.STOPPED)

        self.queue.start()
        self.assertEqual(self.queue.state, QueueState.RUNNING)

        self.queue.stop(wait=True)
        self.assertEqual(self.queue.state, QueueState.STOPPED)

    def test_enqueue_and_process(self):
        """Test enqueue and background processing"""
        self.queue.start()

        # Enqueue entries
        for i in range(10):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            result = self.queue.enqueue(entry)
            self.assertTrue(result)

        # Wait for processing
        self.queue.wait_until_empty(timeout=5.0)

        # Verify in database
        for i in range(10):
            entry = self.db.get(f"hash_{i}", "default")
            self.assertIsNotNone(entry)

    def test_batch_processing(self):
        """Test batch commit optimization"""
        self.queue.start()

        # Enqueue exactly batch_size entries
        for i in range(5):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            self.queue.enqueue(entry)

        # Wait for batch to process
        time.sleep(0.5)

        stats = self.queue.get_stats()
        self.assertGreaterEqual(stats.batch_count, 1)

    def test_queue_overflow(self):
        """Test queue overflow handling"""
        config = AsyncWriteConfig(max_queue_size=5, drop_on_overflow=True)
        queue = AsyncWriteQueue(self.db, config)
        # Don't start - queue will fill up

        # Fill queue
        for i in range(10):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            queue.enqueue(entry)

        stats = queue.get_stats()
        # Some should be dropped
        self.assertGreater(stats.total_dropped, 0)


class TestConcurrency(unittest.TestCase):
    """Test thread safety and concurrency"""

    def test_memory_cache_concurrent_access(self):
        """Test concurrent access to memory cache"""
        cache = MemoryCache(CacheConfig(max_size=1000))
        errors: List[Exception] = []

        def writer(thread_id):
            try:
                for i in range(100):
                    entry = CacheEntry(
                        signature=f"sig_{thread_id}_{i}",
                        thinking_hash=f"hash_{thread_id}_{i}"
                    )
                    cache.set(entry)
            except Exception as e:
                errors.append(e)

        def reader(thread_id):
            try:
                for i in range(100):
                    cache.get(f"hash_{thread_id}_{i}", "default")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_cache_manager_concurrent_access(self):
        """Test concurrent access to cache manager"""
        SignatureCacheManager.reset_instance()

        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "concurrent_test.db")

        config = LayeredCacheConfig(
            l1_config=CacheConfig(max_size=1000),
            l2_config=CacheConfig(db_path=db_path),
            enable_l2=True,
            async_write=False
        )
        manager = SignatureCacheManager(config)
        errors: List[Exception] = []

        def worker(thread_id):
            try:
                for i in range(50):
                    thinking = f"thinking_{thread_id}_{i}"
                    sig = f"sig_{thread_id}_{i}"
                    manager.cache_signature(thinking, sig)
                    manager.get_cached_signature(thinking)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

        manager.shutdown()
        SignatureCacheManager.reset_instance()
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
