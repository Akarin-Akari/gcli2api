"""
Test suite for ContentHashCache
测试 ContentHashCache 的功能

Author: Claude Sonnet 4.5 (浮浮酱)
Date: 2026-01-17
"""

import pytest
import time
from datetime import datetime, timedelta
from src.ide_compat.hash_cache import ContentHashCache, HashCacheEntry, HashCacheStats


class TestContentHashCache:
    """Test ContentHashCache functionality"""

    def test_basic_set_get(self):
        """Test basic set and get operations"""
        cache = ContentHashCache(max_size=100, ttl_seconds=3600)

        # Store signature
        thinking_text = "Let me think about this problem..."
        signature = "EqQBCgxhYmNkZWZnaGlqa2w="

        assert cache.set(thinking_text, signature) is True

        # Retrieve signature
        result = cache.get(thinking_text)
        assert result == signature

    def test_exact_hash_match(self):
        """Test exact hash matching"""
        cache = ContentHashCache()

        thinking_text = "Let me analyze this code carefully."
        signature = "sig123"

        cache.set(thinking_text, signature)

        # Exact match should work
        assert cache.get(thinking_text) == signature

    def test_normalized_hash_match(self):
        """Test normalized hash matching"""
        cache = ContentHashCache()

        # Original text
        thinking_text = "Let me think about this"
        signature = "sig456"

        cache.set(thinking_text, signature)

        # Text with extra spaces should still match
        transformed_text = "Let  me   think    about   this"
        assert cache.get(transformed_text) == signature

        # Text with different line endings should match
        transformed_text2 = "Let me think\r\nabout\rthis"
        assert cache.get(transformed_text2) == signature

    def test_prefix_matching(self):
        """Test prefix matching for truncated text"""
        cache = ContentHashCache(min_prefix_length=20)

        # Store long thinking text
        thinking_text = "Let me analyze this complex problem step by step. First, I need to understand the requirements..."
        signature = "sig789"

        cache.set(thinking_text, signature)

        # Truncated text should match via prefix
        truncated_text = "Let me analyze this complex problem step by step. First"
        result = cache.get_with_prefix_match(truncated_text, min_prefix_len=20)
        assert result == signature

    def test_cache_miss(self):
        """Test cache miss scenarios"""
        cache = ContentHashCache()

        # Get non-existent entry
        assert cache.get("non-existent text") is None

        # Prefix match with too short text
        assert cache.get_with_prefix_match("short", min_prefix_len=100) is None

    def test_ttl_expiration(self):
        """Test TTL expiration"""
        cache = ContentHashCache(ttl_seconds=1)  # 1 second TTL

        thinking_text = "Temporary thinking"
        signature = "temp_sig"

        cache.set(thinking_text, signature)

        # Should be available immediately
        assert cache.get(thinking_text) == signature

        # Wait for expiration
        time.sleep(1.5)

        # Should be expired
        assert cache.get(thinking_text) is None

    def test_lru_eviction(self):
        """Test LRU eviction policy"""
        cache = ContentHashCache(max_size=3)

        # Add 3 entries
        cache.set("text1", "sig1")
        cache.set("text2", "sig2")
        cache.set("text3", "sig3")

        # All should be present
        assert cache.get("text1") == "sig1"
        assert cache.get("text2") == "sig2"
        assert cache.get("text3") == "sig3"

        # Add 4th entry, should evict least recently used (text1)
        cache.set("text4", "sig4")

        # text1 should be evicted
        assert cache.get("text1") is None
        assert cache.get("text2") == "sig2"
        assert cache.get("text3") == "sig3"
        assert cache.get("text4") == "sig4"

    def test_lru_access_order(self):
        """Test LRU access order update"""
        cache = ContentHashCache(max_size=3)

        # Add 3 entries
        cache.set("text1", "sig1")
        cache.set("text2", "sig2")
        cache.set("text3", "sig3")

        # Access text1 to move it to end
        cache.get("text1")

        # Add 4th entry, should evict text2 (now least recently used)
        cache.set("text4", "sig4")

        # text2 should be evicted, text1 should remain
        assert cache.get("text1") == "sig1"
        assert cache.get("text2") is None
        assert cache.get("text3") == "sig3"
        assert cache.get("text4") == "sig4"

    def test_cleanup_expired(self):
        """Test cleanup of expired entries"""
        cache = ContentHashCache(ttl_seconds=1)

        # Add entries
        cache.set("text1", "sig1")
        cache.set("text2", "sig2")

        # Wait for expiration
        time.sleep(1.5)

        # Cleanup
        count = cache.cleanup_expired()
        assert count == 2

        # Entries should be gone
        assert cache.get("text1") is None
        assert cache.get("text2") is None

    def test_statistics(self):
        """Test cache statistics"""
        cache = ContentHashCache(max_size=10)

        # Add entries
        cache.set("text1", "sig1")
        cache.set("text2", "sig2")

        # Access entries
        cache.get("text1")  # Hit
        cache.get("text1")  # Hit
        cache.get("text3")  # Miss

        stats = cache.get_stats()

        assert stats["exact_hits"] == 2
        assert stats["misses"] == 1
        assert stats["total_writes"] == 2
        assert stats["current_size"] == 2
        assert stats["hit_rate"] == 2 / 3

    def test_clear(self):
        """Test clearing all entries"""
        cache = ContentHashCache()

        # Add entries
        cache.set("text1", "sig1")
        cache.set("text2", "sig2")
        cache.set("text3", "sig3")

        # Clear
        count = cache.clear()
        assert count == 3

        # All should be gone
        assert cache.get("text1") is None
        assert cache.get("text2") is None
        assert cache.get("text3") is None

        stats = cache.get_stats()
        assert stats["current_size"] == 0

    def test_normalize_text(self):
        """Test text normalization"""
        # Test strip
        assert ContentHashCache.normalize_text("  text  ") == "text"

        # Test whitespace collapse
        assert ContentHashCache.normalize_text("a  b   c") == "a b c"

        # Test line ending normalization
        assert ContentHashCache.normalize_text("a\r\nb\rc") == "a b c"

        # Test combined
        assert ContentHashCache.normalize_text("  a  \r\n  b  \r  c  ") == "a b c"

    def test_compute_hash(self):
        """Test hash computation"""
        text = "test text"

        # Exact hash
        hash1 = ContentHashCache.compute_hash(text, normalize=False)
        assert len(hash1) == 64  # SHA256 is 64 hex chars

        # Normalized hash
        hash2 = ContentHashCache.compute_hash(text, normalize=True)
        assert len(hash2) == 64

        # Same text should produce same hash
        hash3 = ContentHashCache.compute_hash(text, normalize=False)
        assert hash1 == hash3

        # Different normalization should produce different hash
        text_with_spaces = "test  text"
        hash4 = ContentHashCache.compute_hash(text_with_spaces, normalize=False)
        hash5 = ContentHashCache.compute_hash(text_with_spaces, normalize=True)
        assert hash4 != hash5
        assert hash5 == hash2  # Normalized should match original

    def test_empty_input(self):
        """Test handling of empty input"""
        cache = ContentHashCache()

        # Empty text should return False
        assert cache.set("", "sig") is False
        assert cache.set("text", "") is False

        # Empty get should return None
        assert cache.get("") is None

    def test_concurrent_access(self):
        """Test thread safety (basic check)"""
        import threading

        cache = ContentHashCache(max_size=100)
        results = []

        def worker(i):
            text = f"thinking_{i}"
            sig = f"sig_{i}"
            cache.set(text, sig)
            result = cache.get(text)
            results.append(result == sig)

        # Create multiple threads
        threads = []
        for i in range(10):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # All operations should succeed
        assert all(results)

    def test_hash_cache_entry(self):
        """Test HashCacheEntry data class"""
        entry = HashCacheEntry(
            content_hash="hash1",
            normalized_hash="hash2",
            signature="sig1",
            thinking_text="thinking text",
            thinking_prefix="thinking"
        )

        # Test is_expired
        assert entry.is_expired() is False

        entry.expires_at = datetime.now() - timedelta(seconds=1)
        assert entry.is_expired() is True

        # Test touch
        entry.touch()
        assert entry.access_count == 1
        assert entry.last_accessed_at is not None

    def test_hash_cache_stats(self):
        """Test HashCacheStats data class"""
        stats = HashCacheStats()

        stats.exact_hits = 10
        stats.normalized_hits = 5
        stats.prefix_hits = 2
        stats.misses = 3

        assert stats.total_hits == 17
        assert stats.hit_rate == 17 / 20

        # Test to_dict
        stats_dict = stats.to_dict()
        assert stats_dict["exact_hits"] == 10
        assert stats_dict["total_hits"] == 17
        assert stats_dict["hit_rate"] == 17 / 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
