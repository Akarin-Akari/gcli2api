# Conversation State 表使用文档

## 概述

`conversation_state` 表用于存储基于 SCID (Server Conversation ID) 的会话状态机数据,支持多客户端类型的会话历史管理。

## 表结构

```sql
CREATE TABLE IF NOT EXISTS conversation_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scid TEXT UNIQUE NOT NULL,           -- Server Conversation ID
    client_type TEXT NOT NULL,           -- 'cursor' | 'augment' | 'claude_code' | 'unknown'
    authoritative_history TEXT NOT NULL, -- JSON 格式的权威历史
    last_signature TEXT,                 -- 最后一个有效签名
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    access_count INTEGER DEFAULT 0
)
```

## 索引

```sql
CREATE INDEX IF NOT EXISTS idx_conversation_state_scid ON conversation_state(scid);
CREATE INDEX IF NOT EXISTS idx_conversation_state_expires ON conversation_state(expires_at);
```

## API 方法

### 1. store_conversation_state()

存储或更新会话状态。

**参数:**
- `scid` (str): Server Conversation ID,必填
- `client_type` (str): 客户端类型,必填 ('cursor' | 'augment' | 'claude_code' | 'unknown')
- `history` (str): JSON 格式的权威历史,必填
- `signature` (Optional[str]): 最后一个有效签名,可选
- `ttl_seconds` (Optional[int]): 过期时间(秒),可选,默认使用配置中的 ttl_seconds

**返回值:**
- `bool`: 成功返回 True,失败返回 False

**示例:**
```python
import json
from src.cache.signature_database import SignatureDatabase

db = SignatureDatabase()

# 准备历史数据
history = json.dumps([
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"}
])

# 存储会话状态
success = db.store_conversation_state(
    scid="conv_12345",
    client_type="cursor",
    history=history,
    signature="sig_abc123",
    ttl_seconds=3600  # 1小时后过期
)
```

### 2. get_conversation_state()

获取会话状态。

**参数:**
- `scid` (str): Server Conversation ID

**返回值:**
- `Optional[Dict[str, Any]]`: 成功返回状态字典,失败或不存在返回 None

**返回字典结构:**
```python
{
    "scid": str,                      # Server Conversation ID
    "client_type": str,               # 客户端类型
    "authoritative_history": list,    # 解析后的历史列表
    "last_signature": str,            # 最后签名
    "created_at": str,                # 创建时间 (ISO格式)
    "updated_at": str,                # 更新时间 (ISO格式)
    "expires_at": str,                # 过期时间 (ISO格式)
    "access_count": int               # 访问次数
}
```

**示例:**
```python
state = db.get_conversation_state("conv_12345")
if state:
    print(f"Client Type: {state['client_type']}")
    print(f"History Length: {len(state['authoritative_history'])}")
    print(f"Last Signature: {state['last_signature']}")
    print(f"Access Count: {state['access_count']}")
```

### 3. update_conversation_state()

更新现有会话状态。

**参数:**
- `scid` (str): Server Conversation ID
- `history` (str): 更新后的 JSON 格式历史
- `signature` (Optional[str]): 更新后的签名,可选

**返回值:**
- `bool`: 成功返回 True,失败返回 False

**示例:**
```python
# 添加新的对话轮次
updated_history = json.dumps([
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"},
    {"role": "user", "content": "How are you?"}
])

success = db.update_conversation_state(
    scid="conv_12345",
    history=updated_history,
    signature="sig_xyz789"
)
```

### 4. delete_conversation_state()

删除会话状态。

**参数:**
- `scid` (str): Server Conversation ID

**返回值:**
- `bool`: 成功返回 True,失败或不存在返回 False

**示例:**
```python
success = db.delete_conversation_state("conv_12345")
```

### 5. cleanup_expired_states()

清理所有过期的会话状态。

**参数:** 无

**返回值:**
- `int`: 清理的条目数量

**示例:**
```python
count = db.cleanup_expired_states()
print(f"Cleaned up {count} expired states")
```

## 使用场景

### 场景 1: 多轮对话状态管理

```python
import json
from src.cache.signature_database import SignatureDatabase

db = SignatureDatabase()

# 初始化会话
scid = "conv_user123_20260117"
history = json.dumps([
    {"role": "user", "content": "开始新对话"}
])

db.store_conversation_state(
    scid=scid,
    client_type="cursor",
    history=history,
    signature="init_sig"
)

# 添加新轮次
state = db.get_conversation_state(scid)
if state:
    history_list = state["authoritative_history"]
    history_list.append({"role": "assistant", "content": "你好!"})
    history_list.append({"role": "user", "content": "请帮我写代码"})

    db.update_conversation_state(
        scid=scid,
        history=json.dumps(history_list),
        signature="new_sig"
    )
```

### 场景 2: 客户端类型识别

```python
# 根据客户端类型存储不同的状态
client_types = ["cursor", "augment", "claude_code"]

for client_type in client_types:
    scid = f"conv_{client_type}_001"
    history = json.dumps([{"role": "system", "content": f"Client: {client_type}"}])

    db.store_conversation_state(
        scid=scid,
        client_type=client_type,
        history=history
    )

# 查询特定客户端的状态
state = db.get_conversation_state("conv_cursor_001")
if state and state["client_type"] == "cursor":
    print("This is a Cursor client conversation")
```

### 场景 3: 定期清理过期状态

```python
import time
from datetime import datetime

# 存储短期状态
db.store_conversation_state(
    scid="temp_conv",
    client_type="unknown",
    history=json.dumps([]),
    ttl_seconds=60  # 1分钟后过期
)

# 等待过期
time.sleep(61)

# 清理过期状态
count = db.cleanup_expired_states()
print(f"Cleaned up {count} expired states")
```

## 注意事项

1. **SCID 唯一性**: 每个 SCID 在表中是唯一的,重复存储会更新现有记录
2. **JSON 格式**: `authoritative_history` 必须是有效的 JSON 字符串
3. **自动过期**: 设置 `ttl_seconds` 后,状态会在指定时间后自动过期
4. **访问计数**: 每次 `get_conversation_state()` 调用会自动增加 `access_count`
5. **线程安全**: 所有操作都是线程安全的,使用了 SQLite 的 WAL 模式

## 性能优化建议

1. **批量清理**: 定期调用 `cleanup_expired_states()` 清理过期数据
2. **合理设置 TTL**: 根据实际需求设置合理的过期时间
3. **索引优化**: SCID 和 expires_at 字段已建立索引,查询性能良好
4. **避免频繁更新**: 尽量批量更新历史记录,减少数据库写入次数

## 集成示例

```python
from src.cache.signature_database import SignatureDatabase
from src.cache.cache_interface import CacheConfig
import json

# 初始化数据库
config = CacheConfig(
    db_path="data/signature_cache.db",
    ttl_seconds=3600,  # 默认1小时过期
    wal_mode=True
)
db = SignatureDatabase(config)

# 完整的会话管理流程
class ConversationManager:
    def __init__(self, db: SignatureDatabase):
        self.db = db

    def start_conversation(self, scid: str, client_type: str):
        """开始新会话"""
        history = json.dumps([])
        return self.db.store_conversation_state(
            scid=scid,
            client_type=client_type,
            history=history
        )

    def add_message(self, scid: str, role: str, content: str, signature: str = None):
        """添加消息到会话"""
        state = self.db.get_conversation_state(scid)
        if not state:
            return False

        history = state["authoritative_history"]
        history.append({"role": role, "content": content})

        return self.db.update_conversation_state(
            scid=scid,
            history=json.dumps(history),
            signature=signature
        )

    def get_history(self, scid: str):
        """获取会话历史"""
        state = self.db.get_conversation_state(scid)
        return state["authoritative_history"] if state else []

    def end_conversation(self, scid: str):
        """结束会话"""
        return self.db.delete_conversation_state(scid)

# 使用示例
manager = ConversationManager(db)

# 开始会话
manager.start_conversation("conv_001", "cursor")

# 添加消息
manager.add_message("conv_001", "user", "Hello", "sig_1")
manager.add_message("conv_001", "assistant", "Hi there!", "sig_2")

# 获取历史
history = manager.get_history("conv_001")
print(f"Conversation has {len(history)} messages")

# 结束会话
manager.end_conversation("conv_001")
```

## 更新日志

- **2026-01-17**: 初始版本,添加 conversation_state 表及完整 CRUD 方法
