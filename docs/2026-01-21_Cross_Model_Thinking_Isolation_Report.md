# 跨模型 Thinking 隔离修复报告

**日期**: 2026-01-21
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 问题描述

### 核心问题
当模型路由在同一对话中波动（Claude → Gemini → Claude）时，会发生 "Thinking 污染"：

1. **Gemini 的 thinking 块没有有效的 signature**
2. 当这些无签名的 thinking 块被发送回 Claude 时，Claude 会拒绝处理或返回 400 错误
3. 这导致整个会话的 thinking 功能被禁用

### 第二个问题（2026-01-21 发现）
即使在同一模型（Claude → Claude）的连续对话中，thinking 也会断裂：

**根本原因**：`message_converter.py` 和 `sanitizer.py` 中的 2026-01-20 修复**错误理解**了 Claude 官方文档：
- ❌ 错误理解："thinking signature 是会话绑定的，历史签名在新请求中已失效"
- ✅ 正确理解：signature 是用于验证 thinking 内容完整性的，只要 signature + thinking 内容匹配，任何请求都可以使用

### 根本原因
- Claude 的 Extended Thinking 模式要求每个 thinking 块都有有效的 signature（用于验证）
- Gemini 的 thought 字段不提供 signature
- 现有代码没有区分不同模型家族的 thinking 块
- **2026-01-20 的修复过度激进**，无条件丢弃所有历史 thinking blocks

---

## 解决方案

### 1. 模型类型检测（`model_config.py`）

新增以下函数：

| 函数 | 功能 |
|------|------|
| `is_claude_model(model_name)` | 检测是否为 Claude 模型 |
| `is_gemini_model(model_name)` | 检测是否为 Gemini 模型 |
| `get_model_family(model_name)` | 获取模型家族: "claude", "gemini", "other" |
| `should_preserve_thinking_for_model(source, target)` | 判断是否应保留 thinking 块 |

### 2. Thinking 块过滤（`anthropic_converter.py`）

新增 `filter_thinking_for_target_model()` 函数：

```python
def filter_thinking_for_target_model(
    messages: List[Dict[str, Any]],
    target_model: str,
    *,
    last_model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """根据目标模型过滤消息中的 thinking 块"""
```

**过滤规则**：

| 目标模型 | thinking 有签名 | thinking 无签名 |
|----------|----------------|----------------|
| Claude   | ✅ 保留        | ❌ 移除        |
| Gemini   | ❌ 移除        | ❌ 移除        |
| Other    | ❌ 移除        | ❌ 移除        |

**集成位置**: `convert_anthropic_request_to_antigravity_components()` 函数

### 3. 缓存层增强（`signature_cache.py`）

- 新增 `model_family` 字段到 `CacheEntry`
- `set()` 方法自动计算并存储 `model_family`

### 4. 修复过度激进的丢弃逻辑

#### `message_converter.py` 修复
- **修复前**：无条件丢弃所有历史 thinking blocks
- **修复后**：保留 thinking 内容，让上游恢复 signature

#### `sanitizer.py` 修复
- **修复前**：历史消息的 thinking blocks 直接删除，不尝试恢复
- **修复后**：所有消息的 thinking blocks 都尝试签名恢复，恢复失败则降级为 text

---

## 修改的文件

### `src/converters/model_config.py`
- 新增 Claude/Gemini 模型标识符常量
- 新增 4 个模型检测函数
- 新增详细的 docstring 和示例

### `src/anthropic_converter.py`
- 导入模型检测函数
- 新增 `filter_thinking_for_target_model()` 函数（约 130 行）
- 在请求转换流程中集成过滤调用

### `src/signature_cache.py`
- 导入 `get_model_family`
- `CacheEntry` 新增 `model_family` 字段
- `set()` 方法自动计算 `model_family`

### `src/converters/message_converter.py`
- 修复字符串格式 thinking 的处理：保留内容供上游恢复
- 修复数组格式 thinking 的处理：有签名保留，无签名也保留待恢复

### `src/ide_compat/sanitizer.py`
- 修复 `_validate_and_recover_thinking_blocks()` 方法
- 历史消息的 thinking blocks 也尝试签名恢复
- 恢复失败降级为 text，而不是直接删除

---

## 测试结果

### 模型类型检测测试

| 模型名 | 家族 | is_claude | is_gemini |
|--------|------|-----------|-----------|
| claude-opus-4-5 | claude | ✓ | ✗ |
| claude-sonnet-4-5-thinking | claude | ✓ | ✗ |
| claude-4.5-opus-high-thinking | claude | ✓ | ✗ |
| gemini-3-pro-high | gemini | ✗ | ✓ |
| gemini-3-flash | gemini | ✗ | ✓ |
| gpt-4 | other | ✗ | ✗ |

### 跨模型 Thinking 保留规则测试

| 路径 | 结果 |
|------|------|
| Claude → Claude | ✅ 保留有效签名的 thinking |
| Claude → Gemini | ❌ 移除所有 thinking |
| Gemini → Claude | ❌ 移除所有 thinking（无签名）|
| Gemini → Gemini | ❌ 移除所有 thinking |

### 过滤功能测试

**场景**: 2 条 assistant 消息，各含 1 个 thinking 块
- 消息 1: thinking 有 200 字符签名
- 消息 2: thinking 无签名

**Claude 目标结果**:
- 消息 1: `['thinking', 'text']` - 保留 ✓
- 消息 2: `['text']` - 移除无签名 thinking ✓

**Gemini 目标结果**:
- 消息 1: `['text']` - 移除 ✓
- 消息 2: `['text']` - 移除 ✓

---

## 预期效果

### 修复前
```
用户使用 Claude → 成功（有 thinking）
第二轮对话 → 失败（thinking 被无条件丢弃，thinking 模式被禁用）
降级到 Gemini → 成功（有 thought，无 signature）
恢复到 Claude → 失败（Gemini 的无签名 thinking 污染）
```

### 修复后
```
用户使用 Claude → 成功（有 thinking）
第二轮对话 → 成功（thinking 保留并恢复签名）
降级到 Gemini → 成功（历史 thinking 被过滤）
恢复到 Claude → 成功（Gemini 的 thinking 已被过滤，Claude 的有效 thinking 保留）
```

---

## 架构设计

```
请求入口
    ↓
convert_anthropic_request_to_antigravity_components()
    ↓
filter_thinking_for_target_model()  ← 新增过滤点
    ↓
    ├─ is_claude_model() / is_gemini_model()
    ├─ get_model_family()
    └─ 根据 target_model 决定保留/移除
    ↓
发送到后端
```

---

## 注意事项

1. **最小侵入性**: 修改仅影响 thinking 块过滤，不改变核心转换逻辑
2. **向后兼容**: 单模型场景不受影响
3. **性能考量**: 过滤在请求处理早期进行，避免发送无效数据
4. **日志可观测**: 过滤操作有详细的 INFO 级别日志

---

## 相关链接

- 问题分析: `docs/2026-01-20_Thinking_Signature_Analysis_Report.md`
- Signature 修复: `docs/2026-01-20_Signature_Recovery_Fix_Report.md`
- 模型配置: `src/converters/model_config.py`

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
