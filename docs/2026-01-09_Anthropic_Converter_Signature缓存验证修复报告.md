# Anthropic Converter Signature 缓存验证修复报告

**作者：** 浮浮酱 (Claude Sonnet 4)  
**日期：** 2026-01-09  
**问题级别：** 🔴 高优先级（导致 400 错误）

---

## 1. 问题描述

### 1.1 错误现象

在使用 `claude-opus-4-5-thinking` 模型进行多工具调用时，出现 400 错误：

```
[05:20:10] [ERROR] [ANTIGRAVITY] Request failed with model claude-opus-4-5-thinking: 
Antigravity API error (400): {
  "error": {
    "code": 400,
    "message": "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",
    \"message\":\"messages.7.content.34: Invalid `signature` in `thinking` block\"}
  }
}
```

### 1.2 关键信息

- **错误位置：** `messages.7.content.34`
  - 第 8 条消息（0-indexed）
  - 第 35 个 content 块
- **错误类型：** `Invalid signature in thinking block`
- **场景：** 多工具调用（写 MD 文档等）

---

## 2. 问题根因分析

### 2.1 代码不一致问题

项目中有 **两个消息转换器**，但只有一个被修复：

| 转换器 | 路由 | 修复状态 |
|--------|------|----------|
| `message_converter.py` | OpenAI 格式 `/v1/chat/completions` | ✅ 已修复 |
| `anthropic_converter.py` | Anthropic 格式 `/v1/messages` | ❌ **未修复** |

### 2.2 问题代码

`anthropic_converter.py` 第 498-538 行（修复前）：

```python
if item_type == "thinking":
    if not include_thinking:
        continue

    # ❌ 问题：直接使用消息中的 signature
    signature = item.get("signature")
    if not signature:
        continue

    thinking_text = item.get("thinking", "")
    part: Dict[str, Any] = {
        "text": str(thinking_text),
        "thought": True,
        "thoughtSignature": signature,  # ❌ 直接信任消息的 signature
    }
    parts.append(part)
```

### 2.3 错误流程

```
1. 用户在 Cursor 中进行多轮对话
2. 模型返回 thinking block + 工具调用
3. Cursor 缓存历史消息（包含 thinking blocks 和 signature）
4. 用户重新打开对话 / 继续对话
5. Cursor 发送历史消息到 API 网关
6. anthropic_converter.py 直接使用消息中的旧 signature
7. Claude API 验证 signature 失败
8. 返回 400 错误：Invalid signature in thinking block
```

### 2.4 为什么 message_converter.py 没问题？

`message_converter.py` 已经在 2026-01-09 修复，使用缓存验证：

```python
# ✅ 正确做法：从缓存验证 signature
cached_signature = get_cached_signature(thinking_text)
if cached_signature:
    content_parts.append({
        "text": str(thinking_text),
        "thought": True,
        "thoughtSignature": cached_signature  # ✅ 使用缓存的 signature
    })
else:
    # 缓存未命中，跳过 thinking block
    log.warning("Thinking block 缓存未命中，跳过此 block")
```

---

## 3. 修复方案

### 3.1 修复原则

> **永远不要直接信任消息提供的 signature，始终从缓存验证。**

### 3.2 修复代码

`anthropic_converter.py` 第 498-563 行（修复后）：

```python
if item_type == "thinking":
    if not include_thinking:
        continue

    thinking_text = item.get("thinking", "")
    if thinking_text is None:
        thinking_text = ""
    message_signature = item.get("signature", "")

    # [FIX 2026-01-09] 始终优先使用缓存验证 signature
    from src.signature_cache import get_cached_signature
    if thinking_text:
        cached_signature = get_cached_signature(thinking_text)
        if cached_signature:
            part: Dict[str, Any] = {
                "text": str(thinking_text),
                "thought": True,
                "thoughtSignature": cached_signature,  # ✅ 使用缓存的 signature
            }
            parts.append(part)
            if message_signature and message_signature != cached_signature:
                log.info(f"[ANTHROPIC CONVERTER] 使用缓存 signature 替代消息 signature")
        else:
            # [FIX] 缓存未命中时，跳过 thinking block
            log.warning(f"[ANTHROPIC CONVERTER] Thinking block 缓存未命中，跳过此 block")

elif item_type == "redacted_thinking":
    # 同样的修复逻辑...
```

### 3.3 修复对比

| 方面 | 修复前 | 修复后 |
|------|--------|--------|
| **signature 来源** | 消息中的 `item.get("signature")` | 缓存 `get_cached_signature(thinking_text)` |
| **信任策略** | 直接信任消息 | 只信任缓存 |
| **缓存未命中处理** | 使用消息的 signature（导致 400） | 跳过 thinking block |
| **日志** | 无 | 详细记录缓存命中/未命中情况 |

---

## 4. 影响范围

### 4.1 受影响的路由

- `/v1/messages` (Anthropic 格式)
- `/anthropic/v1/messages`

### 4.2 受影响的场景

- ✅ 多轮对话中的 thinking 模式
- ✅ 多工具调用场景
- ✅ 重新打开对话后继续对话
- ✅ 长时间对话中断后恢复

### 4.3 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `src/anthropic_converter.py` | `convert_messages_to_contents` 函数中的 thinking/redacted_thinking 处理逻辑 |

---

## 5. 测试验证

### 5.1 测试场景

1. **多工具调用**
   - 调用多个工具（如 write_file, read_file 等）
   - 验证 thinking block 的 signature 正确处理

2. **对话恢复**
   - 进行多轮对话
   - 重启服务
   - 继续对话
   - 验证不会出现 400 错误

3. **长对话**
   - 进行 10+ 轮对话
   - 验证所有 thinking blocks 正确处理

### 5.2 验证日志

修复后应该看到以下日志：

```
[ANTHROPIC CONVERTER] 使用缓存 signature: thinking_len=1234
[ANTHROPIC CONVERTER] 使用缓存 signature 替代消息 signature: thinking_len=5678
```

而不是 400 错误。

---

## 6. 经验教训

### 6.1 代码一致性

> 当修复一个模块时，检查是否有其他模块存在相同问题。

本次问题的根因是只修复了 `message_converter.py`，而遗漏了 `anthropic_converter.py`。

### 6.2 Signature 处理原则

1. **永远不信任客户端提供的 signature**
2. **始终从服务端缓存验证 signature**
3. **缓存未命中时，跳过 thinking block 而不是使用无效 signature**

### 6.3 相关修复报告

本次修复是 Signature 缓存系列修复的一部分：

| 日期 | 报告 | 修复内容 |
|------|------|----------|
| 2026-01-07 | Signature缓存方案可行性分析报告 | 初步方案设计 |
| 2026-01-07 | Signature缓存方案实施报告 | 基础实现 |
| 2026-01-08 | Signature提取逻辑修复报告 | 流式响应中的 signature 提取 |
| 2026-01-08 | Signature缓存命中率优化报告 | 提高缓存命中率 |
| 2026-01-09 | Thinking模式Signature_Fallback机制修复报告 | Fallback 机制 |
| 2026-01-09 | **本报告** | anthropic_converter.py 缓存验证 |

---

## 7. 总结

### 7.1 问题

`anthropic_converter.py` 直接信任消息中的 signature，导致多工具调用场景下出现 400 错误。

### 7.2 修复

使用 `get_cached_signature()` 从缓存验证 signature，与 `message_converter.py` 保持一致。

### 7.3 状态

✅ **已修复** - 2026-01-09

---

---

## 8. 后续修复：Fallback 机制问题

### 8.1 新问题发现

修复 `anthropic_converter.py` 后，在工具调用场景下仍然出现 400 错误：

```
[07:15:35] [ERROR] [ANTIGRAVITY] API error (400): {
  "error": {
    "code": 400,
    "message": "messages.1.content.0: Invalid `signature` in `thinking` block"
  }
}
```

### 8.2 问题根因

`antigravity_router.py` 第 1852-1870 行的 fallback 机制存在严重缺陷：

```python
# ❌ 问题代码
from .signature_cache import get_last_signature_with_text
cache_result = get_last_signature_with_text()
if cache_result:
    last_sig, original_thinking_text = cache_result
    thinking_part = {
        "text": original_thinking_text,  # ❌ 使用全局最近缓存的 thinking 文本
        "thought": True,
        "thoughtSignature": last_sig
    }
    parts.insert(0, thinking_part)
```

**问题：** `get_last_signature_with_text()` 返回的是**全局最近缓存的** signature 和 thinking 文本，这些内容可能与当前消息**完全无关**！

### 8.3 错误场景

```
1. 第一轮对话：模型返回 thinking block A + 工具调用
2. Cursor 保存历史消息，可能截断或修改 thinking 内容为 A'
3. 第二轮对话：Cursor 发送历史消息（包含 A'）
4. message_converter.py 查询缓存，A' 与 A 不匹配，缓存未命中
5. 移除 thinking 标签，消息不以 thinking block 开头
6. Fallback 机制使用 A 的 signature 和内容
7. 发送请求：消息包含 A，但 Cursor 期望的是 A'
8. Claude API 验证失败 → 400 错误：Invalid signature in thinking block
```

### 8.4 修复方案

**删除不安全的 fallback 机制**，当无法找到匹配的 thinking block 时，直接禁用 thinking 模式：

```python
# ✅ 修复后的代码
if thinking_part:
    parts.insert(0, thinking_part)
    log.info(f"[ANTIGRAVITY] Added thinking block from previous message")
else:
    # 无法找到有效的 thinking block，禁用 thinking 模式
    log.warning(f"[ANTIGRAVITY] Last assistant message does not start with thinking block, "
               f"cannot find previous thinking block with valid signature. "
               f"DISABLING thinking mode to avoid 400 error.")
    enable_thinking = False
    # 重新清理消息中的 thinking 内容
    messages = strip_thinking_from_openai_messages(messages)
    # 重新转换消息格式（不带 thinking）
    contents = openai_messages_to_antigravity_contents(
        messages,
        enable_thinking=False,
        tools=tools,
        recommend_sequential_thinking=recommend_sequential
    )
```

### 8.5 核心原则

> **Signature 必须与 thinking 内容精确匹配，永远不要使用无关的 signature！**

- ✅ 从缓存查找与当前 thinking 内容匹配的 signature
- ✅ 缓存未命中时，跳过 thinking block 或禁用 thinking 模式
- ❌ 使用全局最近缓存的 signature（可能与当前内容无关）
- ❌ 使用占位符文本（如 `"..."`）配合任意 signature

### 8.6 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/antigravity_router.py` | 删除第 1852-1870 行的 fallback 机制，直接禁用 thinking 模式 |

---

**文档结束** (๑•̀ㅂ•́)و✧

