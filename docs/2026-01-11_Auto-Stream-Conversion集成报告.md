# Auto-Stream Conversion 功能集成报告

**日期**：2026-01-11  
**版本**：gcli2api v2.x  
**功能目标**：消除 429 Resource Exhausted 错误

---

## 背景

Google API 对**流式请求**的配额限制比**非流式请求**宽松得多。这导致 gcli2api 在处理非流式请求时频繁遇到 `429 Resource Exhausted` 错误。

## 解决方案

移植 Antigravity_Tools 项目的 **Auto-Stream Conversion** 功能：
- 在代理层将所有非流式请求自动转换为流式请求
- 收集 SSE 流响应并重组为 JSON 格式返回给客户端

## 变更文件

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/sse_collector.py` | SSE 收集器模块，将 SSE 流转换为 JSON |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/antigravity_api.py` | 修改 `send_antigravity_request_no_stream()` 内部使用流式 API |

## 技术细节

### 核心逻辑

```python
# 原逻辑
f"{antigravity_url}/v1internal:generateContent"

# 新逻辑
f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse"
response_data = await collect_sse_to_json(response.aiter_lines())
```

### 函数命名说明

`send_antigravity_request_no_stream()` 函数名称保持不变，但内部实际使用流式 API。

这是有意设计：
1. **配额优势**：流式请求配额更宽松
2. **向后兼容**：调用方无需修改代码
3. **透明转换**：客户端感知不到转换过程

## 预期效果

| 指标 | 改造前 | 改造后 |
|------|--------|--------|
| 非流式请求成功率 | 10-20% | **95%+** |
| 429 错误发生率 | 频繁 | **几乎消除** |
| 响应延迟 | - | +100-200ms |

## 验证方式

日志中应出现：
```
[ANTIGRAVITY] 🔄 Auto-converting non-stream to stream for better quota
[ANTIGRAVITY] ✓ SSE collected and converted to JSON
```

## 参考来源

- Antigravity_Tools `src-tauri/src/proxy/handlers/claude.rs:622-700`
- Antigravity_Tools `src-tauri/src/proxy/mappers/claude/collector.rs`

---

*报告生成时间：2026-01-11*
