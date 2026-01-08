# Signature 缓存方案实施报告

**日期**: 2026-01-07
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 1. 背景与问题

### 1.1 问题描述

Cursor IDE 使用 OpenAI 兼容格式与后端通信，但 OpenAI 格式不支持 Claude 的 `signature` 字段。这导致：

1. **首轮对话正常**: Claude 返回 thinking block 带有 `signature`
2. **后续对话失败**: Cursor 将历史消息转为 OpenAI 格式时丢失 `signature`
3. **API 报错**: Claude API 要求 thinking block 必须包含有效的 `thoughtSignature`

### 1.2 解决方案

在代理层实现 Signature 缓存机制：

- **响应阶段**: 缓存 `thinking_text` → `signature` 的映射
- **请求阶段**: 当检测到 thinking block 缺少 signature 时，从缓存恢复

---

## 2. 实施内容

### 2.1 Phase 1: SignatureCache 模块 ✅

**文件**: `gcli2api/src/signature_cache.py`

核心功能：
- 线程安全的 LRU 缓存（基于 `OrderedDict`）
- TTL 过期机制（默认 1 小时）
- 基于 thinking_text 前 500 字符的 MD5 哈希作为缓存 key
- 完整的缓存统计（命中率、写入次数、淘汰次数等）

```python
# 主要接口
cache_signature(thinking_text, signature, model=None) -> bool
get_cached_signature(thinking_text) -> Optional[str]
get_cache_stats() -> Dict[str, Any]
```

### 2.2 Phase 2: 流式响应缓存写入 ✅

**文件**: `gcli2api/src/anthropic_streaming.py`

修改内容：
1. 添加 `_current_thinking_text` 字段用于累积 thinking 文本
2. 在处理 thinking delta 时累积文本内容
3. 在 `close_block_if_open()` 时将 signature 写入缓存

```python
# 关键代码位置
# Line 9: from signature_cache import cache_signature
# Line 56: self._current_thinking_text: str = ""
# Line 73-92: 缓存写入逻辑
# Line 310: state._current_thinking_text += thinking_text
```

### 2.3 Phase 3: 消息转换缓存读取 ✅

**文件**: `gcli2api/src/converters/message_converter.py`

修改内容：
1. 添加 `get_cached_signature` 导入
2. 在处理 thinking block 时，当 signature 无效时尝试从缓存恢复
3. 同样处理 redacted_thinking block

```python
# 关键代码位置
# Line 11: from signature_cache import get_cached_signature
# Line 365-378: thinking block 缓存恢复逻辑
# Line 391-404: redacted_thinking block 缓存恢复逻辑
```

### 2.4 Phase 4: 监控 API 端点 ✅

**文件**: `gcli2api/src/web_routes.py`

新增 API 端点：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/cache/signature/stats` | GET | 获取缓存统计信息 |
| `/cache/signature/clear` | POST | 清空所有缓存 |
| `/cache/signature/cleanup` | POST | 清理过期条目 |

---

## 3. 技术细节

### 3.1 缓存 Key 生成策略

```python
def _generate_cache_key(thinking_text: str) -> str:
    # 取前 500 字符，避免长文本影响性能
    text_sample = thinking_text[:500] if len(thinking_text) > 500 else thinking_text
    return hashlib.md5(text_sample.encode('utf-8')).hexdigest()
```

**设计考量**:
- 使用 MD5 而非 SHA256，因为这是缓存 key 而非安全场景
- 只取前 500 字符，平衡唯一性和性能
- 同一 thinking 内容在不同会话中会生成相同的 key

### 3.2 缓存配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_size` | 10000 | 最大缓存条目数 |
| `ttl_seconds` | 3600 | 条目过期时间（1小时） |

### 3.3 优雅降级

当缓存未命中时，系统会：
1. 将 thinking 内容作为普通文本处理
2. 记录 debug 级别日志
3. 不影响请求的正常处理

---

## 4. 日志标识

所有缓存相关日志使用 `[SIGNATURE_CACHE]` 前缀：

```
[SIGNATURE_CACHE] 缓存写入成功: thinking_len=1234, model=claude-3-opus
[SIGNATURE_CACHE] 从缓存恢复 signature: thinking_len=1234
[SIGNATURE_CACHE] 手动清空缓存: 删除 50 条
```

---

## 5. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/signature_cache.py` | 新增 | 核心缓存模块 |
| `src/anthropic_streaming.py` | 修改 | 添加缓存写入 |
| `src/converters/message_converter.py` | 修改 | 添加缓存读取 |
| `src/web_routes.py` | 修改 | 添加监控 API |
| `src/patch_signature_cache.py` | 新增 | 补丁脚本（可删除） |
| `src/patch_message_converter.py` | 新增 | 补丁脚本（可删除） |
| `src/patch_web_routes.py` | 新增 | 补丁脚本（可删除） |

---

## 6. 验证方法

### 6.1 功能验证

1. 使用 Cursor IDE 与 Claude thinking 模型进行多轮对话
2. 观察日志中的 `[SIGNATURE_CACHE]` 条目
3. 确认后续对话不再出现 signature 相关错误

### 6.2 监控验证

```bash
# 获取缓存统计
curl -X GET "http://localhost:8000/cache/signature/stats" \
  -H "Authorization: Bearer <token>"

# 预期响应
{
  "success": true,
  "data": {
    "hits": 10,
    "misses": 2,
    "writes": 12,
    "evictions": 0,
    "expirations": 0,
    "hit_rate": 0.833,
    "cache_size": 12,
    "max_size": 10000,
    "ttl_seconds": 3600
  }
}
```

---

## 7. 后续优化建议

1. **持久化存储**: 考虑将缓存持久化到 Redis，支持服务重启后恢复
2. **分布式支持**: 如果部署多实例，需要共享缓存存储
3. **缓存预热**: 可以考虑从历史日志中预热常用的 signature
4. **监控告警**: 当缓存命中率过低时发送告警

---

## 8. 总结

本次实施成功解决了 Cursor IDE 与 Claude thinking 模型的兼容性问题。通过在代理层实现 signature 缓存机制，实现了：

- ✅ 透明的 signature 恢复，用户无感知
- ✅ 优雅降级，缓存未命中不影响正常使用
- ✅ 完整的监控和统计能力
- ✅ 线程安全，支持高并发场景

---

*文档结束 - 浮浮酱 (´｡• ᵕ •｡`) ♡*
