# Cursor Thinking 深度分析报告

**分析日期**: 2026-01-20
**分析文件**: C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js
**文件大小**: 28.01 MB

---

## 1. Thinking 字段定义

Cursor 定义了以下 thinking 相关字段：

```javascript
name:"thinking",kind:"message",T:Jlt,opt:!0
name:"thinking",kind:"message",T:Jlt,opt:!0
name:"thinking",kind:"message",T:jht,opt:!0
name:"thinking",kind:"message",T:jht,opt:!0
name:"thinking",kind:"message",T:jht,opt:!0
name:"all_thinking_blocks",kind:"message",T:Jlt,repeated:!0
name:"all_thinking_blocks",kind:"message",T:jht,repeated:!0
name:"thinking_style",kind:"enum",T:v.getEnumType(mse),opt:!0
name:"thinking_style",kind:"enum",T:v.getEnumType(mse),opt:!0
name:"thinking_style",kind:"enum",T:k.getEnumType(zht),opt:!0
name:"thinking_style",kind:"enum",T:k.getEnumType(zht),opt:!0
name:"thinking_style",kind:"enum",T:k.getEnumType(zht),opt:!0
name:"thinking_level",kind:"enum",T:v.getEnumType(K7e),opt:!0
name:"thinking_level",kind:"enum",T:k.getEnumType(ebs),opt:!0
```

**分析**：
- `thinking` (no:45): 单个 ThinkingBlock 消息
- `all_thinking_blocks` (no:46): ThinkingBlock 数组
- `thinking_style` (no:85): 枚举类型 (UNSPECIFIED, DEFAULT, CODEX, GPT5)
- `thinking_level` (no:49): 枚举类型 (UNSPECIFIED, MEDIUM, HIGH)

---

## 2. Signature 处理逻辑

```javascript
signature="",this.redactedThinking="",this.isLastThinkingChunk=!1,v.util.initPartial(e,this)}static{this.ru

signature",kind:"scalar",T:9},{no:3,name:"redacted_thinking",kind:"scalar",T:9},{no:4,name:"is_last_thinking_chunk",kind:"scalar",T:8}])}static fromBinary(e,t

signature="",this.redactedThinking="",this.isLastThinkingChunk=!1,k.util.initPartial(e,this)}static{this.ru

signature",kind:"scalar",T:9},{no:3,name:"redacted_thinking",kind:"scalar",T:9},{no:4,name:"is_last_thinking_chunk",kind:"scalar",T:8}])}static fromBinary(e,t

Thinking"}static{this.fields=v.util.newFieldList(()=>[{no:1,name:"text",kind:"scalar",T:9},{no:2,name:"signature",kind:"scalar",T:9},{no:3,name:"redacted_thinking
```

**分析**：
- Cursor 在更新 thinking 时会保留 signature
- 新 signature 非空时覆盖，否则保留原值

---

## 3. 消息构建逻辑

```javascript
conversationMap)){const o={type:r.type,text:r.text,thinking:r.thinking,toolFormerData:r.toolFormerData,capabilityType:r.capabilityType}

conversationMap",r.bubbleId,"text",o))}handleThinkingDelta(i){if(i.length===0)return

conversationMap",r,"thinking",a=>({text:(a?.text??"")+i,signature:a?.signature??""})))}emitAfterModelThought(i,e){const t=this.i

conversationMap",n,"thinkingDurationMs",s)),e.updateComposerDataSetStore(this.composerDataHandle,r=>r("generatingBubbleIds",[]))

conversationMap",t.thinkingBubbleId,"capabilityType",this.type)})
```

---

## 4. Thinking 省略/过滤分析

```javascript
Thinking?.()??""}})}})]}})}}),null

Thinking?.()??""}})}}),null

THINKING},null,2)),ne}}),null

thinking=="object"&&e.thinking!==null

Thinking:r?.supportsThinking??!1}doesModelSupportImages(e){if(e==="default")retu
```

**关键发现**：
发现 thinking 可能被省略的代码路径

---

## 5. 请求构建分析

```javascript
StreamUnifiedChatRequestWithTools"

StreamUnifiedChatRequestWithToolsIdempotent"

StreamUnifiedChatRequest"
```

---

## 6. 历史消息处理

```javascript
PreviousMessages(e,t,n,s){const r=[],o=performance.now()

PreviousMessages took more than 10 seconds to complete"),{attachments:[{filename:"logs.txt",data:new TextEncoder().encode(r.map(w=>w.join(",")).join(`
`)),contentTyp

PreviousMessages",force_upload:"forced"},extra:{conversationLength:t.length,totalDurationMs:performance.now()-o,foundAtMessageIndex:h},fingerprint:["getContextFromPr
```

---

## 7. Thinking 保留分析

```javascript
full_conversation_headers_only",kind:"message",T:Ncl,repeated:!0},{no:2,name:"allow_long_file_scan",kind:"scalar",T:8,opt:!0},{no:3,name:"explicit_context",kind:"message",T:RL},{no:4,name:"can_handle_filenames_after_l

full_conversation_token_count",kind:"message",T:GPr},{no:6,name:"code_chunks_v2",kind:"message",T:cul,repeated:!0},{no:2,name:"folder_exclusion_tooltip",kind:"scalar",T:9},{no:7,name:"bar_fraction",kind:"scalar",T:2},

full_conversation_headers_only",kind:"message",T:ycc,repeated:!0},{no:2,name:"allow_long_file_scan",kind:"scalar",T:8,opt:!0},{no:3,name:"explicit_context",kind:"message",T:z2},{no:4,name:"can_handle_filenames_after_l

full_conversation_token_count",kind:"message",T:TGr},{no:6,name:"code_chunks_v2",kind:"message",T:euc,repeated:!0},{no:2,name:"folder_exclusion_tooltip",kind:"scalar",T:9},{no:7,name:"bar_fraction",kind:"scalar",T:2},

headers_only",kind:"message",T:Ncl,repeated:!0},{no:2,name:"allow_long_file_scan",kind:"scalar",T:8,opt:!0},{no:3,name:"explicit_context",kind:"message",T:RL},{no:4,name:"can_handle_filenames_after_language_ids",
```

**关键发现**：
- `full_conversation_headers_only`: Cursor 可能只发送消息头部，不包含完整内容
- 这可能导致历史消息中的 thinking blocks 被省略

---

## 8. Redacted 处理

```javascript
redactedThinking="",this.isLastThinkingChunk=!1,v.util.initPartial(e,this)}static{this.runtime=v}static{this.typeName="aiserver.v1.ConversationMessage.Thinkin

redacted_thinking",kind:"scalar",T:9},{no:4,name:"is_last_thinking_chunk",kind:"scalar",T:8}])}static fromBinary(e,t){return new Opi().fromBinary(e,t)}static 

redactedThinking="",this.isLastThinkingChunk=!1,k.util.initPartial(e,this)}static{this.runtime=k}static{this.typeName="aiserver.v1.ConversationMessage.Thinkin

redacted_thinking",kind:"scalar",T:9},{no:4,name:"is_last_thinking_chunk",kind:"scalar",T:8}])}static fromBinary(e,t){return new eon().fromBinary(e,t)}static 

REDACTED: user-file-path>",o=r.lastIndex)}return o<i.length&&(t+=i.substr(o)),t}function Fkh(i){if(!i)return i
```

---

## 9. 核心问题分析

### 9.1 Cursor 的 Thinking 处理流程

1. **接收阶段**：Cursor 正确接收 thinking 和 signature
2. **存储阶段**：存储在 conversationMap 中，包含 text 和 signature
3. **发送阶段**：⚠️ 可能存在问题

### 9.2 潜在问题点

1. **full_conversation_headers_only**：
   - Cursor 可能使用 "headers only" 模式发送历史消息
   - 这意味着只发送消息元数据，不包含完整的 thinking blocks

2. **thinking 字段可选**：
   - thinking 字段是 optional (opt:!0)
   - 如果不显式设置，可能不会被包含在请求中

3. **signature 丢失风险**：
   - 如果历史消息不包含 signature，Claude 无法验证 thinking 的完整性
   - 这会导致 Claude 认为之前的 thinking 无效

### 9.3 与 gcli2api 的关系

gcli2api 作为中间层，需要确保：
1. 从 Cursor 接收的请求中提取 thinking 和 signature
2. 正确转发给 Claude API
3. 从 Claude 响应中提取 thinking 和 signature
4. 正确返回给 Cursor

---

## 10. 建议修复方案

### 10.1 gcli2api 层面

1. **确保 thinking blocks 完整传递**：
   - 在请求转换时，保留所有 thinking blocks
   - 在响应转换时，保留 signature

2. **添加 thinking 验证**：
   - 检查历史消息中是否包含 thinking
   - 如果缺失，记录警告日志

### 10.2 Cursor 层面（无法直接修改）

- Cursor 可能需要更新以支持完整的 thinking blocks 传递
- 当前版本可能存在 "headers only" 模式导致的 thinking 丢失

---

## 11. 深入分析：Cursor 的 Thinking 传递机制

### 11.1 FullConversationHeadersOnly 类定义

```javascript
// Ncl 类 - 只包含最基本的消息头信息
Ncl=class gpi extends K{
  constructor(e){
    super(),
    this.bubbleId="",      // 只有 bubbleId
    this.type=Fa.UNSPECIFIED,  // 只有 type
    v.util.initPartial(e,this)
  }
}
```

**关键发现**：`fullConversationHeadersOnly` 只包含 `bubbleId` 和 `type`，**不包含任何消息内容、thinking 或 signature**！

### 11.2 Cursor 发送请求时的处理

```javascript
// Cursor 同时发送两个字段
await this.computeStreamUnifiedChatRequest(e, {
  conversation: tr,  // 完整的 ConversationMessage 数组
  fullConversationHeadersOnly: ne.fullConversationHeadersOnly.map(gl => ({
    bubbleId: gl.bubbleId,
    type: gl.type,
    serverBubbleId: gl.serverBubbleId  // 只有这三个字段！
  })),
  ...
})
```

### 11.3 ConversationMessage 的 thinking 字段

```javascript
// ConversationMessage protobuf 定义
{no:45, name:"thinking", kind:"message", T:Jlt, opt:true},
{no:46, name:"all_thinking_blocks", kind:"message", T:Jlt, repeated:true},
{no:85, name:"thinking_style", kind:"enum", T:ThinkingStyleEnum, opt:true}
```

**Thinking 消息类型 (Jlt)**：
```javascript
{no:1, name:"text", kind:"scalar", T:9},      // thinking 文本
{no:2, name:"signature", kind:"scalar", T:9}, // 签名
{no:3, name:"redacted_thinking", kind:"scalar", T:9},  // 被redact的内容
{no:4, name:"is_last_thinking_chunk", kind:"scalar", T:8}  // 是否最后一块
```

### 11.4 Cursor 本地存储 thinking 的逻辑

```javascript
// Cursor 正确存储 thinking 和 signature
updateComposerDataSetStore(composerDataHandle, o =>
  o("conversationMap", bubbleId, "thinking", a => ({
    text: (a?.text ?? "") + newText,
    signature: a?.signature ?? ""  // 保留 signature
  }))
);

// 处理 thinking 响应时的 signature 更新逻辑
h === void 0
  ? {text: a, signature: l}  // 新建时设置
  : a || l
    ? {text: h.text + a, signature: l !== "" ? l : h.signature}  // 新signature非空则覆盖
    : h
```

### 11.5 序列化时包含 thinking

```javascript
// Cursor 在序列化 conversationMap 时包含 thinking
for(const[s,r] of Object.entries(i.conversationMap)){
  const o = {
    type: r.type,
    text: r.text,
    thinking: r.thinking,  // ✅ 包含 thinking！
    toolFormerData: r.toolFormerData,
    capabilityType: r.capabilityType
  };
  t[s] = JSON.stringify({...h1(),...Zqe(o)})
}
```

---

## 12. 核心结论

### 12.1 Cursor 端的处理是正确的

1. **接收阶段**：✅ 正确接收 thinking 和 signature
2. **存储阶段**：✅ 正确存储在 conversationMap 中
3. **序列化阶段**：✅ 序列化时包含 thinking 字段
4. **发送阶段**：✅ conversation 数组包含完整的 ConversationMessage

### 12.2 潜在问题点

1. **服务器端处理**：
   - 服务器可能只使用 `fullConversationHeadersOnly` 来恢复历史
   - 如果服务器不正确处理 `conversation` 中的 thinking，会导致丢失

2. **gcli2api 中间层**：
   - 需要确保正确传递 thinking 和 signature
   - 需要检查是否在转换过程中丢失了这些字段

3. **Claude API 要求**：
   - Claude API 要求历史消息中的 thinking blocks 必须包含有效的 signature
   - 如果 signature 丢失或无效，Claude 会认为 thinking 无效

---

## 13. 下一步行动

1. **检查 gcli2api 的 thinking 处理逻辑**
   - 验证请求转换时是否保留 thinking 和 signature
   - 验证响应转换时是否正确传递 signature

2. **添加调试日志**
   - 记录收到的请求中 thinking blocks 的数量和 signature 状态
   - 记录发送给 Claude API 的请求中 thinking blocks 的情况

3. **验证 Claude API 响应**
   - 确认 Claude API 返回的 signature 是否被正确传递给 Cursor

4. **考虑缓存机制**
   - 如果 Cursor 不正确传递历史 thinking，考虑在 gcli2api 层面缓存

---

## 14. gcli2api 的 Thinking 处理机制分析

### 14.1 已有的 Signature 缓存机制

gcli2api 已经实现了完整的 signature 缓存机制：

**文件：`src/signature_cache.py`**
```python
class SignatureCache:
    """
    Thinking Signature 缓存管理器
    - 线程安全的 LRU 缓存实现
    - 支持 TTL 过期机制
    - 使用 thinking_text 内容的哈希作为缓存 key
    """
    DEFAULT_MAX_SIZE = 10000
    DEFAULT_TTL_SECONDS = 3600  # 1 小时
```

### 14.2 Signature 恢复策略（多层优先级）

**文件：`src/anthropic_converter.py`**

```python
# 优先级 1: 从缓存恢复
cached_signature = get_cached_signature(thinking_text)
if cached_signature:
    final_signature = cached_signature
    recovery_source = "cache"

# 优先级 2: 如果缓存未命中，检查消息提供的签名是否有效
if not final_signature and message_signature and len(message_signature) >= 10:
    final_signature = message_signature
    recovery_source = "message"

# 优先级 3: 使用最近缓存的签名和配对文本（fallback）
if not final_signature:
    last_result = get_last_signature_with_text()
    if last_result:
        final_signature, cached_thinking_text = last_result
        thinking_text = cached_thinking_text  # 关键：替换文本确保匹配
        recovery_source = "last_cached_with_text"
```

### 14.3 thoughtSignature_fix 模块

**文件：`src/converters/thoughtSignature_fix.py`**

提供统一的 thoughtSignature 处理功能：
- `encode_tool_id_with_signature`: 将签名编码到工具调用ID中
- `decode_tool_id_and_signature`: 从编码的ID中提取签名
- `has_valid_thoughtsignature`: 验证 thinking 块是否有有效签名
- `sanitize_thinking_block`: 清理 thinking 块的额外字段
- `filter_invalid_thinking_blocks`: 过滤消息中的无效 thinking 块

### 14.4 gcli2api 的处理流程

```
Cursor 请求
    ↓
gcli2api (anthropic_converter.py)
    ├── 检查历史消息中的 thinking blocks
    ├── 从 signature_cache 恢复签名（优先级1）
    ├── 使用消息提供的签名（优先级2）
    ├── 使用最近缓存的签名+文本（优先级3）
    └── 如果都失败，降级为 text 块
    ↓
Claude API
    ↓
gcli2api (响应处理)
    ├── 提取 thinking 和 signature
    ├── 缓存到 signature_cache
    └── 返回给 Cursor
```

---

## 15. 最终结论

### 15.1 问题根因分析

经过深入分析 Cursor 源码和 gcli2api 代码，浮浮酱发现：

1. **Cursor 端**：
   - ✅ 正确接收和存储 thinking + signature
   - ✅ 序列化时包含 thinking 字段
   - ✅ 发送请求时包含完整的 ConversationMessage
   - ⚠️ `fullConversationHeadersOnly` 只包含 bubbleId/type，不包含 thinking

2. **gcli2api 端**：
   - ✅ 有完整的 signature 缓存机制
   - ✅ 多层优先级的签名恢复策略
   - ✅ 支持 thoughtSignature 编码到工具调用ID
   - ✅ 无效 thinking 块会降级为 text 块

3. **潜在问题点**：
   - Cursor 服务器可能只使用 `fullConversationHeadersOnly` 恢复历史
   - 如果 Cursor 服务器不正确处理 `conversation` 中的 thinking，会导致丢失
   - gcli2api 的 fallback 策略可能导致 thinking_text 与 signature 不匹配

### 15.2 建议的调试步骤

1. **添加详细日志**：
   - 记录收到的请求中 thinking blocks 的数量
   - 记录每个 thinking block 的 signature 状态
   - 记录 signature 恢复的来源（cache/message/fallback）

2. **验证 Cursor 请求**：
   - 检查 Cursor 发送的 `conversation` 数组是否包含 thinking
   - 检查 `fullConversationHeadersOnly` 是否被服务器使用

3. **验证 Claude API 响应**：
   - 确认 Claude API 返回的 signature 是否被正确缓存
   - 确认 signature 是否正确传递给 Cursor

### 15.3 可能的修复方向

1. **增强 signature 缓存**：
   - 使用更长的 TTL
   - 增加缓存容量
   - 添加持久化存储

2. **改进 fallback 策略**：
   - 不要替换 thinking_text，而是使用占位符签名
   - 或者完全跳过无签名的 thinking 块

3. **与 Cursor 服务器协调**：
   - 确认服务器如何处理 `conversation` 和 `fullConversationHeadersOnly`
   - 如果服务器只使用 headers，需要在 gcli2api 层面完全接管 thinking 管理

---

## 16. 附录：关键代码位置

| 功能 | 文件 | 行号 |
|------|------|------|
| Cursor Thinking 类定义 | workbench.desktop.main.js | ~Jlt 类 |
| Cursor signature 更新逻辑 | workbench.desktop.main.js | ~conversationMap 更新 |
| gcli2api signature 缓存 | src/signature_cache.py | 全文件 |
| gcli2api thinking 处理 | src/anthropic_converter.py | 690-760 |
| gcli2api thoughtSignature 修复 | src/converters/thoughtSignature_fix.py | 全文件 |

---

**分析完成时间**: 2026-01-20
**分析者**: 浮浮酱 (Claude Opus 4.5)

