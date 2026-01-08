# Signature 缓存方案优化报告

**日期**: 2026-01-08
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 1. 问题发现

### 1.1 原始问题

在 2026-01-07 实施的 Signature 缓存方案中，发现缓存**永远无法命中**。

### 1.2 根本原因分析

通过代码审查发现：

| 组件 | 缓存写入 | 缓存读取 | 问题 |
|------|----------|----------|------|
| `anthropic_streaming.py` | ✅ 有 | ❌ 无 | 正常 |
| `antigravity_router.py` | ❌ **无** | ✅ 有 | **关键缺失** |
| `message_converter.py` | ❌ 无 | ✅ 有 | 正常 |

**核心问题**：`antigravity_router.py` 中的 `convert_antigravity_stream_to_openai` 函数：
1. 只导入了 `get_cached_signature`（读取），没有导入 `cache_signature`（写入）
2. 处理 thinking parts 时完全忽略了 `thoughtSignature` 字段
3. `flush_thinking_buffer()` 函数只做格式转换，不进行缓存

**结果**：当用户通过 Antigravity 路由使用 thinking 模型时，signature 从未被缓存，导致后续对话无法恢复 signature。

---

## 2. 修复内容

### 2.1 修改文件

**文件**: `gcli2api/src/antigravity_router.py`

### 2.2 具体修改

#### 修改 1: 添加 cache_signature 导入

```python
# 修改前 (Line 16)
from .signature_cache import get_cached_signature

# 修改后
from .signature_cache import get_cached_signature, cache_signature
```

#### 修改 2: 扩展 state 字典

```python
state = {
    # ... 原有字段 ...
    # [SIGNATURE_CACHE] 用于缓存 thinking signature
    "current_thinking_text": "",      # 累积的 thinking 文本内容
    "current_thinking_signature": "", # 当前 thinking block 的 signature
}
```

#### 修改 3: 修改 thinking parts 处理逻辑

```python
if part.get("thought") is True:
    # ... 原有逻辑 ...

    # [SIGNATURE_CACHE] 累积 thinking 文本和提取 signature
    state["current_thinking_text"] += thinking_text
    # Antigravity 格式中 signature 字段名为 thoughtSignature
    thought_signature = part.get("thoughtSignature", "")
    if thought_signature and thought_signature.strip():
        state["current_thinking_signature"] = thought_signature
        log.debug(f"[SIGNATURE_CACHE] 提取到 thoughtSignature: len={len(thought_signature)}")
```

#### 修改 4: 修改 flush_thinking_buffer 函数

```python
def flush_thinking_buffer() -> Optional[str]:
    if not state["thinking_started"]:
        return None
    state["thinking_buffer"] += "\n</think>\n"
    thinking_block = state["thinking_buffer"]
    state["content_buffer"] += thinking_block

    # [SIGNATURE_CACHE] 在 thinking block 结束时写入缓存
    if state["current_thinking_text"] and state["current_thinking_signature"]:
        success = cache_signature(
            state["current_thinking_text"],
            state["current_thinking_signature"],
            model=model
        )
        if success:
            log.info(f"[SIGNATURE_CACHE] Antigravity 流式响应缓存写入成功: "
                    f"thinking_len={len(state['current_thinking_text'])}, model={model}")
        else:
            log.debug(f"[SIGNATURE_CACHE] Antigravity 流式响应缓存写入失败或跳过")
    elif state["current_thinking_text"] and not state["current_thinking_signature"]:
        log.debug(f"[SIGNATURE_CACHE] Thinking block 没有 signature，无法缓存: "
                 f"thinking_len={len(state['current_thinking_text'])}")

    # 重置 thinking 状态
    state["thinking_buffer"] = ""
    state["thinking_started"] = False
    state["current_thinking_text"] = ""
    state["current_thinking_signature"] = ""
    return thinking_block
```

---

## 3. 数据流对比

### 3.1 修复前

```
Antigravity API Response
    ↓
convert_antigravity_stream_to_openai()
    ↓
提取 thinking text（忽略 thoughtSignature）
    ↓
转换为 <think> 标签格式
    ↓
发送给 Cursor（signature 丢失）
    ↓
后续对话无法恢复 signature → thinking 模式被禁用
```

### 3.2 修复后

```
Antigravity API Response
    ↓
convert_antigravity_stream_to_openai()
    ↓
提取 thinking text + thoughtSignature
    ↓
累积到 state["current_thinking_text"] 和 state["current_thinking_signature"]
    ↓
flush_thinking_buffer() 时调用 cache_signature()
    ↓
缓存写入成功
    ↓
后续对话可以从缓存恢复 signature → thinking 模式保持启用
```

---

## 4. 日志标识

修复后的日志输出示例：

```
[SIGNATURE_CACHE] 提取到 thoughtSignature: len=256
[SIGNATURE_CACHE] Antigravity 流式响应缓存写入成功: thinking_len=1234, model=gemini-2.5-flash-thinking
[SIGNATURE_CACHE] 从缓存恢复 signature: thinking_len=1234
```

---

## 5. 验证方法

### 5.1 功能验证

1. 使用 Cursor IDE 与 Antigravity thinking 模型进行对话
2. 观察日志中的 `[SIGNATURE_CACHE] Antigravity 流式响应缓存写入成功` 条目
3. 进行第二轮对话，观察 `[SIGNATURE_CACHE] 从缓存恢复 signature` 条目
4. 确认 thinking 模式在多轮对话中保持启用

### 5.2 缓存统计验证

```bash
# 获取缓存统计
curl -X GET "http://localhost:8000/cache/signature/stats" \
  -H "Authorization: Bearer <token>"

# 预期：writes 和 hits 都应该有值
{
  "success": true,
  "data": {
    "hits": 5,
    "misses": 1,
    "writes": 6,
    "hit_rate": 0.833,
    ...
  }
}
```

---

## 6. 总结

本次优化修复了 Signature 缓存方案的关键缺陷：

| 问题 | 状态 |
|------|------|
| Antigravity 流式响应不写入缓存 | ✅ 已修复 |
| thoughtSignature 字段被忽略 | ✅ 已修复 |
| 缓存永远无法命中 | ✅ 已修复 |

修复后，无论用户使用 Anthropic 路由还是 Antigravity 路由，signature 缓存都能正常工作，确保 Cursor IDE 与 Claude thinking 模型的多轮对话兼容性。

---

*文档结束 - 浮浮酱 (´｡• ᵕ •｡`) ♡*
