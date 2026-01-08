# Signature 提取逻辑修复报告

**日期**: 2026-01-08
**修复者**: Claude Opus 4.5 (浮浮酱)
**影响文件**: `gcli2api/src/antigravity_router.py`

---

## 问题描述

### 症状
日志中频繁出现 "thinking degradation" 警告：
```
[THINKING DEGRADATION] Possible thinking degradation detected!
thinking_parts=48, total_chars=114, avg_chars=2.4, short_parts=48(100.0%)
```

同时，多轮对话中 thinking 模式会被禁用，因为 signature 缓存未能正确工作。

### 误判分析
"thinking degradation" 警告实际上是一个**误报**：
- 流式响应中，thinking 内容以小块（chunk）形式发送是**正常行为**
- 每个 chunk 平均 2.4 字符是流式传输的典型特征
- 真正的问题不是 chunk 大小，而是 **signature 提取失败**

---

## 根本原因

### 问题定位
在 `antigravity_router.py` 的 `convert_antigravity_stream_to_openai` 函数中：

**原代码逻辑**（L430-440 附近）：
```python
if part.get("thought") is True:
    # ... 处理 thinking 内容 ...
    thought_signature = part.get("thoughtSignature", "")
    if thought_signature:
        state["current_thinking_signature"] = thought_signature
```

**问题**：只在 `thought=true` 的 part 中检查 `thoughtSignature`

### 实际数据格式
参考 `anthropic_streaming.py` L267-283 的注释：
```python
# 兼容：下游可能会把 thoughtSignature 单独作为一个空part发送（此时未必带thought=true）
```

Antigravity API 可能将 `thoughtSignature` 发送在一个**独立的 part** 中，该 part 不包含 `thought=true` 标志。

### 数据示例
```json
// Part 1: thinking 内容
{"text": "Let me think...", "thought": true}

// Part 2: signature（独立发送，没有 thought=true）
{"thoughtSignature": "EqQBCg..."}
```

---

## 修复方案

### 核心修改
将 `thoughtSignature` 提取逻辑移至 `for part in parts:` 循环的**开头**，检查**所有** parts：

```python
for part in parts:
    # [SIGNATURE_CACHE FIX] 在处理任何 part 之前，先检查是否有 thoughtSignature
    # 关键修复：Antigravity API 可能把 thoughtSignature 单独发送在一个没有 thought=true 的 part 中
    # 因此必须在所有 part 中检查 signature，而不仅仅是 thinking parts
    thought_signature = part.get("thoughtSignature", "")
    if thought_signature and thought_signature.strip():
        if state["thinking_started"] or not state["current_thinking_signature"]:
            state["current_thinking_signature"] = thought_signature
            log.info(f"[SIGNATURE_CACHE] 从 part 提取到 thoughtSignature: "
                    f"len={len(thought_signature)}, thinking_started={state['thinking_started']}, "
                    f"part_keys={list(part.keys())}")

    # 处理思考内容
    if part.get("thought") is True:
        # ... 原有逻辑 ...
```

### 具体修改点

| 位置 | 修改内容 |
|------|----------|
| L415-427 | 新增：在循环开头统一提取 `thoughtSignature` |
| L438-440 | 移除：`thought=true` 分支中的重复提取逻辑 |
| L325-328 | 增强：将 signature 缺失的日志级别从 `debug` 改为 `warning` |

---

## 预期效果

修复后，日志应显示：

1. **Signature 提取成功**：
   ```
   [SIGNATURE_CACHE] 从 part 提取到 thoughtSignature: len=xxx, thinking_started=True, part_keys=['thoughtSignature']
   ```

2. **缓存写入成功**：
   ```
   [SIGNATURE_CACHE] Antigravity 流式响应缓存写入成功: thinking_len=xxx, model=xxx
   ```

3. **多轮对话正常**：
   - Thinking 模式在后续轮次中保持启用
   - 不再出现 "thinking 模式被禁用" 的警告

---

## 验证方法

1. 发起一个使用 thinking 模型的请求
2. 检查日志中是否出现 `[SIGNATURE_CACHE] 从 part 提取到 thoughtSignature` 消息
3. 进行多轮对话，确认 thinking 模式持续工作
4. 检查缓存统计：`get_cache_stats()` 应显示 hits > 0

---

## 相关文件

- `gcli2api/src/antigravity_router.py` - 主要修改文件
- `gcli2api/src/signature_cache.py` - 缓存实现（无需修改）
- `gcli2api/src/anthropic_streaming.py` - 参考实现

---

## 附加修复：Thinking Degradation 误报警告

### 问题描述
日志中频繁出现误导性的 "thinking degradation" 警告：
```
[THINKING DEGRADATION] Possible thinking degradation detected!
thinking_parts=48, total_chars=114, avg_chars=2.4, short_parts=48(100.0%)
```

### 根本原因
原有检测逻辑基于 **per-chunk 平均字符数**：
```python
is_degraded = avg_thinking_chars < 100 or short_ratio > 0.5
```

这是错误的判断方式，因为：
- 流式响应中，thinking 内容以小块（chunk）形式发送是 **正常行为**
- 每个 chunk 平均 2-3 个字符是流式传输的典型特征
- 这不代表 thinking 模式有问题

### 修复方案
将检测指标从 **per-chunk 平均值** 改为 **总字符数**：

```python
# 修复前：基于 per-chunk 平均值（错误）
is_degraded = avg_thinking_chars < 100 or short_ratio > 0.5

# 修复后：基于总字符数（正确）
MIN_THINKING_TOTAL_CHARS = 100
if state["total_thinking_chars"] < MIN_THINKING_TOTAL_CHARS:
    log.warning(f"[THINKING OBSERVABILITY] Very short thinking detected: ...")
else:
    log.debug(f"[THINKING OBSERVABILITY] Thinking stats: ...")  # 正常情况用 debug 级别
```

### 具体修改
| 位置 | 修改内容 |
|------|----------|
| L1059-1077 | 重写 thinking 统计逻辑 |
| - | 从判断 `avg_chars < 100` 改为判断 `total_chars < 100` |
| - | 正常情况下的日志从 `log.info` 改为 `log.debug`（减少噪音） |
| - | 将标签从 "THINKING DEGRADATION" 改为 "THINKING OBSERVABILITY" |

---

## 总结

本次修复包含两个问题：

### 问题 1：Signature 提取失败
- **原因**：代码假设 `thoughtSignature` 总是与 `thought=true` 在同一个 part 中
- **实际**：Antigravity API 可能将它们分开发送
- **修复**：检查所有 parts，而不仅仅是 thinking parts

### 问题 2：Thinking Degradation 误报
- **原因**：检测逻辑基于 per-chunk 平均字符数
- **实际**：流式传输中小 chunk 是正常行为
- **修复**：改为检测总字符数

两个修复都遵循了 **KISS 原则**：最小化改动，精准定位问题。
