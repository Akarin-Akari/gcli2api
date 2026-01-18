# 2026-01-08 ThinkingBudget 与 MaxTokens 关系修复报告

## 问题描述

### 现象

在启用 thinking 模式后，Cursor 生成长文本时频繁触发 MAX_TOKENS 限制：

```
[ANTIGRAVITY STREAM] SSE line 1116: {..."finishReason": "MAX_TOKENS"..."candidatesTokenCount": 4096...}
```

日志显示：
- `thinking={'type': 'enabled', 'budget_tokens': 31999}`
- `candidatesTokenCount: 4096`

### 问题分析

在 `anthropic_converter.py:768-785` 中存在以下逻辑：

```python
if budget >= max_tokens:
    adjusted_budget = max(0, max_tokens - 1)
    thinking_config["thinkingBudget"] = adjusted_budget
```

**问题**：当 `thinkingBudget >= max_tokens` 时，budget 被调整为 `max_tokens - 1`

**示例**：
- 用户设置：`thinkingBudget=31999`, `max_tokens=4096`
- 调整后：`thinkingBudget=4095`（几乎所有 token）
- 实际输出空间：**只有 1 个 token！**

即使之前的修复将 `max_tokens` 提升到 16384：
- 调整后：`thinkingBudget=16383`
- 实际输出空间：**仍然只有 1 个 token！**

这就是为什么 MAX_TOKENS 会在 thinking 模式下被快速触发。

## 根本原因

1. **下游 API 的约束**：`thinkingBudget` 必须小于 `max_tokens`
2. **原有逻辑的缺陷**：当 budget >= max_tokens 时，简单地将 budget 调整为 max_tokens - 1
3. **结果**：几乎所有 token 都分配给 thinking，实际输出空间极小

## 修复方案

### 核心思路

确保 `max_tokens >= thinkingBudget + MIN_OUTPUT_TOKENS`

### 修改位置

**文件**: `gcli2api/src/antigravity_anthropic_router.py` 第 560-591 行

### 修改内容

```python
# [FIX 2026-01-08] 设置最小 max_tokens 保护
MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值（无 thinking 时）
MIN_OUTPUT_TOKENS = 16384  # 最小输出 token 数（有 thinking 时）
original_max_tokens = max_tokens

# [FIX 2026-01-08 Part2] 当启用 thinking 时，确保 max_tokens 足够容纳 thinkingBudget + 实际输出
thinking_budget = 0
if thinking_present and isinstance(thinking_value, dict):
    thinking_budget = thinking_value.get("budget_tokens", 0) or 0
    if isinstance(thinking_budget, int) and thinking_budget > 0:
        # 确保 max_tokens >= thinkingBudget + MIN_OUTPUT_TOKENS
        required_max_tokens = thinking_budget + MIN_OUTPUT_TOKENS
        if isinstance(max_tokens, int) and max_tokens < required_max_tokens:
            max_tokens = required_max_tokens
            payload["max_tokens"] = max_tokens
            log.info(
                f"[ANTHROPIC] max_tokens 因 thinking 自动提升: {original_max_tokens} -> {max_tokens} "
                f"(thinkingBudget={thinking_budget}, MIN_OUTPUT_TOKENS={MIN_OUTPUT_TOKENS})"
            )

# 无 thinking 时的基础保护
if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
    max_tokens = MIN_MAX_TOKENS
    payload["max_tokens"] = max_tokens
    log.info(
        f"[ANTHROPIC] max_tokens 自动提升: {original_max_tokens} -> {max_tokens} "
        f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
    )
```

## 预期效果

### 修复前

| thinkingBudget | max_tokens (原始) | max_tokens (调整后) | 实际输出空间 |
|----------------|-------------------|---------------------|--------------|
| 31999          | 4096              | 16384               | 1 token      |
| 31999          | 16384             | 16384               | 1 token      |

### 修复后

| thinkingBudget | max_tokens (原始) | max_tokens (调整后) | 实际输出空间 |
|----------------|-------------------|---------------------|--------------|
| 31999          | 4096              | 48383               | 16384 tokens |
| 31999          | 16384             | 48383               | 16384 tokens |
| 0              | 4096              | 16384               | 16384 tokens |

## 日志示例

修复后的日志：

```
[ANTHROPIC] max_tokens 因 thinking 自动提升: 4096 -> 48383 (thinkingBudget=31999, MIN_OUTPUT_TOKENS=16384)
[ANTHROPIC] /messages 收到请求: client=127.0.0.1:7824, model=claude-opus-4-5-20251101, stream=True, messages=113, max_tokens=48383 (original=4096), thinking_present=True, thinking={'type': 'enabled', 'budget_tokens': 31999}, ua=python-httpx/0.28.1
```

## 备份文件

- `antigravity_anthropic_router.py.bak.20260108_064711` - Part1 修复备份
- `antigravity_anthropic_router.py.bak.20260108_065552` - Part2 修复备份

## 补丁脚本

- `patch_max_tokens.py` - Part1 补丁（基础 max_tokens 保护）
- `patch_thinking_budget.py` - Part2 补丁（thinkingBudget 关系修复）

## 遵循原则

- **KISS**: 简单的数学计算，不引入复杂逻辑
- **用户体验优先**: 自动调整，无需用户手动配置
- **可观测性**: 详细的日志记录，方便诊断

## 关联问题

此修复与以下问题相关：

1. **MAX_TOKENS 导致输出截断** - 已修复
2. **Cursor 不写文件直接在 chat 输出** - 可能因 MAX_TOKENS 导致工具调用不完整
3. **孤儿 tool_result 问题** - 已在之前的修复中处理

---

## [FIX 2026-01-09] 双向限制策略

### 问题发现

原有修复存在问题：当 `thinkingBudget` 很大（如 31999）时，`max_tokens` 会被提升到非常高的值（如 48383），这**会触发后端 429 错误**。

### 解决方案：双向限制

核心思路：既要保证足够的输出空间，又不能让 `max_tokens` 过大触发 429。

**策略**：
```
如果 thinkingBudget + MIN_OUTPUT_TOKENS > MAX_ALLOWED_TOKENS:
    1. 先下调 thinkingBudget = MAX_ALLOWED_TOKENS - MIN_OUTPUT_TOKENS
    2. max_tokens 设置为 MIN(budget + MIN_OUTPUT_TOKENS, MAX_ALLOWED_TOKENS)
```

**参数配置**：

| 参数 | 值 | 说明 |
|------|------|------|
| `MIN_OUTPUT_TOKENS` | 4096 | 实际输出的最小保障空间 |
| `MAX_ALLOWED_TOKENS` | 32000 | max_tokens 的绝对上限（防止 429） |

### 计算示例

**示例1**：当 `thinkingBudget=31999`, 客户端 `max_tokens=4096` 时：

| 步骤 | 计算 | 结果 |
|------|------|------|
| 1. 检查总需求 | `31999 + 4096 = 36095` | > 32000 ❌ |
| 2. 下调 thinkingBudget | `32000 - 4096 = 27904` | ✅ |
| 3. 计算 max_tokens | `MIN(27904 + 4096, 32000) = 32000` | ✅ |
| **最终结果** | `max_tokens=32000, thinkingBudget=27904` | 输出空间=4096 ✅ |

**示例2**：当 `thinkingBudget=8192`, 客户端 `max_tokens=4096` 时：

| 步骤 | 计算 | 结果 |
|------|------|------|
| 1. 检查总需求 | `8192 + 4096 = 12288` | < 32000 ✅ |
| 2. 保持 thinkingBudget | 不变 | 8192 |
| 3. 计算 max_tokens | `MIN(8192 + 4096, 32000) = 12288` | ✅ |
| **最终结果** | `max_tokens=12288, thinkingBudget=8192` | 输出空间=4096 ✅ |

### 修改文件

**文件**: `gcli2api/src/anthropic_converter.py`

**新增常量**（第 11-14 行）：
```python
# [FIX 2026-01-09] 双向限制策略常量定义
MAX_ALLOWED_TOKENS = 32000   # max_tokens 的绝对上限（防止 429 错误）
MIN_OUTPUT_TOKENS = 4096     # 实际输出的最小保障空间
```

**修改逻辑**（第 781-837 行）：完整重写 thinking budget 调整逻辑

### 日志示例

修复后的日志：

```
[ANTHROPIC][thinking] 双向限制生效：thinkingBudget 下调 31999 -> 27904 (MAX_ALLOWED=32000, MIN_OUTPUT=4096)
[ANTHROPIC][thinking] 双向限制生效：maxOutputTokens 提升 4096 -> 32000 (thinkingBudget=27904, 实际输出空间=4096)
```

### 预期效果

- ✅ 保证至少 4096 tokens 的输出空间
- ✅ 防止 max_tokens 超过 32000 导致 429 错误
- ✅ 自动调整 thinkingBudget 和 max_tokens 的关系
- ✅ 详细的日志记录调整过程
