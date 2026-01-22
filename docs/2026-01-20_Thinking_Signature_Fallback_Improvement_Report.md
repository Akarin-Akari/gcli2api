# Thinking Signature Fallback 策略改进报告

**日期**: 2026-01-20
**作者**: 浮浮酱 (Claude Opus 4.5)
**版本**: v1.0.0

---

## 1. 背景

### 1.1 问题描述

在 Cursor IDE 与 gcli2api 网关的交互过程中，发现 thinking signature 处理存在以下问题：

1. **Cursor 的 `fullConversationHeadersOnly` 模式**：只包含 `bubbleId` 和 `type`，不包含 thinking 和 signature
2. **Fallback 策略过于激进**：当无法恢复 signature 时，会用缓存的旧 thinking_text 替换当前的，导致上下文错乱
3. **日志不够详细**：难以追踪 thinking blocks 的传递和 signature 恢复状态

### 1.2 影响范围

- 多轮对话中 thinking 内容可能与实际上下文不匹配
- Claude API 可能因 signature 验证失败返回 400 错误
- 调试困难，无法确定问题发生在哪个环节

---

## 2. 解决方案

### 2.1 改进 Fallback 策略

**修改文件**: `src/anthropic_converter.py`

**原策略**（优先级 3）:
```python
# 使用最近缓存的签名和配对文本（fallback）
if not final_signature:
    last_result = get_last_signature_with_text()
    if last_result:
        final_signature, cached_thinking_text = last_result
        thinking_text = cached_thinking_text  # ⚠️ 问题：替换了当前文本！
```

**新策略**:
```python
# [FIX 2026-01-20] 移除优先级 3（替换文本策略）
# 原策略：使用最近缓存的签名和配对文本，但会替换当前 thinking_text
# 问题：替换文本可能导致上下文错乱，模型看到的是旧的 thinking 内容
# 新策略：如果前两层都未命中，直接降级为普通 text 块，保留原始内容

if not final_signature:
    # 直接降级为 text 块，保留原始内容
    if thinking_text and thinking_text.strip():
        parts.append({"text": f"[Thinking: {thinking_text}]"})
```

**优点**:
- 保留原始 thinking 内容，不会造成上下文错乱
- 避免 signature 与 thinking_text 不匹配导致的 API 400 错误
- 降级后的 text 块仍然包含思考过程，只是不再作为 thinking 块处理

### 2.2 添加详细日志监控

**新增日志标签**: `[THINKING MONITOR]`

**监控点**:

1. **接收 thinking 块时**:
```python
log.info(
    f"[THINKING MONITOR] 收到 thinking 块: "
    f"thinking_len={len(thinking_text)}, "
    f"has_signature={bool(message_signature)}, "
    f"signature_len={len(message_signature) if message_signature else 0}"
)
```

2. **签名恢复成功时**:
```python
log.info(
    f"[THINKING MONITOR] 签名恢复成功: "
    f"source={recovery_source}, "
    f"thinking_len={len(thinking_text)}, "
    f"sig_len={len(final_signature)}"
)
```

3. **签名恢复失败时**:
```python
log.warning(
    f"[THINKING MONITOR] 签名恢复失败，降级为 text 块: "
    f"thinking_len={len(thinking_text)}, "
    f"原因=无有效签名(cache_miss + message_signature_invalid)"
)
```

### 2.3 Cursor JS 补丁

**目录**: `patches/cursor-api-hijack/`

**文件列表**:
| 文件 | 说明 |
|------|------|
| `hijack.js` | 补丁核心代码 |
| `apply-patch.ps1` | Windows 补丁应用脚本 |
| `remove-patch.ps1` | Windows 补丁卸载脚本 |
| `README.md` | 使用文档 |

**功能**:
- 劫持 Cursor 的第三方 API 请求
- 重定向到 gcli2api 网关 (默认 `http://127.0.0.1:8181`)
- 自动检测 "Use own API key" 模式
- 支持配置网关地址
- 提供调试接口

**使用方法**:
```powershell
# 以管理员身份运行
cd F:\antigravity2api\gcli2api\patches\cursor-api-hijack
.\apply-patch.ps1
```

---

## 3. 签名恢复策略（更新后）

### 3.1 优先级顺序

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | 缓存精确匹配 | 使用 thinking_text 哈希查找缓存的 signature |
| 2 | 消息提供的签名 | 使用请求中携带的 signature（如果有效） |
| 3 | **降级为 text 块** | 保留原始内容，避免 API 验证错误 |

### 3.2 与旧策略对比

| 方面 | 旧策略 | 新策略 |
|------|--------|--------|
| 优先级 3 | 替换 thinking_text 为缓存的旧文本 | 直接降级为 text 块 |
| 上下文一致性 | ❌ 可能错乱 | ✅ 保持一致 |
| API 兼容性 | ❌ 可能触发 400 错误 | ✅ 安全降级 |
| 信息保留 | ✅ 保留 thinking 块类型 | ⚠️ 降级为普通文本 |

---

## 4. 日志分析指南

### 4.1 正常流程

```
[THINKING MONITOR] 收到 thinking 块: thinking_len=1500, has_signature=True, signature_len=200
[THINKING MONITOR] 签名恢复成功: source=message, thinking_len=1500, sig_len=200
```

### 4.2 缓存命中

```
[THINKING MONITOR] 收到 thinking 块: thinking_len=1500, has_signature=False, signature_len=0
[THINKING MONITOR] 签名恢复成功: source=cache, thinking_len=1500, sig_len=200
```

### 4.3 降级处理

```
[THINKING MONITOR] 收到 thinking 块: thinking_len=1500, has_signature=False, signature_len=0
[THINKING MONITOR] 签名恢复失败，降级为 text 块: thinking_len=1500, 原因=无有效签名(cache_miss + message_signature_invalid)
```

---

## 5. 测试验证

### 5.1 验证 Fallback 策略

1. 启动 gcli2api 网关
2. 使用 Cursor 进行多轮对话
3. 观察日志中的 `[THINKING MONITOR]` 标签
4. 确认无 "替换文本" 的日志出现

### 5.2 验证 Cursor 补丁

1. 应用补丁：`.\apply-patch.ps1`
2. 重启 Cursor
3. 打开开发者工具 (Ctrl+Shift+I)
4. 在 Console 中执行：`window.__GCLI2API_HIJACK_STATUS()`
5. 确认返回正确的状态信息

---

## 6. 后续优化建议

1. **增强缓存持久化**：将 signature 缓存持久化到 SQLite，避免服务重启后丢失
2. **添加 metrics 监控**：统计各恢复策略的使用频率，优化缓存策略
3. **Cursor 补丁自动更新**：检测 Cursor 更新后自动重新应用补丁
4. **WebSocket 支持**：劫持 Cursor 的 WebSocket 连接（如果有）

---

## 7. 相关文件

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `src/anthropic_converter.py` | 修改 | 改进 fallback 策略，添加日志 |
| `patches/cursor-api-hijack/hijack.js` | 新增 | Cursor 劫持补丁核心代码 |
| `patches/cursor-api-hijack/apply-patch.ps1` | 新增 | Windows 补丁应用脚本 |
| `patches/cursor-api-hijack/remove-patch.ps1` | 新增 | Windows 补丁卸载脚本 |
| `patches/cursor-api-hijack/README.md` | 新增 | 补丁使用文档 |

---

**报告完成时间**: 2026-01-20
**维护者**: 浮浮酱 (Claude Opus 4.5)
