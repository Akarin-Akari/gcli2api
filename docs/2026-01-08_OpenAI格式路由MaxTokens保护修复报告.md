# 2026-01-08 OpenAI 格式路由 Max Tokens 保护修复报告

## 问题描述

### 现象

在 Cursor 中使用工具调用时卡住，日志显示：

```
POST /antigravity/v1/chat/completions
finishReason=MAX_TOKENS, candidatesTokenCount=4096
```

### 问题分析

之前的修复只针对 **Anthropic 格式路由** (`/antigravity/v1/messages`)，而 Cursor 实际使用的是 **OpenAI 格式路由** (`/antigravity/v1/chat/completions`)。

| 路由 | 文件 | 修复状态 |
|------|------|----------|
| `/antigravity/v1/messages` | `antigravity_anthropic_router.py` | ✅ 已修复 |
| `/antigravity/v1/chat/completions` | `antigravity_router.py` | ❌ 未修复 → ✅ 现已修复 |

## 根本原因

1. **OpenAI 格式路由没有 max_tokens 保护**
   - 第 1829-1836 行获取 `max_tokens` 参数
   - 直接传递给 `generate_generation_config()`，没有任何保护

2. **`generate_generation_config()` 中硬编码 thinkingBudget=1024**
   - 在 `tool_converter.py:457-460`
   - 但这不是主要问题，主要问题是 max_tokens 太小

3. **Cursor 发送的 max_tokens=4096**
   - 即使 thinkingBudget=1024，也只留 3072 tokens 给输出
   - 对于长文本生成（如研究报告）远远不够

## 修复方案

### 修改位置

**文件**: `gcli2api/src/antigravity_router.py` 第 1838-1866 行

### 修改内容

```python
# [FIX 2026-01-08] 同步 Anthropic 格式的 max_tokens 保护逻辑
MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值（无 thinking 时）
MIN_OUTPUT_TOKENS = 16384  # 最小输出 token 数（有 thinking 时）
original_max_tokens = parameters.get("max_tokens")

# 当启用 thinking 时，确保 max_tokens 足够容纳 thinkingBudget + 实际输出
if enable_thinking:
    thinking_budget_estimate = 32000  # 估算最大 thinking budget
    required_max_tokens = thinking_budget_estimate + MIN_OUTPUT_TOKENS
    current_max = parameters.get("max_tokens", 0) or 0
    if isinstance(current_max, int) and current_max < required_max_tokens:
        parameters["max_tokens"] = required_max_tokens
        log.info(
            f"[ANTIGRAVITY] max_tokens 因 thinking 自动提升: {original_max_tokens} -> {required_max_tokens} "
            f"(thinking_budget_estimate={thinking_budget_estimate}, MIN_OUTPUT_TOKENS={MIN_OUTPUT_TOKENS})"
        )
else:
    # 无 thinking 时的基础保护
    current_max = parameters.get("max_tokens", 0) or 0
    if isinstance(current_max, int) and current_max < MIN_MAX_TOKENS:
        parameters["max_tokens"] = MIN_MAX_TOKENS
        log.info(
            f"[ANTIGRAVITY] max_tokens 自动提升: {original_max_tokens} -> {MIN_MAX_TOKENS} "
            f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
        )
```

## 预期效果

### 修复前

| 场景 | max_tokens (原始) | max_tokens (实际) | 输出空间 |
|------|-------------------|-------------------|----------|
| Cursor + thinking | 4096 | 4096 | ~3072 tokens |
| Cursor + 无 thinking | 4096 | 4096 | 4096 tokens |

### 修复后

| 场景 | max_tokens (原始) | max_tokens (实际) | 输出空间 |
|------|-------------------|-------------------|----------|
| Cursor + thinking | 4096 | 48384 | 16384+ tokens |
| Cursor + 无 thinking | 4096 | 16384 | 16384 tokens |

## 日志示例

修复后的日志：

```
[ANTIGRAVITY] max_tokens 因 thinking 自动提升: 4096 -> 48384 (thinking_budget_estimate=32000, MIN_OUTPUT_TOKENS=16384)
```

或：

```
[ANTIGRAVITY] max_tokens 自动提升: 4096 -> 16384 (MIN_MAX_TOKENS=16384)
```

## 备份文件

- `antigravity_router.py.bak.20260108_072927`

## 补丁脚本

- `patch_openai_max_tokens.py` - OpenAI 格式路由 max_tokens 保护补丁

## 完整修复清单

| 修复 | 文件 | 补丁脚本 |
|------|------|----------|
| Anthropic 格式基础保护 | `antigravity_anthropic_router.py` | `patch_max_tokens.py` |
| Anthropic 格式 thinking 保护 | `antigravity_anthropic_router.py` | `patch_thinking_budget.py` |
| OpenAI 格式完整保护 | `antigravity_router.py` | `patch_openai_max_tokens.py` |

## 遵循原则

- **KISS**: 简单的阈值检查
- **DRY**: 与 Anthropic 格式使用相同的保护逻辑
- **用户体验优先**: 自动调整，无需用户手动配置

## 关联问题

1. **MAX_TOKENS 导致输出截断** - ✅ 已修复
2. **工具调用时卡住** - ✅ 应该已修复（因为有足够的 token 空间）
3. **Cursor 不写文件直接在 chat 输出** - 可能因 MAX_TOKENS 导致工具调用不完整

## 后续建议

1. **重启服务**：应用补丁后需要重启 gcli2api 服务
2. **观察日志**：检查 max_tokens 自动提升是否正常工作
3. **监控 MAX_TOKENS**：观察修复后是否还有 MAX_TOKENS 截断
