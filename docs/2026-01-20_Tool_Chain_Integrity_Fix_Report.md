# Tool Chain Integrity Fix Report

**日期**: 2026-01-20
**修复者**: 浮浮酱 (Claude Opus 4.5)
**严重级别**: P0 Critical

## 问题描述

### 错误信息
```json
{
  "code": 400,
  "message": "messages.5: `tool_use` ids were found without `tool_result` blocks immediately after: toolu_vrtx_01QuNj422YNisi98Q28Z5adY"
}
```

### 问题现象
- Claude API 返回 400 错误
- 错误原因：消息中存在 `tool_use` 块，但没有对应的 `tool_result` 块
- 这会导致 Cursor IDE 中的工具调用流程中断

## 根本原因分析

### 代码路径不一致

1. **`anthropic_converter.py` 中的 `_validate_and_fix_tool_chain` 函数**（第 511-613 行）
   - ✅ 会检测并**过滤**孤儿 `tool_use`
   - 但只在 `convert_anthropic_request_to_antigravity_components` 函数中被调用

2. **`sanitizer.py` 中的 `_ensure_tool_chain_integrity` 方法**（第 435-489 行）
   - ❌ 只**检测**问题，**不修复**！
   - 注释写着："当前版本不修复,仅记录"

3. **`unified_gateway_router.py` 使用的是 `AnthropicSanitizer`**
   - 调用 `sanitizer.sanitize_messages()` 进行消息净化
   - 但 sanitizer 不会修复工具链断裂问题

### 问题流程图

```
请求进入 → unified_gateway_router.py
         ↓
    AnthropicSanitizer.sanitize_messages()
         ↓
    _ensure_tool_chain_integrity() → 只检测，不修复！❌
         ↓
    请求发送到 Claude API
         ↓
    400 错误：tool_use 没有对应的 tool_result
```

## 修复方案

### 方案 A（已实施）：在 `AnthropicSanitizer._ensure_tool_chain_integrity()` 中添加修复逻辑

**修改文件**: `src/ide_compat/sanitizer.py`

**核心变更**:
1. 使用 `decode_tool_id_and_signature` 解码 tool_id（因为 tool_id 可能包含编码的签名）
2. 收集所有 `tool_use` 和 `tool_result`，使用解码后的原始ID进行匹配
3. 找出孤儿 `tool_use`（没有对应 `tool_result` 的）
4. 过滤掉孤儿 `tool_use`，避免 Claude API 400 错误
5. 如果过滤后消息内容为空，添加占位符文本块

### 关键代码变更

```python
def _ensure_tool_chain_integrity(self, messages: List[Dict]) -> List[Dict]:
    """
    确保 tool_use/tool_result 链条完整

    [FIX 2026-01-20] 不仅检测，还要修复断裂的工具链
    """
    # 收集所有 tool_use 和 tool_result
    # 使用解码后的原始ID作为key，确保与tool_result匹配
    tool_uses = {}  # original_tool_id -> (message_index, encoded_tool_id)
    tool_results = set()  # original_tool_use_id 集合

    for msg_idx, msg in enumerate(messages):
        # ... 遍历消息收集 tool_use 和 tool_result ...
        if block_type == "tool_use":
            encoded_tool_id = block.get("id")
            if encoded_tool_id:
                # 解码 tool_id (可能包含编码的签名)
                original_id, _ = decode_tool_id_and_signature(encoded_tool_id)
                tool_uses[original_id] = (msg_idx, encoded_tool_id)

    # 找出孤儿 tool_use (没有对应 tool_result)
    orphan_tool_uses = {}
    for original_id, (msg_idx, encoded_id) in tool_uses.items():
        if original_id not in tool_results:
            orphan_tool_uses[encoded_id] = original_id

    # 过滤掉孤儿 tool_use
    # ... 过滤逻辑 ...

    return cleaned_messages
```

## 测试验证

### 测试用例 1：孤儿 tool_use
```python
messages = [
    {'role': 'user', 'content': 'Hello'},
    {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'Let me help you'},
        {'type': 'tool_use', 'id': 'toolu_vrtx_01QuNj422YNisi98Q28Z5adY', ...}
    ]},
    # 没有对应的 tool_result！
    {'role': 'user', 'content': 'Thanks'}
]
```
**结果**: ✅ `tool_use` 被过滤，`tool_chains_fixed: 1`

### 测试用例 2：正常配对
```python
messages = [
    {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'toolu_123', ...}]},
    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_123', ...}]}
]
```
**结果**: ✅ `tool_use` 保留，`tool_chains_fixed: 0`

### 测试用例 3：混合场景
```python
messages = [
    {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': 'toolu_paired', ...},
        {'type': 'tool_use', 'id': 'toolu_orphan', ...}
    ]},
    {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'toolu_paired', ...}
        # toolu_orphan 没有对应的 tool_result
    ]}
]
```
**结果**: ✅ `toolu_paired` 保留，`toolu_orphan` 被过滤，`tool_chains_fixed: 1`

## 影响范围

- **修复文件**: `src/ide_compat/sanitizer.py`
- **影响路由**: `unified_gateway_router.py` 中使用 `AnthropicSanitizer` 的所有请求
- **向后兼容**: ✅ 完全兼容，只是增加了修复逻辑

## 相关问题

此修复与之前的 "Thinking 退化" 问题研究相关：
- 当 Cursor IDE 过滤掉 thinking 块时，可能也会破坏 tool_use/tool_result 的配对
- 这会导致后续请求发送不完整的历史消息

## 后续建议

1. 监控日志中的 `[SANITIZER] 检测到孤儿 tool_use` 警告
2. 如果频繁出现，可能需要调查 Cursor IDE 的消息处理逻辑
3. 考虑在 `tool_loop_recovery.py` 中添加更智能的恢复策略

---

**修复状态**: ✅ 已完成并测试通过

---

## 补充修复 (2026-01-20 12:00)

### 问题发现

在初次修复后，问题仍然存在。经过深入分析，发现了**第二个问题点**：

### 根本原因（补充）

**请求实际走的是 Anthropic 路由（`/antigravity/v1/messages`），而不是 OpenAI 路由！**

1. **Cursor 使用的是 Anthropic API 格式**
   - 端点：`/antigravity/v1/messages`
   - 路由器：`antigravity_anthropic_router.py`

2. **`anthropic_converter.py` 中的 `reorganize_tool_messages` 函数存在问题**
   - 这个函数在 `_validate_and_fix_tool_chain` **之后**被调用
   - 它会重新组织消息，将 `functionCall` 和 `functionResponse` 配对
   - **问题**：即使没有对应的 `functionResponse`，`functionCall` 仍然会被添加到输出！

### 问题代码（修复前）

```python
# anthropic_converter.py 第 994-1002 行
if isinstance(part, dict) and "functionCall" in part:
    tool_id = (part.get("functionCall") or {}).get("id")
    new_contents.append({"role": "model", "parts": [part]})  # ❌ 无条件添加！

    if tool_id is not None and str(tool_id) in tool_results:
        new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})
    # ❌ 如果没有匹配的 tool_result，functionCall 仍然存在！
```

### 修复方案

**修改文件**: `src/anthropic_converter.py`

**核心变更**: 只有当存在对应的 `functionResponse` 时才添加 `functionCall`

```python
if isinstance(part, dict) and "functionCall" in part:
    tool_id = (part.get("functionCall") or {}).get("id")

    # [FIX 2026-01-20] 只有当存在对应的 functionResponse 时才添加 functionCall
    if tool_id is not None and str(tool_id) in tool_results:
        new_contents.append({"role": "model", "parts": [part]})
        new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})
    else:
        # 孤儿 functionCall - 跳过，不添加到输出
        log.warning(
            f"[ANTHROPIC CONVERTER] Skipping orphan functionCall in reorganize_tool_messages: "
            f"tool_id={tool_id} has no corresponding functionResponse."
        )
```

### 测试验证（补充）

#### 测试 1：孤儿 tool_use（完整转换流程）
```
Input messages: 2
[WARNING] 检测到孤儿 tool_use: 1 个
[INFO] 过滤孤儿 tool_use: toolu_vrtx_01QuNj422YNisi98Q28Z5adY
Output contents: 2
Found text at contents[0].parts[0]: Hello...
Found text at contents[1].parts[0]: I will use a tool...
SUCCESS: No orphan functionCall in output!
```

#### 测试 2：正常工具调用
```
Input messages: 3
Output contents: 4
Found functionCall at contents[2].parts[0]: id=toolu_123, name=test_tool
Found functionResponse at contents[3].parts[0]: id=toolu_123, name=test_tool
SUCCESS: Both functionCall and functionResponse present!
```

#### 测试 3：混合场景（配对 + 孤儿）
```
Input messages: 3
[WARNING] 检测到孤儿 tool_use: 1 个
[INFO] 过滤孤儿 tool_use: toolu_orphan
Output contents: 4
Found paired functionCall at contents[2].parts[0]
Found paired functionResponse at contents[3].parts[0]
SUCCESS: Paired tool preserved, orphan tool filtered!
```

### 修复文件汇总

| 文件 | 修改内容 |
|------|----------|
| `src/ide_compat/sanitizer.py` | `_ensure_tool_chain_integrity` 方法：从"只检测"改为"检测并修复" |
| `src/anthropic_converter.py` | `reorganize_tool_messages` 函数：只有当存在对应的 `functionResponse` 时才添加 `functionCall` |

### 双重保护机制

现在有两层保护：

1. **第一层**：`_validate_and_fix_tool_chain` 在 Anthropic 格式的 messages 上过滤孤儿 `tool_use`
2. **第二层**：`reorganize_tool_messages` 在转换后的 contents 上过滤孤儿 `functionCall`

这确保了即使第一层遗漏了某些情况，第二层也能捕获并处理。

---

**最终修复状态**: ✅ 已完成并测试通过（双重保护）
