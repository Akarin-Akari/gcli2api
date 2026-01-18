"""
Phase 1 Verification Script
验证 Phase 1 所有模块正常工作
"""

import os
import sys
import tempfile
import shutil

# Setup paths
_current_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.dirname(_current_dir)
_gcli2api_dir = os.path.dirname(_src_dir)

if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _gcli2api_dir not in sys.path:
    sys.path.insert(0, _gcli2api_dir)

def test_imports():
    """Test all imports work correctly"""
    print("=" * 60)
    print("Testing imports...")

    from cache import (
        # Core interfaces
        ICacheLayer,
        CacheEntry,
        CacheConfig,
        CacheStats,
        # L1 Memory Cache
        MemoryCache,
        RWLock,
        # L2 SQLite Database
        SignatureDatabase,
        # Layered Cache Manager
        SignatureCacheManager,
        LayeredCacheConfig,
        AggregatedStats,
        # Async Write Queue
        AsyncWriteQueue,
        AsyncWriteConfig,
        QueueStats,
        QueueState,
        WriteTask,
        # Convenience functions
        generate_thinking_hash,
        build_cache_key,
        parse_cache_key,
    )

    print("[OK] All imports successful!")
    return True


def test_cache_interface():
    """Test cache interface utilities"""
    print("\n" + "=" * 60)
    print("Testing cache interface...")

    from cache import CacheEntry, CacheConfig, generate_thinking_hash, build_cache_key, parse_cache_key

    # Test CacheEntry
    entry = CacheEntry(
        signature="test_signature",
        thinking_hash="abc123",
        namespace="test"
    )
    assert entry.signature == "test_signature"
    assert entry.thinking_hash == "abc123"
    assert entry.namespace == "test"
    assert entry.access_count == 0
    print("  [OK] CacheEntry creation works")

    # Test touch
    entry.touch()
    assert entry.access_count == 1
    print("  [OK] CacheEntry.touch() works")

    # Test serialization
    data = entry.to_dict()
    restored = CacheEntry.from_dict(data)
    assert restored.signature == entry.signature
    print("  [OK] CacheEntry serialization works")

    # Test hash generation
    text = "This is a test thinking text"
    hash_val, prefix = generate_thinking_hash(text, prefix_length=10)
    assert len(hash_val) == 64  # SHA-256 hex
    assert prefix == "This is a "
    print("  [OK] generate_thinking_hash() works")

    # Test cache key
    key = build_cache_key("hash123", "namespace1", "conv456")
    assert key == "namespace1:conv456:hash123"
    ns, conv, h = parse_cache_key(key)
    assert ns == "namespace1"
    assert conv == "conv456"
    assert h == "hash123"
    print("  [OK] build_cache_key() and parse_cache_key() work")

    print("[OK] Cache interface tests passed!")
    return True


def test_memory_cache():
    """Test L1 memory cache"""
    print("\n" + "=" * 60)
    print("Testing L1 memory cache...")

    from cache import MemoryCache, CacheConfig, CacheEntry

    config = CacheConfig(max_size=100, ttl_seconds=3600)
    cache = MemoryCache(config)

    # Test set and get
    entry = CacheEntry(
        signature="test_sig",
        thinking_hash="test_hash",
        namespace="default"
    )
    result = cache.set(entry)
    assert result == True
    print("  [OK] MemoryCache.set() works")

    retrieved = cache.get("test_hash", "default")
    assert retrieved is not None
    assert retrieved.signature == "test_sig"
    print("  [OK] MemoryCache.get() works")

    # Test miss
    miss = cache.get("nonexistent", "default")
    assert miss is None
    print("  [OK] MemoryCache miss handling works")

    # Test delete
    deleted = cache.delete("test_hash", "default")
    assert deleted == True
    assert cache.get("test_hash", "default") is None
    print("  [OK] MemoryCache.delete() works")

    # Test bulk operations
    entries = [
        CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
        for i in range(10)
    ]
    count = cache.bulk_set(entries)
    assert count == 10
    assert cache.size() == 10
    print("  [OK] MemoryCache.bulk_set() works")

    # Test clear
    cleared = cache.clear()
    assert cleared == 10
    assert cache.size() == 0
    print("  [OK] MemoryCache.clear() works")

    # Test stats
    stats = cache.get_stats()
    assert stats.hits >= 0
    assert stats.misses >= 0
    print("  [OK] MemoryCache.get_stats() works")

    print("[OK] L1 memory cache tests passed!")
    return True


def test_signature_database():
    """Test L2 SQLite database"""
    print("\n" + "=" * 60)
    print("Testing L2 SQLite database...")

    from cache import SignatureDatabase, CacheConfig, CacheEntry

    # Use temporary database
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_cache.db")

    try:
        config = CacheConfig(db_path=db_path, ttl_seconds=3600)
        db = SignatureDatabase(config)

        # Test set and get
        entry = CacheEntry(
            signature="test_sig",
            thinking_hash="test_hash",
            namespace="default"
        )
        result = db.set(entry)
        assert result == True
        print("  [OK] SignatureDatabase.set() works")

        retrieved = db.get("test_hash", "default")
        assert retrieved is not None
        assert retrieved.signature == "test_sig"
        print("  [OK] SignatureDatabase.get() works")

        # Test persistence
        db.close()
        db2 = SignatureDatabase(config)
        retrieved2 = db2.get("test_hash", "default")
        assert retrieved2 is not None
        assert retrieved2.signature == "test_sig"
        print("  [OK] SignatureDatabase persistence works")

        # Test bulk set
        entries = [
            CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            for i in range(10)
        ]
        count = db2.bulk_set(entries)
        assert count == 10
        print("  [OK] SignatureDatabase.bulk_set() works")

        # Test size
        size = db2.size()
        assert size == 11  # 1 original + 10 bulk
        print("  [OK] SignatureDatabase.size() works")

        db2.close()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] L2 SQLite database tests passed!")
    return True


def test_signature_cache_manager():
    """Test layered cache manager"""
    print("\n" + "=" * 60)
    print("Testing layered cache manager...")

    from cache import SignatureCacheManager, LayeredCacheConfig, CacheConfig

    # Reset singleton
    SignatureCacheManager.reset_instance()

    # Use temporary database
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_cache.db")

    try:
        config = LayeredCacheConfig(
            l1_config=CacheConfig(max_size=100, ttl_seconds=3600),
            l2_config=CacheConfig(db_path=db_path, ttl_seconds=86400),
            enable_l2=True,
            async_write=False,  # Sync for testing
            fallback_any_namespace=True
        )
        manager = SignatureCacheManager(config)

        # Test cache and retrieve
        thinking = "This is test thinking text"
        signature = "test_signature_value"

        result = manager.cache_signature(
            thinking_text=thinking,
            signature=signature,
            model="claude-3",
            namespace="test"
        )
        assert result == True
        print("  [OK] SignatureCacheManager.cache_signature() works")

        retrieved = manager.get_cached_signature(
            thinking_text=thinking,
            namespace="test"
        )
        assert retrieved == signature
        print("  [OK] SignatureCacheManager.get_cached_signature() works")

        # Test exists
        exists = manager.exists(thinking, namespace="test")
        assert exists == True
        print("  [OK] SignatureCacheManager.exists() works")

        # Test delete
        manager.delete(thinking, namespace="test")
        exists_after = manager.exists(thinking, namespace="test")
        assert exists_after == False
        print("  [OK] SignatureCacheManager.delete() works")

        # Test stats
        stats = manager.get_stats()
        assert stats.total_requests >= 0
        print("  [OK] SignatureCacheManager.get_stats() works")

        # Test size
        sizes = manager.size()
        assert "l1_size" in sizes
        assert "l2_size" in sizes
        print("  [OK] SignatureCacheManager.size() works")

        manager.shutdown(sync_before_close=False)
        SignatureCacheManager.reset_instance()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] Layered cache manager tests passed!")
    return True


def test_async_write_queue():
    """Test async write queue"""
    print("\n" + "=" * 60)
    print("Testing async write queue...")

    from cache import AsyncWriteQueue, AsyncWriteConfig, SignatureDatabase, CacheConfig, CacheEntry, QueueState
    import time

    # Use temporary database
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_async.db")

    try:
        db = SignatureDatabase(CacheConfig(db_path=db_path))

        config = AsyncWriteConfig(
            batch_size=5,
            batch_timeout_ms=100,
            max_retries=2
        )
        queue = AsyncWriteQueue(db, config)

        # Test start/stop
        assert queue.state == QueueState.STOPPED
        queue.start()
        assert queue.state == QueueState.RUNNING
        print("  [OK] AsyncWriteQueue.start() works")

        # Test enqueue
        for i in range(5):
            entry = CacheEntry(signature=f"sig_{i}", thinking_hash=f"hash_{i}")
            result = queue.enqueue(entry)
            assert result == True
        print("  [OK] AsyncWriteQueue.enqueue() works")

        # Wait for processing
        queue.wait_until_empty(timeout=5.0)

        # Verify in database
        for i in range(5):
            entry = db.get(f"hash_{i}", "default")
            assert entry is not None, f"Entry hash_{i} not found in database"
        print("  [OK] AsyncWriteQueue background processing works")

        # Test stats
        stats = queue.get_stats()
        assert stats.total_enqueued >= 5
        print("  [OK] AsyncWriteQueue.get_stats() works")

        queue.stop(wait=True)
        assert queue.state == QueueState.STOPPED
        print("  [OK] AsyncWriteQueue.stop() works")

        db.close()

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] Async write queue tests passed!")
    return True


def main():
    """Run all Phase 1 verification tests"""
    print("\n" + "=" * 60)
    print("  Phase 1 Verification - Cache Module")
    print("=" * 60)

    all_passed = True

    try:
        test_imports()
    except Exception as e:
        print(f"[FAIL] Import test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()
        return False

    try:
        test_cache_interface()
    except Exception as e:
        print(f"[FAIL] Cache interface test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()

    try:
        test_memory_cache()
    except Exception as e:
        print(f"[FAIL] Memory cache test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()

    try:
        test_signature_database()
    except Exception as e:
        print(f"[FAIL] Signature database test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()

    try:
        test_signature_cache_manager()
    except Exception as e:
        print(f"[FAIL] Signature cache manager test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()

    try:
        test_async_write_queue()
    except Exception as e:
        print(f"[FAIL] Async write queue test failed: {e}")
        all_passed = False
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    if all_passed:
        print("  [OK] ALL PHASE 1 TESTS PASSED!")
    else:
        print("  [FAIL] SOME TESTS FAILED")
    print("=" * 60 + "\n")

    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
