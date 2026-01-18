# 流式响应 Signature 缓存修复报告

**文档创建时间**: 2026-01-17 12:00
**作者**: Claude Opus 4.5 (浮浮酱)
**项目**: gcli2api
**严重程度**: P1 Critical

---

## 一、问题描述

### 1.1 用户报告

在使用 Claude Code 进行工具调用时，出现以下警告：

```
[11:23:25] [WARNING] [SIGNATURE_RECOVERY] All 6 layers failed for tool_id=call_0c68821eddd0f4f88bdf7cf0
[11:23:25] [WARNING] [ANTHROPIC CONVERTER] No signature found for tool call: call_0c68821eddd0f4f88bdf7cf0, using placeholder
```

尽管已经实现了 Tool Cache 和 Session Cache 的 SQLite 持久化，但每次新会话开始时仍然出现签名恢复失败。

### 1.2 问题现象

1. 每次工具调用都显示 `[STREAMING] No thoughtSignature available for tool call: xxx`
2. 6层签名恢复策略全部失败，只能走 Layer 6 (last signature fallback)
3. Tool Cache (Layer 5) 从未被填充
4. 服务器重启后缓存丢失

---

## 二、根因分析

### 2.1 问题链条

```
上游 (Antigravity API) 响应
    ↓
流式 SSE 数据包含 functionCall
    ↓
尝试获取 thoughtSignature:
  1. part.get("thoughtSignature") → None (上游未提供)
  2. state._current_thinking_signature → None (thinking块已关闭)
  3. state._last_thinking_signature → None (从未被设置)
    ↓
所有来源都为空
    ↓
Tool Cache 不写入
    ↓
后续请求 6 层恢复全部失败
```

### 2.2 核心问题

1. **上游不发送 thoughtSignature**: Antigravity API 的 functionCall part 可能不包含 `thoughtSignature` 字段

2. **状态重置时机问题**: 当 thinking 块关闭时，`_current_thinking_signature` 被重置为 None，但如果上游从未发送过 signature，`_last_thinking_signature` 也是 None

3. **缺少 fallback 机制**: 在流式响应阶段，没有从全局缓存获取 signature 的 fallback 逻辑

4. **SQLite 读取缺失**: `get_last_signature()` 只从内存缓存读取，不从 SQLite 读取

---

## 三、修复方案

### 3.1 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/anthropic_streaming.py` | 添加 `get_last_signature()` 作为最终 fallback |
| `src/signature_cache.py` | 添加 SQLite 读取支持 |

### 3.2 具体修改

#### 3.2.1 anthropic_streaming.py - 导入 get_last_signature

```python
from .signature_cache import cache_signature, cache_tool_signature, get_last_signature
```

#### 3.2.2 anthropic_streaming.py - close_block_if_open() 添加 fallback

```python
def close_block_if_open(self) -> Optional[bytes]:
    if self._current_block_type is None:
        return None

    if self._current_block_type == "thinking" and self._current_thinking_text:
        # [FIX 2026-01-17] 如果没有 signature，尝试从全局缓存获取
        effective_signature = self._current_thinking_signature
        if not effective_signature:
            try:
                cached_sig = get_last_signature()
                if cached_sig:
                    effective_signature = cached_sig
                    log.info(f"[STREAMING] close_block: Using cached last signature as fallback")
            except Exception as e:
                log.warning(f"[STREAMING] close_block: Failed to get last signature: {e}")

        if effective_signature:
            self._last_thinking_signature = effective_signature
            # ... 缓存写入逻辑 ...
```

#### 3.2.3 anthropic_streaming.py - functionCall 处理添加 fallback

```python
# 获取 signature 的优先级链
thoughtsignature = (
    part.get("thoughtSignature")
    or state._current_thinking_signature
    or state._last_thinking_signature
)

# [FIX 2026-01-17] 如果本地状态都为空，尝试从全局缓存获取
if not thoughtsignature:
    try:
        cached_sig = get_last_signature()
        if cached_sig:
            thoughtsignature = cached_sig
            log.info(f"[STREAMING] Using cached last signature as fallback")
    except Exception as e:
        log.warning(f"[STREAMING] Failed to get last signature from cache: {e}")
```

#### 3.2.4 signature_cache.py - get_last_signature() 添加 SQLite 支持

```python
def get_last_signature() -> Optional[str]:
    # ... 内存缓存查找逻辑 ...

    # [FIX 2026-01-17] 内存缓存为空时，尝试从 SQLite 读取
    log.debug("[SIGNATURE_CACHE] get_last_signature: 内存缓存为空，尝试从 SQLite 读取")
    if _is_migration_mode():
        facade = _get_migration_facade()
        if facade:
            try:
                db_result = facade.get_last_session_signature_from_db()
                if db_result:
                    signature, thinking_text = db_result
                    log.info(f"[SIGNATURE_CACHE] get_last_signature: 从 SQLite 恢复 signature")
                    return signature
            except Exception as e:
                log.warning(f"[SIGNATURE_CACHE] get_last_signature: 从 SQLite 读取失败: {e}")

    return None
```

---

## 四、修复后的 Signature 获取优先级

### 4.1 流式响应阶段 (functionCall 处理)

```
1. part.get("thoughtSignature")           ← 上游直接提供
2. state._current_thinking_signature      ← 当前 thinking 块的 signature
3. state._last_thinking_signature         ← 上一个 thinking 块的 signature
4. get_last_signature()                   ← 全局缓存 (内存 + SQLite)
```

### 4.2 get_last_signature() 内部优先级

```
1. CacheFacade.get_last_signature()       ← 迁移门面
2. SignatureCache._cache (内存)           ← 本地内存缓存
3. SignatureDatabase (SQLite)             ← 持久化存储
```

---

## 五、预期效果

### 5.1 修复前

```
新会话开始
    ↓
上游不发送 thoughtSignature
    ↓
所有本地状态为空
    ↓
Tool Cache 不写入
    ↓
6层签名恢复全部失败
    ↓
使用 placeholder，thinking 模式降级
```

### 5.2 修复后

```
新会话开始
    ↓
上游不发送 thoughtSignature
    ↓
从全局缓存获取 fallback signature
    ↓
Tool Cache 正确写入
    ↓
后续请求 Layer 5 命中
    ↓
thinking 模式正常工作
```

---

## 六、验证方法

### 6.1 日志观察

修复后应该看到以下日志：

```
[STREAMING] close_block: Using cached last signature as fallback, sig_len=1800
[STREAMING] Encoded thoughtSignature into tool_id: call_xxx, sig_len=1800
[STREAMING] Cached tool signature: tool_id=call_xxx
```

而不是：

```
[STREAMING] No thoughtSignature available for tool call: call_xxx
```

### 6.2 功能验证

1. 启动新会话
2. 执行工具调用
3. 检查日志是否显示 signature 被正确缓存
4. 检查后续请求是否命中 Tool Cache (Layer 5)

---

## 七、总结

### 7.1 修改内容

- ✅ `anthropic_streaming.py`: 添加 `get_last_signature()` 作为最终 fallback
- ✅ `anthropic_streaming.py`: 在 `close_block_if_open()` 中添加 fallback 逻辑
- ✅ `signature_cache.py`: 添加 SQLite 读取支持

### 7.2 核心原则

**Signature 获取的 fallback 链**:
- 优先使用上游提供的 signature
- 其次使用本地状态中的 signature
- 最后从全局缓存（内存 + SQLite）获取

**缓存写入的保证**:
- 即使上游不发送 signature，也要尝试从缓存获取并写入 Tool Cache
- 确保后续请求可以命中缓存

---

**文档结束**

祝测试顺利喵～ ฅ'ω'ฅ
