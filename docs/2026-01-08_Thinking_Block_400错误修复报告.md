# Thinking Block 400 错误修复报告

**日期**: 2026-01-08
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 1. 问题描述

### 1.1 错误现象

在多轮工具调用场景中，模型进行多步推理时会触发 400 错误，导致对话过早终结：

```
[ERROR] [ANTIGRAVITY] API error (400): {
  "error": {
    "code": 400,
    "message": "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"message\":\"messages.13.content.0.type: Expected `thinking` or `redacted_thinking`, but found `text`. When `thinking` is enabled, a final `assistant` message must start with a thinking block...\"
  }
}
```

### 1.2 触发条件

- 使用 Thinking 模型（如 claude-3-5-opus）
- 历史消息中包含 thinking block
- Thinking block 的 signature 在 OpenAI 格式转换中丢失
- Signature 缓存未命中

---

## 2. 根本原因分析

### 2.1 Claude API 要求

当 `thinking` 模式启用时，Claude API 有严格要求：
> **最后一条 assistant 消息必须以 thinking block 开头**

thinking block 的格式必须是：
```json
{
  "text": "thinking content...",
  "thought": true,
  "thoughtSignature": "valid-signature-string"
}
```

### 2.2 问题代码位置

**文件**: `gcli2api/src/converters/message_converter.py`

**问题代码 (Line 375-378 和 401-406)**:

当 signature 缓存未命中时，代码会将 thinking 内容转换为普通文本：

```python
# 原始代码（有问题）
else:
    # 没有找到有效的 signature，转换为普通文本
    content_parts.append({"text": str(thinking_text)})
```

### 2.3 问题分析

| 步骤 | 行为 | 问题 |
|------|------|------|
| 1 | 历史消息含 thinking block | 正常 |
| 2 | 转换到 OpenAI 格式 | signature 丢失 |
| 3 | 转回 Antigravity 格式 | 尝试从缓存恢复 signature |
| 4 | 缓存未命中 | **问题触发点** |
| 5 | 转换为普通 `text` 类型 | 违反 API 要求 |
| 6 | API 返回 400 错误 | 对话终止 |

**核心问题**：将 thinking 内容转换为 `{"text": "..."}` 后，assistant 消息第一个 part 的类型变成了 `text`，而不是 `thinking`，违反了 Claude API 的要求。

---

## 3. 修复方案

### 3.1 修复策略

**策略选择**：缓存未命中时，**跳过整个 thinking block** 而不是转换为普通文本。

这样可以：
1. 避免产生 `text` 类型的 part 在 assistant 消息开头
2. 让 `antigravity_router.py` 中的验证逻辑检测到缺少有效 thinking block
3. 触发 thinking 模式禁用，安全回退到非 thinking 模式

### 3.2 修改内容

**文件**: `gcli2api/src/converters/message_converter.py`

#### 修改 1: thinking 类型处理 (Line 375-379)

```python
# 修改前
else:
    # 没有找到有效的 signature，转换为普通文本
    content_parts.append({"text": str(thinking_text)})

# 修改后
else:
    # [FIX] 缓存未命中时，跳过 thinking block 而不是转换为普通文本
    # 将 thinking 转换为普通文本会导致 assistant 消息不以 thinking block 开头
    # 这会触发 400 错误：Expected 'thinking' or 'redacted_thinking', but found 'text'
    log.warning(f"[SIGNATURE_CACHE] Thinking block 缓存未命中，跳过此 block 以避免 400 错误: thinking_len={len(thinking_text)}")
```

#### 修改 2: redacted_thinking 类型处理 (Line 402-406)

```python
# 修改前
else:
    # 没有找到有效的 signature，转换为普通文本
    content_parts.append({"text": str(thinking_text)})

# 修改后
else:
    # [FIX] 缓存未命中时，跳过 redacted_thinking block 而不是转换为普通文本
    # 将 thinking 转换为普通文本会导致 assistant 消息不以 thinking block 开头
    # 这会触发 400 错误：Expected 'thinking' or 'redacted_thinking', but found 'text'
    log.warning(f"[SIGNATURE_CACHE] Redacted thinking block 缓存未命中，跳过此 block 以避免 400 错误: thinking_len={len(thinking_text)}")
```

---

## 4. 逻辑流程对比

### 4.1 修复前流程

```
历史消息含 thinking block
    ↓
Signature 缓存未命中
    ↓
转换为普通 text 类型: {"text": "thinking content..."}
    ↓
Assistant 消息第一个 part 是 text 类型
    ↓
Claude API: "Expected 'thinking' or 'redacted_thinking', but found 'text'"
    ↓
400 错误 → 对话终止
```

### 4.2 修复后流程

```
历史消息含 thinking block
    ↓
Signature 缓存未命中
    ↓
跳过 thinking block（不添加任何内容）
    ↓
antigravity_router.py 检测到缺少有效 thinking block
    ↓
禁用 thinking 模式 (enable_thinking = False)
    ↓
清理历史消息中的 thinking 内容
    ↓
正常请求（无 thinking 模式）→ 对话继续
```

---

## 5. 相关组件协同

### 5.1 `antigravity_router.py` 的验证逻辑 (Line 1614-1622)

```python
# 只有当检测到 thinking block 但没有有效 signature 时才禁用 thinking
# 如果没有检测到任何 thinking block（首轮对话），则保持 thinking 启用
if has_thinking_block and not has_valid_signature:
    log.warning(f"[ANTIGRAVITY] Thinking 已启用，但历史消息中的 thinking block 没有有效的 signature（缓存也未命中），禁用 thinking 模式以避免 400 错误")
    enable_thinking = False
elif has_thinking_block and has_valid_signature:
    log.info(f"[ANTIGRAVITY] 历史消息中检测到有效的 thinking block，保持 thinking 模式启用")
else:
    log.debug(f"[ANTIGRAVITY] 历史消息中没有 thinking block（可能是首轮对话），保持 thinking 模式启用")
```

此验证逻辑与 `message_converter.py` 的修复协同工作：
- 当缓存未命中时，thinking block 被跳过
- router 检测到 `has_thinking_block = True` 但 `has_valid_signature = False`
- 自动禁用 thinking 模式，避免 400 错误

---

## 6. 日志标识

修复后，当缓存未命中时会输出以下日志：

```
[SIGNATURE_CACHE] Thinking block 缓存未命中，跳过此 block 以避免 400 错误: thinking_len=1234
[ANTIGRAVITY] Thinking 已启用，但历史消息中的 thinking block 没有有效的 signature（缓存也未命中），禁用 thinking 模式以避免 400 错误
```

---

## 7. 验证方法

### 7.1 功能验证

1. 使用 Cursor IDE 与 Thinking 模型进行多轮工具调用对话
2. 确保第一轮产生 thinking 内容
3. 继续对话，观察是否发生 400 错误
4. 如果缓存未命中，应看到日志提示 thinking 模式被禁用
5. 对话应能正常继续（以非 thinking 模式）

### 7.2 理想情况（缓存命中）

1. 第一轮：产生 thinking + signature
2. Signature 被缓存（见 `2026-01-08_Signature缓存方案优化报告.md`）
3. 后续轮次：从缓存恢复 signature
4. Thinking 模式保持启用

---

## 8. 总结

| 问题 | 状态 |
|------|------|
| 多轮工具调用时 400 错误 | ✅ 已修复 |
| Thinking block 转换为 text 类型 | ✅ 已修复 |
| 缓存未命中导致对话终止 | ✅ 已修复 |

**关键改进**：
- 缓存未命中时优雅降级（跳过 thinking block）而非错误转换
- 与 `antigravity_router.py` 的验证逻辑协同工作
- 保证对话连续性，避免过早终止

---

*文档结束 - 浮浮酱 (´｡• ᵕ •｡`) ♡*
