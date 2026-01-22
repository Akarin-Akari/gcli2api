/**
 * Cursor Thinking 处理逻辑分析脚本
 * 分析 Cursor IDE 如何处理 Claude 的 thinking blocks 和 signature
 */

const fs = require('fs');
const path = require('path');

const CURSOR_MAIN_JS = 'C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js';
const OUTPUT_FILE = 'F:/antigravity2api/gcli2api/docs/2026-01-20_Cursor_Thinking_Deep_Analysis.md';

// 读取文件内容
console.log('Reading Cursor source file...');
const content = fs.readFileSync(CURSOR_MAIN_JS, 'utf-8');
console.log(`File size: ${(content.length / 1024 / 1024).toFixed(2)} MB`);

const results = {
    thinkingFields: [],
    signatureHandling: [],
    messageConstruction: [],
    thinkingOmission: [],
    requestBuilding: [],
    historyProcessing: []
};

// 1. 分析 thinking 字段定义
console.log('\n1. Analyzing thinking field definitions...');
const thinkingFieldPatterns = [
    /name:"thinking"[^}]+/g,
    /name:"all_thinking_blocks"[^}]+/g,
    /name:"thinking_style"[^}]+/g,
    /name:"thinking_level"[^}]+/g
];
for (const pattern of thinkingFieldPatterns) {
    const matches = content.match(pattern) || [];
    results.thinkingFields.push(...matches.slice(0, 5));
}

// 2. 分析 signature 处理逻辑
console.log('2. Analyzing signature handling...');
const signaturePatterns = [
    /signature[^;]{0,100}thinking[^;]{0,50}/gi,
    /thinking[^;]{0,100}signature[^;]{0,50}/gi
];
for (const pattern of signaturePatterns) {
    const matches = content.match(pattern) || [];
    results.signatureHandling.push(...matches.slice(0, 10));
}

// 3. 分析消息构建逻辑
console.log('3. Analyzing message construction...');
const messagePatterns = [
    /conversationMap[^;]{0,150}thinking[^;]{0,100}/gi,
    /bubbleId[^;]{0,100}thinking[^;]{0,100}/gi
];
for (const pattern of messagePatterns) {
    const matches = content.match(pattern) || [];
    results.messageConstruction.push(...matches.slice(0, 10));
}

// 4. 分析 thinking 是否被省略
console.log('4. Analyzing thinking omission...');
const omissionPatterns = [
    /thinking[^;]{0,30}undefined/gi,
    /thinking[^;]{0,30}null/gi,
    /thinking[^;]{0,30}\?\?[^;]{0,50}/gi,
    /delete[^;]{0,30}thinking/gi,
    /omit[^;]{0,30}thinking/gi
];
for (const pattern of omissionPatterns) {
    const matches = content.match(pattern) || [];
    results.thinkingOmission.push(...matches.slice(0, 10));
}

// 5. 分析请求构建
console.log('5. Analyzing request building...');
const requestPatterns = [
    /StreamUnifiedChatRequest[^}]{0,300}/gi,
    /conversation[^;]{0,50}push[^;]{0,100}/gi
];
for (const pattern of requestPatterns) {
    const matches = content.match(pattern) || [];
    results.requestBuilding.push(...matches.slice(0, 5));
}

// 6. 分析历史消息处理
console.log('6. Analyzing history processing...');
const historyPatterns = [
    /previousMessage[^;]{0,150}/gi,
    /historyMessage[^;]{0,150}/gi,
    /cutoffConversation[^;]{0,150}/gi
];
for (const pattern of historyPatterns) {
    const matches = content.match(pattern) || [];
    results.historyProcessing.push(...matches.slice(0, 5));
}

// 7. 特别搜索：thinking 在历史消息中是否被保留
console.log('7. Searching for thinking preservation in history...');
const preservationPatterns = [
    /full_conversation[^;]{0,200}/gi,
    /headers_only[^;]{0,200}/gi
];
const preservationResults = [];
for (const pattern of preservationPatterns) {
    const matches = content.match(pattern) || [];
    preservationResults.push(...matches.slice(0, 5));
}

// 8. 搜索关键的 redacted 处理
console.log('8. Searching for redacted handling...');
const redactedMatches = content.match(/redacted[^;]{0,150}/gi) || [];

// 生成报告
console.log('\nGenerating report...');

const report = `# Cursor Thinking 深度分析报告

**分析日期**: 2026-01-20
**分析文件**: ${CURSOR_MAIN_JS}
**文件大小**: ${(content.length / 1024 / 1024).toFixed(2)} MB

---

## 1. Thinking 字段定义

Cursor 定义了以下 thinking 相关字段：

\`\`\`javascript
${results.thinkingFields.join('\n')}
\`\`\`

**分析**：
- \`thinking\` (no:45): 单个 ThinkingBlock 消息
- \`all_thinking_blocks\` (no:46): ThinkingBlock 数组
- \`thinking_style\` (no:85): 枚举类型 (UNSPECIFIED, DEFAULT, CODEX, GPT5)
- \`thinking_level\` (no:49): 枚举类型 (UNSPECIFIED, MEDIUM, HIGH)

---

## 2. Signature 处理逻辑

\`\`\`javascript
${results.signatureHandling.slice(0, 5).join('\n\n')}
\`\`\`

**分析**：
- Cursor 在更新 thinking 时会保留 signature
- 新 signature 非空时覆盖，否则保留原值

---

## 3. 消息构建逻辑

\`\`\`javascript
${results.messageConstruction.slice(0, 5).join('\n\n')}
\`\`\`

---

## 4. Thinking 省略/过滤分析

\`\`\`javascript
${results.thinkingOmission.slice(0, 5).join('\n\n')}
\`\`\`

**关键发现**：
${results.thinkingOmission.length > 0 ? '发现 thinking 可能被省略的代码路径' : '未发现明显的 thinking 省略逻辑'}

---

## 5. 请求构建分析

\`\`\`javascript
${results.requestBuilding.slice(0, 3).join('\n\n')}
\`\`\`

---

## 6. 历史消息处理

\`\`\`javascript
${results.historyProcessing.slice(0, 3).join('\n\n')}
\`\`\`

---

## 7. Thinking 保留分析

\`\`\`javascript
${preservationResults.slice(0, 5).join('\n\n')}
\`\`\`

**关键发现**：
- \`full_conversation_headers_only\`: Cursor 可能只发送消息头部，不包含完整内容
- 这可能导致历史消息中的 thinking blocks 被省略

---

## 8. Redacted 处理

\`\`\`javascript
${redactedMatches.slice(0, 5).join('\n\n')}
\`\`\`

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

## 11. 下一步行动

1. 检查 gcli2api 的 thinking 处理逻辑
2. 添加日志记录 thinking blocks 的传递情况
3. 验证 signature 是否正确传递
4. 考虑在 gcli2api 层面缓存 thinking blocks

`;

fs.writeFileSync(OUTPUT_FILE, report, 'utf-8');
console.log(`\nReport saved to: ${OUTPUT_FILE}`);

// 输出关键发现摘要
console.log('\n=== 关键发现摘要 ===');
console.log(`1. Thinking 字段定义: ${results.thinkingFields.length} 个`);
console.log(`2. Signature 处理: ${results.signatureHandling.length} 处`);
console.log(`3. 消息构建: ${results.messageConstruction.length} 处`);
console.log(`4. Thinking 省略: ${results.thinkingOmission.length} 处`);
console.log(`5. 请求构建: ${results.requestBuilding.length} 处`);
console.log(`6. 历史处理: ${results.historyProcessing.length} 处`);
console.log(`7. Redacted 处理: ${redactedMatches.length} 处`);
