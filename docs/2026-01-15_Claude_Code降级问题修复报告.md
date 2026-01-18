# Claude Code 模型降级问题修复报告

**日期**: 2026-01-15  
**问题**: Claude Code 在使用时，模型被直接降级为 gemini3，而 Cursor 等 IDE 行为正常

## 问题分析

### 问题现象
- Claude Code 客户端请求时，即使选择了 Claude 模型，也会被自动降级为 gemini3
- Cursor IDE 使用相同模型时行为正常，不会降级

### 根本原因

**位置**: `src/antigravity_api.py` 第 1037-1049 行

**问题逻辑**：
1. Claude Code 客户端检测 → `enable_cross_pool_fallback = True`
2. 获取当前模型凭证时，如果因为**模型级冷却**或**配额保护**暂时不可用
3. 代码**立即触发跨池降级**，直接降级到 gemini3
4. 更新请求体中的模型名为 gemini3 → 用户看到的是 gemini3 而不是 Claude

**关键代码**：
```python
# 第 1037-1049 行
if enable_cross_pool_fallback:
    # Claude Code 模式：尝试跨池降级
    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
    if fallback_model:
        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=fallback_model
        )
        if cred_result:
            # 更新请求体中的模型名
            request_body["model"] = fallback_model
            model_name = fallback_model  # 更新本地变量
            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")
```

### 为什么 Cursor 正常？

Cursor 的 `enable_cross_pool_fallback = False`，所以不会触发这个降级逻辑，即使凭证暂时不可用，也会等待或报错，而不是立即降级。

## 修复方案

### 修复策略

**核心原则**：跨池降级应该只在**真正无法获取凭证**时才触发，而不是在**暂时不可用**时就立即降级。

**修复逻辑**：
1. ✅ 先等待模型级冷却（已有逻辑，第 1003-1022 行）
2. ✅ 先尝试退化到任意凭证（已有逻辑，第 1024-1035 行）
3. ❌ **问题**：跨池降级在退化尝试之后立即触发，即使退化可能成功
4. ✅ **修复**：只有在退化尝试也失败后，才考虑跨池降级

### 修复代码

**修改位置**: `src/antigravity_api.py` 第 1037-1049 行

**修复前**：
```python
if enable_cross_pool_fallback:
    # Claude Code 模式：尝试跨池降级
    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
    if fallback_model:
        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 凭证不可用，尝试降级到 {fallback_model}")
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=fallback_model
        )
        if cred_result:
            # 更新请求体中的模型名
            request_body["model"] = fallback_model
            model_name = fallback_model  # 更新本地变量
            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")
```

**修复后**：
```python
# 只有在退化尝试也失败后，才考虑跨池降级
if enable_cross_pool_fallback and not cred_result:
    # Claude Code 模式：尝试跨池降级（仅在真正无法获取凭证时）
    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
    if fallback_model:
        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 所有凭证尝试均失败，尝试跨池降级到 {fallback_model}")
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=fallback_model
        )
        if cred_result:
            # 更新请求体中的模型名
            request_body["model"] = fallback_model
            model_name = fallback_model  # 更新本地变量
            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")
```

**关键改动**：
- 添加条件 `and not cred_result`：只有在退化尝试也失败后，才触发跨池降级
- 更新日志信息：明确说明是"所有凭证尝试均失败"后才降级

### 同时修复流式请求

**位置**: `src/antigravity_api.py` 第 567-579 行（流式请求的相同逻辑）

需要应用相同的修复。

## 验证步骤

1. **测试 Claude Code**：
   - 使用 Claude Code 发送请求，选择 Claude 模型
   - 验证不会因为模型级冷却而误降级到 gemini3
   - 验证只有在真正无法获取凭证时才降级

2. **测试 Cursor**：
   - 使用 Cursor 发送请求，验证行为不变
   - 验证不会因为修复而影响 Cursor 的正常使用

3. **测试降级功能**：
   - 模拟真正无法获取凭证的场景（如所有凭证都被禁用）
   - 验证跨池降级功能仍然正常工作

## 影响范围

- **修改文件**: 
  - `src/antigravity_api.py` (两处：流式和非流式请求)
- **影响功能**: 
  - Claude Code 客户端的模型选择行为
  - 跨池降级功能的触发时机
- **不影响**: 
  - Cursor 等其他客户端的正常使用
  - 其他降级逻辑（如额度用尽后的降级）

## 总结

本次修复解决了 Claude Code 客户端在使用时被误降级到 gemini3 的问题。修复后，跨池降级只会在真正无法获取凭证时才触发，而不是在凭证暂时不可用时立即降级。这确保了用户选择的模型能够被正确使用，同时保留了跨池降级作为最后的兜底机制。
