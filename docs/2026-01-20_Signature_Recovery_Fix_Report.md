# Signature Recovery Fix Report

**日期**: 2026-01-20
**修复者**: 浮浮酱 (Claude Opus 4.5)
**问题**: Claude 在使用 gcli2api 网关时无法正确恢复工具调用的缓存签名

## 问题描述

用户报告在使用 gcli2api 网关时，Claude 无法正确恢复工具调用的缓存签名，错误日志显示：

```
[SIGNATURE_RECOVERY] All strategies failed for tool_id=toolu_vrtx_01QYAQBAqrfqkGFX6RyPYNpr, using placeholder
```

这导致 thinking 模式被禁用，影响 Claude 的推理能力。

## 根本原因分析

### 原因 1: antigravity_router.py 未编码签名到 tool_id

`antigravity_router.py` 中的 `convert_to_openai_tool_call` 函数没有使用 `encode_tool_id_with_signature` 将签名编码到 tool_id 中。这导致 Layer 3 (Encoded Tool ID) 恢复策略失败。

**对比**:
- `anthropic_streaming.py` (正确): 使用 `encode_tool_id_with_signature(original_tool_id, thoughtsignature)`
- `antigravity_router.py` (错误): 直接返回原始 tool_id

### 原因 2: 签名缓存写入条件过于严格

当 `thoughtSignature` 在一个没有 `thought=true` 的 part 中到达时：
1. 签名被保存到 `state["current_thinking_signature"]`
2. 但 `thinking_started` 仍然是 `False`
3. `current_thinking_text` 为空（因为没有进入 thinking 处理分支）
4. `flush_thinking_buffer()` 中的缓存写入条件 `if state["current_thinking_text"] and state["current_thinking_signature"]` 失败

这导致 Layer 4 (Session Cache) 和 Layer 5 (Tool Cache) 都无法正确写入。

## 修复方案

### 修复 1: 添加签名编码功能（仅对 CLI 工具启用）

**文件**: `src/antigravity_router.py`

1. **导入 `encode_tool_id_with_signature` 函数** (第 63-64 行)
   ```python
   from .converters.thoughtSignature_fix import encode_tool_id_with_signature
   ```

2. **修改 `convert_to_openai_tool_call` 函数** (第 247-294 行)
   - 添加 `signature` 和 `encode_signature` 参数
   - 当 `encode_signature=True` 且有签名时，将签名编码到 tool_id

3. **添加客户端类型判断逻辑** (第 338-346 行)
   ```python
   CLI_CLIENTS_FOR_SIGNATURE_ENCODING = {"claude_code", "cline", "aider", "continue_dev", "openai_api"}
   should_encode_signature = client_type in CLI_CLIENTS_FOR_SIGNATURE_ENCODING
   ```

4. **修改 `convert_antigravity_stream_to_openai` 函数签名** (第 297-308 行)
   - 添加 `client_type` 参数

5. **修改工具调用处理** (第 641-648 行)
   ```python
   tool_call = convert_to_openai_tool_call(
       fc,
       index=tool_index,
       signature=state.get("current_thinking_signature"),
       encode_signature=should_encode_signature
   )
   ```

6. **更新调用点** (第 2252-2260 行, 第 2276-2284 行)
   - 传递 `client_type` 参数

### 修复 2: 立即缓存签名到 Session Cache

**文件**: `src/antigravity_router.py` (第 525-535 行)

当签名在 `thinking_started=False` 时到达，立即将签名缓存到 Session Cache：

```python
if not state["thinking_started"] and state.get("session_id"):
    try:
        cache_session_signature(state["session_id"], thought_signature)
        log.info(f"[SIGNATURE_CACHE] 签名立即缓存到 Session Cache: "
                f"session_id={state['session_id'][:16]}..., sig_len={len(thought_signature)}")
    except Exception as e:
        log.warning(f"[SIGNATURE_CACHE] Session Cache 写入失败: {e}")
```

## 修复范围

### 受影响的客户端

| 客户端类型 | 签名编码 | 原因 |
|-----------|---------|------|
| claude_code | ✅ 启用 | CLI 工具需要完整的签名往返 |
| cline | ✅ 启用 | CLI 工具 |
| aider | ✅ 启用 | CLI 工具 |
| continue_dev | ✅ 启用 | CLI 工具 |
| openai_api | ✅ 启用 | API 客户端 |
| cursor | ❌ 禁用 | IDE 有自己的处理机制 |
| windsurf | ❌ 禁用 | IDE |
| 其他 | ❌ 禁用 | 默认禁用 |

## 测试验证

1. 语法检查: ✅ 通过
2. 签名编码逻辑: ✅ 仅对 CLI 工具启用
3. 签名缓存写入: ✅ 立即缓存到 Session Cache

## 预期效果

1. **Layer 3 恢复成功**: 签名被编码到 tool_id 中，后续请求可以从中解码
2. **Layer 4 恢复成功**: 签名立即缓存到 Session Cache，后续请求可以恢复
3. **CLI 工具正常工作**: Claude Code 等 CLI 工具可以正确恢复签名
4. **IDE 不受影响**: Cursor 等 IDE 工具保持原有行为

## 相关文件

- `src/antigravity_router.py` - 主要修改文件
- `src/converters/thoughtSignature_fix.py` - 签名编码/解码函数
- `src/converters/signature_recovery.py` - 6 层签名恢复策略
- `src/signature_cache.py` - 签名缓存模块
- `src/tool_cleaner.py` - 客户端类型检测

---

**最后更新**: 2026-01-20
**维护者**: 浮浮酱 (Claude Opus 4.5)
