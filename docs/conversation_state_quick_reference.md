# Conversation State API 快速参考

## 导入

```python
from src.cache.signature_database import SignatureDatabase
import json

db = SignatureDatabase()
```

## 基本操作

### 存储状态
```python
db.store_conversation_state(
    scid="conv_001",
    client_type="cursor",  # 'cursor' | 'augment' | 'claude_code' | 'unknown'
    history=json.dumps([{"role": "user", "content": "Hello"}]),
    signature="sig_123",   # 可选
    ttl_seconds=3600       # 可选,默认使用配置
)
```

### 获取状态
```python
state = db.get_conversation_state("conv_001")
# 返回: {
#     "scid": "conv_001",
#     "client_type": "cursor",
#     "authoritative_history": [...],  # 已解析的列表
#     "last_signature": "sig_123",
#     "created_at": "2026-01-17T20:00:00",
#     "updated_at": "2026-01-17T20:30:00",
#     "expires_at": "2026-01-17T21:00:00",
#     "access_count": 5
# }
```

### 更新状态
```python
db.update_conversation_state(
    scid="conv_001",
    history=json.dumps([...]),  # 更新后的历史
    signature="sig_456"         # 可选
)
```

### 删除状态
```python
db.delete_conversation_state("conv_001")
```

### 清理过期状态
```python
count = db.cleanup_expired_states()
print(f"清理了 {count} 个过期状态")
```

## 完整示例

```python
from src.cache.signature_database import SignatureDatabase
import json

db = SignatureDatabase()

# 1. 开始会话
scid = "conv_user123"
history = [{"role": "user", "content": "你好"}]
db.store_conversation_state(
    scid=scid,
    client_type="cursor",
    history=json.dumps(history)
)

# 2. 添加新消息
state = db.get_conversation_state(scid)
if state:
    history = state["authoritative_history"]
    history.append({"role": "assistant", "content": "你好!有什么可以帮助你的?"})

    db.update_conversation_state(
        scid=scid,
        history=json.dumps(history),
        signature="new_sig"
    )

# 3. 查看状态
state = db.get_conversation_state(scid)
print(f"对话轮次: {len(state['authoritative_history'])}")
print(f"访问次数: {state['access_count']}")

# 4. 结束会话
db.delete_conversation_state(scid)
```

## 注意事项

- ✅ SCID 必须唯一
- ✅ history 必须是有效的 JSON 字符串
- ✅ 自动处理过期检查
- ✅ 线程安全
- ✅ 自动更新访问统计
