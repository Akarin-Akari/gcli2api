# Cursor Thinking 模式 400 错误修复报告

> **日期**: 2026-01-20  
> **作者**: 浮浮酱 (Claude Opus 4.5)  
> **问题**: `Invalid signature in thinking block` 400 错误导致 Cursor thinking + tool call 失败

---

## 问题背景

在 Cursor IDE 中使用 Claude Extended Thinking 模式时，当用户请求涉及 **thinking + tool call** 的多轮对话，会触发以下错误：

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "messages.3.content.0: Invalid `signature` in `thinking` block"
  }
}
```

---

## 根因分析

### Claude Thinking Signature 的本质

| 特性 | 说明 |
|------|------|
| 会话绑定性 | Signature 是针对**特定 API 请求**生成的 |
| 不可复用 | 跨请求使用会被 Claude API 拒绝 |
| 官方策略 | 历史 thinking blocks 可以直接省略 |

### 代码中的架构理解错误

gcli2api 中存在多处代码尝试用**缓存的签名恢复历史 thinking blocks**，这是根本性的错误：

```
请求 1: Claude 生成 thinking + signature_A
请求 2: 代码尝试用 signature_A 恢复历史 thinking block → 400 错误！
```

---

## 修复内容

### 修复原则

> **历史 thinking blocks → 直接删除，不尝试任何恢复**

### 修复清单

| # | 文件 | 行号 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | `antigravity_router.py` | 1864-1873 | 因历史签名无效而禁用 thinking | 移除该逻辑，信任 Sanitizer |
| 2 | `message_converter.py` | 405-436 | 用 `get_cached_signature()` 恢复 thinking block | 改为直接丢弃 |
| 3 | `antigravity_router.py` | 1984-2034 | Fallback 用 `get_last_signature_with_text()` 注入 | 移除整个 fallback |
| 4 | `tool_loop_recovery.py` | 234-271 | 在 assistant 消息开头注入 thinking block | 移除注入逻辑 |

### 保留的安全逻辑

`sanitizer.py` 的逻辑是**正确的**，保持不变：
- 只对**最新 2 条 assistant 消息**尝试签名恢复
- 历史消息的 thinking blocks 直接删除
- 使用 `use_placeholder_fallback=False`，失败时降级为 text

---

## 代码变更摘要

### 1. antigravity_router.py (第一层 + 第三层)

```diff
- # 检测到历史签名无效，禁用 thinking
- if not all_thinking_valid and any_thinking_found:
-     enable_thinking = False

+ # 历史签名无效是正常的（会话绑定），信任 Sanitizer 清理
+ # 不再禁用 thinking 模式
```

```diff
- # Fallback: 从缓存恢复 thinking block
- cached_result = get_last_signature_with_text()
- if cached_result:
-     parts.insert(0, {"thought": True, "thoughtSignature": cached_signature})

+ # 不尝试恢复，直接禁用 thinking 让 Claude 生成新的
+ enable_thinking = False
```

### 2. message_converter.py (第二层)

```diff
- # 尝试从缓存恢复 signature
- cached_signature = get_cached_signature(thinking_content)
- if cached_signature:
-     content_parts.append({"thought": True, "thoughtSignature": cached_signature})

+ # 根据 Claude 官方文档，直接丢弃历史 thinking blocks
+ log.info(f"[MESSAGE_CONVERTER] 丢弃历史 thinking block: len={len(thinking_content)}")
```

### 3. tool_loop_recovery.py (第四层)

```diff
- # 从缓存恢复签名并注入 thinking block
- last_result = get_last_signature_with_text()
- thinking_block = {"type": "thinking", "thinking": text, "signature": sig}
- content.insert(0, thinking_block)

+ # 不注入 thinking block（签名是会话绑定的，无法复用）
+ logger.info("Skipping thinking block injection (signatures are session-bound)")
```

---

## 验证结果

修复后的预期行为：

| 场景 | 期望结果 |
|------|---------|
| 首次 thinking 请求 | ✅ 正常生成 `<think>` 内容 |
| Thinking + Tool Call | ✅ 不再出现 400 错误 |
| 多轮对话 | ✅ 每轮生成新的 thinking（旧的被丢弃） |
| 重新打开对话 | ✅ 正常工作（历史 thinking 被安全跳过） |

---

## 附录：Gemini 3 Pro 的 Thinking 差异

| 特性 | Claude Extended Thinking | Gemini 3 Pro |
|------|-------------------------|--------------|
| Thinking 内容 | ✅ 流式发送给客户端 | ❌ 不发送（服务端内部） |
| 用户可见性 | ✅ 可见 `<think>` 标签 | ❌ 不可见 |
| 确认方式 | `thought: true` 标记 | `thoughtsTokenCount` 在 metadata |

Gemini 的 thinking 是**服务端内部进行**的，不暴露给客户端，这是设计如此。

---

## 总结

本次修复统一了 gcli2api 中所有与 thinking signature 相关的处理逻辑，确保遵循 Claude 官方文档的正确策略：

> **历史 thinking blocks 可以安全省略，API 会在新响应中生成新的 thinking 内容。**
