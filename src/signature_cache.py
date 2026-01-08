"""
Signature Cache Module - Thinking Block Signature 缓存管理器

用于缓存 Claude Extended Thinking 模式中的 signature，解决 Cursor IDE
在 OpenAI 兼容格式转换过程中丢失 signature 的问题。

核心功能：
1. 响应阶段：缓存 Antigravity 返回的 thinking + signature
2. 请求阶段：从缓存恢复 signature，注入到请求中

设计原则：
- 线程安全：使用 threading.Lock 保护并发访问
- LRU 淘汰：使用 OrderedDict 实现最近最少使用淘汰
- TTL 过期：支持时间过期机制
- 优雅降级：缓存失败不影响主流程

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-07
"""

import hashlib
import threading
import time
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

log = logging.getLogger("gcli2api.signature_cache")


@dataclass
class CacheEntry:
    """缓存条目数据结构"""
    signature: str
    thinking_text: str
    timestamp: float
    access_count: int = 0
    model: Optional[str] = None

    def is_expired(self, ttl_seconds: float) -> bool:
        """检查缓存条目是否过期"""
        return time.time() - self.timestamp > ttl_seconds


@dataclass
class CacheStats:
    """缓存统计数据"""
    hits: int = 0
    misses: int = 0
    writes: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        """计算缓存命中率"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": f"{self.hit_rate:.2%}",
            "total_requests": self.hits + self.misses
        }


class SignatureCache:
    """
    Thinking Signature 缓存管理器

    线程安全的 LRU 缓存实现，支持 TTL 过期机制。
    使用 thinking_text 内容的哈希作为缓存 key。

    Usage:
        cache = SignatureCache(max_size=10000, ttl_seconds=3600)

        # 响应阶段：缓存 signature
        cache.set(thinking_text="Let me think...", signature="EqQBCg...")

        # 请求阶段：恢复 signature
        signature = cache.get(thinking_text="Let me think...")
    """

    # 默认配置
    DEFAULT_MAX_SIZE = 10000
    DEFAULT_TTL_SECONDS = 3600  # 1 小时
    DEFAULT_KEY_PREFIX_LENGTH = 500  # 用于生成 key 的文本前缀长度

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        key_prefix_length: int = DEFAULT_KEY_PREFIX_LENGTH
    ):
        """
        初始化缓存管理器

        Args:
            max_size: 最大缓存条目数，超过后使用 LRU 淘汰
            ttl_seconds: 缓存过期时间（秒）
            key_prefix_length: 用于生成哈希 key 的文本前缀长度
        """
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._key_prefix_length = key_prefix_length
        self._stats = CacheStats()

        log.info(
            f"[SIGNATURE_CACHE] 初始化缓存管理器: "
            f"max_size={max_size}, ttl={ttl_seconds}s, key_prefix={key_prefix_length}"
        )

    def _normalize_thinking_text(self, thinking_text: str) -> str:
        """
        规范化 thinking 文本，去除可能的标签包裹

        这是 Part 5 修复的关键：确保写入和读取时使用相同的规范化内容，
        从而提高缓存命中率。

        Args:
            thinking_text: 原始 thinking 文本，可能包含 <think> 或 <reasoning> 标签

        Returns:
            规范化后的 thinking 文本
        """
        import re

        if not thinking_text:
            return ""

        text = thinking_text.strip()

        # 去除 <think>...</think> 标签
        match = re.match(r'^<think>\s*(.*?)\s*</think>\s*$', text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            log.debug(f"[SIGNATURE_CACHE] 规范化：去除 <think> 标签")

        # 去除 <reasoning>...</reasoning> 标签
        match = re.match(r'^<(?:redacted_)?reasoning>\s*(.*?)\s*</(?:redacted_)?reasoning>\s*$', text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            log.debug(f"[SIGNATURE_CACHE] 规范化：去除 <reasoning> 标签")

        return text

    def _generate_key(self, thinking_text: str) -> str:
        """
        生成缓存 key（基于规范化后的 thinking 内容的哈希）

        使用 MD5 哈希算法，取文本前 N 个字符以提高性能。
        MD5 足够用于缓存 key 生成，不需要加密级别的安全性。

        [Part 5 改进] 在生成 key 之前先规范化文本，确保写入和读取时
        使用相同的规范化内容，从而提高缓存命中率。

        Args:
            thinking_text: thinking 块的文本内容

        Returns:
            32 字符的十六进制哈希字符串
        """
        if not thinking_text:
            return ""

        # [Part 5] 规范化处理，去除可能的标签包裹
        normalized_text = self._normalize_thinking_text(thinking_text)
        if not normalized_text:
            return ""

        # 取前 N 个字符，避免过长的 thinking 影响性能
        text_prefix = normalized_text[:self._key_prefix_length]
        return hashlib.md5(text_prefix.encode('utf-8')).hexdigest()

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
            log.debug("[SIGNATURE_CACHE] 跳过缓存：thinking_text 或 signature 为空")
            return False

        # 验证 signature 格式
        if not self._is_valid_signature(signature):
            log.warning(f"[SIGNATURE_CACHE] 跳过缓存：signature 格式无效")
            return False

        key = self._generate_key(thinking_text)
        if not key:
            return False

        with self._lock:
            # 创建缓存条目
            entry = CacheEntry(
                signature=signature,
                thinking_text=thinking_text[:200],  # 只保存前 200 字符用于调试
                timestamp=time.time(),
                model=model
            )

            # 如果 key 已存在，先删除（更新访问顺序）
            if key in self._cache:
                del self._cache[key]

            # 添加到缓存
            self._cache[key] = entry
            self._stats.writes += 1

            # LRU 淘汰
            while len(self._cache) > self._max_size:
                oldest_key, _ = self._cache.popitem(last=False)
                self._stats.evictions += 1
                log.debug(f"[SIGNATURE_CACHE] LRU 淘汰: key={oldest_key[:16]}...")

            log.debug(
                f"[SIGNATURE_CACHE] 缓存写入成功: key={key[:16]}..., "
                f"thinking={thinking_text[:50]}..., "
                f"cache_size={len(self._cache)}"
            )
            return True

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

        key = self._generate_key(thinking_text)
        if not key:
            return None

        with self._lock:
            if key not in self._cache:
                self._stats.misses += 1
                log.debug(f"[SIGNATURE_CACHE] 缓存未命中: key={key[:16]}...")
                return None

            entry = self._cache[key]

            # 检查 TTL
            if entry.is_expired(self._ttl_seconds):
                del self._cache[key]
                self._stats.expirations += 1
                self._stats.misses += 1
                log.debug(f"[SIGNATURE_CACHE] 缓存已过期: key={key[:16]}...")
                return None

            # 更新访问顺序（LRU）
            self._cache.move_to_end(key)
            entry.access_count += 1
            self._stats.hits += 1

            log.info(
                f"[SIGNATURE_CACHE] 缓存命中: key={key[:16]}..., "
                f"thinking={thinking_text[:50]}..., "
                f"access_count={entry.access_count}"
            )
            return entry.signature

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

        with self._lock:
            if key in self._cache:
                del self._cache[key]
                log.info(f"[SIGNATURE_CACHE] 缓存失效: key={key[:16]}...")
                return True
            return False

    def clear(self) -> int:
        """
        清空所有缓存

        Returns:
            清除的条目数量
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            log.info(f"[SIGNATURE_CACHE] 清空缓存: 删除 {count} 条")
            return count

    def cleanup_expired(self) -> int:
        """
        清理所有过期的缓存条目

        Returns:
            清理的条目数量
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired(self._ttl_seconds)
            ]

            for key in expired_keys:
                del self._cache[key]
                self._stats.expirations += 1

            if expired_keys:
                log.info(f"[SIGNATURE_CACHE] 清理过期缓存: {len(expired_keys)} 条")

            return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息

        Returns:
            包含命中率、写入次数等统计信息的字典
        """
        with self._lock:
            stats = self._stats.to_dict()
            stats["cache_size"] = len(self._cache)
            stats["max_size"] = self._max_size
            stats["ttl_seconds"] = self._ttl_seconds
            return stats

    def _is_valid_signature(self, signature: str) -> bool:
        """
        验证 signature 格式是否正确

        Claude signature 通常是 base64 编码的长字符串。

        Args:
            signature: 待验证的 signature

        Returns:
            是否是有效的 signature 格式
        """
        if not signature or not isinstance(signature, str):
            return False

        # 长度检查（Claude signature 通常很长）
        if len(signature) < 50:
            return False

        # 跳过占位符
        if signature == "skip_thought_signature_validator":
            return False

        # 检查是否是 base64 格式（宽松检查）
        import re
        if not re.match(r'^[A-Za-z0-9+/=_-]+$', signature):
            return False

        return True

    @property
    def size(self) -> int:
        """当前缓存大小"""
        with self._lock:
            return len(self._cache)

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return (
            f"SignatureCache(size={self.size}, max_size={self._max_size}, "
            f"ttl={self._ttl_seconds}s, hit_rate={self._stats.hit_rate:.2%})"
        )


# 全局缓存实例（单例模式）
_global_cache: Optional[SignatureCache] = None
_global_cache_lock = threading.Lock()


def get_signature_cache() -> SignatureCache:
    """
    获取全局缓存实例（线程安全的单例）

    Returns:
        全局 SignatureCache 实例
    """
    global _global_cache

    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = SignatureCache()
                log.info("[SIGNATURE_CACHE] 创建全局缓存实例")

    return _global_cache


def reset_signature_cache() -> None:
    """
    重置全局缓存实例（主要用于测试）
    """
    global _global_cache

    with _global_cache_lock:
        if _global_cache is not None:
            _global_cache.clear()
            _global_cache = None
            log.info("[SIGNATURE_CACHE] 重置全局缓存实例")


# 便捷函数
def cache_signature(thinking_text: str, signature: str, model: Optional[str] = None) -> bool:
    """
    缓存 signature（便捷函数）

    Args:
        thinking_text: thinking 块的文本内容
        signature: 对应的 signature 值
        model: 可选的模型名称

    Returns:
        是否成功缓存
    """
    return get_signature_cache().set(thinking_text, signature, model)


def get_cached_signature(thinking_text: str) -> Optional[str]:
    """
    获取缓存的 signature（便捷函数）

    Args:
        thinking_text: thinking 块的文本内容

    Returns:
        缓存的 signature，如果未命中则返回 None
    """
    return get_signature_cache().get(thinking_text)


def get_cache_stats() -> Dict[str, Any]:
    """
    获取缓存统计信息（便捷函数）

    Returns:
        缓存统计信息字典
    """
    return get_signature_cache().get_stats()
