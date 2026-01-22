# Cursor 工具回合 Thinking 禁用问题修复报告

**修复日期**: 2026-01-22
**修复人**: 浮浮酱 (Claude Opus 4.5)
**问题描述**: SCID架构已实现，但Cursor在工具回合依旧被迫关闭thinking，否则继续报错400
**修复方案**: 方案3 + 方案1 组合

---

## 📋 执行摘要

### 核心问题

**SCID架构虽然已实现并集成，但在工具回合存在致命缺陷**：

1. ❌ **流式回写不保留thinking块结构**：将content拼接成字符串，thinking块和签名丢失
2. ❌ **工具回合检测到问题后强制禁用thinking**：签名恢复要求严格文本匹配，容易失败
3. ❌ **权威历史中的thinking块缺失**：导致后续无法从权威历史恢复

**结果**：Cursor在工具回合时，thinking被强制禁用，无法使用extended thinking功能！

### 修复方案

**方案1**：修复流式回写逻辑，保留thinking块结构
**方案3**：在antigravity_router.py中添加从SCID权威历史恢复thinking的逻辑
**兜底措施**：保留原有的禁用thinking机制，确保不会因修复导致400错误

---

## 🔧 修复详情

### 修复1：流式回写逻辑修复（方案1）

**文件**: `src/unified_gateway_router.py`
**函数**: `_wrap_stream_with_writeback` (第4505-4690行)

#### 问题分析

**旧逻辑**：
```python
# 收集文本内容
if "content" in delta:
    content = delta["content"]
    if isinstance(content, str):
        collected_content.append(content)  # 只收集字符串
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                # 提取签名
                if block.get("type") in ("thinking", "redacted_thinking"):
                    sig = block.get("thoughtSignature") or block.get("signature")
                    if sig and len(sig) > 50:
                        last_signature = sig
                # ❌ 但是没有收集block本身！

# 构建 assistant 消息
assistant_message = {
    "role": "assistant",
    "content": "".join(collected_content)  # ❌ 拼接成字符串
}
```

**问题**：
- `collected_content` 只包含文本字符串
- thinking块的结构（type, thinking, thoughtSignature）**完全丢失**
- 最后拼接成字符串时，thinking块变成了普通文本

#### 修复内容

**新逻辑**：
```python
# 第4525-4531行：新增变量
collected_content = []  # 改为收集content blocks（保留结构）
collected_tool_calls = []
last_signature = None
last_thinking_block = None  # 保存最后一个thinking块
stream_completed = False
has_error = False
has_text_content = False  # 标记是否有文本内容

# 第4559-4587行：修改content收集逻辑
if "content" in delta:
    content = delta["content"]
    if isinstance(content, str):
        # 字符串内容：创建text block
        if content:  # 只收集非空内容
            collected_content.append({
                "type": "text",
                "text": content
            })
            has_text_content = True
    elif isinstance(content, list):
        # 列表内容：直接收集blocks
        for block in content:
            if isinstance(block, dict):
                # 收集block
                collected_content.append(block)

                # 提取thinking块和签名
                if block.get("type") in ("thinking", "redacted_thinking"):
                    sig = block.get("thoughtSignature") or block.get("signature")
                    if sig and len(sig) > 50 and sig != "skip_thought_signature_validator":
                        last_signature = sig
                        # 保存完整的thinking块
                        last_thinking_block = block.copy()
                        # 归一化签名字段
                        if "signature" in last_thinking_block and "thoughtSignature" not in last_thinking_block:
                            last_thinking_block["thoughtSignature"] = sig

# 第4605-4641行：修改assistant_message构建
# 构建 assistant 消息（保留block结构）
assistant_message = {
    "role": "assistant"
}

# 设置content（优先使用block列表，兼容旧格式）
if collected_content:
    # 合并相邻的text blocks（优化）
    merged_content = []
    pending_text = []

    for block in collected_content:
        if block.get("type") == "text":
            pending_text.append(block.get("text", ""))
        else:
            # 非text block：先flush pending text
            if pending_text:
                merged_content.append({
                    "type": "text",
                    "text": "".join(pending_text)
                })
                pending_text = []
            # 添加非text block
            merged_content.append(block)

    # flush剩余的text
    if pending_text:
        merged_content.append({
            "type": "text",
            "text": "".join(pending_text)
        })

    # 设置content为block列表
    assistant_message["content"] = merged_content
else:
    # 空内容
    assistant_message["content"] = ""
```

#### 修复效果

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| **content格式** | 字符串 | block列表 |
| **thinking块** | 丢失 | ✅ 完整保留 |
| **签名** | 只有`last_signature` | ✅ thinking块中包含`thoughtSignature` |
| **权威历史** | 无thinking块结构 | ✅ 完整的thinking块+签名 |

---

### 修复2：从SCID权威历史恢复thinking（方案3）

**文件**: `src/antigravity_router.py`
**位置**: 第2198-2245行（工具回合检测逻辑中）

#### 问题分析

**旧逻辑**：
```python
# 只使用签名缓存恢复
recovered_pair = get_recent_signature_with_text(time_window_seconds=time_window, client_type=client_type, owner_id=owner_id)

if recovered_pair:
    pair_sig, pair_text = recovered_pair
    # ⚠️ 问题：严格文本匹配
    if isinstance(pair_text, str) and pair_text.strip() == combined_lead_text:
        # 使用签名
        recovered_leading = True

if not recovered_leading:
    # ⚠️ 问题：强制禁用thinking
    disable_thinking_for_this_request = True
```

**问题**：
- 要求 `pair_text.strip() == combined_lead_text` **严格匹配**
- Cursor回放的thinking文本可能已经被**变形**（trim、换行、截断等）
- 导致匹配失败 → 签名恢复失败 → thinking被禁用！

#### 修复内容

**新逻辑**：
```python
# [FIX 2026-01-22] 方案3：优先从SCID权威历史恢复thinking块
# 如果有SCID，直接使用权威历史中的完整thinking块（包括签名）
# 这样可以避免文本匹配问题，因为权威历史中的thinking块是原始的、未变形的
scid = request_body.get("_scid") if request_body else None
if scid:
    try:
        from src.ide_compat.state_manager import ConversationStateManager
        from src.cache.signature_database import SignatureDatabase

        db = SignatureDatabase()
        state_manager = ConversationStateManager(db)
        state = state_manager.get_or_create_state(scid, client_type or "unknown")

        # 从权威历史获取最后一条assistant消息
        authoritative_history = state.authoritative_history
        last_assistant = None
        for msg in reversed(authoritative_history):
            if msg.get("role") == "assistant":
                last_assistant = msg
                break

        # 提取thinking块
        if last_assistant:
            content = last_assistant.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"):
                        thinking_text = block.get("thinking", "")
                        signature = block.get("thoughtSignature") or block.get("signature", "")

                        if thinking_text and signature and len(signature) >= MIN_SIGNATURE_LENGTH:
                            # 找到了有效的thinking块，直接使用
                            new_leading = {
                                "thought": True,
                                "text": thinking_text,
                                "thoughtSignature": signature,
                            }
                            # 替换当前parts中的thinking块
                            parts = [new_leading] + [p for p in parts if not (isinstance(p, dict) and p.get("thought") is True)]
                            contents[last_model_idx]["parts"] = parts
                            recovered_leading = True
                            log.info(
                                "[ANTIGRAVITY] Recovered leading thinking from SCID authoritative history; "
                                f"scid={scid[:20]}..., thinking_len={len(thinking_text)}, sig_len={len(signature)}"
                            )
                            break
    except Exception as e:
        log.warning(f"[ANTIGRAVITY] Failed to recover from SCID authoritative history: {e}")

# 如果SCID恢复失败，继续使用原有的签名恢复策略
recovered_pair = None
if not recovered_leading:
    try:
        # 使用客户端特定窗口（默认 5min；IDE 更长），尽量命中同一会话的最近条目
        time_window = 300
        if client_type:
            client_ttl = get_ttl_for_client(client_type)
            time_window = client_ttl // 2
        recovered_pair = get_recent_signature_with_text(time_window_seconds=time_window, client_type=client_type, owner_id=owner_id)
    except Exception as e:
        log.debug(f"[ANTIGRAVITY] get_recent_signature_with_text failed: {e}")

    if recovered_pair:
        pair_sig, pair_text = recovered_pair
        # 仅当文本严格匹配时才使用，避免跨会话/跨请求误注入导致 invalid signature
        if isinstance(pair_text, str) and pair_text.strip() == combined_lead_text:
            cached_sig = str(pair_sig or "").strip()
            if cached_sig and cached_sig != SKIP_SIGNATURE_VALIDATOR and len(cached_sig) >= MIN_SIGNATURE_LENGTH:
                new_leading = {
                    "thought": True,
                    # 关键：使用缓存中"与签名同源"的原始 thinking_text（不做额外规范化）
                    "text": pair_text,
                    "thoughtSignature": cached_sig,
                }
                parts = [new_leading] + parts[leading_thought_count:]
                contents[last_model_idx]["parts"] = parts
                recovered_leading = True
                log.info(
                    "[ANTIGRAVITY] Recovered leading thoughtSignature (paired replay) for tool_use continuation; "
                    "keeping thinking enabled"
                )

if not recovered_leading:
    # 无法满足 Claude 的工具回合硬规则：仅对本次请求禁用 thinking，避免 400
    disable_thinking_for_this_request = True
```

#### 修复效果

**恢复策略优先级**：
1. **优先**：从SCID权威历史恢复（方案3）- 避免文本匹配问题
2. **次选**：从签名缓存恢复（原有策略）- 严格文本匹配
3. **兜底**：禁用thinking（保留安全机制）- 避免400错误

---

### 修复3：传递SCID到antigravity_router

**文件**: `src/unified_gateway_router.py`
**位置**: 第4958-4962行

#### 修复内容

```python
# ================================================================
# [SCID] Step 3: 添加 SCID 到请求头和请求体（供下游使用）
# ================================================================
if scid:
    headers["x-ag-conversation-id"] = scid
    # [FIX 2026-01-22] 将SCID添加到请求体中，供antigravity_router使用
    # antigravity_router需要SCID来从权威历史恢复thinking块
    body["_scid"] = scid
```

#### 修复效果

- ✅ SCID通过请求体传递到`antigravity_router.py`
- ✅ `antigravity_router.py`可以访问SCID并从权威历史恢复thinking块

---

## 📊 修复验证

### 功能验证检查点

- [x] **流式回写保留thinking块结构**：`collected_content`收集block列表
- [x] **权威历史包含完整thinking块**：`assistant_message.content`为block列表
- [x] **SCID传递到antigravity_router**：`body["_scid"]`传递
- [x] **从权威历史恢复thinking块**：优先使用SCID权威历史
- [x] **兜底措施保留**：恢复失败时仍然禁用thinking

### 错误消除验证

- [ ] 不再出现 `Invalid signature in thinking block`
- [ ] 不再出现 `thinking disabled but thinking block present`
- [ ] 不再出现 `Claude-family tool_use continuation requires leading thinking+signature, but recovery failed`
- [ ] Cursor工具调用后对话不再中断

### 日志验证

**期望看到的日志**：
- ✅ `[SCID] Streaming writeback complete: ... content_blocks=N, has_thinking_block=True, has_signature=True`
- ✅ `[ANTIGRAVITY] Recovered leading thinking from SCID authoritative history; scid=...`
- ✅ `[SCID] Merged messages with authoritative history`

**不应再看到的日志**：
- ❌ `[ANTIGRAVITY] ... disabling thinking for THIS request only to avoid 400`

---

## 🎯 修复原理

### 问题链条（修复前）

```
Cursor发送工具回合请求
  ↓
SCID架构：使用权威历史替换客户端回放
  ↓
❌ 问题1：流式回写不保留thinking块结构
  ↓
❌ 问题2：权威历史中的thinking块缺失
  ↓
antigravity_router.py：检测到工具回合 + 没有有效leading thinking
  ↓
❌ 问题3：尝试从缓存恢复签名，但要求严格文本匹配
  ↓
❌ 问题4：匹配失败（thinking文本可能被变形）
  ↓
❌ 问题5：强制禁用thinking（disable_thinking_for_this_request = True）
  ↓
结果：Cursor在工具回合无法使用extended thinking
```

### 修复链条（修复后）

```
Cursor发送工具回合请求
  ↓
SCID架构：使用权威历史替换客户端回放
  ↓
✅ 修复1：流式回写保留thinking块结构
  ↓
✅ 修复2：权威历史包含完整thinking块+签名
  ↓
antigravity_router.py：检测到工具回合 + 没有有效leading thinking
  ↓
✅ 修复3：SCID传递到antigravity_router
  ↓
✅ 修复4：从SCID权威历史恢复完整thinking块（避免文本匹配）
  ↓
✅ 恢复成功：thinking块+签名完整，继续使用extended thinking
  ↓
✅ 兜底措施：如果恢复失败，仍然禁用thinking避免400错误
  ↓
结果：Cursor在工具回合可以正常使用extended thinking
```

---

## 🔍 关键代码位置

| 修复 | 文件 | 行号 | 说明 |
|------|------|------|------|
| **修复1** | `src/unified_gateway_router.py` | 4525-4531 | 新增变量（last_thinking_block等） |
| **修复1** | `src/unified_gateway_router.py` | 4559-4587 | 修改content收集逻辑（保留block结构） |
| **修复1** | `src/unified_gateway_router.py` | 4605-4641 | 修改assistant_message构建（使用block列表） |
| **修复1** | `src/unified_gateway_router.py` | 4669-4687 | 增强日志（添加content_blocks和has_thinking_block） |
| **修复2** | `src/antigravity_router.py` | 2198-2245 | 从SCID权威历史恢复thinking块 |
| **修复3** | `src/unified_gateway_router.py` | 4958-4962 | 将SCID添加到请求体 |

---

## 📝 总结

### 修复成果

1. ✅ **修复了流式回写逻辑**：保留thinking块结构，权威历史完整
2. ✅ **实现了从权威历史恢复**：优先使用SCID权威历史，避免文本匹配问题
3. ✅ **保留了兜底措施**：恢复失败时仍然禁用thinking，确保不会400错误
4. ✅ **完善了日志输出**：便于问题追踪和验证

### 修复复杂度

**中等复杂度**：
- 修改了3个关键位置
- 涉及流式响应处理、状态管理、签名恢复等多个模块
- 需要理解SCID架构和工具回合逻辑

### 下一步行动

1. **测试验证**：测试Cursor工具+思考调用场景
2. **日志分析**：检查修复后的日志输出，确认thinking块恢复成功
3. **性能监控**：观察SCID权威历史恢复的性能影响
4. **文档更新**：更新SCID架构文档，说明工具回合的特殊处理

---

**报告生成时间**: 2026-01-22
**修复验证**: 待测试
**修复范围**: SCID架构、流式回写、工具回合处理、签名恢复逻辑

---

## 🔧 附录：修复前后对比

### 流式回写对比

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| **collected_content** | `["text1", "text2", ...]` | `[{type:"text", text:"..."}, {type:"thinking", thinking:"...", thoughtSignature:"..."}, ...]` |
| **assistant_message.content** | `"text1text2..."` | `[{type:"text", text:"..."}, {type:"thinking", thinking:"...", thoughtSignature:"..."}, ...]` |
| **thinking块保留** | ❌ 丢失 | ✅ 完整保留 |
| **签名保留** | ❌ 只有last_signature | ✅ thinking块中包含thoughtSignature |

### 工具回合恢复对比

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| **恢复策略** | 只使用签名缓存 | 优先SCID权威历史，次选签名缓存 |
| **文本匹配** | 严格匹配 | SCID恢复不需要匹配 |
| **恢复成功率** | 低（容易因文本变形失败） | 高（使用原始thinking块） |
| **兜底措施** | 禁用thinking | ✅ 保留 |

---

**浮浮酱的小结**: 主人你看喵～ (★ω★) 浮浮酱已经完成了**方案3+方案1**的所有修复！核心思想是：

1. **修复流式回写**：保留thinking块结构，确保权威历史完整 ✅
2. **优先权威历史**：直接使用SCID权威历史的thinking块，避免文本匹配问题 ✅
3. **保留兜底措施**：如果恢复失败，仍然禁用thinking避免400错误 ✅

现在需要测试验证修复效果喵～ ฅ'ω'ฅ
