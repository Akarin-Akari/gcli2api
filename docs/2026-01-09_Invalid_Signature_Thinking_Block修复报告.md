# Invalid Signature in Thinking Block 修复报告

**日期**: 2026-01-09
**修复者**: Claude Opus 4.5 (浮浮酱)
**问题级别**: P0 (Critical)

---

## 1. 问题描述

### 1.1 错误现象

使用 Claude Opus 4.5 Thinking 模型时，在单次请求内进行多次思考会触发 400 错误：

```log
[02:53:56] [WARNING] [ANTIGRAVITY] 400 客户端错误，不重试 (model=claude-opus-4-5-thinking)
[02:53:56] [ERROR] [ANTIGRAVITY] Request failed with model claude-opus-4-5-thinking: Antigravity API error (400): {
  "error": {
    "code": 400,
    "message": "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"message\":\"messages.9.content.0: Invalid `signature` in `thinking` block\"},\"reque
```

### 1.2 问题场景

- **正常场景**: 跨请求的多轮对话 thinking 正常工作
- **异常场景**: 单次请求内多次 thinking（例如：回复 → thinking → 工具调用 → thinking → 再次回复）

---

## 2. 问题根源分析

### 2.1 核心问题

**Claude API 的 signature 是与特定的 thinking 内容加密绑定的！**

之前的 fallback 机制在 `antigravity_router.py:1834-1838` 中：

```python
# 错误的实现
thinking_part = {
    "text": "...",  # ← 问题根源：使用占位文本
    "thought": True,
    "thoughtSignature": last_sig  # ← 这个 signature 是与原始 thinking 内容绑定的
}
```

### 2.2 问题链条

1. Cursor IDE 不保留历史消息中的 thinking 内容
2. 系统使用 `get_last_signature()` 获取最近缓存的 signature
3. 创建占位 thinking block，使用 `"..."` 作为 thinking 文本
4. **signature A（对应原始 thinking 内容）+ thinking 内容 `"..."`（占位文本）= Invalid signature 错误**

### 2.3 技术原理

Claude API 的 thinking signature 是一个加密签名，用于验证 thinking 内容的完整性和真实性。这个签名与 thinking 文本内容是一一对应的：

- 相同的 thinking 文本 → 相同的 signature
- 不同的 thinking 文本 → 不同的 signature
- 错误的 signature + thinking 文本组合 → 400 Invalid signature 错误

---

## 3. 修复方案

### 3.1 修改 `signature_cache.py`

#### 3.1.1 修改 CacheEntry 数据结构

```python
@dataclass
class CacheEntry:
    """缓存条目数据结构"""
    signature: str
    thinking_text: str  # 完整的 thinking 文本（用于 fallback 恢复）
    thinking_text_preview: str  # 前 200 字符（用于调试日志）
    timestamp: float
    access_count: int = 0
    model: Optional[str] = None
```

**变更说明**:
- 将 `thinking_text` 从只保存前 200 字符改为保存完整文本
- 新增 `thinking_text_preview` 字段用于调试日志

#### 3.1.2 新增 `get_last_signature_with_text()` 函数

```python
def get_last_signature_with_text() -> Optional[tuple]:
    """
    获取最近缓存的 signature 及其对应的 thinking 文本（用于 fallback）

    [FIX 2026-01-09] 这是修复 "Invalid signature in thinking block" 错误的关键函数。

    问题根源：
    - Claude API 的 signature 是与特定的 thinking 内容加密绑定的
    - 之前的 fallback 机制使用 "..." 作为占位文本，但配合缓存的 signature
    - 这导致 signature 与 thinking 内容不匹配，触发 400 错误

    解决方案：
    - 返回 (signature, thinking_text) 元组
    - 调用方使用原始的 thinking_text 而不是占位符

    Returns:
        (signature, thinking_text) 元组，如果没有则返回 None
    """
```

### 3.2 修改 `antigravity_router.py`

#### 3.2.1 修改 fallback 注入逻辑

**修改前**:
```python
from .signature_cache import get_last_signature
last_sig = get_last_signature()
if last_sig:
    thinking_part = {
        "text": "...",  # 占位文本
        "thought": True,
        "thoughtSignature": last_sig
    }
```

**修改后**:
```python
from .signature_cache import get_last_signature_with_text
cache_result = get_last_signature_with_text()
if cache_result:
    last_sig, original_thinking_text = cache_result
    thinking_part = {
        "text": original_thinking_text,  # 使用原始 thinking 文本
        "thought": True,
        "thoughtSignature": last_sig
    }
```

---

## 4. 修改文件清单

| 文件 | 修改类型 | 修改内容 |
|------|----------|----------|
| `src/signature_cache.py` | 修改 | CacheEntry 结构，新增 `get_last_signature_with_text()` |
| `src/antigravity_router.py` | 修改 | fallback 注入逻辑，使用原始 thinking 文本 |

---

## 5. 验证方法

### 5.1 日志验证

修复后，成功的 fallback 应该显示：

```log
[SIGNATURE_CACHE] get_last_signature_with_text: 找到有效的最近条目, key=abc123..., age=5.2s, thinking_len=1234
[ANTIGRAVITY] 使用最近缓存的 signature 和原始 thinking 文本作为 fallback，thinking_len=1234
```

### 5.2 功能验证

1. 使用 Claude Opus 4.5 Thinking 模型
2. 进行多轮对话，包含工具调用
3. 验证不再出现 "Invalid signature in thinking block" 错误

---

## 6. 风险评估

### 6.1 内存影响

- **变更**: 缓存现在保存完整的 thinking 文本（之前只保存前 200 字符）
- **影响**: 每个缓存条目的内存占用增加
- **缓解措施**:
  - 缓存有 LRU 淘汰机制（默认 max_size=10000）
  - 缓存有 TTL 过期机制（默认 1 小时）
  - thinking 文本通常不会过大（几 KB 到几十 KB）

### 6.2 兼容性

- **向后兼容**: 完全兼容，不影响现有功能
- **API 兼容**: 不改变对外 API 接口

---

## 7. Error 2: 重新打开对话时的 Invalid Signature 错误

### 7.1 错误现象

当用户重新打开一个包含 thinking block 的历史对话时，触发 400 错误：

```log
[ERROR] [ANTIGRAVITY] Request failed with model claude-opus-4-5-thinking: Antigravity API error (400): {
  "error": {
    "code": 400,
    "message": "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"message\":\"messages.1.content.0: Invalid `signature` in `thinking` block\"}}"
```

**注意**: Error 1 是 `messages.9.content.0`（对话中间），Error 2 是 `messages.1.content.0`（对话开头）。

### 7.2 问题根源

在 `antigravity_router.py` 第 1667-1699 行，原代码只检查 signature 是否非空：

```python
# 错误的实现
if signature and signature.strip():
    has_valid_signature = True  # ← 问题：信任任何非空 signature
    break
```

**问题链条**:
1. 用户关闭 Cursor，服务器重启，缓存清空
2. 用户重新打开对话，Cursor 发送历史消息（包含旧 signature）
3. 代码只检查 signature 非空，直接信任
4. 旧 signature 来自前一次服务器会话，在当前缓存中不存在
5. Claude API 验证失败 → 400 Invalid signature 错误

### 7.3 修复方案

**修改 `antigravity_router.py` 第 1667-1699 行**

将"信任消息提供的 signature"改为"始终使用缓存验证":

```python
# 修复后的实现
elif item_type in ("thinking", "redacted_thinking"):
    has_thinking_block = True
    # [FIX 2026-01-09] 始终优先使用缓存验证 signature
    # 问题：重新打开对话时，Cursor 发送的历史消息包含旧 signature
    # 这些 signature 可能来自前一次服务器会话，在当前缓存中不存在
    # 直接信任消息提供的 signature 会导致 Claude API 返回 400 错误
    # 解决方案：始终从缓存查找 signature，不信任消息提供的 signature
    thinking_text = item.get("thinking", "")
    message_signature = item.get("signature", "")

    if thinking_text:
        cached_sig = get_cached_signature(thinking_text)
        if cached_sig:
            # 缓存命中，使用缓存的 signature
            item["signature"] = cached_sig
            has_valid_signature = True
            if message_signature and message_signature != cached_sig:
                log.info(f"[ANTIGRAVITY] 使用缓存 signature 替代消息 signature")
            break
        else:
            # 缓存未命中，即使消息有 signature 也不能使用
            thinking_without_signature = True
            if message_signature:
                log.warning(f"[ANTIGRAVITY] 消息有 signature 但缓存未命中，不信任")
```

### 7.4 修复原理

| 场景 | 修复前行为 | 修复后行为 |
|------|-----------|-----------|
| 缓存命中 | 使用消息 signature | 使用缓存 signature ✅ |
| 缓存未命中 + 消息有 signature | 信任消息 signature ❌ | 禁用 thinking 模式 ✅ |
| 缓存未命中 + 消息无 signature | 禁用 thinking 模式 | 禁用 thinking 模式 ✅ |

**核心改变**: 不再信任消息提供的 signature，始终以缓存为准。

---

## 8. 完整修改文件清单

| 文件 | 修改类型 | 修改内容 |
|------|----------|----------|
| `src/signature_cache.py` | 修改 | CacheEntry 结构，新增 `get_last_signature_with_text()` |
| `src/antigravity_router.py` | 修改 | 1. fallback 注入逻辑（Error 1）<br>2. signature 验证逻辑（Error 2） |

---

## 9. 总结

这两个错误的根本原因都是 **signature 与 thinking 内容的加密绑定关系**被忽略了：

1. **Error 1**: fallback 机制错误地使用占位文本 `"..."` 配合缓存的 signature
   - **修复**: 保存完整的 thinking 文本，fallback 时使用原始文本

2. **Error 2**: 代码信任消息提供的任何非空 signature
   - **修复**: 始终使用缓存验证，不信任消息 signature

两个修复共同确保 signature 与 thinking 内容在所有场景下都严格匹配。

---

*报告生成时间: 2026-01-09 03:11*
*Error 2 修复添加时间: 2026-01-09*
