# 400 错误修复报告：messages.*.content.*.thinking.signature: Field required

**日期**: 2026-01-21
**版本**: Hotfix
**作者**: Claude Opus 4.5 (浮浮酱)
**状态**: ✅ 已完成

---

## 一、问题描述

### 1.1 错误信息

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "messages.1.content.0.thinking.signature: Field required"
  }
}
```

### 1.2 问题现象

- Cursor IDE 使用 Claude Extended Thinking 模式时
- 在多轮对话中，历史消息包含 thinking blocks
- 当签名恢复失败时，无效的 thinking blocks 仍然被发送到 Claude API
- Claude API 返回 400 错误

## 二、根因分析

### 2.1 问题根因

**发现两个核心问题**：

#### 问题 1：Middleware TARGET_PATHS 不完整

**文件**: `src/ide_compat/middleware.py`

原来的 TARGET_PATHS 只包含 Anthropic Native API 路径：
```python
TARGET_PATHS = [
    "/antigravity/v1/messages",
    "/v1/messages",
]
```

但是 **Cursor 等 IDE 客户端使用 OpenAI 兼容格式**，请求发送到：
- `/antigravity/v1/chat/completions`
- `/v1/chat/completions`

这导致 **Sanitizer（消息净化器）从未被调用**！

#### 问题 2：Router 检测到无效 thinking blocks 但未清理

**文件**: `src/antigravity_router.py`

代码检测到无效的 thinking blocks 后，只是设置 `all_thinking_valid = False`，然后说"信任 Sanitizer 清理"：

```python
if any_thinking_found and not all_thinking_valid:
    log.info("信任 Sanitizer 清理，保持 thinking 模式启用")
    # 原逻辑已移除：enable_thinking = False
```

问题在于：
1. Middleware 在 Router 之前运行，但 TARGET_PATHS 不包含 OpenAI 兼容路径
2. 即使 Sanitizer 运行了，它也只处理 Anthropic 格式的数组 content
3. Router 检测到无效 thinking blocks 后**没有实际清理它们**
4. 无效的 thinking blocks（没有 signature）仍然被发送到 Claude API

### 2.2 问题流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    问题流程（修复前）                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Cursor 发送请求到 /antigravity/v1/chat/completions      │
│     ↓                                                       │
│  2. Middleware 检查 TARGET_PATHS                            │
│     → 路径不匹配！跳过净化                                   │
│     ↓                                                       │
│  3. Router 收到原始请求（未净化）                            │
│     ↓                                                       │
│  4. Router 检测到 thinking blocks                           │
│     ↓                                                       │
│  5. 尝试签名恢复 → 失败                                      │
│     ↓                                                       │
│  6. all_thinking_valid = False                              │
│     ↓                                                       │
│  7. 代码说"信任 Sanitizer 清理"但 Sanitizer 没有运行！       │
│     ↓                                                       │
│  8. 无效的 thinking blocks 被发送到 Claude API              │
│     ↓                                                       │
│  9. Claude API 返回 400 错误：signature: Field required     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 三、修复方案

### 3.1 修复 1：扩展 Middleware TARGET_PATHS

**文件**: `src/ide_compat/middleware.py`
**行号**: 51-61

```python
# [FIX 2026-01-21] 添加 OpenAI 兼容路径，修复 Cursor 等 IDE 客户端的 thinking block 处理
TARGET_PATHS = [
    # Anthropic Native API
    "/antigravity/v1/messages",
    "/v1/messages",
    # OpenAI Compatible API (Cursor, Windsurf 等 IDE 使用此路径)
    "/antigravity/v1/chat/completions",
    "/v1/chat/completions",
]
```

### 3.2 修复 2：Router 主动清理无效 thinking blocks

**文件**: `src/antigravity_router.py`
**行号**: 1980-1987

```python
if any_thinking_found and not all_thinking_valid:
    # [FIX 2026-01-21] 主动清理无效的 thinking blocks！
    # 使用 strip_thinking_from_openai_messages 清理所有 thinking 内容
    # 这样可以避免 400 错误：messages.*.content.*.thinking.signature: Field required
    original_msg_count = len(messages) if hasattr(messages, '__len__') else 0
    messages = strip_thinking_from_openai_messages(messages)
    log.info(f"[ANTIGRAVITY] 历史消息中的某些 thinking block 签名无效，"
            f"已主动清理所有 thinking blocks (messages={original_msg_count})，保持 thinking 模式启用")
```

## 四、代码变更汇总

| 文件 | 变更类型 | 行号 | 说明 |
|------|---------|------|------|
| `src/ide_compat/middleware.py` | 修改 | 45-61 | 扩展 TARGET_PATHS，添加 OpenAI 兼容路径 |
| `src/antigravity_router.py` | 修改 | 1970-1987 | 主动清理无效 thinking blocks |

## 五、修复后流程

```
┌─────────────────────────────────────────────────────────────┐
│                    修复后流程                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Cursor 发送请求到 /antigravity/v1/chat/completions      │
│     ↓                                                       │
│  2. Middleware 检查 TARGET_PATHS                            │
│     → 路径匹配！进行净化 ✅                                  │
│     ↓                                                       │
│  3. Sanitizer 处理消息                                      │
│     → 清理/降级无效的 thinking blocks                       │
│     ↓                                                       │
│  4. Router 收到净化后的请求                                 │
│     ↓                                                       │
│  5. Router 检测 thinking blocks                             │
│     → 如果仍有无效 blocks，主动调用 strip_thinking ✅        │
│     ↓                                                       │
│  6. 干净的消息被发送到 Claude API                           │
│     ↓                                                       │
│  7. Claude API 正常响应 ✅                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 六、测试验证

### 6.1 语法检查

```bash
python -m py_compile src/ide_compat/middleware.py
# ✓ 语法检查通过

python -m py_compile src/antigravity_router.py
# ✓ 语法检查通过
```

### 6.2 预期行为

| 场景 | 修复前 | 修复后 |
|------|-------|-------|
| Cursor 多轮对话 | 400 错误 | 正常工作 |
| 签名恢复失败 | thinking blocks 未清理 | 主动清理 |
| 新会话首轮 | 正常 | 正常 |

## 七、防御性设计

此次修复采用**双重保障**策略：

1. **第一道防线：Middleware Sanitizer**
   - 在请求进入 Router 之前净化消息
   - 处理 Anthropic 格式的 thinking blocks
   - 降级无效 blocks 为 text blocks

2. **第二道防线：Router 主动清理**
   - 即使 Sanitizer 未运行或处理不完整
   - Router 检测到无效 thinking blocks 时主动清理
   - 使用 `strip_thinking_from_openai_messages` 移除所有 thinking 内容

## 八、相关文档

- [Cursor 签名恢复能力增强方案](./2026-01-21_Cursor_Signature_Recovery_Enhancement_Plan.md)
- [Phase 1 实现报告](./2026-01-21_Cursor_Signature_Recovery_Phase1_Implementation.md)
- [Phase 2 实现报告](./2026-01-21_Cursor_Signature_Recovery_Phase2_Implementation.md)
- [Phase 3 集成报告](./2026-01-21_Cursor_Signature_Recovery_Phase3_Integration.md)

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
**修复优先级**: P0 (Critical)
**影响范围**: Cursor, Windsurf 等使用 OpenAI 兼容 API 的 IDE 客户端
