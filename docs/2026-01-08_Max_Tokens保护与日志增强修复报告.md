# 2026-01-08 Max Tokens 保护与日志增强修复报告

## 问题描述

### 问题 1：MAX_TOKENS 导致输出截断

Cursor 在准备写研究报告时突然停止，API 日志显示：

```
finishReason: MAX_TOKENS
candidatesTokenCount: 4096
```

模型在生成 4096 个 token 后被强制停止，因为达到了请求中设置的 `max_tokens` 限制。

### 问题 2：缺少诊断信息

原有日志不记录 `max_tokens` 值，导致无法快速诊断此类问题。

### 问题 3：不爱写文件，直接在 chat 窗口输出

可能与以下因素相关：
- 工具调用历史被破坏（孤儿 tool_result 问题）
- MAX_TOKENS 限制导致工具调用不完整
- 模型"学习"到工具调用不可靠，转而选择直接输出文本

## 根本原因分析

1. **Cursor 的 max_tokens 设置过小**：Cursor 可能默认设置 `max_tokens=4096`，对于长文本生成（如研究报告）来说太小
2. **第三方 API 不受 Cursor "max 模式" 影响**：Cursor 的 max 模式可能只对官方 API 生效
3. **ConnectionResetError 是无害的**：Windows asyncio 清理噪音，不影响实际功能

## 修复方案

### 1. 设置最小 max_tokens 保护

**位置**: `gcli2api/src/antigravity_anthropic_router.py` 第 560-571 行

**修改内容**:
- 设置 `MIN_MAX_TOKENS = 16384`
- 当请求的 `max_tokens` 小于最小值时，自动提升

```python
# [FIX 2026-01-08] 设置最小 max_tokens 保护
# Cursor 等客户端可能设置较小的 max_tokens（如 4096），导致长文本生成被截断
# 对于需要生成长文本（如研究报告）的场景，自动提升到更合理的值
MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值
original_max_tokens = max_tokens
if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
    max_tokens = MIN_MAX_TOKENS
    payload["max_tokens"] = max_tokens
    log.info(
        f"[ANTHROPIC] max_tokens 自动提升: {original_max_tokens} -> {max_tokens} "
        f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
    )
```

### 2. 增强日志记录

**位置**: `gcli2api/src/antigravity_anthropic_router.py` 第 573-577 行

**修改内容**:
- 在请求日志中添加 `max_tokens` 和 `original_max_tokens` 信息

```python
# [FIX 2026-01-08] 增强日志记录，添加 max_tokens 信息用于诊断
log.info(
    f"[ANTHROPIC] /messages 收到请求: client={client_host}:{client_port}, model={model}, "
    f"stream={stream}, messages={len(messages)}, max_tokens={max_tokens} (original={original_max_tokens}), "
    f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
)
```

## 预期效果

1. **长文本生成不再被截断**：即使 Cursor 请求 `max_tokens=4096`，也会被自动提升到 16384
2. **诊断更方便**：日志中可以看到原始和调整后的 `max_tokens` 值
3. **减少 MAX_TOKENS 错误**：模型有更多空间完成输出

## 日志示例

修复前：
```
[ANTHROPIC] /messages 收到请求: client=127.0.0.1:7824, model=claude-opus-4-5-20251101, stream=True, messages=113, thinking_present=True, thinking={'type': 'enabled', 'budget_tokens': 31999}, ua=python-httpx/0.28.1
```

修复后：
```
[ANTHROPIC] max_tokens 自动提升: 4096 -> 16384 (MIN_MAX_TOKENS=16384)
[ANTHROPIC] /messages 收到请求: client=127.0.0.1:7824, model=claude-opus-4-5-20251101, stream=True, messages=113, max_tokens=16384 (original=4096), thinking_present=True, thinking={'type': 'enabled', 'budget_tokens': 31999}, ua=python-httpx/0.28.1
```

## 备份文件

- `antigravity_anthropic_router.py.bak.20260108_064711`

## 补丁脚本

- `patch_max_tokens.py` - 可重复执行的补丁脚本

## 关于 ConnectionResetError

日志中出现的以下错误是**无害的 Windows asyncio 清理噪音**：

```
ConnectionResetError: [WinError 10054] 远程主机强制关闭了一个现有的连接
```

这是 Windows 的 ProactorEventLoop 在客户端断开连接后产生的清理信息，不影响实际功能。

## 遵循原则

- **KISS**: 简单的阈值检查，不引入复杂逻辑
- **用户体验优先**: 自动提升 max_tokens，无需用户手动配置
- **可观测性**: 增强日志记录，方便诊断

## 后续建议

1. **观察日志**：检查是否还有大量孤儿工具警告
2. **监控 MAX_TOKENS**：观察修复后是否还有 MAX_TOKENS 截断
3. **考虑可配置化**：如果需要，可以将 `MIN_MAX_TOKENS` 改为配置项
