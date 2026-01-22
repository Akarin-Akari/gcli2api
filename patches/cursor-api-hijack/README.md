# Cursor API 劫持补丁

**版本**: v1.0.0
**创建日期**: 2026-01-20
**作者**: 浮浮酱 (Claude Opus 4.5)

---

## 概述

本补丁用于劫持 Cursor IDE 的第三方 API 请求，将其重定向到 gcli2api 网关服务器。

### 解决的问题

1. **Thinking Signature 丢失**: Cursor 的 `fullConversationHeadersOnly` 模式只包含 `bubbleId` 和 `type`，不包含 thinking 和 signature
2. **历史消息不完整**: 服务器可能只使用 headers_only 模式恢复历史，导致 thinking 块丢失
3. **Signature 验证失败**: Claude API 要求历史消息中的 thinking 必须包含有效的 signature

### 劫持策略

当 Cursor 启用第三方 API（"Use own API key" 开关）时，补丁会：
1. 拦截发往第三方 API 端点的请求
2. 重定向到本地 gcli2api 网关 (默认 `http://127.0.0.1:8181`)
3. gcli2api 负责处理 thinking signature 缓存和恢复

---

## 安装方法

### 1. 定位 Cursor 核心文件

Cursor 核心工作台文件位于：
```
C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js
```

### 2. 备份原始文件

```powershell
# 创建备份目录
New-Item -ItemType Directory -Force -Path "C:/Program Files/cursor/resources/app/out/vs/workbench/backups"

# 备份原始文件
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item "C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js" `
          "C:/Program Files/cursor/resources/app/out/vs/workbench/backups/workbench.desktop.main.js.backup.$timestamp"
```

### 3. 应用补丁

#### 方法 A: 使用自动补丁脚本

```powershell
# 以管理员身份运行 PowerShell
cd F:\antigravity2api\gcli2api\patches\cursor-api-hijack
.\apply-patch.ps1
```

#### 方法 B: 手动注入

1. 打开 `workbench.desktop.main.js`
2. 在文件开头（第一行）插入补丁代码
3. 保存文件
4. 重启 Cursor

### 4. 验证安装

1. 打开 Cursor
2. 打开开发者工具 (Ctrl+Shift+I)
3. 在 Console 中输入：`window.__GCLI2API_HIJACK_VERSION`
4. 应该显示补丁版本号

---

## 配置选项

补丁支持通过环境变量或配置文件进行配置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `GCLI2API_GATEWAY_URL` | `http://127.0.0.1:8181` | 网关服务器地址 |
| `GCLI2API_ENABLE_HIJACK` | `true` | 是否启用劫持 |
| `GCLI2API_DEBUG` | `false` | 是否启用调试日志 |
| `GCLI2API_HIJACK_PATTERNS` | 见下文 | 劫持的 URL 模式 |

### 默认劫持的 URL 模式

```javascript
[
  /^https:\/\/api\.anthropic\.com\//,
  /^https:\/\/api\.openai\.com\//,
  /^https:\/\/.+\.anthropic\.com\/v1\/messages/
]
```

---

## 卸载方法

### 方法 A: 使用卸载脚本

```powershell
cd F:\antigravity2api\gcli2api\patches\cursor-api-hijack
.\remove-patch.ps1
```

### 方法 B: 手动恢复

```powershell
# 恢复最近的备份
$latestBackup = Get-ChildItem "C:/Program Files/cursor/resources/app/out/vs/workbench/backups/*.backup.*" |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
Copy-Item $latestBackup.FullName "C:/Program Files/cursor/resources/app/out/vs/workbench/workbench.desktop.main.js"
```

---

## Cursor 更新后重新应用

每次 Cursor 更新后，补丁会被覆盖。需要重新应用：

```powershell
cd F:\antigravity2api\gcli2api\patches\cursor-api-hijack
.\apply-patch.ps1
```

建议在 Cursor 更新后检查补丁状态。

---

## 技术原理

### 1. 请求拦截

补丁通过重写 `fetch` 和 `XMLHttpRequest` 来拦截网络请求：

```javascript
const originalFetch = window.fetch;
window.fetch = async function(url, options) {
  if (shouldHijack(url)) {
    url = redirectToGateway(url);
    options = modifyOptions(options);
  }
  return originalFetch.call(this, url, options);
};
```

### 2. 第三方 API 检测

补丁检测 Cursor 的 "Use own API key" 设置：

```javascript
function isThirdPartyAPIEnabled() {
  // 检测 Cursor 配置
  return window.__CURSOR_SETTINGS__?.useOwnAPIKey === true;
}
```

### 3. URL 重写规则

| 原始 URL | 重写后 URL |
|----------|------------|
| `https://api.anthropic.com/v1/messages` | `http://127.0.0.1:8181/v1/messages` |
| `https://api.openai.com/v1/chat/completions` | `http://127.0.0.1:8181/openai/v1/chat/completions` |

---

## 调试

### 启用调试日志

1. 设置环境变量 `GCLI2API_DEBUG=true`
2. 或在 Cursor 开发者控制台执行：`window.__GCLI2API_DEBUG = true`

### 查看劫持状态

```javascript
// 在 Cursor 开发者控制台执行
window.__GCLI2API_HIJACK_STATUS()
```

### 临时禁用劫持

```javascript
// 在 Cursor 开发者控制台执行
window.__GCLI2API_DISABLE_HIJACK()
```

---

## 已知问题

1. **Cursor 更新后失效**: 每次 Cursor 更新会覆盖补丁，需要重新应用
2. **签名验证**: 修改后的文件可能触发 Cursor 的完整性检查（目前未发现问题）
3. **性能影响**: 请求拦截会增加约 1-2ms 延迟

---

## 更新日志

### v1.0.0 (2026-01-20)
- 初始版本
- 支持 Anthropic API 和 OpenAI API 劫持
- 自动检测第三方 API 模式
- 支持配置网关地址

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `hijack.js` | 补丁核心代码 |
| `apply-patch.ps1` | Windows 补丁应用脚本 |
| `remove-patch.ps1` | Windows 补丁卸载脚本 |
| `apply-patch.sh` | Linux/macOS 补丁应用脚本 |
| `README.md` | 本文档 |

---

**维护者**: 浮浮酱 (Claude Opus 4.5)
