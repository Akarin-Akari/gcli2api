# Cursor Thinking Preservation Patch

**版本**: v1.0.0
**创建日期**: 2026-01-20
**作者**: 浮浮酱 (Claude Opus 4.5)

---

## 问题分析

### 根本问题

根据 `2026-01-20_Cursor_Thinking_Deep_Analysis.md` 的分析：

1. **Cursor 发送请求时同时包含两个字段**：
   - `conversation`: 完整的 ConversationMessage 数组（包含 thinking + signature）
   - `fullConversationHeadersOnly`: 只包含 `bubbleId`, `type`, `serverBubbleId`

2. **`fullConversationHeadersOnly` 的定义**：
   ```javascript
   // Ncl 类 - 只包含最基本的消息头信息
   Ncl = class gpi extends K {
     constructor(e) {
       super(),
       this.bubbleId = "",        // 只有 bubbleId
       this.type = Fa.UNSPECIFIED, // 只有 type
       v.util.initPartial(e, this)
     }
   }
   ```

3. **潜在问题**：
   - 如果服务器使用 `fullConversationHeadersOnly` 来恢复历史消息
   - thinking 和 signature 就会丢失
   - 导致 Claude API 无法验证 thinking 的完整性

### Cursor 端的处理流程

```
接收响应 → 存储 thinking + signature → 序列化时包含 thinking
                                              ↓
                                    但 fullConversationHeadersOnly 不包含！
```

---

## 解决方案

本补丁提供三种模式：

### 模式 1: `inject`（推荐）

在 `fullConversationHeadersOnly` 中注入 thinking 数据：

```javascript
// 原始 headersOnly
{ bubbleId: "xxx", type: 1, serverBubbleId: "yyy" }

// 注入后
{
  bubbleId: "xxx",
  type: 1,
  serverBubbleId: "yyy",
  thinking: {
    text: "Let me think...",
    signature: "EqQBCg..."
  }
}
```

### 模式 2: `remove`

完全移除 `fullConversationHeadersOnly` 字段，强制服务器使用 `conversation`：

```javascript
// 原始请求
{
  conversation: [...],
  fullConversationHeadersOnly: [...]
}

// 处理后
{
  conversation: [...]  // 只保留完整的 conversation
}
```

### 模式 3: `monitor`

仅监控，不修改数据。用于调试和分析：

```
[Cursor Thinking Patch] 监控: headersOnly=10, 有thinking=8, 有signature=8
```

---

## 安装方法

### 1. 定位 Cursor 核心文件

```
C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js
```

### 2. 备份原始文件

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item "C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js" `
          "C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js.backup.$timestamp"
```

### 3. 注入补丁

将 `thinking-patch.js` 的内容注入到 `workbench.desktop.main.js` 的开头。

### 4. 重启 Cursor

---

## 使用方法

### 验证安装

在 Cursor 开发者工具 (Ctrl+Shift+I) 的 Console 中：

```javascript
// 查看版本
window.__CURSOR_THINKING_PATCH_VERSION
// 输出: "1.0.0"

// 查看状态
window.__CURSOR_THINKING_PATCH_STATUS()
// 输出: { version: "1.0.0", enabled: true, mode: "inject", ... }
```

### 切换模式

```javascript
// 切换到注入模式
window.__CURSOR_THINKING_PATCH_SET_MODE('inject')

// 切换到移除模式
window.__CURSOR_THINKING_PATCH_SET_MODE('remove')

// 切换到监控模式
window.__CURSOR_THINKING_PATCH_SET_MODE('monitor')
```

### 启用调试日志

```javascript
window.__CURSOR_THINKING_PATCH_DEBUG = true
```

### 临时禁用/启用

```javascript
// 禁用
window.__CURSOR_THINKING_PATCH_DISABLE()

// 启用
window.__CURSOR_THINKING_PATCH_ENABLE()
```

---

## 技术原理

### 1. 拦截点

补丁通过以下方式拦截数据：

1. **JSON.stringify 拦截**：捕获 JSON 序列化的请求数据
2. **fetch 拦截**：在请求发送前修改 body
3. **状态存储监控**：缓存 conversationMap 用于数据提取

### 2. 数据流

```
Cursor 构建请求
       ↓
补丁拦截 (fetch / JSON.stringify)
       ↓
检测 fullConversationHeadersOnly
       ↓
从 conversation 提取 thinking
       ↓
注入到 fullConversationHeadersOnly
       ↓
发送修改后的请求
```

### 3. 关键代码

```javascript
function enhanceHeadersOnlyEntry(entry, conversationMap) {
  const thinking = extractThinkingFromConversationMap(conversationMap, entry.bubbleId);
  if (!thinking) return entry;

  return {
    ...entry,
    thinking: {
      text: thinking.text,
      signature: thinking.signature
    }
  };
}
```

---

## 与 gcli2api 的配合

### 场景 1: 使用 Cursor 官方服务器

- 补丁确保 `fullConversationHeadersOnly` 包含 thinking
- 服务器可以正确恢复历史消息

### 场景 2: 使用第三方 API（通过 gcli2api）

- 补丁确保请求中包含完整的 thinking 数据
- gcli2api 可以正确处理和缓存 signature
- 即使 Cursor 不传递 signature，gcli2api 也可以从缓存恢复

### 推荐配置

```javascript
// 如果使用 gcli2api，推荐使用 'remove' 模式
// 这样强制使用 conversation，gcli2api 可以完整处理
window.__CURSOR_THINKING_PATCH_SET_MODE('remove')
```

---

## 已知限制

1. **Protobuf 二进制数据**：目前主要处理 JSON 格式的请求，对于纯 protobuf 二进制数据的处理有限
2. **Cursor 更新**：每次 Cursor 更新后需要重新应用补丁
3. **状态存储访问**：获取 conversationMap 的方法是启发式的，可能需要根据 Cursor 版本调整

---

## 调试指南

### 启用详细日志

```javascript
window.__CURSOR_THINKING_PATCH_DEBUG = true
```

### 查看 conversationMap

```javascript
window.__CURSOR_THINKING_PATCH_GET_CONVERSATION_MAP()
```

### 监控请求

使用 `monitor` 模式观察 thinking 数据的传递情况：

```javascript
window.__CURSOR_THINKING_PATCH_SET_MODE('monitor')
```

然后进行对话，观察 Console 中的日志。

---

## 文件列表

| 文件 | 说明 |
|------|------|
| `thinking-patch.js` | 补丁核心代码 |
| `README.md` | 本文档 |

---

## 更新日志

### v1.0.0 (2026-01-20)
- 初始版本
- 支持三种模式：inject, remove, monitor
- 拦截 JSON.stringify 和 fetch
- 提供调试接口

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
