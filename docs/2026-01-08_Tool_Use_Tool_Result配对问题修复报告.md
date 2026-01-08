# 2026-01-08 Tool Use/Tool Result 配对问题修复报告

## 问题描述

Cursor 转发调用工具时出现 400 错误：

```
[ERROR] [ANTIGRAVITY] API error (400): {
  "error": {
    "code": 400,
    "message": "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"message\":\"messages.2.content.0: unexpected `tool_use_id` found in `tool_result` blocks: toolu_vrtx_018svntKvs9NVJpAev9viGTN. Each `tool_result` block must have a corresponding `tool_use` block in the previous message.\"},\"request_id\":\"req_vrtx_011CWthYGr6urNb5sWWj9wPQ\"}",
    "status": "INVALID_ARGUMENT"
  }
}
```

## 根本原因分析

Anthropic API 要求每个 `tool_result` 块必须在前一条消息中有对应的 `tool_use` 块。当以下情况发生时，会导致"孤儿" tool_result：

1. **Thinking 处理导致 tool_use 丢失**：当 thinking 功能被禁用或 thinking block 因缺少有效 signature 被过滤时，包含 tool_use 的 assistant 消息可能被清空或跳过
2. **消息顺序问题**：在某些边缘情况下，消息顺序可能被打乱
3. **上下文压缩**：在上下文压缩过程中，tool_use 消息可能被移除但 tool_result 保留

## 修复方案

### 1. 修复 `anthropic_converter.py`

**位置**: `gcli2api/src/anthropic_converter.py` 第 557-574 行

**修改内容**:
- 在处理 `tool_result` 之前，验证对应的 `tool_use` 是否存在于 `tool_use_id_to_name` 映射中
- 如果不存在，跳过该 `tool_result` 并记录警告日志

```python
elif item_type == "tool_result":
    tool_use_id = item.get("tool_use_id")

    # [FIX 2026-01-08] 验证对应的 tool_use 是否存在
    if not tool_use_id or str(tool_use_id) not in tool_use_id_to_name:
        log.warning(f"[ANTHROPIC CONVERTER] Skipping orphan tool_result: "
                   f"tool_use_id={tool_use_id} not found in tool_use_id_to_name mapping.")
        continue

    output = _extract_tool_result_output(item.get("content"))
    tool_name = tool_use_id_to_name[str(tool_use_id)]
    # ... 继续处理
```

### 2. 修复 `message_converter.py`

**位置**: `gcli2api/src/converters/message_converter.py`

**修改内容**:

#### 2.1 添加 tool_call_id 映射构建（第 228 行后）

```python
# [FIX 2026-01-08] 建立 tool_call_id -> tool_name 的映射
tool_call_id_to_name: dict = {}
for msg in messages:
    msg_tool_calls = getattr(msg, "tool_calls", None)
    if msg_tool_calls:
        for tc in msg_tool_calls:
            tc_id = getattr(tc, "id", None)
            tc_function = getattr(tc, "function", None)
            if tc_id and tc_function:
                tc_name = getattr(tc_function, "name", "")
                if tc_name:
                    tool_call_id_to_name[str(tc_id)] = tc_name
```

#### 2.2 添加 tool 消息验证（第 476 行后）

```python
# [FIX 2026-01-08] 验证对应的 tool_use 是否存在
if str(tool_call_id) not in tool_call_id_to_name:
    log.warning(f"[ANTIGRAVITY] Skipping orphan tool message: "
               f"tool_call_id={tool_call_id} not found in tool_call_id_to_name mapping.")
    continue
```

## 测试验证

### 测试用例 1：孤儿 tool_result 应被跳过

```python
messages = [
    {'role': 'user', 'content': 'Hello'},
    {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'I will use a tool'}
        # 没有 tool_use
    ]},
    {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'orphan_id_123', 'content': 'result'}
    ]}
]
```

**预期结果**: 孤儿 tool_result 被跳过，日志显示警告

**实际结果**: ✅ 通过
```
[WARNING] [ANTHROPIC CONVERTER] Skipping orphan tool_result: tool_use_id=orphan_id_123 not found in tool_use_id_to_name mapping.
```

### 测试用例 2：有效配对应被保留

```python
messages = [
    {'role': 'user', 'content': 'Hello'},
    {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'I will use a tool'},
        {'type': 'tool_use', 'id': 'valid_id_456', 'name': 'test_tool', 'input': {}}
    ]},
    {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'valid_id_456', 'content': 'result'}
    ]}
]
```

**预期结果**: tool_use 和 tool_result 都被保留

**实际结果**: ✅ 通过，functionResponse 正确生成

## 影响范围

- **Anthropic 格式请求**: 通过 `antigravity_anthropic_router.py` 处理的请求
- **OpenAI 格式请求**: 通过 `antigravity_router.py` 处理的请求（Cursor 使用此格式）

## 备份文件

- `anthropic_converter.py.bak.20260108_052501`
- `message_converter.py.bak.20260108_052914`
- `message_converter.py.bak.20260108_062629`

## 追加修复：strip_thinking_from_openai_messages 中 tool_calls 丢失问题

### 问题发现

在分析 Cursor 运行日志时发现，即使修复了孤儿 tool_result 的跳过逻辑，仍然有大量孤儿 tool_result 被跳过：

```
[WARNING] Skipping orphan tool message: tool_call_id=toolu_vrtx_01UHtvveYettYu7desL151Rn not found...
[WARNING] Skipping orphan tool message: tool_call_id=toolu_vrtx_019vJu9QL67WZhSXweUkfBjQ not found...
[WARNING] Skipping orphan tool message: tool_call_id=toolu_vrtx_01AjfwK2ZWaSyFr4R6X2JQdk not found...
[WARNING] Skipping orphan tool message: tool_call_id=toolu_vrtx_01XTC46gdBnoajrCptpqaNvd not found...
```

### 根本原因

在 `strip_thinking_from_openai_messages` 函数中，当处理 Pydantic 模型对象时，创建新的 `OpenAIChatMessage` 时**没有复制 `tool_calls` 字段**：

```python
# 问题代码（第 82-84 行）
from src.models import OpenAIChatMessage
cleaned_msg = OpenAIChatMessage(role=role, content=content)  # 没有传递 tool_calls！
```

这导致当 thinking 被禁用并重新清理消息时，assistant 消息中的 `tool_calls` 丢失，进而导致对应的 `tool_result` 变成孤儿。

### 修复方案

在创建新的 `OpenAIChatMessage` 时，保留原消息的 `tool_calls` 字段：

```python
# 修复后的代码
from src.models import OpenAIChatMessage
tool_calls = getattr(msg, "tool_calls", None)
cleaned_msg = OpenAIChatMessage(role=role, content=content, tool_calls=tool_calls)
```

### 修复位置

- `gcli2api/src/converters/message_converter.py` 第 82-84 行（Pydantic 模型 + 字符串 content）
- `gcli2api/src/converters/message_converter.py` 第 102-106 行（Pydantic 模型 + 数组 content）

### 预期效果

修复后，当 thinking 被禁用时：
1. thinking block 会被正确过滤
2. **tool_calls 会被保留**
3. 对应的 tool_result 不再变成孤儿
4. 减少不必要的警告日志

## 遵循原则

- **KISS**: 简单直接的验证逻辑，不引入复杂的状态管理
- **DRY**: 两个文件使用相同的验证模式
- **用户体验优先**: 通过跳过无效消息而非报错，保证请求能够继续处理
