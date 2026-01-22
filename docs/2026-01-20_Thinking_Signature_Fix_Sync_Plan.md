# Thinking Signature 修复同步方案：旧网关 → 新网关

> **日期**: 2026-01-20  
> **作者**: 浮浮酱 (Claude Opus 4.5)  
> **目标**: 将 thinking signature 相关修复从旧网关架构同步到新网关架构

---

## 架构对照

| 功能 | 旧网关 | 新网关 (`src/gateway/`) |
|------|--------|------------------------|
| 请求路由 | `antigravity_router.py` | `backends/antigravity.py` |
| 消息转换 | `converters/message_converter.py` | `normalization.py` |
| 工具循环恢复 | `converters/tool_loop_recovery.py` | `tool_loop.py` |
| 流式处理 | `antigravity_router.py` 内嵌 | `sse/*.py` |
| 消息净化 | `ide_compat/sanitizer.py` | *待确认* |

---

## 同步工作清单

### ✅ 第一优先级：核心修复

#### 1. `src/gateway/backends/antigravity.py`

**检查项**:
- [ ] 是否存在 `get_cached_signature()` 或 `get_last_signature_with_text()` 调用
- [ ] 是否存在 thinking block 恢复/注入逻辑
- [ ] 是否存在基于历史签名禁用 thinking 的逻辑

**修复原则**:
```python
# ❌ 错误做法
cached_sig = get_cached_signature(thinking_text)
if cached_sig:
    parts.append({"thought": True, "thoughtSignature": cached_sig})

# ✅ 正确做法
log.info(f"Dropping historical thinking block: len={len(thinking_text)}")
# 不添加到 parts，直接跳过
```

---

#### 2. `src/gateway/normalization.py`

**检查项**:
- [ ] `normalize_messages()` 函数中是否处理 thinking blocks
- [ ] 是否尝试恢复 `thoughtSignature`
- [ ] 历史 assistant 消息中的 thinking blocks 如何处理

**修复原则**:
```python
# 处理 assistant 消息时
if block.get("type") in ("thinking", "redacted_thinking"):
    # ❌ 不要尝试恢复签名
    # ✅ 直接跳过/删除历史 thinking blocks
    log.debug(f"Skipping historical thinking block")
    continue
```

---

#### 3. `src/gateway/tool_loop.py`

**检查项**:
- [ ] 是否存在 `get_last_signature_with_text()` 调用
- [ ] 是否在 assistant 消息中注入 thinking block
- [ ] 工具循环恢复逻辑是否依赖签名缓存

**修复原则**:
```python
# ❌ 错误做法：注入 thinking block
thinking_block = {"type": "thinking", "signature": cached_sig}
content.insert(0, thinking_block)

# ✅ 正确做法：不注入，让请求正常发送
logger.info("Skipping thinking block injection (session-bound signatures)")
```

---

### 📋 第二优先级：流式处理

#### 4. `src/gateway/sse/*.py`

**检查项**:
- [ ] 流式响应中如何提取 `thoughtSignature`
- [ ] 签名缓存逻辑是否正确（只缓存当前响应的签名）
- [ ] 是否存在历史签名复用

**当前正确的做法（保持）**:
```python
# 从当前响应提取签名并缓存（这是正确的）
if part.get("thoughtSignature"):
    state["current_thinking_signature"] = signature
    cache_signature(thinking_text, signature)  # 仅用于当前会话
```

---

## 实施步骤

### Step 1: 代码审查

```bash
# 在新网关目录搜索相关代码
cd f:/antigravity2api/gcli2api/src/gateway
grep -rn "get_cached_signature\|get_last_signature" .
grep -rn "thoughtSignature\|thought.*True" .
grep -rn "thinking.*block\|redacted_thinking" .
```

### Step 2: 逐文件修复

1. 打开 `backends/antigravity.py`，定位所有 thinking 相关逻辑
2. 打开 `normalization.py`，检查消息规范化中的 thinking 处理
3. 打开 `tool_loop.py`，移除 thinking block 注入

### Step 3: 测试验证

```bash
# 重启网关
python web.py

# 测试场景
1. 纯 thinking 请求 → 应正常返回 <think> 内容
2. Thinking + Tool Call → 不应出现 400 错误
3. 多轮对话 → 每轮正常生成新 thinking
```

---

## 关键代码模式

### 需要删除的模式

```python
# Pattern 1: 从缓存恢复签名
from src.signature_cache import get_cached_signature
cached_sig = get_cached_signature(thinking_text)
if cached_sig:
    block["thoughtSignature"] = cached_sig

# Pattern 2: 用 fallback 注入 thinking block
from src.signature_cache import get_last_signature_with_text
result = get_last_signature_with_text()
if result:
    sig, text = result
    parts.insert(0, {"thought": True, "thoughtSignature": sig})

# Pattern 3: 基于签名有效性禁用 thinking
if not signature_valid:
    enable_thinking = False
```

### 应该保留的模式

```python
# Pattern A: 从当前响应提取并缓存签名（用于当前会话）
if part.get("thoughtSignature"):
    cache_signature(current_thinking_text, signature)

# Pattern B: Sanitizer 对最新消息的签名恢复（有保护机制）
if is_latest_message:
    recovery_result = recover_signature(...)
    if not recovery_result.success:
        # 降级为 text，不报错
```

---

## 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| 新网关没有相关代码 | 低 | 确认后无需修改 |
| 新网关有不同实现 | 中 | 需要仔细分析并适配 |
| 修改引入新 bug | 中 | 充分测试 thinking + tool 场景 |
| 与其他功能冲突 | 低 | 修复仅涉及 thinking 处理 |

---

## 总结

本文档定义了将 thinking signature 修复从旧网关同步到新网关的完整方案。核心原则是：

> **任何历史 thinking blocks 都应该被删除，不尝试恢复签名。**
> **签名缓存仅用于当前会话内的签名提取和日志记录。**

执行此方案前，请先用 grep 搜索确认新网关中是否存在需要修复的代码。
