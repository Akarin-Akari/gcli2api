# Cursor Thinking/Signature 代码分析

**分析日期**: 2026-01-20
**分析目录**: C:/Program Files/cursor/resources/app/out/

---

## 1. thoughtSignature 搜索结果

在 Cursor 源码中未找到 `thoughtSignature` 关键字。Cursor 使用的是 `signature` 字段名。

---

## 2. thinking block 相关

### 2.1 Thinking 数据结构定义

Cursor 定义了一个 Thinking 类，包含以下字段：

```javascript
// 位置: vs/workbench/workbench.desktop.main.js
class ThinkingBlock {
    text = "";           // thinking 文本内容
    signature = "";      // 签名 (用于 extended thinking)
    redactedThinking = ""; // 被编辑的思考内容
    isLastThinkingChunk = false; // 是否为最后一个 thinking chunk
}
```

### 2.2 Protobuf 字段定义

```javascript
// 消息字段定义
{no:1, name:"text", kind:"scalar", T:9},
{no:2, name:"signature", kind:"scalar", T:9},
{no:3, name:"redacted_thinking", kind:"scalar", T:9},
{no:4, name:"is_last_thinking_chunk", kind:"scalar", T:8}
```

### 2.3 Thinking 相关消息字段

```javascript
// 响应消息中的 thinking 相关字段
{no:45, name:"thinking", kind:"message", T:ThinkingBlock, opt:true},
{no:46, name:"all_thinking_blocks", kind:"message", T:ThinkingBlock, repeated:true},
{no:85, name:"thinking_style", kind:"enum", T:ThinkingStyleEnum, opt:true}
```

### 2.4 Thinking 模型配置

```javascript
// 支持 thinking 的模型
"claude-4.5-opus-high-thinking"
"claude-4.5-sonnet-thinking"
"claude-3.7-sonnet-thinking"
"claude-3.7-sonnet-thinking-max"
```

### 2.5 ThinkingLevel 配置

```javascript
// 默认 thinkingLevel
thinkingLevel: "none"

// 可选值
"none" | "low" | "medium" | "high" | "max"
```

---

## 3. signature 相关

### 3.1 Signature 处理逻辑

```javascript
// 位置: vs/workbench/workbench.desktop.main.js

// 更新 thinking 时保留 signature
updateComposerDataSetStore(composerDataHandle, o => 
    o("conversationMap", bubbleId, "thinking", a => ({
        text: (a?.text ?? "") + newText,
        signature: a?.signature ?? ""  // 保留原有 signature
    }))
);

// 处理 thinking 响应
if (thinkingBubbleId !== void 0 && e.thinking !== void 0) {
    const text = e.thinking?.text ?? "";
    const signature = e.thinking?.signature ?? "";
    
    updateComposerDataSetStore(composerHandle, d => 
        d("conversationMap", thinkingBubbleId, "thinking", h => 
            h === void 0 
                ? {text: text, signature: signature}
                : text || signature 
                    ? {text: h.text + text, signature: signature !== "" ? signature : h.signature}
                    : h
        )
    );
}
```

### 3.2 Signature 更新规则

1. 如果新 signature 不为空，使用新 signature
2. 如果新 signature 为空，保留原有 signature
3. Signature 在 thinking 块结束时才会有最终值

---

## 4. thinkingBubble 处理流程

### 4.1 Thinking 开始

```javascript
// 检测到 thinking 开始
if (thinking !== null) {
    ignoreThinkTags = true;
    if (thinkingStartTime === void 0) {
        thinkingStartTime = Date.now();
    }
    if (thinkingBubbleId === void 0) {
        // 创建新的 thinking bubble
        thinkingBubbleId = createNewBubble();
    }
}
```

### 4.2 Thinking 结束

```javascript
// Thinking 结束时记录持续时间
if (thinkingBubbleId !== void 0) {
    const duration = Date.now() - thinkingStartTime;
    updateComposerDataSetStore(composerHandle, d => 
        d("conversationMap", thinkingBubbleId, "thinkingDurationMs", duration)
    );
    emitAfterModelThought(thinkingBubbleId, duration);
    thinkingStartTime = void 0;
    thinkingBubbleId = void 0;
}
```

---

## 5. 初步结论

### 5.1 Cursor 的 Thinking/Signature 架构

1. **数据结构**: Cursor 使用 `ThinkingBlock` 类存储 thinking 内容，包含 `text`、`signature`、`redactedThinking`、`isLastThinkingChunk` 四个字段

2. **Signature 用途**: `signature` 字段用于 Anthropic Extended Thinking 功能，是加密签名，用于验证 thinking 内容的完整性

3. **处理流程**:
   - 流式接收 thinking 内容
   - 累积 text 内容
   - 在最后一个 chunk 时获取 signature
   - 保存到 conversationMap 中

4. **模型支持**: 明确支持 `claude-4.5-opus-high-thinking`、`claude-4.5-sonnet-thinking` 等 thinking 模型

### 5.2 与 gcli2api 的对比

gcli2api 项目中使用 `thoughtSignature` 命名，而 Cursor 使用 `signature`。两者功能相同，都是用于 Anthropic Extended Thinking 的签名验证。

### 5.3 关键发现

1. **all_thinking_blocks**: Cursor 支持多个 thinking blocks，使用 `all_thinking_blocks` 数组存储

2. **thinking_style**: 有 thinking 风格枚举，可能对应不同的 thinking 模式

3. **thinkingLevel**: 支持 "none"、"low"、"medium"、"high"、"max" 等级别

4. **redactedThinking**: 支持被编辑/隐藏的 thinking 内容

---

## 6. 建议

1. 确保 gcli2api 正确传递 `signature` 字段给 Cursor
2. 支持 `all_thinking_blocks` 数组格式
3. 正确设置 `is_last_thinking_chunk` 标志
4. 考虑支持 `thinking_style` 枚举
