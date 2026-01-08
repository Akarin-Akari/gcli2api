# Cursor 与 Thinking 模型兼容性研究报告

**日期**: 2026-01-07
**研究人**: Claude Opus 4.5 (浮浮酱)
**研究目标**: 分析 Cursor IDE 与第三方 API 提供的 Thinking 模型的兼容性问题

---

## 研究背景

用户提出问题：
> "Cursor 本身是支持第三方 API 的，那第三方 API 如果提供的全是带 thinking 的推理模型该怎么办？难道全部不支持吗？之前我们通过禁用 thinking 功能让 Cursor 勉强能用，但我还是想不通这个问题。"

---

## 研究发现

### 1. Claude Extended Thinking API 规范

根据 Anthropic 官方文档，Extended Thinking 功能的 API 规范如下：

#### 请求参数

```json
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 16000,
  "thinking": {
    "type": "enabled",
    "budget_tokens": 10000
  },
  "messages": [...]
}
```

#### 响应格式

```json
{
  "content": [
    {
      "type": "thinking",
      "thinking": "Let me analyze this step by step...",
      "signature": "WaUjzkypQ2mUEVM36O2TxuC06KN8xyfbJwyem2dw3URve/op91XWHOEBLLqIOMfFG..."
    },
    {
      "type": "text",
      "text": "Based on my analysis..."
    }
  ]
}
```

#### 关键约束

| 约束 | 说明 |
|------|------|
| **Signature 必需** | 每个 thinking block 必须包含 `signature` 字段 |
| **历史消息约束** | 启用 thinking 时，最后一条 assistant 消息的第一个 block 必须是 `thinking` 或 `redacted_thinking` |
| **Budget 约束** | `budget_tokens` 必须小于 `max_tokens` |
| **Tool Use 约束** | 只支持 `tool_choice: auto` 或 `none` |
| **状态切换约束** | 不能在 tool use 循环中途切换 thinking 状态 |

#### 流式事件

- `thinking_delta`: 增量 thinking 内容
- `signature_delta`: 增量 signature 内容
- `text_delta`: 增量文本内容

### 2. 模型差异

| 模型 | Thinking 输出 |
|------|---------------|
| Claude 4 系列 | 返回**摘要版 thinking**（非原始输出） |
| Claude Sonnet 3.7 | 返回**完整 thinking 输出** |
| Gemini 2.5 Pro | 最近改为返回**摘要版**（类似 OpenAI） |

### 3. Cursor 论坛讨论

根据 Cursor 社区讨论：
- Google Gemini 2.5 Pro 从原始 thinking 改为"summaries"
- 这与 OpenAI 的 thinking 模型行为类似
- Cursor 可能与 Google 有特殊协议获取 thinking 访问权限

---

## gcli2api 现有处理逻辑分析

### 核心文件

#### `src/anthropic_converter.py`

```python
def _should_strip_thinking_blocks(payload: Dict[str, Any]) -> bool:
    """
    判断是否应该在请求转换时清理 thinking blocks。
    清理条件（满足任一即清理）：
    1. thinking 被显式禁用（type: disabled 或 thinking: false）
    2. thinking=null（不下发 thinkingConfig，下游视为禁用）
    3. 没有 thinking 字段（不下发 thinkingConfig，下游视为禁用）
    4. thinking 启用但历史消息不满足约束（不下发 thinkingConfig，下游视为禁用）

    核心原则：只要不会下发 thinkingConfig，就应该清理 thinking blocks，
    避免下游报错 "When thinking is disabled, an assistant message cannot contain thinking"
    """
```

```python
def _strip_thinking_blocks_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从消息列表中移除所有 thinking/redacted_thinking blocks。
    当 thinking 被禁用时，历史消息中的 thinking blocks 会导致 400 错误：
    "When thinking is disabled, an `assistant` message..."
    """
```

#### `src/anthropic_streaming.py`

```python
def open_thinking_block(self, signature: Optional[str]) -> bytes:
    """打开 thinking block，包含 signature"""
    idx = self._next_index()
    self._current_block_type = "thinking"
    self._current_thinking_signature = signature
    block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
    if signature:
        block["signature"] = signature
    # ...
```

处理 `thinking_delta` 和 `signature_delta` 事件：
```python
if part.get("thought") is True:
    # 处理 thinking block
    thinking_text = part.get("text", "")
    evt = _sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": state._current_block_index,
            "delta": {"type": "thinking_delta", "thinking": thinking_text},
        },
    )
```

#### `src/context_analyzer.py`

```python
def has_valid_thinking_in_messages(messages: List[Any]) -> bool:
    """
    检查历史消息中是否有有效的 thinking block（包含 signature）

    如果 thinking 启用但历史消息没有有效的 thinking block，则应该禁用 thinking
    这可以避免 400 错误："thinking.signature: Field required"
    """
```

```python
def should_disable_thinking(
    enable_thinking: bool,
    messages: List[Any],
) -> Tuple[bool, str]:
    """
    判断是否应该禁用 thinking 模式
    """
    if not enable_thinking:
        return False, ""

    if has_valid_thinking_in_messages(messages):
        return False, ""

    reason = "Thinking enabled but no valid thinking block (with signature) found in history messages"
    return True, reason
```

---

## 问题根因分析

### 为什么禁用 thinking 才能让 Cursor 工作？

#### 原因 1: Cursor 不完整支持 thinking 协议

Cursor 在处理 API 响应时：
- 可能不会在后续请求中回传 thinking blocks
- 可能不会保留 signature 字段
- 导致 API 报错："thinking.signature: Field required"

#### 原因 2: 历史消息约束冲突

```
启用 thinking 时的约束：
┌─────────────────────────────────────────────────────────┐
│ 最后一条 assistant 消息的第一个 block 必须是：          │
│   - thinking                                            │
│   - redacted_thinking                                   │
│                                                         │
│ 如果 Cursor 没有正确保留 thinking blocks：              │
│   → 触发 400 错误                                       │
└─────────────────────────────────────────────────────────┘
```

#### 原因 3: Tool Use 与 Thinking 的复杂交互

```
Cursor Agent 模式：
┌──────────────────────────────────────────────────────────┐
│ 用户请求 → 模型响应 → 工具调用 → 工具结果 → 模型响应 ...│
│                                                          │
│ 约束：不能在 tool use 循环中途切换 thinking 状态         │
│                                                          │
│ 问题：Cursor 频繁调用工具，容易触发约束冲突              │
└──────────────────────────────────────────────────────────┘
```

---

## 解决方案

### 方案 A: 智能 Thinking 降级（当前 gcli2api 已实现）

**处理流程**：

```
请求进入
    ↓
检查 thinking 参数
    ↓
检查历史消息是否满足约束
    ├── 满足 → 正常启用 thinking，传递 thinkingConfig
    └── 不满足 → 清理历史中的 thinking blocks，不下发 thinkingConfig
    ↓
转发请求到后端
    ↓
响应返回
    ↓
正确处理 thinking_delta/signature_delta 事件
```

**优点**：
- 透明兼容，无需 Cursor 修改
- 自动检测并处理约束冲突

**缺点**：
- 丢失 thinking 可见性
- 用户无法看到模型的推理过程

### 方案 B: Thinking 内容转换（可增强）

将 thinking blocks 转换为普通 text 内容：

```python
# 伪代码
if thinking_block:
    # 转换为普通文本
    text_block = {
        "type": "text",
        "text": f"<thinking>\n{thinking_block['thinking']}\n</thinking>"
    }
```

**优点**：
- 保留推理可见性
- 用户可以看到模型的思考过程

**缺点**：
- 无法利用 thinking 的特殊处理
- 可能增加 token 消耗

### 方案 C: 完整 Thinking 支持（需要 Cursor 配合）

需要 Cursor 实现：
1. 正确保留和回传 thinking blocks
2. 保留 signature 字段
3. 遵守 thinking 状态切换约束

**优点**：
- 完整的 thinking 体验
- 最佳的模型性能

**缺点**：
- 依赖 Cursor 官方更新
- 短期内无法实现

---

## 结论

### 核心问题解答

**Q: 第三方 API 如果只提供 thinking 模型，Cursor 能用吗？**

**A: 可以用，但需要中间层（如 gcli2api）做兼容处理。**

关键点：
1. **Thinking 模型本身是可用的** - 只是需要正确处理 thinking blocks
2. **gcli2api 已实现智能降级** - 当检测到 Cursor 没有正确回传 thinking 历史时，自动清理并禁用
3. **代价是丢失 thinking 可见性** - 模型仍会内部思考，但 thinking 内容不会返回给用户

### 为什么禁用 thinking 能让 Cursor 工作？

因为禁用 thinking 后：
- ✅ 不需要在历史消息中保留 thinking blocks
- ✅ 不需要传递 signature
- ✅ 避免了所有 thinking 相关的约束检查
- ✅ 模型仍然会进行内部推理，只是不返回 thinking 内容

这就是为什么"禁用 thinking 功能让 Cursor 勉强能用"的原因 - 它绕过了所有复杂的 thinking 协议约束。

---

## 建议

1. **当前方案可行** - gcli2api 的智能降级机制已经解决了兼容性问题
2. **可选增强** - 如果想保留 thinking 可见性，可以将 thinking 内容转换为普通文本
3. **长期方案** - 等待 Cursor 官方支持完整的 thinking 协议

---

## 相关文件

| 文件 | 功能 |
|------|------|
| `src/anthropic_converter.py` | Anthropic 请求转换，thinking 处理核心逻辑 |
| `src/anthropic_streaming.py` | 流式响应处理，thinking_delta/signature_delta 事件 |
| `src/context_analyzer.py` | 上下文分析，thinking 有效性检测 |
| `src/antigravity_anthropic_router.py` | Anthropic 路由，请求处理入口 |

---

## 参考资料

1. [Claude Extended Thinking Documentation](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
2. [Cursor Forum - Thinking Model Discussion](https://forum.cursor.com/t/google-gemini-2-5-pro-thinking-summaries/76879)
3. gcli2api 源代码分析
