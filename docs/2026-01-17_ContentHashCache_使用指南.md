# ContentHashCache 使用指南

**创建时间**: 2026-01-17
**作者**: Claude Sonnet 4.5 (浮浮酱)
**目标读者**: gcli2api 开发者

---

## 一、概述

`ContentHashCache` 是一个专为 IDE 兼容性设计的签名缓存层,用于通过 thinking 文本的内容哈希快速查找签名。

### 1.1 设计目的

- **IDE 文本变形处理**: IDE 可能会变形 thinking 文本(添加空格、换行等)
- **标准化匹配**: 通过 normalize + hash 可以匹配变形后的内容
- **前缀匹配**: 处理 IDE 截断 thinking 文本的情况

### 1.2 核心特性

- **双哈希策略**: 精确哈希 + 标准化哈希
- **前缀匹配**: 作为 fallback 策略
- **LRU 淘汰**: 自动淘汰最少使用的条目
- **TTL 过期**: 支持时间过期机制
- **线程安全**: 所有操作都是线程安全的

---

## 二、架构设计

### 2.1 缓存层级

```
┌─────────────────────────────────────────────────────────┐
│                   ContentHashCache                       │
├─────────────────────────────────────────────────────────┤
│  L1: Exact Hash Cache                                   │
│      _exact_cache: OrderedDict[str, HashCacheEntry]     │
│      - 精确哈希匹配                                       │
│      - LRU 淘汰                                          │
├─────────────────────────────────────────────────────────┤
│  L2: Normalized Hash Cache                              │
│      _normalized_cache: OrderedDict[str, HashCacheEntry]│
│      - 标准化哈希匹配                                     │
│      - 处理文本变形                                       │
├─────────────────────────────────────────────────────────┤
│  L3: Prefix Index                                       │
│      _prefix_index: Dict[str, List[HashCacheEntry]]     │
│      - 前缀匹配                                          │
│      - 处理截断文本                                       │
└─────────────────────────────────────────────────────────┘
```

### 2.2 查找策略

```python
def get(thinking_text: str) -> Optional[str]:
    """
    查找策略:
    1. 计算精确哈希和标准化哈希
    2. 尝试精确哈希匹配 (exact_cache)
    3. 尝试标准化哈希匹配 (normalized_cache 或 exact_cache)
    4. 返回 None (缓存未命中)
    """
```

### 2.3 存储策略

```python
def set(thinking_text: str, signature: str) -> bool:
    """
    存储策略:
    1. 计算精确哈希和标准化哈希
    2. 存储到 exact_cache (精确哈希)
    3. 如果标准化哈希 != 精确哈希,存储到 normalized_cache
    4. 更新前缀索引
    5. LRU 淘汰 (如果超过 max_size)
    """
```

---

## 三、使用示例

### 3.1 基本用法

```python
from src.ide_compat.hash_cache import ContentHashCache

# 创建缓存实例
cache = ContentHashCache(
    max_size=10000,      # 最大缓存条目数
    ttl_seconds=3600,    # 1 小时过期
    min_prefix_length=100  # 最小前缀长度
)

# 存储签名
thinking_text = "Let me think about this problem..."
signature = "EqQBCgxhYmNkZWZnaGlqa2w="
cache.set(thinking_text, signature)

# 获取签名 (精确匹配)
result = cache.get(thinking_text)
print(result)  # "EqQBCgxhYmNkZWZnaGlqa2w="

# 获取签名 (标准化匹配 - IDE 添加了额外空格)
transformed_text = "Let  me   think    about   this   problem..."
result = cache.get(transformed_text)
print(result)  # "EqQBCgxhYmNkZWZnaGlqa2w="

# 获取签名 (前缀匹配 - IDE 截断了文本)
truncated_text = "Let me think about this problem"
result = cache.get_with_prefix_match(truncated_text, min_prefix_len=20)
print(result)  # "EqQBCgxhYmNkZWZnaGlqa2w="
```

### 3.2 文本标准化

```python
from src.ide_compat.hash_cache import ContentHashCache

# 标准化文本
original = "  Let  me   think\r\nabout\rthis  "
normalized = ContentHashCache.normalize_text(original)
print(normalized)  # "Let me think about this"

# 标准化规则:
# 1. 去除首尾空白
# 2. 合并连续空白为单个空格
# 3. 统一换行符为 \n
```

### 3.3 哈希计算

```python
from src.ide_compat.hash_cache import ContentHashCache

text = "Let me think about this"

# 精确哈希
exact_hash = ContentHashCache.compute_hash(text, normalize=False)
print(exact_hash)  # 64 字符的 SHA256 哈希

# 标准化哈希
normalized_hash = ContentHashCache.compute_hash(text, normalize=True)
print(normalized_hash)  # 64 字符的 SHA256 哈希

# 对于已经标准化的文本,两个哈希相同
print(exact_hash == normalized_hash)  # True
```

### 3.4 缓存统计

```python
from src.ide_compat.hash_cache import ContentHashCache

cache = ContentHashCache()

# 添加条目
cache.set("text1", "sig1")
cache.set("text2", "sig2")

# 访问条目
cache.get("text1")  # 命中
cache.get("text1")  # 命中
cache.get("text3")  # 未命中

# 获取统计信息
stats = cache.get_stats()
print(stats)
# {
#     "exact_hits": 2,
#     "normalized_hits": 0,
#     "prefix_hits": 0,
#     "total_hits": 2,
#     "misses": 1,
#     "evictions": 0,
#     "expirations": 0,
#     "total_writes": 2,
#     "current_size": 2,
#     "max_size": 10000,
#     "hit_rate": 0.6666666666666666
# }
```

### 3.5 过期清理

```python
from src.ide_compat.hash_cache import ContentHashCache
import time

# 创建短 TTL 缓存
cache = ContentHashCache(ttl_seconds=1)

# 添加条目
cache.set("text1", "sig1")
cache.set("text2", "sig2")

# 等待过期
time.sleep(1.5)

# 手动清理过期条目
count = cache.cleanup_expired()
print(f"Cleaned up {count} expired entries")  # 2

# 或者在 get() 时自动清理
result = cache.get("text1")  # None (已过期)
```

### 3.6 清空缓存

```python
from src.ide_compat.hash_cache import ContentHashCache

cache = ContentHashCache()

# 添加条目
cache.set("text1", "sig1")
cache.set("text2", "sig2")

# 清空所有条目
count = cache.clear()
print(f"Cleared {count} entries")  # 2

# 验证已清空
result = cache.get("text1")  # None
```

---

## 四、集成到现有系统

### 4.1 与 SignatureCache 配合使用

```python
from src.signature_cache import SignatureCache
from src.ide_compat.hash_cache import ContentHashCache

# 创建两个缓存实例
signature_cache = SignatureCache()  # 主缓存
hash_cache = ContentHashCache()     # IDE 兼容层

# 存储签名 (同时存储到两个缓存)
def cache_signature(thinking_text: str, signature: str):
    signature_cache.set(thinking_text, signature)
    hash_cache.set(thinking_text, signature)

# 获取签名 (优先使用 hash_cache)
def get_signature(thinking_text: str) -> Optional[str]:
    # 1. 尝试 hash_cache (处理 IDE 变形)
    result = hash_cache.get(thinking_text)
    if result:
        return result

    # 2. 尝试 hash_cache 前缀匹配
    result = hash_cache.get_with_prefix_match(thinking_text)
    if result:
        return result

    # 3. 回退到 signature_cache
    return signature_cache.get(thinking_text)
```

### 4.2 在 AnthropicConverter 中使用

```python
from src.ide_compat.hash_cache import ContentHashCache

class AnthropicConverter:
    def __init__(self):
        self.hash_cache = ContentHashCache(
            max_size=10000,
            ttl_seconds=3600
        )

    def cache_thinking_signature(self, thinking_text: str, signature: str):
        """缓存 thinking 签名"""
        self.hash_cache.set(thinking_text, signature)

    def recover_thinking_signature(self, thinking_text: str) -> Optional[str]:
        """恢复 thinking 签名"""
        # 1. 尝试精确/标准化匹配
        signature = self.hash_cache.get(thinking_text)
        if signature:
            return signature

        # 2. 尝试前缀匹配
        signature = self.hash_cache.get_with_prefix_match(thinking_text)
        if signature:
            return signature

        return None
```

---

## 五、性能优化建议

### 5.1 合理设置 max_size

```python
# 根据内存大小和使用场景调整
cache = ContentHashCache(
    max_size=10000,  # 小型应用
    # max_size=50000,  # 中型应用
    # max_size=100000,  # 大型应用
)
```

### 5.2 合理设置 TTL

```python
# 根据签名有效期调整
cache = ContentHashCache(
    ttl_seconds=3600,    # 1 小时 (短期会话)
    # ttl_seconds=86400,   # 24 小时 (日常使用)
    # ttl_seconds=604800,  # 7 天 (长期缓存)
)
```

### 5.3 定期清理过期条目

```python
import threading
import time

def cleanup_worker(cache: ContentHashCache, interval: int = 300):
    """后台清理线程"""
    while True:
        time.sleep(interval)
        count = cache.cleanup_expired()
        if count > 0:
            print(f"Cleaned up {count} expired entries")

# 启动清理线程
cache = ContentHashCache()
cleanup_thread = threading.Thread(
    target=cleanup_worker,
    args=(cache, 300),  # 每 5 分钟清理一次
    daemon=True
)
cleanup_thread.start()
```

---

## 六、注意事项

### 6.1 线程安全

- 所有操作都是线程安全的,可以在多线程环境中使用
- 内部使用 `threading.Lock` 保护并发访问

### 6.2 内存使用

- 每个条目存储完整的 thinking 文本,可能占用较多内存
- 通过 `max_size` 限制条目数量
- 通过 `ttl_seconds` 自动过期旧条目

### 6.3 哈希冲突

- 使用 SHA256 哈希,冲突概率极低
- 即使发生冲突,也会通过文本验证确保正确性

### 6.4 前缀匹配限制

- 前缀匹配仅适用于文本长度 >= `min_prefix_length` 的情况
- 前缀匹配可能返回错误结果(如果多个条目有相同前缀)
- 建议仅在精确/标准化匹配失败时使用

---

## 七、故障排查

### 7.1 缓存未命中

**问题**: 明明存储了签名,但 `get()` 返回 `None`

**可能原因**:
1. 条目已过期 (检查 TTL 设置)
2. 条目被 LRU 淘汰 (检查 max_size 设置)
3. 文本差异过大 (标准化无法匹配)

**解决方法**:
```python
# 检查统计信息
stats = cache.get_stats()
print(stats)

# 增加 TTL
cache = ContentHashCache(ttl_seconds=86400)  # 24 小时

# 增加 max_size
cache = ContentHashCache(max_size=50000)

# 使用前缀匹配
result = cache.get_with_prefix_match(thinking_text)
```

### 7.2 内存占用过高

**问题**: 缓存占用过多内存

**解决方法**:
```python
# 减小 max_size
cache = ContentHashCache(max_size=5000)

# 减小 TTL
cache = ContentHashCache(ttl_seconds=1800)  # 30 分钟

# 定期清理
cache.cleanup_expired()

# 清空缓存
cache.clear()
```

### 7.3 性能问题

**问题**: 缓存操作过慢

**可能原因**:
1. 缓存条目过多 (LRU 操作变慢)
2. 前缀索引过大

**解决方法**:
```python
# 减小 max_size
cache = ContentHashCache(max_size=10000)

# 增加 min_prefix_length (减少前缀索引条目)
cache = ContentHashCache(min_prefix_length=200)

# 避免频繁使用前缀匹配
```

---

## 八、测试

完整的测试套件位于 `tests/test_hash_cache.py`:

```bash
# 运行所有测试
pytest tests/test_hash_cache.py -v

# 运行特定测试
pytest tests/test_hash_cache.py::TestContentHashCache::test_normalized_hash_match -v
```

---

**最后更新**: 2026-01-17
**维护者**: Claude Sonnet 4.5 (浮浮酱)
