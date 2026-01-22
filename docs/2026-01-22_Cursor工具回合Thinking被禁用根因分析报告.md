# Cursor 工具回合 Thinking 被禁用根因分析报告

**分析日期**: 2026-01-22
**分析人**: 浮浮酱 (Claude Opus 4.5)
**问题描述**: SCID架构早就实现了，但Cursor在工具回合依旧被迫关闭thinking，否则继续报错400
**分析工具**: ACE (Augment Context Engine) + 代码审查

---

## 📋 执行摘要

### 核心发现

**❌ 关键问题：SCID架构虽然已集成，但在工具回合有致命缺陷！**

浮浮酱通过ACE扫描发现了**两个关键问题**：

1. **问题1**: `antigravity_router.py` 中的工具回合检测逻辑**强制禁用thinking**
2. **问题2**: SCID架构的权威历史替换**在工具回合时未生效**

**结果**: Cursor在工具回合时，thinking被强制禁用，导致无法使用extended thinking功能！ (￣^￣)

---

## 🔍 问题1：工具回合强制禁用Thinking

### 1.1 问题代码位置

**文件**: `src/antigravity_router.py`
**行号**: 528-646

### 1.2 问题代码分析

```python
# [FIX 2026-01-21] 跨模型 thinking 块隔离（OpenAI /chat/completions 路径）
#
# 目标：
# - Claude：任何 thought=True 的 part 必须带有效 thoughtSignature，否则会 400
#   - 特别是"前思考后工具"（tool_use continuation）场景：最后一条 assistant/model 的首个 part 必须是 thinking+signature
# - Gemini：不强制 thoughtSignature（部分实现会使用 skip sentinel）
#
# 策略（颗粒度更细）：
# 1) 若检测到 Claude + tool_use continuation：
#    - 先尝试从权威历史/缓存回填 leading thoughtSignature
#    - 失败则"仅对本次请求"禁用 thinking，并重建 contents（不影响后续轮次重新启用 thinking）
# 2) 非 tool_use continuation：对 Claude 的无签名 thought 降级为普通 text part 以避免 400

# ... 省略部分代码 ...

# 1) Claude + tool_use continuation：优先保证"leading thinking+signature"规则
if enable_thinking and target_family == "claude" and contents:
    last_model_idx = -1
    # 有些路径 role 可能是 "assistant" 而不是 "model"，都需要纳入检查
    for i in range(len(contents) - 1, -1, -1):
        if contents[i].get("role") in ("model", "assistant"):
            last_model_idx = i
            break

    if last_model_idx >= 0:
        parts = contents[last_model_idx].get("parts", [])
        if isinstance(parts, list) and _has_tool_use(parts):
            tool_use_continuation = True
            if _has_valid_leading_thought(parts):
                pass
            else:
                # [FIX 2026-01-21] tool_use continuation 必须以"完整 thinking 块"开头：
                # Claude 校验 signature 时要求 signature 对应"该 thinking 块全文"。
                # 但流式/中间层可能把 thinking 拆成多个 thought=true 分片（每片没有 signature）。
                # 若仅用 parts[0].text 回填 signature，会导致签名与全文不匹配 → 400 Invalid signature。
                #
                # 策略：把连续的 leading thought 分片合并为一个 thought part，然后按合并后的全文回填 signature。
                recovered_leading = False

                # ... 省略签名恢复逻辑 ...

                if not recovered_leading:
                    # ⚠️ 关键问题：无法满足 Claude 的工具回合硬规则：仅对本次请求禁用 thinking，避免 400
                    disable_thinking_for_this_request = True

if disable_thinking_for_this_request:
    log.warning(
        "[ANTIGRAVITY] Claude-family tool_use continuation requires leading thinking+signature, but recovery failed; "
        "disabling thinking for THIS request only to avoid 400"
    )
    # ⚠️ 关键问题：强制禁用thinking！
    enable_thinking = False
    messages = strip_thinking_from_openai_messages(messages)
    contents = openai_messages_to_antigravity_contents(
        messages,
        enable_thinking=False,  # ⚠️ 强制禁用！
        tools=tools,
        recommend_sequential_thinking=recommend_sequential
    )
```

### 1.3 问题分析

**问题根因**：

1. **检测到工具回合** (`tool_use_continuation = True`)
2. **检测到没有有效的leading thinking** (`not _has_valid_leading_thought(parts)`)
3. **尝试恢复签名失败** (`not recovered_leading`)
4. **强制禁用thinking** (`disable_thinking_for_this_request = True`)

**为什么会失败**？

根据代码分析，签名恢复失败的原因是：

```python
# 从缓存恢复签名
from src.signature_cache import get_last_signature_with_text
recovered_pair = get_last_signature_with_text()

if recovered_pair:
    pair_sig, pair_text = recovered_pair
    # ⚠️ 关键问题：严格文本匹配！
    if isinstance(pair_text, str) and pair_text.strip() == combined_lead_text:
        # 只有文本严格匹配时才使用
        # ...
        recovered_leading = True
```

**问题**：
- 要求 `pair_text.strip() == combined_lead_text` **严格匹配**
- 但Cursor回放的thinking文本可能已经被**变形**（trim、换行、截断等）
- 导致匹配失败 → 签名恢复失败 → thinking被禁用！

---

## 🔍 问题2：SCID权威历史替换未生效

### 2.1 问题代码位置

**文件**: `src/unified_gateway_router.py`
**行号**: 4745-4756

### 2.2 问题代码分析

```python
# 如果有 SCID，尝试获取权威历史和最后签名
if scid and state_manager:
    state = state_manager.get_or_create_state(scid, client_info.client_type.value)
    last_signature = state.last_signature

    # ⚠️ 关键问题：使用权威历史合并客户端消息
    client_messages = body.get("messages", [])
    merged_messages = state_manager.merge_with_client_history(scid, client_messages)

    if merged_messages != client_messages:
        log.info(f"[SCID] Merged messages with authoritative history: {len(client_messages)} -> {len(merged_messages)}", tag="GATEWAY")
        body["messages"] = merged_messages
```

### 2.3 问题分析

**问题根因**：

1. **SCID架构确实在使用权威历史**
2. **但权威历史中的thinking块可能也没有有效签名**
3. **导致后续在 `antigravity_router.py` 中仍然检测到"没有有效的leading thinking"**
4. **最终仍然被强制禁用thinking**

**为什么权威历史中的thinking块没有有效签名**？

可能的原因：

1. **权威历史保存时，thinking块的签名就已经丢失**
2. **或者签名保存了，但在恢复时没有正确恢复**
3. **或者签名恢复了，但在后续处理中被清理掉了**

---

## 🎯 根因总结

### 核心问题链

```
Cursor发送工具回合请求
  ↓
SCID架构：使用权威历史替换客户端回放
  ↓
⚠️ 问题1：权威历史中的thinking块可能没有有效签名
  ↓
antigravity_router.py：检测到工具回合 + 没有有效leading thinking
  ↓
⚠️ 问题2：尝试从缓存恢复签名，但要求严格文本匹配
  ↓
⚠️ 问题3：匹配失败（thinking文本可能被变形）
  ↓
⚠️ 问题4：强制禁用thinking（disable_thinking_for_this_request = True）
  ↓
结果：Cursor在工具回合无法使用extended thinking
```

### 关键矛盾

**SCID架构的设计目标**：
- 使用权威历史替换客户端回放，避免thinking文本变形导致签名失效

**实际问题**：
1. **权威历史中的thinking块也可能没有有效签名**（问题1）
2. **签名恢复要求严格文本匹配**（问题2）
3. **工具回合检测到问题后强制禁用thinking**（问题3）

**结果**：SCID架构虽然实现了，但在工具回合**没有真正解决问题**！ (￣^￣)

---

## 💡 解决方案分析

### 方案1：修复权威历史中的签名保存

**思路**：确保权威历史保存时，thinking块的签名被正确保存

**实现**：
1. 检查 `ConversationStateManager.update_authoritative_history()` 方法
2. 确保响应中的thinking块和签名被完整保存
3. 确保保存时不会丢失签名

**优点**：
- 从源头解决问题
- 权威历史中有有效签名，后续恢复就不会失败

**缺点**：
- 需要检查整个保存流程
- 可能涉及多个模块

### 方案2：放宽签名恢复的匹配条件

**思路**：不要求严格文本匹配，使用模糊匹配或其他策略

**实现**：
```python
# 当前逻辑（严格匹配）
if isinstance(pair_text, str) and pair_text.strip() == combined_lead_text:
    # 使用签名

# 修改为模糊匹配
if isinstance(pair_text, str):
    # 1. 规范化文本（移除多余空格、统一换行符等）
    normalized_pair = normalize_thinking_text(pair_text)
    normalized_combined = normalize_thinking_text(combined_lead_text)

    # 2. 模糊匹配（允许一定程度的差异）
    if normalized_pair == normalized_combined:
        # 使用签名
    # 或者使用相似度匹配
    elif similarity(normalized_pair, normalized_combined) > 0.95:
        # 使用签名
```

**优点**：
- 提高签名恢复成功率
- 减少thinking被禁用的情况

**缺点**：
- 可能导致签名与文本不匹配（仍然会400）
- 需要仔细设计匹配策略

### 方案3：工具回合时使用权威历史的完整thinking块

**思路**：在工具回合时，直接使用权威历史中的完整thinking块（包括签名），而不是尝试恢复

**实现**：
```python
# 在 antigravity_router.py 中
if tool_use_continuation:
    # 1. 检查是否有SCID
    scid = headers.get("x-ag-conversation-id")

    if scid:
        # 2. 从权威历史获取完整的thinking块
        state_manager = ConversationStateManager()
        state = state_manager.get_or_create_state(scid, client_type)
        authoritative_history = state.authoritative_history

        # 3. 找到最后一条assistant消息
        last_assistant = None
        for msg in reversed(authoritative_history):
            if msg.get("role") == "assistant":
                last_assistant = msg
                break

        # 4. 如果有thinking块，直接使用
        if last_assistant:
            content = last_assistant.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "thinking" and block.get("signature"):
                        # 找到了有效的thinking块，直接使用
                        # 替换当前contents中的thinking块
                        # ...
                        recovered_leading = True
                        break
```

**优点**：
- 直接使用权威历史，避免文本匹配问题
- 符合SCID架构的设计思想

**缺点**：
- 需要在 `antigravity_router.py` 中访问SCID状态管理器
- 增加了模块间的耦合

### 方案4：在Sanitizer中处理工具回合

**思路**：将工具回合的thinking恢复逻辑移到 `AnthropicSanitizer` 中

**实现**：
```python
# 在 AnthropicSanitizer.sanitize_messages() 中
def sanitize_messages(self, messages, thinking_enabled, session_id, last_thought_signature, owner_id):
    # 1. 检测是否是工具回合
    is_tool_continuation = self._detect_tool_continuation(messages)

    if is_tool_continuation:
        # 2. 从权威历史恢复thinking块
        if session_id and self.state_manager:
            state = self.state_manager.get_or_create_state(session_id, "cursor")
            authoritative_history = state.authoritative_history

            # 3. 找到最后一条assistant消息的thinking块
            last_thinking_block = self._extract_last_thinking_from_history(authoritative_history)

            # 4. 替换当前消息中的thinking块
            if last_thinking_block:
                messages = self._replace_thinking_block(messages, last_thinking_block)

    # 5. 继续正常的sanitize流程
    # ...
```

**优点**：
- 集中处理thinking相关逻辑
- 不需要修改 `antigravity_router.py`

**缺点**：
- Sanitizer的职责变得更复杂
- 需要访问权威历史

---

## 🚀 推荐方案

### 推荐：方案3 + 方案1 组合

**理由**：

1. **方案3**：在工具回合时直接使用权威历史的完整thinking块
   - 符合SCID架构的设计思想
   - 避免文本匹配问题
   - 直接解决工具回合thinking被禁用的问题

2. **方案1**：同时修复权威历史中的签名保存
   - 从源头确保权威历史的质量
   - 避免权威历史中也没有签名的问题

### 实现步骤

#### Step 1：修复权威历史中的签名保存

**位置**: `src/ide_compat/state_manager.py`

**检查点**：
1. `update_authoritative_history()` 方法是否正确保存thinking块
2. 响应处理时是否提取了thinking块的签名
3. 保存时是否保留了签名字段

#### Step 2：在antigravity_router.py中使用权威历史

**位置**: `src/antigravity_router.py` (行号: 528-646)

**修改逻辑**：
```python
# 在工具回合检测逻辑中
if tool_use_continuation:
    if not _has_valid_leading_thought(parts):
        # 1. 检查是否有SCID
        scid = request_body.get("_scid") if request_body else None

        if scid:
            # 2. 尝试从权威历史恢复
            try:
                from src.ide_compat.state_manager import ConversationStateManager
                from src.cache.signature_database import SignatureDatabase

                db = SignatureDatabase()
                state_manager = ConversationStateManager(db)
                state = state_manager.get_or_create_state(scid, client_type)

                # 3. 从权威历史获取最后一条assistant消息
                authoritative_history = state.authoritative_history
                last_assistant = None
                for msg in reversed(authoritative_history):
                    if msg.get("role") == "assistant":
                        last_assistant = msg
                        break

                # 4. 提取thinking块
                if last_assistant:
                    content = last_assistant.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if block.get("type") == "thinking":
                                thinking_text = block.get("thinking", "")
                                signature = block.get("signature", "")

                                if thinking_text and signature and len(signature) >= MIN_SIGNATURE_LENGTH:
                                    # 5. 替换当前contents中的thinking块
                                    new_leading = {
                                        "thought": True,
                                        "text": thinking_text,
                                        "thoughtSignature": signature,
                                    }
                                    parts = [new_leading] + [p for p in parts if not (isinstance(p, dict) and p.get("thought") is True)]
                                    contents[last_model_idx]["parts"] = parts
                                    recovered_leading = True
                                    log.info("[ANTIGRAVITY] Recovered leading thinking from SCID authoritative history")
                                    break
            except Exception as e:
                log.warning(f"[ANTIGRAVITY] Failed to recover from SCID authoritative history: {e}")

        # 6. 如果仍然没有恢复，才禁用thinking
        if not recovered_leading:
            disable_thinking_for_this_request = True
```

#### Step 3：传递SCID到antigravity_router

**位置**: `src/unified_gateway_router.py`

**修改逻辑**：
```python
# 在调用route_request_with_fallback之前
if scid:
    # 将SCID添加到请求体中，供antigravity_router使用
    body["_scid"] = scid
```

---

## 📊 验证检查点

### 功能验证

- [ ] 权威历史中的thinking块包含有效签名
- [ ] 工具回合时能从权威历史恢复thinking块
- [ ] 工具回合时thinking不再被禁用
- [ ] Cursor工具调用后对话不再中断

### 错误消除验证

- [ ] 不再出现 `Invalid signature in thinking block`
- [ ] 不再出现 `thinking disabled but thinking block present`
- [ ] 不再出现 `Claude-family tool_use continuation requires leading thinking+signature, but recovery failed`

### 日志验证

- [ ] 看到日志：`[ANTIGRAVITY] Recovered leading thinking from SCID authoritative history`
- [ ] 看到日志：`[SCID] Merged messages with authoritative history`
- [ ] 不再看到日志：`[ANTIGRAVITY] ... disabling thinking for THIS request only to avoid 400`

---

## 📝 结论

### 当前状态

**SCID架构虽然已实现并集成，但在工具回合存在致命缺陷**：

1. ❌ 权威历史中的thinking块可能没有有效签名
2. ❌ 签名恢复要求严格文本匹配，容易失败
3. ❌ 工具回合检测到问题后强制禁用thinking

**结果**：Cursor在工具回合时，thinking被强制禁用，无法使用extended thinking功能！

### 修复必要性

**🔴 高优先级**：必须立即修复

- Cursor工具调用是核心功能
- Extended thinking是重要特性
- 当前实现导致功能完全不可用

### 修复复杂度

**中等复杂度**：

- 需要修改2-3个文件
- 需要理解SCID架构和工具回合逻辑
- 需要仔细测试各种场景

---

## 🚀 下一步行动

1. **立即修复**：按照推荐方案（方案3 + 方案1）修复
2. **测试验证**：测试Cursor工具+思考调用场景
3. **日志分析**：检查修复后的日志输出
4. **文档更新**：更新SCID架构文档，说明工具回合的特殊处理

---

**报告生成时间**: 2026-01-22
**分析工具**: ACE (Augment Context Engine) + 代码审查
**分析范围**: SCID架构、工具回合处理、签名恢复逻辑

---

## 🔧 附录：关键代码位置

| 问题 | 文件 | 行号 | 说明 |
|------|------|------|------|
| 工具回合强制禁用thinking | `src/antigravity_router.py` | 528-646 | 检测到工具回合且签名恢复失败时禁用thinking |
| 签名严格匹配 | `src/antigravity_router.py` | 610-628 | 要求thinking文本严格匹配才使用签名 |
| SCID权威历史替换 | `src/unified_gateway_router.py` | 4745-4756 | 使用权威历史合并客户端消息 |
| Sanitizer调用 | `src/unified_gateway_router.py` | 4851-4856 | 调用AnthropicSanitizer净化消息 |

---

**浮浮酱的小结**: 主人你说得对喵！SCID架构虽然实现了，但在工具回合有致命缺陷！(￣^￣) 关键问题是签名恢复要求严格文本匹配，而Cursor回放的thinking文本可能已经变形，导致匹配失败 → thinking被禁用！需要修复权威历史的签名保存，并在工具回合时直接使用权威历史的完整thinking块喵～ ฅ'ω'ฅ
