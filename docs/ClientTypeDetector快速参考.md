# ClientTypeDetector 快速参考

## 导入

```python
from src.ide_compat import ClientType, ClientInfo, ClientTypeDetector
```

## 基本用法

```python
# 检测客户端类型
client_info = ClientTypeDetector.detect(dict(request.headers))

# 访问检测结果
client_info.client_type        # ClientType 枚举
client_info.display_name       # 显示名称 (如 "Cursor IDE")
client_info.version            # 版本号 (如 "1.5.0")
client_info.user_agent         # User-Agent 字符串
client_info.needs_sanitization # 是否需要净化
client_info.enable_cross_pool_fallback  # 是否启用跨池 fallback
client_info.scid               # Server Conversation ID
```

## 客户端类型速查表

| 客户端 | 枚举值 | 净化 | Fallback |
|-------|--------|------|----------|
| Claude Code | `CLAUDE_CODE` | ❌ | ✅ |
| Cursor | `CURSOR` | ✅ | ❌ |
| Augment | `AUGMENT` | ✅ | ❌ |
| Windsurf | `WINDSURF` | ✅ | ❌ |
| Cline | `CLINE` | ✅ | ✅ |
| Continue.dev | `CONTINUE_DEV` | ✅ | ✅ |
| Aider | `AIDER` | ✅ | ✅ |
| Zed | `ZED` | ✅ | ❌ |
| Copilot | `COPILOT` | ✅ | ❌ |
| OpenAI API | `OPENAI_API` | ❌ | ✅ |
| Unknown | `UNKNOWN` | ✅ | ❌ |

## 常用模式

### 消息净化

```python
if client_info.needs_sanitization:
    from src.ide_compat import AnthropicSanitizer
    sanitizer = AnthropicSanitizer()
    messages = sanitizer.sanitize_messages(messages)
```

### 跨池降级

```python
if client_info.enable_cross_pool_fallback:
    try:
        response = await call_opus_api(request)
    except QuotaExceededError:
        response = await call_sonnet_api(request)
```

### 会话状态管理

```python
if client_info.scid:
    # 加载已有会话
    state = load_conversation_state(client_info.scid)
else:
    # 创建新会话
    scid = generate_scid()
    state = create_conversation_state(scid, messages)
    response.headers["X-AG-Conversation-Id"] = scid
```

### Augment 请求判断

```python
if ClientTypeDetector.is_augment_request(headers):
    force_disable = headers.get("x-disable-thinking-signature") == "1"
```

## SCID 协议

**请求**:
```http
POST /api/endpoint
X-AG-Conversation-Id: scid_1737100800_a1b2c3d4e5f6
```

**响应**:
```http
HTTP/1.1 200 OK
X-AG-Conversation-Id: scid_1737100800_a1b2c3d4e5f6
```

## 网关转发

```python
headers = {
    "user-agent": "nginx/1.0",
    "x-forwarded-user-agent": "cursor/1.0"  # 优先使用
}
```

## 测试

```bash
python -m pytest tests/test_client_detector.py -v
```

---

**更多信息**: 参见 [ClientTypeDetector使用指南.md](./ClientTypeDetector使用指南.md)
