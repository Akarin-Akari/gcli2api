# Session 签名缓存使用指南

## 快速开始

### 1. 导入模块

```python
from signature_cache import (
    generate_session_fingerprint,
    cache_session_signature,
    get_session_signature,
    get_session_signature_with_text
)
```

### 2. 生成会话指纹

```python
# 从消息列表生成会话指纹
messages = [
    {"role": "system", "content": "You are a helpful assistant"},
    {"role": "user", "content": "Hello, how are you?"}
]

session_id = generate_session_fingerprint(messages)
# 返回: "c5c8fb4dde9ef50d" (MD5 哈希的前 16 位)
```

### 3. 缓存签名

```python
# 缓存 Session 级别的签名
signature = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
thinking_text = "Let me think about this problem..."

success = cache_session_signature(session_id, signature, thinking_text)
# 返回: True (缓存成功)
```

### 4. 获取签名

```python
# 方式 1: 只获取签名
cached_sig = get_session_signature(session_id)
# 返回: "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"

# 方式 2: 同时获取签名和 thinking 文本
result = get_session_signature_with_text(session_id)
if result:
    sig, text = result
    print(f"Signature: {sig}")
    print(f"Thinking: {text}")
```

---

## 使用场景

### 场景 1: API 请求中的签名恢复

```python
def handle_api_request(messages):
    # 1. 生成会话指纹
    session_id = generate_session_fingerprint(messages)

    # 2. 尝试从缓存获取签名
    cached_sig = get_session_signature(session_id)

    if cached_sig:
        # 使用缓存的签名
        print(f"使用缓存的签名: {cached_sig[:20]}...")
        return cached_sig
    else:
        # 没有缓存，需要生成新的签名
        print("缓存未命中，生成新签名")
        return None
```

### 场景 2: 响应处理中的签名缓存

```python
def handle_api_response(messages, signature, thinking_text):
    # 1. 生成会话指纹
    session_id = generate_session_fingerprint(messages)

    # 2. 缓存签名和 thinking 文本
    success = cache_session_signature(session_id, signature, thinking_text)

    if success:
        print(f"签名已缓存到 Session: {session_id}")
    else:
        print("签名缓存失败")
```

### 场景 3: 多轮对话中的签名复用

```python
def multi_turn_conversation():
    # 第一轮对话
    messages_1 = [
        {"role": "user", "content": "What is Python?"}
    ]
    session_id_1 = generate_session_fingerprint(messages_1)

    # 假设从 API 获得了签名
    signature_1 = "EqQBCgwIARIIEAEYASABKAEQARgBIAEoATABOAFAAUgBUAFYAWABaAFwAXgB"
    thinking_1 = "Let me explain Python..."
    cache_session_signature(session_id_1, signature_1, thinking_1)

    # 第二轮对话（相同的初始消息）
    messages_2 = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is..."},
        {"role": "user", "content": "Tell me more"}
    ]
    session_id_2 = generate_session_fingerprint(messages_2)

    # session_id_2 与 session_id_1 相同（基于第一条用户消息）
    assert session_id_1 == session_id_2

    # 可以复用缓存的签名
    cached_sig = get_session_signature(session_id_2)
    print(f"复用缓存的签名: {cached_sig[:20]}...")
```

---

## 高级用法

### 使用 SignatureCache 类

```python
from signature_cache import SignatureCache

# 创建自定义缓存实例
cache = SignatureCache(
    max_size=10000,      # 最大缓存条目数
    ttl_seconds=3600     # 缓存过期时间（1小时）
)

# 缓存签名
cache.cache_session_signature(session_id, signature, thinking_text)

# 获取签名
cached_sig = cache.get_session_signature(session_id)

# 获取签名和文本
result = cache.get_session_signature_with_text(session_id)
```

### 处理多模态内容

```python
# 多模态消息
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Analyze this image"},
            {"type": "image", "source": {"type": "base64", "data": "..."}}
        ]
    }
]

# generate_session_fingerprint 会自动提取文本部分
session_id = generate_session_fingerprint(messages)
# 基于 "Analyze this image" 生成指纹
```

---

## 注意事项

### 1. Session 指纹生成策略

- 基于**第一条用户消息**的内容生成指纹
- 如果没有用户消息，使用**系统消息**
- 多模态内容只提取 `type="text"` 的部分
- 返回 MD5 哈希的**前 16 位**

### 2. 缓存过期

- 默认 TTL: 3600 秒（1小时）
- 过期的条目会自动删除
- 可以通过 `SignatureCache(ttl_seconds=...)` 自定义

### 3. 线程安全

- 所有操作都是线程安全的
- 使用独立的 `_session_lock` 保护
- 支持高并发场景

### 4. 签名验证

- 签名必须是有效的 base64 格式
- 长度至少 50 个字符
- 不接受占位符（如 `"skip_thought_signature_validator"`）

---

## 三层缓存架构

Session 缓存是三层缓存架构的一部分：

```
Layer 1: 工具ID缓存
  └── cache_tool_signature(tool_id, signature)
  └── get_tool_signature(tool_id)

Layer 2: thinking hash 缓存
  └── cache_signature(thinking_text, signature)
  └── get_cached_signature(thinking_text)

Layer 3: Session 缓存 (本功能)
  └── cache_session_signature(session_id, signature, thinking_text)
  └── get_session_signature(session_id)
  └── get_session_signature_with_text(session_id)
```

### 查找优先级建议

1. 如果有 `tool_id` → 使用 Layer 1
2. 如果有 `session_id` → 使用 Layer 3
3. 如果有 `thinking_text` → 使用 Layer 2

---

## 错误处理

```python
# 空 session_id
result = cache_session_signature("", signature)
# 返回: False

# 空 signature
result = cache_session_signature(session_id, "")
# 返回: False

# 无效的 signature 格式
result = cache_session_signature(session_id, "invalid")
# 返回: False

# 不存在的 session
cached_sig = get_session_signature("non_existent")
# 返回: None
```

---

## 性能考虑

### 内存占用

- 每个 `CacheEntry` 包含:
  - `signature`: ~100-200 字节
  - `thinking_text`: 完整文本（可能很大）
  - `thinking_text_preview`: 前 200 字符
  - `timestamp`: 8 字节
  - `access_count`: 4 字节

### 优化建议

1. **合理设置 TTL**: 避免长期缓存不再使用的签名
2. **定期清理**: 使用 `cleanup_expired()` 清理过期条目
3. **监控缓存大小**: 使用 `get_stats()` 查看缓存统计

---

## 调试

### 启用详细日志

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("gcli2api.signature_cache")
logger.setLevel(logging.DEBUG)
```

### 查看缓存统计

```python
from signature_cache import get_cache_stats

stats = get_cache_stats()
print(stats)
# 输出:
# {
#     "hits": 10,
#     "misses": 5,
#     "writes": 15,
#     "evictions": 0,
#     "expirations": 2,
#     "hit_rate": "66.67%",
#     "total_requests": 15,
#     "cache_size": 13,
#     "max_size": 10000,
#     "ttl_seconds": 3600
# }
```

---

## 常见问题

### Q1: Session 指纹会冲突吗？

A: 理论上可能，但概率极低。MD5 哈希的前 16 位有 2^64 种可能性，足够用于会话识别。

### Q2: 如何清空所有 Session 缓存？

A: 使用 `reset_signature_cache()` 清空所有缓存（包括三层）。

### Q3: Session 缓存支持迁移模式吗？

A: 是的，所有便捷函数都支持迁移模式代理，与 DUAL_WRITE 架构兼容。

### Q4: 如何自定义 Session 指纹生成策略？

A: 当前版本基于第一条用户消息。如需自定义，可以直接传入自己生成的 `session_id`，不使用 `generate_session_fingerprint()`。

---

## 更多信息

- 完整实现报告: `docs/2026-01-17_Session签名缓存功能完善报告.md`
- 测试脚本: `test_session_cache.py`
- 源代码: `src/signature_cache.py`

---

**最后更新**: 2026-01-17
**作者**: Claude Sonnet 4.5
