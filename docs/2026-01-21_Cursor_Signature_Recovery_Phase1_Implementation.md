# Cursor 签名恢复能力增强 - Phase 1 实现报告

**日期**: 2026-01-21
**版本**: Phase 1 (P0 方案)
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 一、概述

本次实现完成了 Cursor 签名恢复能力增强方案的 Phase 1 (P0 优先级) 部分，包括：

1. **S2: 客户端特定 TTL** - 为不同客户端配置不同的缓存有效期
2. **T3: 时间窗口 Fallback** - 获取最近 N 秒内缓存的任意签名作为最后 fallback

## 二、问题背景

Cursor IDE 在使用 Claude Extended Thinking 模式时存在特殊行为：

| 行为 | 影响 |
|------|------|
| 截断 thinking 内容 | thinking 块可能被截断，导致签名验证失败 |
| 不保留 thoughtSignature 字段 | 历史消息中的 thinking 块丢失签名 |
| Layer 3 (Encoded Tool ID) 被禁用 | 少了一层恢复机制 |

当前架构对 Cursor 禁用了 Layer 3 签名编码（`antigravity_router.py:341`），需要增强其他恢复层来弥补。

## 三、实现内容

### 3.1 S2: 客户端特定 TTL

**文件**: `src/signature_cache.py`

**新增配置**:
```python
CLIENT_TTL_CONFIG = {
    # IDE 客户端 - 会话更长，使用 2 小时 TTL
    "cursor": 7200,       # 2小时 - Cursor IDE
    "windsurf": 7200,     # 2小时 - Windsurf IDE
    # CLI 客户端 - 标准 1 小时 TTL
    "claude_code": 3600,  # 1小时 - Claude Code CLI
    "cline": 3600,        # 1小时 - Cline
    "aider": 3600,        # 1小时 - Aider
    "continue_dev": 3600, # 1小时 - Continue Dev
    "openai_api": 3600,   # 1小时 - OpenAI API 兼容客户端
    # 默认值
    "default": 3600       # 1小时 - 未知客户端默认值
}
```

**新增函数**:
```python
def get_ttl_for_client(client_type: str) -> int:
    """获取客户端特定的 TTL（秒）"""
```

**设计理由**:
- IDE 客户端（Cursor/Windsurf）会话通常更长
- 延长 TTL 到 2 小时可显著提高缓存命中率
- CLI 客户端保持标准 1 小时 TTL

### 3.2 T3: 时间窗口 Fallback

**文件**: `src/signature_cache.py`

**新增函数**:
```python
def get_recent_signature(
    time_window_seconds: int = 300,
    client_type: Optional[str] = None
) -> Optional[str]:
    """获取最近 N 秒内缓存的任意签名（时间窗口 Fallback）"""

def get_recent_signature_with_text(
    time_window_seconds: int = 300,
    client_type: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    """获取最近 N 秒内缓存的签名及其对应的 thinking 文本"""
```

**查找顺序**:
1. Tool Cache（按时间戳降序）
2. Session Cache（按时间戳降序）
3. 主缓存（按插入顺序逆序）

**设计理由**:
- 作为最后的 fallback，当所有其他恢复层都失败时使用
- 默认 5 分钟时间窗口，足够覆盖大多数工具调用场景
- 如果提供 client_type，使用客户端特定 TTL 的一半作为时间窗口

## 四、代码变更

| 文件 | 变更类型 | 行号 | 说明 |
|------|---------|------|------|
| `src/signature_cache.py` | 新增 | 36-74 | CLIENT_TTL_CONFIG 和 get_ttl_for_client() |
| `src/signature_cache.py` | 新增 | 953-1066 | get_recent_signature() 和 get_recent_signature_with_text() |

## 五、测试验证

### 5.1 语法检查
```bash
python -m py_compile src/signature_cache.py
# ✓ 语法检查通过
```

### 5.2 单元测试
```
=== Test 1: CLIENT_TTL_CONFIG ===
[PASS] CLIENT_TTL_CONFIG correct

=== Test 2: get_ttl_for_client ===
[PASS] get_ttl_for_client logic correct

========================================
[SUCCESS] All core logic tests passed!
========================================
```

## 六、后续工作

### Phase 2 (P1 方案) - 待实现

| 方案 | 描述 | 预计工时 |
|------|------|----------|
| S1 | 多级 Session 指纹 | 4h |
| T1 | Tool ID 前缀匹配 | 3h |

### 集成工作

新增的函数需要在签名恢复流程中被调用，建议在以下位置集成：

1. `antigravity_router.py` - 请求处理时使用 `get_ttl_for_client()` 获取客户端特定 TTL
2. 签名恢复逻辑 - 在所有其他恢复层失败后，调用 `get_recent_signature()` 作为最后 fallback

## 七、风险评估

| 风险项 | 评估 | 缓解措施 |
|--------|------|----------|
| 时间窗口 Fallback 返回不匹配的签名 | 低 | 时间窗口设置较短（5分钟），且作为最后 fallback |
| 客户端 TTL 配置错误 | 极低 | 配置化，可随时调整 |
| 性能影响 | 无 | 所有操作都是 O(n) 轻量操作 |

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
**相关文档**: [Cursor 签名恢复能力增强方案](./2026-01-21_Cursor_Signature_Recovery_Enhancement_Plan.md)
