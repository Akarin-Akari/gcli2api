# 2026-01-17 IDE 工具调用与 Claude 协议差异深度分析报告

## 执行摘要

浮浮酱通过深度分析，揭示了**为什么第三方 API 无法完美兼容 Cursor/Augment 等 IDE** 的根本原因喵～ (๑•̀ㅂ•́)و✧

### 核心发现

1. **Cursor 已完美支持** - gcli2api 已实现完整的 Claude 原生 SSE 格式，Cursor 的问题是**签名恢复**而非格式
2. **Augment 需要协议扩展** - NDJSON 格式缺少 THINKING 节点类型，导致 thinking 被当文本输出
3. **工具调用的固有限制** - OpenAI 格式的工具调用必须累积完成后才能输出（协议设计决定）

---

## 一、gcli2api 当前实现状态

### 1.1 格式支持矩阵

| IDE/客户端 | 期望格式 | gcli2api 实现状态 | 问题 |
|------------|----------|-------------------|------|
| **Cursor** | Claude 原生 SSE | ✅ **已完整实现** | 签名恢复问题（非格式问题） |
| **Claude Code** | Claude 原生 SSE | ✅ **已完整实现** | 无 |
| **Augment** | NDJSON | ⚠️ 基础实现，缺少 THINKING 类型 | thinking 被当文本输出 |
| **通用 OpenAI 客户端** | OpenAI SSE | ✅ 已实现 | 无 thinking 支持 |

### 1.2 关键代码位置

| 格式 | 文件 | 端点 | 状态 |
|------|------|------|------|
| Claude 原生 SSE | `anthropic_streaming.py` | `/antigravity/v1/messages` | ✅ 完整 |
| NDJSON | `augment_compat/` + `unified_gateway_router.py` | `/gateway/chat-stream` | ⚠️ 需扩展 |
| OpenAI SSE | `antigravity_router.py` | `/v1/chat/completions` | ✅ 完整 |

---

## 二、三种流式格式对比

### 2.1 Claude 原生 SSE 格式（Cursor/Claude Code 使用）

```
event: message_start
data: {"type": "message_start", "message": {...}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me think..."}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "ErUB..."}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: content_block_start
data: {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_xxx", "name": "read_file", "input": {}}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"path\": \"/tmp/a.txt\"}"}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 1}

event: message_stop
data: {"type": "message_stop"}
```

**特点**：
- 使用 **content blocks** 架构
- `thinking`、`text`、`tool_use` 是独立的 content block 类型
- 每个 block 有完整的生命周期（start → delta → stop）
- `signature` 用于多轮对话验证
- **gcli2api 已完整实现此格式**

### 2.2 Augment NDJSON 格式

```json
{"type": 0, "data": {"text": "Hello ", "delta": true}}
{"type": 0, "data": {"text": "World!", "delta": true}}
{"type": 5, "data": {"tool_use": {"id": "toolu_xxx", "name": "read_file", "input": {"path": "/tmp/a.txt"}}}}
{"type": 3, "stop_reason": "tool_use"}
```

**特点**：
- 每行一个 JSON 对象（Newline Delimited JSON）
- `type=0`: RAW_RESPONSE（文本）
- `type=5`: TOOL_USE（工具调用）
- `type=1`: TOOL_RESULT（工具结果）
- `type=3`: MAIN_TEXT_FINISHED（停止）
- **❌ 没有定义 THINKING 类型** ← 这是 Augment 问题的根源

### 2.3 OpenAI 兼容 SSE 格式

```
data: {"id": "chatcmpl-xxx", "choices": [{"delta": {"content": "Hello"}, "index": 0}]}
data: {"id": "chatcmpl-xxx", "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_xxx", "function": {"name": "read_file", "arguments": ""}}]}, "index": 0}]}
data: [DONE]
```

**特点**：
- 扁平的 `delta.content` 和 `delta.tool_calls`
- **没有 `thinking` 类型**
- 工具调用参数是**增量累积**的

---

## 三、Cursor 问题分析（已解决格式问题）

### 3.1 Cursor 的格式已正确支持

Cursor 请求 `/antigravity/v1/messages` 端点，gcli2api 返回 Claude 原生 SSE 格式：

```
Cursor 请求流程：
/antigravity/v1/messages
    → antigravity_anthropic_router.py
    → antigravity_sse_to_anthropic_sse()
    → Claude 原生 SSE 格式 ✅
```

**流式吐字正常 = 格式转换正确**

### 3.2 Cursor 400 错误的真正原因

Cursor 调用工具后返回 `400 Invalid signature in thinking block` 错误，这是**签名问题**，不是格式问题：

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 签名丢失 | Cursor 过滤了 thinking 块中的 signature | 增强签名恢复策略 |
| 签名不匹配 | 恢复的签名与 thinking 内容不对应 | 三层缓存架构 |
| 工具循环断裂 | thinking 块被过滤后无法继续 | Tool Loop Recovery |

**详见**：`docs/2026-01-17_三层签名缓存架构升级规划.md`

---

## 四、Augment 问题分析（需要协议扩展）

### 4.1 问题根因

Augment 的 thinking 被当文本输出，原因是 **NDJSON 协议没有定义 THINKING 节点类型**。

**问题链路**：

1. 上游返回 thinking 内容
2. `stream_openai_with_nodes_bridge()` 只处理 `delta.content` 和 `delta.tool_calls`
3. thinking 内容被放在 `delta.content` 中
4. 被当作普通文本输出到 `{"type": 0, "data": {"text": "..."}}`
5. 插件端没有 thinking 的特殊处理逻辑

**代码证据**（`unified_gateway_router.py:1522-1526`）：

```python
# Text streaming
delta = choice0.get("delta") or {}
content = delta.get("content")
if isinstance(content, str) and content:
    yield json.dumps({"text": content}, ensure_ascii=False, separators=(",", ":")) + "\n"
```

**没有处理 thinking 的逻辑！**

### 4.2 工具调用无法流式的原因

**问题链路**：

1. OpenAI 格式的工具调用参数是**增量发送**的
2. 例如：`{"arguments": "{\"path\":"}` → `{"arguments": " \"/tmp/a.txt\"}"}`
3. 必须累积完整的 JSON 才能解析
4. 因此必须等待 `finish_reason: tool_calls` 后才能输出

**代码证据**（`unified_gateway_router.py:1557-1602`）：

```python
if saw_tool_calls and tool_calls_by_index:
    nodes: List[Dict[str, Any]] = []
    # ... 累积所有工具调用后一次性输出
    if nodes:
        yield json.dumps({"text": "", "nodes": nodes, "stop_reason": "tool_use"}, ...) + "\n"
```

**这是协议设计的限制，不是实现的 bug！**

---

## 五、Augment 解决方案建议

### 5.1 方案 A：插件端检测（最快实现）

**在自研 Augment 插件中添加 thinking 内容检测**：

```typescript
// 插件端伪代码
function handleNDJSONLine(line: string) {
    const node = JSON.parse(line);

    if (node.type === 0) {
        const text = node.data?.text || "";

        // 检测 thinking 内容（通过特殊标记）
        if (text.startsWith("<think>") || text.includes("[Thinking]")) {
            displayThinking(text);
        } else {
            displayText(text);
        }
    }
}
```

**优点**：无需修改后端，立即可用
**缺点**：依赖文本特征检测，不够优雅

### 5.2 方案 B：扩展 NDJSON 协议（推荐）

**在 gcli2api 中添加 THINKING 节点类型**：

**Step 1**: 修改 `augment_compat/types.py`

```python
class ChatResultNodeType:
    RAW_RESPONSE = 0
    TOOL_RESULT = 1
    MAIN_TEXT_FINISHED = 3
    TOOL_USE = 5
    THINKING = 6  # 新增！
```

**Step 2**: 修改 `augment_compat/ndjson.py`

```python
def create_thinking_node(thinking: str, signature: Optional[str] = None) -> str:
    """创建 THINKING 节点"""
    node = {
        "type": ChatResultNodeType.THINKING,
        "data": {"thinking": thinking}
    }
    if signature:
        node["data"]["signature"] = signature
    return ndjson_encode_line(node)
```

**Step 3**: 修改 `unified_gateway_router.py` 的 `stream_openai_with_nodes_bridge()`

```python
# 在处理 delta.content 之前，检测 thinking 内容
if is_thinking_content(content):
    yield create_thinking_node(content, signature=current_signature)
else:
    yield json.dumps({"text": content}, ...) + "\n"
```

**Step 4**: 修改插件端处理

```typescript
function handleNDJSONLine(line: string) {
    const node = JSON.parse(line);

    switch (node.type) {
        case 0:  // RAW_RESPONSE
            displayText(node.data.text);
            break;
        case 5:  // TOOL_USE
            displayToolCall(node.data.tool_use);
            break;
        case 6:  // THINKING（新增）
            displayThinking(node.data.thinking);
            break;
    }
}
```

**优点**：协议清晰，插件端处理简单
**缺点**：需要同时修改后端和插件

### 5.3 方案 C：完全模拟 Claude 原生格式（最彻底）

**让 Augment 插件直接使用 Claude 原生 SSE 格式**：

1. 修改插件，请求 `/antigravity/v1/messages` 端点
2. 使用 Claude 官方 SDK 解析响应
3. 无需任何后端修改

**优点**：复用已有的完整实现，thinking + signature 完美支持
**缺点**：需要大幅修改插件端

---

## 六、开发成本对比

### 6.1 Augment 各方案成本

| 方案 | 后端改动 | 插件改动 | 开发时间 | 推荐度 |
|------|----------|----------|----------|--------|
| **A: 插件端检测** | 无 | 小 | 0.5 天 | ⭐⭐⭐ |
| **B: 扩展 NDJSON** | 中等 | 中等 | 2-3 天 | ⭐⭐⭐⭐⭐ |
| **C: 使用 Claude SSE** | 无 | 大 | 3-5 天 | ⭐⭐⭐⭐ |

### 6.2 推荐实施路径

```
Phase 1（立即）: 方案 A - 插件端检测
    ↓ 验证可行性
Phase 2（短期）: 方案 B - 扩展 NDJSON 协议
    ↓ 如果需要更完整的支持
Phase 3（长期）: 方案 C - 迁移到 Claude SSE 格式
```

---

## 七、关于工具调用流式的技术限制

### 7.1 为什么 Claude 原生格式可以流式工具调用？

Claude 的 `tool_use` content block 设计：

```
event: content_block_start
data: {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "xxx", "name": "read_file", "input": {}}}

event: content_block_delta
data: {"delta": {"type": "input_json_delta", "partial_json": "{\"path\":"}}

event: content_block_delta
data: {"delta": {"type": "input_json_delta", "partial_json": " \"/tmp/a.txt\"}"}}

event: content_block_stop
```

- 工具名称在 `content_block_start` 时就已知
- 参数是增量的，但可以边接收边显示
- 客户端可以提前显示"正在调用 read_file..."

### 7.2 为什么 NDJSON/OpenAI 格式无法做到？

- 工具名称和参数分开发送
- 参数是 JSON 字符串的片段
- 必须累积完整才能解析

### 7.3 结论

**工具调用的流式限制是协议设计决定的**：
- 如果使用 OpenAI/NDJSON 格式 → 无法真正流式
- 如果使用 Claude 原生格式 → 可以流式显示工具名称和进度

---

## 八、总结

### 8.1 各 IDE 问题与解决方案

| IDE | 问题 | 根因 | 解决方案 | 优先级 |
|-----|------|------|----------|--------|
| **Cursor** | 工具调用后 400 错误 | 签名恢复失败 | 三层缓存 + Tool Loop Recovery | P0 |
| **Augment** | thinking 被当文本 | NDJSON 无 THINKING 类型 | 扩展协议 + 插件适配 | P1 |
| **Augment** | 工具调用非流式 | 协议设计限制 | 迁移到 Claude SSE（可选） | P2 |

### 8.2 核心结论

1. **Cursor 的格式已正确** - 问题在签名恢复，参见三层缓存架构规划
2. **Augment 需要协议扩展** - 添加 THINKING 节点类型是最佳方案
3. **工具调用流式是协议限制** - 除非迁移到 Claude 原生格式，否则无法解决

---

**分析时间**：2026-01-17 05:30（修订：06:15）
**分析人员**：浮浮酱 (Claude Opus 4.5)
**分析方法**：Sequential Thinking + acemcp 源码扫描

喵～这次浮浮酱修正了之前的乌龙，希望这份报告能帮助主人理解问题的本质呢！(๑•̀ㅂ•́)و✧
