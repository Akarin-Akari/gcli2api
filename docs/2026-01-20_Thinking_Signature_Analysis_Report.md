# Thinking Signature 深度分析报告

**日期**: 2026-01-20
**分析者**: 浮浮酱 (Claude Opus 4.5)
**主题**: Claude Extended Thinking 签名机制与 Cursor 兼容性分析

## 1. 问题背景

### 1.1 观察到的现象

在 Cursor IDE 中使用 Claude Thinking 模型时，出现以下问题：

1. **400 错误**：`tool_use ids were found without tool_result blocks immediately after`
2. **Thinking 退化**：长对话中推理深度和质量下降
3. **签名失效**：历史 thinking blocks 的签名在新请求中被拒绝

### 1.2 用户假设

> "从发生的现象来看，'先思考后工具出现400错误'说明调用工具后cursor开启了新的会话导致旧签名失效"

## 2. 官方文档关键规则

根据 Claude 官方文档 (https://platform.claude.com/docs/zh-CN/build-with-claude/extended-thinking)：

### 2.1 签名验证机制

- `signature` 字段用于验证 thinking block 是由 Claude 生成的
- 签名是**会话绑定**的，跨请求时可能失效
- 如果签名无效，API 会返回 400 错误

### 2.2 工具使用中的 Thinking 保留

> "在工具使用期间，必须将 thinking 块传回 API，并保持与从 Claude 接收时**完全相同的状态（包括签名）**"

### 2.3 Claude Opus 4.5 的新行为

- **交错思考（Interleaved Thinking）**：在工具调用之间进行思考
- **总结的思考（Summarized Thinking）**：返回思考过程的摘要
- 默认保留来自先前助手轮次的思维块

## 3. Antigravity 现有实现分析

### 3.1 核心组件

| 组件 | 文件 | 策略 |
|------|------|------|
| 消息转换器 | `message_converter.py` | 丢弃历史 thinking blocks |
| 签名编码 | `thoughtSignature_fix.py` | 将签名编码到 tool_id 中 |
| 签名恢复 | `signature_recovery.py` | 6层签名恢复策略 |
| 签名缓存 | `signature_cache.py` | 三层缓存架构 |
| 消息净化 | `sanitizer.py` | 工具链完整性检查 |

### 3.2 关键代码分析

#### message_converter.py (第 405-441 行)

```python
# [FIX 2026-01-20] 根据 Claude 官方文档，历史 thinking blocks 应该直接丢弃
# 原因：thinking signature 是「会话绑定」的，即使从缓存恢复也会被 Claude API 拒绝
# 策略：直接移除历史 thinking 内容，不尝试签名恢复
log.info(f"[MESSAGE_CONVERTER] 丢弃历史 thinking block: thinking_len={len(thinking_content)}")
```

#### thoughtSignature_fix.py

```python
# 在工具调用ID中嵌入 thoughtSignature 的分隔符
# 这使得签名能够在客户端往返传输中保留
THOUGHT_SIGNATURE_SEPARATOR = "__thought__"

def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    return f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"
```

### 3.3 占位符签名机制

```python
# 占位符签名（用于绕过验证）
SKIP_SIGNATURE_VALIDATOR = "skip_thought_signature_validator"
```

## 4. 用户假设验证

### 4.1 假设分析

| 假设部分 | 验证结果 | 说明 |
|---------|---------|------|
| "调用工具后" | ✅ 正确 | 问题确实发生在工具调用场景 |
| "cursor开启新会话" | ⚠️ 部分正确 | 更准确的说法是"跨请求时签名失效" |
| "旧签名失效" | ✅ 正确 | 签名确实是会话绑定的 |

### 4.2 更精确的问题描述

**问题的根源不是"Cursor 主动开启新会话"，而是：**

1. 工具执行过程中发生了中断（网络错误、超时等）
2. Cursor 重试时发送了不完整的历史消息
3. 历史消息中的 thinking block 签名已失效
4. Claude API 拒绝了带有失效签名的 thinking block

### 4.3 两种场景的区分

| 场景 | 签名有效性 | 正确处理 |
|------|-----------|---------|
| **同一请求内的工具循环** | ✅ 有效 | 必须保留 thinking blocks |
| **跨请求的历史消息** | ❌ 失效 | 应该丢弃 thinking blocks |

## 5. 处理策略建议

### 5.1 决策树

```
收到请求 → 检查历史消息中的 thinking blocks
              ↓
        ┌─────┴─────┐
        ↓           ↓
   有 thinking   无 thinking
        ↓           ↓
   检查签名有效性  正常处理
        ↓
   ┌────┴────┐
   ↓         ↓
 有效签名  无效/缺失签名
   ↓         ↓
 保留块    丢弃块（不尝试恢复）
```

### 5.2 核心原则

| 原则 | 说明 | 理由 |
|------|------|------|
| **签名失效则丢弃** | 不尝试恢复失效的签名 | 官方文档明确：签名是会话绑定的 |
| **丢弃而非转换** | 直接移除无效 thinking block | 转换为 text 可能导致上下文污染 |
| **工具循环内保留** | 同一请求内的工具循环必须保留 thinking | 官方文档明确要求 |
| **跨请求丢弃** | 历史请求的 thinking 应该丢弃 | 签名已失效，保留会导致 400 错误 |

### 5.3 对 Claude Sonnet 4.5 Thinking 的处理建议

1. **保持现有的"丢弃历史 thinking"策略** - 这是正确的
2. **确保工具循环内的 thinking 保留** - 通过签名编码到 tool_id 实现
3. **添加更详细的日志** - 便于诊断签名失效问题
4. **考虑添加配置选项** - 允许用户选择是否启用 thinking 模式

## 6. 与官方文档的对齐验证

| 官方文档规则 | Antigravity 实现 | 状态 |
|-------------|-----------------|------|
| 保留思考块（工具循环内） | 通过签名编码到 tool_id 实现 | ✅ |
| 签名验证 | 通过 `has_valid_thoughtsignature` 实现 | ✅ |
| 不能在轮次中间切换思考模式 | 未明确处理 | ⚠️ 需要验证 |
| 历史 thinking 处理 | 直接丢弃 | ✅ 符合实际需求 |

## 7. 结论

### 7.1 用户假设的验证结论

用户的假设**基本正确**，但需要更精确的表述：

- ❌ "Cursor 开启新会话" → ✅ "跨请求时签名失效"
- 问题的本质是**签名的会话绑定性**，而非 Cursor 的主动行为

### 7.2 Antigravity 的现有实现评估

- ✅ **正确**：丢弃历史 thinking blocks 的策略
- ✅ **正确**：签名编码到 tool_id 的机制
- ⚠️ **需要验证**：工具循环内的 thinking 保留是否完整

### 7.3 后续建议

1. 监控日志中的签名验证失败情况
2. 考虑添加更详细的诊断信息
3. 验证 Claude Sonnet 4.5 Thinking 模型的兼容性

---

**报告状态**: ✅ 已完成
**相关文档**:
- `docs/2026-01-20_Tool_Chain_Integrity_Fix_Report.md`
- `docs/2026-01-20_Thinking_Signature_Fix_Sync_Plan.md`
