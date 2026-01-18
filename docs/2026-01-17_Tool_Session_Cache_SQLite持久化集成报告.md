# Tool Cache 和 Session Cache SQLite 持久化集成报告

**文档创建时间**: 2026-01-17 10:00
**作者**: Claude Opus 4.5 (浮浮酱)
**项目**: gcli2api
**严重程度**: P1 Enhancement

---

## 一、问题描述

### 1.1 用户报告

在实现了6层签名缓存架构后，每次新对话开始时仍然出现签名恢复失败：

```
[09:50:31] [WARNING] [SIGNATURE_RECOVERY] All 6 layers failed for tool_id=call_45aeeb8597f15b1b7b6d3734
[09:50:31] [WARNING] [ANTHROPIC CONVERTER] No signature found for tool call: call_45aeeb8597f15b1b7b6d3734, using placeholder
```

### 1.2 问题分析

- **Tool Cache 和 Session Cache 只存在于内存中**
- 服务器重启或进程断开后，缓存丢失
- 新会话开始时，内存缓存为空，导致6层恢复全部失败
- 一旦缓存被填充，后续请求可以正常工作

---

## 二、解决方案

### 2.1 架构设计

将 Tool Cache 和 Session Cache 集成到现有的 SQLite 持久化架构中：

```
┌─────────────────────────────────────────────────────────────┐
│                    signature_cache.py                        │
│                    (便捷函数层)                               │
├─────────────────────────────────────────────────────────────┤
│  cache_tool_signature()    →  内存 + SQLite 双写            │
│  get_tool_signature()      →  内存优先，SQLite 回填          │
│  cache_session_signature() →  内存 + SQLite 双写            │
│  get_session_signature()   →  内存优先，SQLite 回填          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    CacheFacade                               │
│                    (缓存门面层)                               │
├─────────────────────────────────────────────────────────────┤
│  cache_tool_signature()    →  SignatureDatabase              │
│  get_tool_signature()      →  SignatureDatabase              │
│  cache_session_signature() →  SignatureDatabase              │
│  get_session_signature()   →  SignatureDatabase              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                  SignatureDatabase                           │
│                  (SQLite 持久化层)                            │
├─────────────────────────────────────────────────────────────┤
│  tool_signature_cache 表                                     │
│  session_signature_cache 表                                  │
│  WAL 模式 + TTL 过期 + 访问统计                               │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据库表结构

**Tool Cache 表**:
```sql
CREATE TABLE IF NOT EXISTS tool_signature_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id TEXT UNIQUE NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TEXT
)
```

**Session Cache 表**:
```sql
CREATE TABLE IF NOT EXISTS session_signature_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    signature TEXT NOT NULL,
    thinking_text TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TEXT
)
```

---

## 三、修改内容

### 3.1 SignatureDatabase (src/cache/signature_database.py)

**新增表结构**:
- `CREATE_TOOL_CACHE_TABLE_SQL` - Tool Cache 表定义
- `CREATE_SESSION_CACHE_TABLE_SQL` - Session Cache 表定义
- 相应的索引定义

**新增方法**:
- `set_tool_signature(tool_id, signature, ttl_seconds)` - 存储工具签名
- `get_tool_signature(tool_id)` - 获取工具签名
- `delete_tool_signature(tool_id)` - 删除工具签名
- `cleanup_expired_tool_cache()` - 清理过期工具缓存
- `set_session_signature(session_id, signature, thinking_text, ttl_seconds)` - 存储会话签名
- `get_session_signature(session_id)` - 获取会话签名
- `delete_session_signature(session_id)` - 删除会话签名
- `cleanup_expired_session_cache()` - 清理过期会话缓存
- `get_last_session_signature()` - 获取最近的会话签名

### 3.2 CacheFacade (src/cache/cache_facade.py)

**新增方法**:
- `_get_signature_db()` - 获取 SignatureDatabase 实例
- `cache_tool_signature(tool_id, signature)` - 缓存工具签名
- `get_tool_signature(tool_id)` - 获取工具签名
- `cache_session_signature(session_id, signature, thinking_text)` - 缓存会话签名
- `get_session_signature(session_id)` - 获取会话签名
- `get_last_session_signature_from_db()` - 获取最近的会话签名

**新增便捷函数**:
- `cache_tool_signature()` - 模块级便捷函数
- `get_tool_signature()` - 模块级便捷函数
- `cache_session_signature()` - 模块级便捷函数
- `get_session_signature()` - 模块级便捷函数
- `get_last_session_signature_from_db()` - 模块级便捷函数

### 3.3 signature_cache.py

**修改便捷函数**:
- `cache_tool_signature()` - 添加 SQLite 双写支持
- `get_tool_signature()` - 添加 SQLite 回填支持
- `cache_session_signature()` - 添加 SQLite 双写支持
- `get_session_signature()` - 添加 SQLite 回填支持
- `get_session_signature_with_text()` - 添加 SQLite 回填支持

---

## 四、工作流程

### 4.1 写入流程 (Dual Write)

```
cache_tool_signature(tool_id, signature)
    ↓
1. 写入内存缓存 (SignatureCache._tool_signatures)
    ↓
2. 如果迁移模式启用:
   写入 SQLite (SignatureDatabase.set_tool_signature)
    ↓
3. 返回成功
```

### 4.2 读取流程 (Memory First, SQLite Fallback)

```
get_tool_signature(tool_id)
    ↓
1. 查询内存缓存
    ↓
2. 如果命中 → 返回结果
    ↓
3. 如果未命中且迁移模式启用:
   查询 SQLite
    ↓
4. 如果 SQLite 命中:
   - 回填到内存缓存
   - 返回结果
    ↓
5. 返回 None
```

---

## 五、测试验证

### 5.1 SignatureDatabase 测试

```
=== 测试 SignatureDatabase Tool/Session Cache ===
--- Tool Cache 测试 ---
set_tool_signature: True
get_tool_signature: True
signature match: True

--- Session Cache 测试 ---
set_session_signature: True
get_session_signature: True
signature match: True
thinking_text match: True
get_last_session_signature: True
```

### 5.2 CacheFacade 测试

```
=== 测试 CacheFacade Tool/Session Cache ===
--- CacheFacade Tool Cache 测试 ---
cache_tool_signature: True
get_tool_signature: True
signature match: True

--- CacheFacade Session Cache 测试 ---
cache_session_signature: True
get_session_signature: True
signature match: True
thinking_text match: True
get_last_session_signature_from_db: True
```

### 5.3 便捷函数测试

```
=== 测试 signature_cache 便捷函数持久化 ===
迁移模式启用: True
--- Tool Cache 便捷函数测试 ---
cache_tool_signature: True
get_tool_signature: True
signature match: True

--- Session Cache 便捷函数测试 ---
cache_session_signature: True
get_session_signature: True
signature match: True
get_session_signature_with_text: True
thinking_text match: True
```

### 5.4 语法检查

```
Syntax check passed!
```

---

## 六、预期效果

### 6.1 修复前

```
新会话开始
    ↓
内存缓存为空
    ↓
6层签名恢复全部失败
    ↓
使用 placeholder，thinking 模式降级
```

### 6.2 修复后

```
新会话开始
    ↓
内存缓存为空
    ↓
查询 SQLite 持久化缓存
    ↓
如果找到 → 回填内存 + 返回签名
    ↓
6层签名恢复成功（Layer 4/5 命中）
```

---

## 七、附加修复

### 7.1 Tool Loop Recovery 对话重置问题

**问题**: Tool Loop Recovery 注入的 `[Proceed]` User 消息导致模型认为是新对话开始

**修复**: 移除合成消息注入，只在原始 assistant 消息中注入 thinking 块

**文件**: `src/converters/tool_loop_recovery.py`

---

## 八、后续建议

### 8.1 P1 优化

1. **缓存预热**: 服务启动时从 SQLite 预加载最近的缓存到内存
2. **过期清理**: 定期清理 SQLite 中的过期条目

### 8.2 P2 优化

1. **监控面板**: 在 Web 管理界面显示 SQLite 缓存统计
2. **缓存压缩**: 对 thinking_text 进行压缩存储

---

## 九、总结

### 9.1 修改内容

- ✅ 在 SignatureDatabase 中添加 Tool Cache 和 Session Cache 表
- ✅ 在 SignatureDatabase 中添加 CRUD 方法
- ✅ 在 CacheFacade 中添加 Tool Cache 和 Session Cache 接口
- ✅ 修改 signature_cache.py 便捷函数支持 SQLite 持久化
- ✅ 修复 Tool Loop Recovery 对话重置问题

### 9.2 预期效果

- 服务器重启后缓存不丢失
- 新会话可以从 SQLite 恢复签名
- 6层签名恢复策略更加稳定

---

**文档结束**

祝下一个 Claude Agent 开发顺利喵～ ฅ'ω'ฅ
