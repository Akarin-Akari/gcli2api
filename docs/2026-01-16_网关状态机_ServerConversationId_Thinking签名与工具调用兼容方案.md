# 网关状态机：`server_conversation_id` 驱动的 Thinking/Signature 与工具调用兼容方案（Cursor / augment-BYOK）

> 目的：把“历史消息回放的可靠性”从客户端（Cursor/augment-BYOK 等 IDE）收口到网关侧，使网关成为 **权威状态机**，从根源消除 `Invalid signature in thinking block` 引发的 400 中断，同时与现有“三层缓存 + 六层签名恢复”对齐并强化。

---

## 0. 结论先行（我们在讨论中达成的共识）

1. Anthropic `thinking.signature` 与 **特定的 thinking 文本字节序列强绑定**。  
   
   - `thinking` 文本任何变化（trim/换行/截断/标签剥离/重排）都会让旧 `signature` 立即失效。  
   - 网关 **无法生成** 新的合法 `signature`（只能复用历史真实配对的 `(thinking_text, signature)`）。

2. Cursor / augment-BYOK 等客户端在“回放历史 + 工具调用”链路中，存在大量不可控的结构/文本变形点：  
   
   - 丢弃未知字段（`signature`/`thoughtSignature`/`cache_control` 等）；  
   - 重序列化/归一化（`\r\n`↔`\n`、trim、合并块、顺序调整）；  
   - 截断/摘要（为了 UI/性能）；  
   - tool_use/tool_result 与 assistant 内容的重新拼接。  
     这些都可能造成“签名仍在/或被恢复出来，但 thinking 文本已变形”，最终触发 400。

3. 现有实现已覆盖关键修复点，但仍存在“逻辑判定 OK、payload 未修复/清理”的缺陷路径：  
   
   - 典型表现：使用 `get_last_signature()` 之类“仅签名存在性”的 fallback 把校验状态置为 True，却没有对 messages 中的无效 thinking block 做删除/降级/替换；导致下游仍 400。

4. **最稳定的策略**是把“可验证则有状态，不可验证则无状态（或弱无状态）”落地成网关统一规则：  
   
   - **可验证**：只使用缓存/存档中能证明匹配的 `(thinking_text, signature)`；  
   - **不可验证**：绝不把 thinking 当 `thinking` block 发下游（改为 text 或删除），并同步禁用 thinkingConfig。

---

## 1. 相关背景（问题复盘）

现象：

- 在一轮“前有思考后有工具调用”的对话中，工具调用后会出现“调用完工具对话结束/中断”的现象；
- 下游返回错误常见为：
  - `messages.N.content.M: Invalid signature in thinking block`
  - 或 thinking 模式约束错误（例如 thinking disabled 时仍带 thinking block、或 assistant 第一块类型不符合约束）。

根因类型（讨论结论）：

- **签名与文本错配**：fallback 使用了不匹配的 `signature` + `thinking_text` 组合（或 trusting message_signature 导致错配）。  
- **客户端回放不可信**：Cursor/IDE 对历史的再编码导致 thinking 文本变形（签名再怎么恢复也无法通过验签）。  
- **网关仅“判定有效”但未“修复 payload”**：校验阶段把 `enable_thinking/all_thinking_valid` 状态放过，但请求仍携带坏块。

---

## 2. 现状梳理（与既有“三层缓存 + 六层恢复”对齐）

### 2.1 三层缓存（概念归纳）

> 注意：以下是本项目讨论语义层面的“三层”，不强绑定具体文件结构；实现可跨 `src/signature_cache.py` 与 `src/cache/*`。

- Layer 1：tool_id -> signature（应对客户端丢字段，尤其 tool_use）  
- Layer 2：thinking_text -> signature（严格绑定，命中才可回放）  
- Layer 3：session / conversation -> (signature, thinking_text)（用于“成对 fallback”，避免错配）

### 2.2 六层恢复（讨论对齐版）

统一原则：**任何“恢复”最终必须回到两个不变量**：

- thinking block 只有在 `(thinking_text, signature)` 能证明匹配时才允许回放；
- tool_use/tool_result 的链条必须保持一致（必要时可降级为“文本化工具结果摘要”）。

常见层级（示意）：

1) 客户端显式提供且可验证（极少可信，除非来自网关权威回放或严格验证）  
2) 上下文持有（例如本次请求/本次流中的 state）  
3) 编码 tool_id 解码得到的 signature（自研优势）  
4) Session Cache：`(sig, text)` 成对取回  
5) Tool Cache：tool_id -> signature  
6) Last Cache：最近条目（**必须成对**，禁止 signature-only）

---

## 3. 方案总览：模仿 `augment_compat` 的“网关特殊兼容层 + server_conversation_id”

### 3.1 目标

- 彻底消除：
  - 由于客户端回放变形导致的 `Invalid signature in thinking block`
  - 工具调用回合引发的后续对话中断
- 保留能力：
  - 工具调用链稳定（tool_use/tool_result 不中断）
  - 在“可验证”条件下继续支持 extended thinking
- 兼容性：
  - 不要求 Cursor/augment-BYOK 可靠地回放 thinking/signature
  - 在客户端无保活/跨进程/重启时，仍可通过 server_conversation_id 维持状态（若客户端愿意携带）

### 3.2 核心思路

引入 `server_conversation_id`（下称 **SCID**），并把网关升级为“权威状态机”：

- 网关对每个新会话生成 SCID；
- 网关侧存储“权威历史”（包含 **原始配对的 thinking 文本与 signature**、工具调用结构、必要的上下文字段）；
- 后续请求如果携带 SCID：
  - 网关优先使用服务端权威历史重建下游请求；
  - 客户端回放的 assistant/thinking/tool 历史视为 **不可信输入**，仅作为可选参考或直接忽略；
- 如果请求不携带 SCID（或 SCID 未命中）：
  - 走“无状态兼容”路径：对输入做严格 sanitize，遇到无法验证的 thinking 直接降级为 text/删除，并禁用 thinkingConfig。

---

## 4. 协议与不变量（必须实现的对齐规则）

### 4.1 Thinking 不变量

1) **禁止 signature-only fallback**  
   仅凭 “最近 signature 存在” 不能让 thinking 保持启用；任何 fallback 必须成对返回 `(signature, thinking_text)`。

2) **禁止信任无法验证的 message_signature**  
   缓存 miss 时，优先降级 thinking block；除非该 signature 能被证明来自网关权威历史且 thinking 文本未变。

3) **thinkingConfig 与 payload 强一致**  
   若最终 payload 中存在 thinking block，则 thinkingConfig 必须被下发且满足下游约束；反之如果不下发 thinkingConfig，则必须确保 payload 中不存在 thinking block（否则会触发另一类 400）。

### 4.2 工具调用不变量

1) tool_use/tool_result 必须成链（id 对齐）；  
2) 在工具回合（或检测到回放不可信时），允许进入“弱无状态”：
   - 删除/降级 thinking blocks；
   - 保留工具结果语义（必要时把 tool_result 文本化成摘要 text，作为上下文注入）。

---

## 5. 数据模型（建议）

### 5.1 SCID 传递方式

建议同时支持：

- Request Header：`X-AG-Conversation-Id: <scid>`
- Response Header：`X-AG-Conversation-Id: <scid>`
- Response JSON 元信息（可选）：`{"_gateway": {"conversation_id": "<scid>"}}`

> 原则：尽量不破坏既有 API；客户端不支持 header 时仍可从 body 取回再带回。

### 5.2 服务端权威状态存储

建议最小持久化结构（可内存 LRU + 可选 SQLite）：

- `ConversationState`：
  - `scid: str`
  - `created_at, updated_at`
  - `history: List[AnthropicMessage]`（网关规范化后的权威消息）
  - `last_valid_thinking: Optional[Tuple[signature, thinking_text]]`
  - `tool_map: Dict[tool_use_id, ToolRecord]`（必要时）

可选扩展：

- TTL / 最大轮数 / 最大 token 预算
- 多租户隔离（key 前缀按 credential/user 分区）

---

## 6. 请求处理流程（两条路径）

### 6.1 有 SCID（推荐路径：网关有状态）

1) 从请求中取 SCID；命中则加载 `ConversationState`  
2) 只接纳客户端本轮“新输入”（通常是最后一条 user message）；客户端回放的 assistant/tool/thinking 历史默认忽略  
3) 基于服务端权威历史 + 本轮 user 输入构造下游 messages  
4) 对构造结果执行一次最终 sanitize（保证不变量）
5) 发下游

### 6.2 无 SCID（兼容路径：网关无状态）

1) 使用客户端传入 messages 作为输入  
2) 执行 sanitize：
   - 对所有 thinking/redacted_thinking：  
     - 能通过缓存严格验证 → 保留  
     - 不能验证 → 降级为 text（保上下文）或删除（更稳）  
   - 禁用 thinkingConfig（只要存在任何不可验证 thinking）  
   - 工具链：保持 tool_use/tool_result 对齐；必要时把 tool_result 文本化摘要注入  
3) 发下游

> 该路径实现了“可验证则有状态，不可验证则视为第一次/无状态”的语义：不是伪造签名，而是降级协议形态。

---

## 7. 响应处理流程（状态写入 + 兼容输出）

1) 从下游响应中提取 assistant blocks  
2) 对 thinking/redacted_thinking：  
   - 若包含合法 signature，则将 `(thinking_text, signature)` 写入现有缓存体系（Layer 2/3）  
   - 同时写入 ConversationState（权威历史）  
3) 对 tool_use：维持你们现有的“编码 tool_id + tool_id cache”策略（Layer 1）  
4) 返回给客户端时：
   - 返回正常 Anthropic/OpenAI 兼容结构
   - 额外携带 `X-AG-Conversation-Id`（或 body meta）供客户端续传

---

## 8. 与现有实现的集成点（建议改造点）

> 目标：不要在多个 router/converter 里分散维护 thinking 规则；改为“最后一跳统一 sanitizer + 可选 state manager”。

建议新增两个核心模块：

1) `src/gateway_conversation_state.py`  
   
   - 负责 SCID 生成、加载/保存状态、TTL/LRU、可选持久化

2) `src/gateway_anthropic_sanitizer.py`  
   
   - 负责最终发送前的 messages + thinkingConfig 一致性校验与修复
   - 只暴露一个入口：`sanitize_and_build(payload, state) -> payload`

并在“发送下游请求”的出口处调用（而非中间某个 converter 分支）。

---

## 9. 少量示例代码（仅示意，供 Claude 开发落地）

### 9.1 状态对象（示意）

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import time

@dataclass
class ConversationState:
    scid: str
    history: List[Dict[str, Any]] = field(default_factory=list)  # Anthropic messages (normalized)
    last_valid_thinking: Optional[Tuple[str, str]] = None        # (signature, thinking_text)
    updated_at: float = field(default_factory=lambda: time.time())
```

### 9.2 最终 sanitizer（示意）

```python
def sanitize_thinking_blocks(messages: List[Dict[str, Any]], cache, state: ConversationState | None) -> tuple[List[Dict[str, Any]], bool]:
    """
    Returns:
      (sanitized_messages, allow_thinking_config)
    """
    allow_thinking_config = True
    sanitized = []

    for msg in messages:
        if msg.get("role") != "assistant":
            sanitized.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            sanitized.append(msg)
            continue

        new_content = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t in ("thinking", "redacted_thinking"):
                thinking_text = block.get("thinking", "") or ""
                signature = block.get("signature") or ""

                # 1) 可验证：cache 命中且严格匹配（或来自 state 的权威历史）
                cached_sig = cache.get_cached_signature(thinking_text) if thinking_text else None
                if cached_sig:
                    new_content.append({"type": t, "thinking": thinking_text, "signature": cached_sig})
                    continue

                # 2) 不可验证：降级为 text（保上下文），并禁止下发 thinkingConfig
                allow_thinking_config = False
                if thinking_text.strip():
                    new_content.append({"type": "text", "text": f"<think>{thinking_text}</think>"})
                continue

            new_content.append(block)

        sanitized.append({**msg, "content": new_content})

    if not allow_thinking_config:
        # thinkingConfig 不下发时，确保没有 thinking blocks
        # （此处仅示意：实现中应保证上面逻辑已全部降级/删除）
        pass

    return sanitized, allow_thinking_config
```

> 注意：示例仅表达“不可验证就降级为 text”的语义。最终实现需要同时覆盖：
> 
> - tool_use/tool_result 的链条一致性（以及必要时 tool_result 文本化摘要注入）；  
> - “assistant 消息必须以 thinking 起始块”等下游约束（allow_thinking_config 为 False 时必须避免触发）；  
> - 有 SCID 时优先用 state.history 构造 messages（而不是信任客户端回放）。

---

## 10. 关键取舍（必须提前定策略）

### 10.1 不可验证 thinking 的处理方式

两档可选（建议默认 A）：

- A) 降级为 text（保上下文，最符合“语义完整性”诉求）  
- B) 直接删除（最稳，但上下文损失更大）

### 10.2 工具回合策略

建议默认：

- 工具回合优先走“弱无状态”：禁用 thinkingConfig + 降级/删除 thinking blocks  
- 保留 tool_result（必要时文本化摘要）

### 10.3 SCID 的部署策略

建议引入 feature flag：

- `GATEWAY_STATE_MACHINE_ENABLED=true/false`
- `STATE_MACHINE_TTL_SECONDS=...`
- `STATE_MACHINE_MAX_TURNS=...`

---

## 11. 验证与观测（建议）

必须观测的指标：

- 下游 400 错误分类计数：`Invalid signature in thinking block`、thinking disabled 但含 thinking、起始块约束等
- sanitize 触发次数（不可验证 thinking 降级/删除）
- SCID 命中率、state 过期率、state 重建成功率
- 工具链一致性错误（孤儿 tool_result / id 不匹配）

---

## 12. 交付给 Claude 的开发任务拆分（建议）

1) 引入 SCID：生成/返回/读取；实现最小 `ConversationState` 存储（内存 LRU + TTL）  
2) 实现统一 sanitizer：在“发送下游请求前最后一跳”执行，强制 thinkingConfig 与 payload 一致  
3) 无 SCID 兼容：不可验证 thinking 降级为 text（或删除），工具回合禁用 thinking  
4) 有 SCID 路径：使用 state.history 重建 payload，忽略客户端回放的 assistant/thinking/tool（仅接纳本轮 user + 必要 tool_result）  
5) 补测试：至少覆盖“前有 thinking + 工具调用 + 客户端回放变形”场景不再 400

---

## 13. 备注：为什么这套方案与现有“三层缓存 + 六层恢复”一致且更强

- 现有缓存/恢复解决的是“签名字段被丢/无法取回”的问题；  
- SCID 状态机解决的是更根本的“客户端回放不可信导致文本变形”的问题；  
- 两者组合后：  
  - “可验证时”仍能完整保留 extended thinking；  
  - “不可验证时”也不会因为误拼装 thinking block 而中断（降级为 text + 禁用 thinkingConfig）；  
  - 对 Cursor / augment-BYOK 等 IDE 的兼容性显著提升，因为网关成为协议一致性的最终裁判。
