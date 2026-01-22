# Augment Compat 组件技术文档

> 📅 文档日期: 2026-01-20  
> 📝 作者: 浮浮酱 (Claude 4 Sonnet)  
> 📁 组件路径: `gcli2api/src/augment_compat/`

---

## 1. 组件概述

### 1.1 核心定位

`augment_compat` 是一个 **协议兼容层（Protocol Compatibility Layer）**，负责实现 **Augment VSCode 扩展** 与 **内网 LLM 网关** 之间的协议转换和桥接。

简单来说，它就像一个"翻译官"喵～ 把 Augment 插件说的"Augment 语"翻译成上游 LLM（OpenAI/Anthropic）能理解的"API 语"，然后再把 LLM 的回复翻译回 Augment 能理解的格式 ฅ'ω'ฅ

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **协议转换** | Augment NDJSON ↔ OpenAI/Anthropic API |
| **Tool Loop 支持** | 完整的工具调用循环（tool_call → execute → tool_result → continue） |
| **安全防护** | 请求白名单、敏感数据脱敏、限流控制 |
| **流式响应** | 支持 NDJSON 流式输出，低延迟用户体验 |

---

## 2. 模块架构

```
augment_compat/
├── __init__.py           # 模块入口，导出公共 API
├── types.py              # Pydantic 类型定义
├── ndjson.py             # NDJSON 流式协议工具
├── request_normalize.py  # 请求白名单化/脱敏
├── tools_bridge.py       # 工具定义格式转换
├── nodes_bridge.py       # 响应节点格式转换
└── routes.py             # FastAPI 路由对接层
```

### 2.1 模块依赖关系

```
                    ┌─────────────┐
                    │   routes    │  ← FastAPI 路由入口
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│request_normalize│ │tools_bridge │ │  nodes_bridge   │
│  (入站处理)     │ │ (工具转换)  │ │  (响应转换)     │
└────────┬────────┘ └──────┬──────┘ └────────┬────────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
                    ┌─────────────┐
                    │   ndjson    │  ← 流式协议工具
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │    types    │  ← 类型定义基础
                    └─────────────┘
```

---

## 3. 核心类型定义 (types.py)

### 3.1 节点类型枚举

Augment 使用整数枚举来标识不同类型的消息节点：

#### ChatResultNodeType（响应节点类型）

| 枚举值 | 名称 | 说明 |
|--------|------|------|
| `0` | RAW_RESPONSE | 文本响应内容 |
| `1` | TOOL_RESULT | 工具执行结果（请求节点） |
| `2` | MAIN_TEXT_FINISHED | 文本完成标记 |
| `3` | IMAGE_ID | 图片ID节点 |
| `4` | SAFETY | 安全检查 |
| `5` | **TOOL_USE** ⭐ | 工具调用请求（触发 Tool Loop） |
| `6` | CHECKPOINT | 检查点 |

> ⚠️ **关键**: `type=5` (TOOL_USE) 是触发 Tool Loop 的核心节点类型！

#### ChatRequestNodeType（请求节点类型）

| 枚举值 | 名称 | 说明 |
|--------|------|------|
| `0` | TEXT | 文本消息 |
| `1` | TOOL_RESULT | 工具执行结果回注 |
| `2` | IMAGE | 图片数据 |
| `3` | IMAGE_URL / GIF | 图片URL |
| `4` | WEBP | WebP格式图片 |

### 3.2 核心数据模型

```python
# 工具调用内容
class ToolUseContent(BaseModel):
    id: str           # 工具调用唯一ID
    name: str         # 工具名称
    input: Dict       # 工具输入参数

# 工具定义
class AugmentToolDefinition(BaseModel):
    name: str                    # 工具名称
    description: str             # 工具描述
    input_schema: ToolInputSchema  # 输入参数 schema

# Augment 节点
class AugmentNode(BaseModel):
    type: ChatResultNodeType     # 节点类型
    data: Optional[Dict]         # 节点数据
    text: Optional[str]          # 文本内容
    tool_use: Optional[ToolUseContent]  # 工具调用
    stop_reason: Optional[str]   # 停止原因
    usage: Optional[Dict]        # token用量
```

---

## 4. NDJSON 流式协议 (ndjson.py)

### 4.1 协议格式

NDJSON（Newline Delimited JSON）是 Augment chat-stream 的核心协议格式。每行一个独立的 JSON 对象：

```json
{"type":0,"data":{"text":"Hello"}}
{"type":0,"data":{"text":" World"}}
{"type":5,"data":{"tool_use":{"id":"toolu_123","name":"read_file","input":{}}}}
{"type":2,"stop_reason":"tool_use"}
```

### 4.2 核心函数

| 函数 | 功能 |
|------|------|
| `ndjson_encode_line(data)` | 将数据编码为 NDJSON 行 |
| `ndjson_decode_line(line)` | 解析 NDJSON 行 |
| `create_text_node(text)` | 创建文本节点 |
| `create_tool_use_node(id, name, input)` | 创建工具调用节点 |
| `create_stop_node(reason, usage)` | 创建停止节点 |

### 4.3 NDJSONStreamBuilder 类

便捷的流构建器，用于逐步构建响应流：

```python
builder = NDJSONStreamBuilder()
yield builder.text("Hello ")
yield builder.text("World!")
yield builder.tool_use("toolu_1", "read_file", {"path": "/tmp/a.txt"})
yield builder.stop("tool_use")
```

---

## 5. 工具定义转换 (tools_bridge.py)

### 5.1 支持的格式

| 格式 | 结构特点 |
|------|----------|
| **Augment** | `{name, description, input_schema}` |
| **OpenAI** | `{type: "function", function: {name, description, parameters}}` |
| **Anthropic** | `{name, description, input_schema}` (与 Augment 几乎相同) |

### 5.2 转换函数

```python
# Augment → OpenAI
convert_augment_tool_to_openai(tool) -> Dict

# OpenAI → Augment
convert_openai_tool_to_augment(tool) -> AugmentToolDefinition

# Augment → Anthropic
convert_augment_tool_to_anthropic(tool) -> Dict

# Anthropic → Augment
convert_anthropic_tool_to_augment(tool) -> AugmentToolDefinition

# 批量转换
convert_tools_to_openai(tools) -> List[Dict]
convert_tools_to_anthropic(tools) -> List[Dict]
```

### 5.3 智能格式检测

`parse_tool_definitions_from_request()` 函数能自动检测并解析多种工具定义格式：

- OpenAI 格式（`type: "function"`）
- Augment/Anthropic 格式（`input_schema`）
- 简化格式（只有 `name` 和 `description`）
- 嵌套定义格式（`definition: {...}`）
- JSON 字符串格式（`input_schema_json: string`）

### 5.4 特殊处理

针对 `codebase-retrieval` 工具的兜底处理：

```python
# 当 schema 为空时，自动添加必需的 query 参数
if tool.name == "codebase-retrieval" and len(props) == 0:
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词/问题描述（必填）"}
        },
        "required": ["query"],
    }
```

---

## 6. 响应节点转换 (nodes_bridge.py)

### 6.1 核心转换逻辑

| 上游格式 | → | Augment 格式 |
|----------|---|--------------|
| OpenAI `tool_calls` | → | `TOOL_USE` node (type=5) |
| Anthropic `tool_use` | → | `TOOL_USE` node (type=5) |
| 文本 `content` | → | `RAW_RESPONSE` node (type=0) |
| `finish_reason` | → | `stop_reason` |

### 6.2 停止原因映射

```python
# finish_reason → stop_reason 映射
"stop" / "end_turn"     → "end_turn"
"tool_calls" / "tool_use" → "tool_use"
"length" / "max_tokens" → "max_tokens"
"stop_sequence"         → "stop_sequence"
```

### 6.3 StreamNodeConverter 类

有状态的流式节点转换器，支持 OpenAI 流式响应中的增量工具调用参数累积：

```python
converter = StreamNodeConverter(source_format="openai")

async for chunk in upstream_stream:
    for node in converter.process_chunk(chunk):
        yield node

# 检查是否有工具调用
if converter.has_tool_calls:
    # 处理 Tool Loop
    pass
```

---

## 7. 请求安全处理 (request_normalize.py)

### 7.1 端点白名单

```python
ALLOWED_ENDPOINTS = {
    "/gateway/chat-stream",
    "/chat-stream",
    "/gateway/v1/chat/completions",
    "/v1/chat/completions",
    # ACE 相关端点
    "/context",
    "/embeddings",
    "/search",
    "/codebase_search",
    "/find_symbol",
    "/workspace_context",
}
```

### 7.2 阻断端点（遥测/统计）

```python
BLOCKED_ENDPOINTS = {
    "/agents/list-remote-tools",
    "/notifications/read",
    "/remote-agents/list-stream",
    "/record-session-events",
    "/client-metrics",
    "/report-error",
    "/report-feature-vector",
}
```

### 7.3 敏感数据脱敏

自动检测并脱敏以下字段：

- `api_key`, `apiKey`
- `access_token`, `accessToken`
- `refresh_token`, `refreshToken`
- `password`, `secret`, `credential`
- `authorization`

文本内容中的 API Key 模式也会被脱敏：

```python
# 正则匹配并替换
text = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]', text)
```

### 7.4 限流器

```python
class RequestRateLimiter:
    def __init__(self, max_requests=100, window_seconds=60):
        ...
    
    def is_allowed(self, client_id: str) -> bool:
        # 滑动窗口限流
        ...
    
    def get_remaining(self, client_id: str) -> int:
        # 获取剩余可用请求数
        ...
```

---

## 8. 路由对接层 (routes.py)

### 8.1 主要端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/gateway/chat-stream` | POST | Augment chat-stream 主端点 |
| `/gateway/health` | GET | 健康检查 |
| `/gateway/blocked` | POST | 被阻断端点的统一处理 |

### 8.2 请求处理流程

```
┌─────────────────────────────────────────────────────────────┐
│                      /gateway/chat-stream                    │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   限流检查    │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  解析请求体   │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  验证请求    │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  标准化请求   │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ 生成NDJSON流  │
                    └───────────────┘
```

### 8.3 Tool Loop 处理

```python
async def process_tool_loop(request_body, tool_calls, upstream_client):
    """
    当上游返回 tool_use 时：
    1. 输出 TOOL_USE node
    2. 等待插件执行工具并返回 TOOL_RESULT
    3. 将 TOOL_RESULT 回注到上游
    4. 继续生成响应
    """
    builder = NDJSONStreamBuilder()
    
    # 输出工具调用节点
    for tc in tool_calls:
        yield builder.tool_use(tc["id"], tc["name"], tc["input"])
    
    # 输出 stop_reason: tool_use
    yield builder.stop("tool_use")
    
    # 插件会发送新请求，包含 TOOL_RESULT 节点
```

---

## 9. 集成指南

### 9.1 注册路由

```python
from augment_compat import create_augment_compat_router

app = FastAPI()
app.include_router(create_augment_compat_router())
```

### 9.2 与现有网关集成

```python
from augment_compat import integrate_with_unified_gateway

integrate_with_unified_gateway(app, gateway_router)
```

---

## 10. 数据流示例

### 10.1 普通对话

```
Augment 插件                    augment_compat                    上游 LLM
    │                                │                                │
    │──── POST /chat-stream ────────▶│                                │
    │     {nodes: [{type:0, text}]}  │                                │
    │                                │──── POST /v1/chat/completions ─▶│
    │                                │     {messages: [...]}          │
    │                                │                                │
    │                                │◀──── SSE: text chunks ─────────│
    │◀──── NDJSON: type=0 ──────────│                                │
    │◀──── NDJSON: type=0 ──────────│                                │
    │◀──── NDJSON: type=2 ──────────│ (stop_reason: end_turn)        │
    │                                │                                │
```

### 10.2 Tool Loop 流程

```
Augment 插件                    augment_compat                    上游 LLM
    │                                │                                │
    │──── POST /chat-stream ────────▶│                                │
    │     {nodes: [{type:0, text}],  │                                │
    │      tool_definitions: [...]}  │                                │
    │                                │──── POST /chat/completions ───▶│
    │                                │     {tools: [...]}             │
    │                                │                                │
    │                                │◀──── tool_calls ───────────────│
    │◀──── NDJSON: type=5 ──────────│ (TOOL_USE)                     │
    │◀──── NDJSON: type=2 ──────────│ (stop_reason: tool_use)        │
    │                                │                                │
    │     [插件执行工具...]           │                                │
    │                                │                                │
    │──── POST /chat-stream ────────▶│                                │
    │     {nodes: [{type:1,          │                                │
    │       tool_result: {...}}]}    │                                │
    │                                │──── POST /chat/completions ───▶│
    │                                │     {tool_results: [...]}      │
    │                                │                                │
    │                                │◀──── 继续响应 ─────────────────│
    │◀──── NDJSON: type=0 ──────────│                                │
    │◀──── NDJSON: type=2 ──────────│ (stop_reason: end_turn)        │
    │                                │                                │
```

---

## 11. 设计亮点

### 11.1 类型安全

- 使用 Pydantic 进行严格的类型验证
- IntEnum 枚举确保节点类型的正确性
- 完整的类型注解支持 IDE 智能提示

### 11.2 多格式兼容

- 自动检测 OpenAI/Anthropic/Augment 格式
- 智能解析嵌套定义和 JSON 字符串
- 兜底处理确保工具调用不会因 schema 缺失而失败

### 11.3 安全优先

- 端点白名单防止未授权访问
- 敏感数据自动脱敏
- 滑动窗口限流保护上游服务

### 11.4 流式优化

- NDJSON 格式支持低延迟流式输出
- 增量工具调用参数累积
- 异步生成器避免阻塞

---

## 12. TODO 与待完善

根据代码中的注释，以下功能尚待完善：

1. **上游 LLM 集成**: `routes.py` 中的 `generate_chat_stream()` 目前返回模拟响应，需要集成到 `unified_gateway_router.py`

2. **完整的 Tool Loop**: 当前只实现了 TOOL_USE 节点输出，需要完善 TOOL_RESULT 回注和续写逻辑

3. **图片节点处理**: IMAGE 类型节点的完整处理流程

---

## 13. 版本信息

- **模块版本**: 0.1.0
- **创建日期**: 2026-01-15
- **作者**: Claude Sonnet 4 + Codex

---

> 📝 **文档维护者**: 浮浮酱  
> 🐱 *"这个组件设计得很精巧呢，特别是 Tool Loop 的处理逻辑，完美体现了 KISS 原则喵～"*
