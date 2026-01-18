# AnthropicSanitizer 使用指南

## 概述

`AnthropicSanitizer` 是 IDE 兼容层的核心兜底组件,用于处理各种 IDE 客户端的特殊行为,确保消息符合 Anthropic API 规范。

## 核心功能

1. **Thinking Block 签名验证和恢复**
   - 验证 thinking block 的签名有效性
   - 使用 6层签名恢复策略尝试恢复无效签名
   - 恢复失败时优雅降级为 text block

2. **Tool Use 签名恢复**
   - 从编码的 tool_id 中解码签名
   - 使用多层缓存策略恢复签名
   - 失败时使用占位符签名

3. **ThinkingConfig 同步**
   - 确保 thinkingConfig 与消息内容一致
   - 没有有效 thinking block 时自动禁用 thinking

4. **Tool Chain 完整性检查**
   - 检测断裂的 tool_use/tool_result 链条
   - 记录警告但不修复 (由其他模块负责)

## 设计原则

1. **兜底保护** - 绝不抛出异常,所有错误都转换为降级处理
2. **签名恢复** - 6层签名恢复策略,最大化恢复成功率
3. **优雅降级** - 无法恢复时降级为 text block,保留内容
4. **详细日志** - 所有操作都有详细日志,便于问题追踪

## 使用方法

### 基本用法

```python
from src.ide_compat.sanitizer import AnthropicSanitizer

# 创建 sanitizer 实例
sanitizer = AnthropicSanitizer()

# 净化消息
sanitized_messages, should_enable_thinking = sanitizer.sanitize_messages(
    messages=messages,
    thinking_enabled=True,
    session_id="session_123",  # 可选
    last_thought_signature="abc..."  # 可选
)
```

### 使用全局单例

```python
from src.ide_compat.sanitizer import get_sanitizer

# 获取全局 sanitizer 实例
sanitizer = get_sanitizer()

# 使用方式相同
sanitized_messages, should_enable_thinking = sanitizer.sanitize_messages(
    messages=messages,
    thinking_enabled=True
)
```

### 便捷函数

```python
from src.ide_compat.sanitizer import sanitize_anthropic_messages

# 直接调用便捷函数
sanitized_messages, should_enable_thinking = sanitize_anthropic_messages(
    messages=messages,
    thinking_enabled=True,
    session_id="session_123"
)
```

## 签名恢复策略

AnthropicSanitizer 使用 6层签名恢复策略:

1. **Client** - 客户端提供的签名 (如果有效)
2. **Context** - 上下文中的 last_thought_signature
3. **Encoded Tool ID** - 从编码的工具ID解码 (gcli2api 独有优势)
4. **Session Cache** - 会话级别缓存 (Layer 3)
5. **Tool Cache** - 工具ID级别缓存 (Layer 1)
6. **Last Signature** - 最近缓存的配对 (全局 fallback)

## 处理流程

### Thinking Block 处理

```
1. 检查是否有有效签名
   ├─ 有效 → 清理额外字段后保留
   └─ 无效 → 尝试恢复
       ├─ 恢复成功 → 更新签名并缓存
       └─ 恢复失败 → 降级为 text block
```

### Tool Use Block 处理

```
1. 解码 tool_id (可能包含编码的签名)
2. 尝试恢复签名
   ├─ 恢复成功 → 更新签名
   └─ 恢复失败 → 使用占位符签名
```

### ThinkingConfig 同步

```
1. 检查消息中是否有有效的 thinking block
   ├─ 有 → 保持 thinking_enabled 状态
   └─ 无 → 禁用 thinking (返回 False)
```

## 统计信息

```python
# 获取统计信息
stats = sanitizer.get_stats()

print(stats)
# {
#     "total_messages": 10,
#     "thinking_blocks_validated": 5,
#     "thinking_blocks_recovered": 3,
#     "thinking_blocks_downgraded": 2,
#     "tool_use_blocks_recovered": 4,
#     "tool_chains_fixed": 1
# }

# 重置统计信息
sanitizer.reset_stats()
```

## 实际应用场景

### 场景 1: Cursor IDE - 过滤 Thinking 块

Cursor IDE 可能会过滤掉 thinking 块,只保留 tool_use:

```python
messages = [
    {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "call_123",
                "name": "read_file",
                "input": {"path": "test.py"}
            }
        ]
    }
]

sanitized, thinking_enabled = sanitizer.sanitize_messages(
    messages,
    thinking_enabled=True
)

# 结果: thinking_enabled = False (因为没有有效的 thinking block)
```

### 场景 2: Augment - 修改签名

Augment 可能会修改 thinking 块的签名:

```python
messages = [
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": "Let me analyze...",
                "thoughtSignature": "invalid_sig"  # 被修改的无效签名
            }
        ]
    }
]

sanitized, thinking_enabled = sanitizer.sanitize_messages(
    messages,
    thinking_enabled=True,
    session_id="session_123"
)

# 结果:
# 1. 尝试从 session cache 恢复签名
# 2. 恢复成功 → thinking block 保留
# 3. 恢复失败 → 降级为 text block
```

### 场景 3: 工具调用签名恢复

```python
messages = [
    {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "call_123__thought__abc123",  # 编码的 tool_id
                "name": "get_weather",
                "input": {"city": "London"}
            }
        ]
    }
]

sanitized, thinking_enabled = sanitizer.sanitize_messages(
    messages,
    thinking_enabled=True
)

# 结果:
# 1. 从 tool_id 解码签名: "abc123"
# 2. 更新 tool_use block 的 thoughtSignature
# 3. 缓存到 tool cache
```

## 日志示例

```
[SANITIZER] 消息净化完成: messages=3, thinking_enabled=True->False, recovered=2, downgraded=1
[SANITIZER] Thinking block 签名恢复成功: source=SESSION_CACHE, thinking_len=150
[SANITIZER] Thinking block 签名恢复失败: thinking_len=100, 将降级为 text block
[SANITIZER] 降级 thinking block 为 text block: content_len=100
[SANITIZER] Tool use 签名恢复成功: tool_id=call_123, source=ENCODED_TOOL_ID
[SANITIZER] 检测到断裂的工具调用链: broken_count=1, tool_ids=['call_456']
```

## 错误处理

AnthropicSanitizer 采用兜底保护策略,所有异常都会被捕获:

```python
try:
    sanitized, thinking_enabled = sanitizer.sanitize_messages(
        messages,
        thinking_enabled=True
    )
except Exception as e:
    # 不会发生 - sanitizer 内部会捕获所有异常
    pass

# 如果内部发生异常,会返回原始消息
# 日志: [SANITIZER] 消息净化失败,返回原始消息: <error>
```

## 性能考虑

1. **缓存优先** - 优先使用缓存,减少计算
2. **延迟导入** - 避免循环依赖,提高启动速度
3. **最小修改** - 只修改需要处理的部分,保留其他内容

## 集成建议

### 在 anthropic_converter.py 中使用

```python
from src.ide_compat.sanitizer import sanitize_anthropic_messages

def convert_anthropic_to_gemini(payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = payload.get("messages", [])
    thinking_enabled = payload.get("thinking") is not None

    # 净化消息
    sanitized_messages, final_thinking_enabled = sanitize_anthropic_messages(
        messages,
        thinking_enabled,
        session_id=get_session_id(payload)
    )

    # 使用净化后的消息和 thinking 配置
    payload["messages"] = sanitized_messages
    if not final_thinking_enabled:
        payload.pop("thinking", None)

    # 继续转换...
```

### 在路由层使用

```python
from src.ide_compat.sanitizer import get_sanitizer

async def handle_anthropic_request(request: Request):
    payload = await request.json()

    # 净化消息
    sanitizer = get_sanitizer()
    messages = payload.get("messages", [])
    thinking_enabled = payload.get("thinking") is not None

    sanitized_messages, final_thinking_enabled = sanitizer.sanitize_messages(
        messages,
        thinking_enabled
    )

    # 更新 payload
    payload["messages"] = sanitized_messages
    if not final_thinking_enabled:
        payload.pop("thinking", None)

    # 继续处理...
```

## 测试

运行单元测试:

```bash
cd F:/antigravity2api/gcli2api
python -m pytest tests/test_sanitizer.py -v
```

## 相关模块

- `src/converters/thoughtSignature_fix.py` - Thinking 块签名验证和清理
- `src/converters/signature_recovery.py` - 6层签名恢复策略
- `src/signature_cache.py` - 签名缓存管理
- `src/converters/tool_loop_recovery.py` - 工具循环恢复

## 更新日志

### 2026-01-17
- 初始实现
- 支持 thinking block 签名验证和恢复
- 支持 tool use 签名恢复
- 支持 thinkingConfig 同步
- 支持 tool chain 完整性检查
- 完整的单元测试覆盖

---

**Author**: Claude Sonnet 4.5 (浮浮酱)
**Date**: 2026-01-17
