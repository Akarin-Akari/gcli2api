# ClientTypeDetector 使用指南

## 概述

`ClientTypeDetector` 是一个客户端类型检测器,用于根据 HTTP Headers 识别请求来源的客户端类型。

## 功能特性

- **客户端类型检测**: 支持检测 10+ 种客户端类型
- **版本号提取**: 自动提取客户端版本号
- **SCID 提取**: 提取 Server Conversation ID (会话标识)
- **净化策略判断**: 自动判断是否需要消息净化
- **Fallback 策略**: 自动判断是否启用跨池降级
- **大小写不敏感**: 支持大小写不敏感的 header 处理
- **网关转发支持**: 支持 X-Forwarded-User-Agent

## 支持的客户端类型

| 客户端类型 | 需要净化 | 跨池 Fallback | 说明 |
|-----------|---------|--------------|------|
| `CLAUDE_CODE` | ❌ | ✅ | Claude Code CLI (原生支持) |
| `CURSOR` | ✅ | ❌ | Cursor IDE |
| `AUGMENT` | ✅ | ❌ | Augment/Bugment |
| `WINDSURF` | ✅ | ❌ | Windsurf IDE |
| `CLINE` | ✅ | ✅ | Cline VSCode 扩展 |
| `CONTINUE_DEV` | ✅ | ✅ | Continue.dev |
| `AIDER` | ✅ | ✅ | Aider |
| `ZED` | ✅ | ❌ | Zed 编辑器 |
| `COPILOT` | ✅ | ❌ | GitHub Copilot |
| `OPENAI_API` | ❌ | ✅ | 标准 OpenAI API 调用 |
| `UNKNOWN` | ✅ | ❌ | 未知客户端 (保守策略) |

## 基本用法

### 1. 导入模块

```python
from src.ide_compat import ClientType, ClientInfo, ClientTypeDetector
```

### 2. 检测客户端类型

```python
# 从 FastAPI Request 对象检测
from fastapi import Request

@router.post("/api/endpoint")
async def endpoint(request: Request):
    # 提取 headers
    headers = dict(request.headers)

    # 检测客户端类型
    client_info = ClientTypeDetector.detect(headers)

    # 使用检测结果
    print(f"Client Type: {client_info.client_type.value}")
    print(f"Display Name: {client_info.display_name}")
    print(f"Version: {client_info.version}")
    print(f"Needs Sanitization: {client_info.needs_sanitization}")
    print(f"Enable Fallback: {client_info.enable_cross_pool_fallback}")
    print(f"SCID: {client_info.scid}")
```

### 3. 根据客户端类型执行不同逻辑

```python
client_info = ClientTypeDetector.detect(headers)

# 判断是否需要消息净化
if client_info.needs_sanitization:
    messages = sanitize_messages(messages)

# 判断是否启用跨池 fallback
if client_info.enable_cross_pool_fallback:
    enable_cross_pool_fallback = True

# 提取 SCID (会话状态管理)
if client_info.scid:
    conversation_state = load_conversation_state(client_info.scid)
```

## 高级用法

### 1. 判断是否为 Augment 请求

```python
is_augment = ClientTypeDetector.is_augment_request(headers)

if is_augment:
    # Augment 特殊处理逻辑
    force_disable_thinking = headers.get("x-disable-thinking-signature") == "1"
```

### 2. 网关转发场景

```python
# 网关转发时,使用 X-Forwarded-User-Agent 保留原始客户端身份
headers = {
    "user-agent": "nginx/1.0",  # 网关自己的 UA
    "x-forwarded-user-agent": "cursor/1.0"  # 真实客户端的 UA
}

client_info = ClientTypeDetector.detect(headers)
# 会使用 "cursor/1.0" 进行检测
```

### 3. SCID 传递协议

**请求时**:
```http
POST /antigravity/v1/messages
X-AG-Conversation-Id: scid_1737100800_a1b2c3d4e5f6
```

**响应时**:
```http
HTTP/1.1 200 OK
X-AG-Conversation-Id: scid_1737100800_a1b2c3d4e5f6
```

## ClientInfo 数据结构

```python
@dataclass
class ClientInfo:
    client_type: ClientType          # 客户端类型枚举
    user_agent: str                  # User-Agent 字符串
    needs_sanitization: bool         # 是否需要消息净化
    enable_cross_pool_fallback: bool # 是否启用跨池 fallback
    scid: Optional[str] = None       # Server Conversation ID
    version: str = ""                # 客户端版本
    display_name: str = ""           # 显示名称
```

## 实际应用场景

### 场景 1: 消息净化

IDE 客户端可能会变形 thinking 文本,需要在发送前进行净化:

```python
client_info = ClientTypeDetector.detect(headers)

if client_info.needs_sanitization:
    # 对 messages 进行净化
    from src.ide_compat import AnthropicSanitizer

    sanitizer = AnthropicSanitizer()
    sanitized_messages = sanitizer.sanitize_messages(messages)
```

### 场景 2: 跨池降级

某些客户端支持跨池降级 (从 Opus 降级到 Sonnet):

```python
client_info = ClientTypeDetector.detect(headers)

if client_info.enable_cross_pool_fallback:
    # 启用跨池降级逻辑
    try:
        response = await call_opus_api(request)
    except QuotaExceededError:
        log.warning("Opus quota exceeded, falling back to Sonnet")
        response = await call_sonnet_api(request)
```

### 场景 3: 会话状态管理

使用 SCID 管理会话状态:

```python
client_info = ClientTypeDetector.detect(headers)

if client_info.scid:
    # 加载已有会话状态
    state = load_conversation_state(client_info.scid)

    # 使用服务端权威历史
    messages = state.get_authoritative_history()
else:
    # 生成新 SCID
    scid = generate_scid()

    # 创建新会话状态
    state = create_conversation_state(scid, messages)

    # 在响应中返回 SCID
    response.headers["X-AG-Conversation-Id"] = scid
```

## 日志输出

检测器会自动记录详细日志:

```
[CLIENT_DETECTOR] Detected client: Cursor IDE (type=cursor, version=1.5.0, sanitize=True, fallback=False, scid=None)
[CLIENT_DETECTOR] Detected client: Claude Code (type=claude_code, version=1.2.3, sanitize=False, fallback=True, scid=scid_1737100800_...)
```

## 注意事项

1. **大小写不敏感**: 所有 header 查找都是大小写不敏感的
2. **优先级顺序**: User-Agent 匹配按优先级顺序进行,精确匹配优先
3. **保守策略**: 未知客户端默认需要净化,不启用 fallback
4. **网关转发**: 优先使用 `X-Forwarded-User-Agent` 保留原始客户端身份
5. **SCID 提取**: 优先级为 `X-AG-Conversation-Id` > `X-Conversation-Id`

## 测试

运行测试验证功能:

```bash
cd F:/antigravity2api/gcli2api
python -m pytest tests/test_client_detector.py -v
```

## 相关文档

- [IDE 兼容层架构设计](./2026-01-17_SCID状态机与内容Hash组合方案_开发指导文档.md)
- [AnthropicSanitizer 使用指南](./AnthropicSanitizer使用指南.md)
- [会话状态管理](./会话状态管理.md)

---

**最后更新**: 2026-01-17
**维护者**: Claude Sonnet 4.5
