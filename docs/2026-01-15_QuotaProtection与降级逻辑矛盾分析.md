# QuotaProtection 与降级逻辑矛盾分析报告

**日期**: 2026-01-15  
**问题**: Claude Code 在使用时，由于 QuotaProtection 触发，模型被降级为 gemini3，但应该优先尝试其他凭证的同模型

## 问题分析

### 用户指出的核心问题

1. **Claude 模型的优先级应该最高**
2. **如果某个凭证的 Claude 模型不可用（被 QuotaProtection 保护），应该尝试其他凭证的 Claude 模型**
3. **不应该降级到同凭证的不同模型（比如从 Claude 降级到 Gemini）**

### 当前逻辑的问题

#### 1. QuotaProtection 的设计缺陷

**位置**: `src/quota_protection.py` 第 88-99 行

**问题**：
- QuotaProtection 是基于**凭证级别**的保护
- 当某个凭证的某个模型配额低于阈值时，会**禁用整个凭证**
- 例如：凭证A的 `claude-opus-4-5` 配额低 → 整个凭证A被禁用
- 但是凭证B可能还有 `claude-opus-4-5` 的配额，却无法使用

**代码**：
```python
# 第 88-99 行
if self._is_monitored_model(model_name, monitored_models):
    if percentage <= threshold:
        # 禁用账号（整个凭证）
        await self._disable_credential(
            credential_name, is_antigravity,
            f"配额保护: {model_name} = {percentage}%"
        )
        return False
```

#### 2. 凭证选择逻辑的问题

**位置**: `src/antigravity_api.py` 第 1024-1051 行

**当前逻辑流程**：
1. 尝试获取指定模型的凭证（`model_key=claude-opus-4-5`）
2. 如果失败（可能因为 QuotaProtection 保护了所有凭证的该模型）
3. 尝试获取任意凭证（`model_key=None`）→ **问题：这会获取任意模型的凭证**
4. 如果还失败，跨池降级到不同模型（如 gemini3）

**问题**：
- 第3步的 `model_key=None` 会获取任意模型的凭证，而不是继续尝试其他凭证的 `claude-opus-4-5`
- 应该优先尝试所有凭证的 `claude-opus-4-5`，而不是降级到不同模型

#### 3. get_valid_credential 的循环机制

**位置**: `src/credential_manager.py` 第 77-109 行

**当前机制**：
- `get_valid_credential` 有循环尝试机制（`max_attempts=10`）
- 每次循环会随机选择一个凭证
- 如果 QuotaProtection 拒绝，会尝试下一个

**问题**：
- 如果所有凭证的 `claude-opus-4-5` 都被 QuotaProtection 保护，循环10次后返回 None
- 此时应该继续尝试其他凭证的 `claude-opus-5`（如果有），而不是降级到 gemini3

## 修复方案

### 修复策略

**核心原则**：
1. **模型优先级最高**：优先尝试所有凭证的同一模型
2. **凭证降级优先于模型降级**：先尝试其他凭证的同模型，再考虑降级到不同模型
3. **QuotaProtection 应该基于模型级别**：只保护特定模型的配额，而不是整个凭证

### 修复方案1：改进凭证选择逻辑（推荐）

**位置**: `src/antigravity_api.py` 第 1024-1051 行

**修复逻辑**：
1. 尝试获取指定模型的凭证（`model_key=claude-opus-4-5`）
2. 如果失败，**增加重试次数**，确保尝试所有凭证的该模型
3. 如果所有凭证的该模型都不可用，考虑：
   - **优先**：尝试其他 Claude 模型（如 `claude-sonnet-4-5`）
   - **最后**：跨池降级到 Gemini

**修复代码**：
```python
# 修复前：第 1024-1036 行
if model_name:
    try:
        relaxed = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=None  # ❌ 问题：获取任意模型
        )
    except Exception:
        relaxed = None
    if relaxed:
        log.warning(
            f"[ANTIGRAVITY] model_key={model_name} 无可用凭证，已退化为任意凭证选择"
        )
        cred_result = relaxed

# 修复后
if model_name:
    # 先尝试增加重试次数，确保尝试所有凭证的该模型
    # get_valid_credential 已经有 max_attempts=10，但可能需要更多
    
    # 如果仍然失败，优先尝试其他 Claude 模型
    from src.fallback_manager import get_model_pool, CLAUDE_THIRD_PARTY_POOL
    
    current_pool = get_model_pool(model_name)
    if current_pool == "claude" and not cred_result:
        # 尝试其他 Claude 模型（按优先级）
        claude_models = [
            "claude-opus-4-5-thinking",
            "claude-sonnet-4-5-thinking", 
            "claude-sonnet-4-5",
            # ... 其他 Claude 模型
        ]
        for alt_model in claude_models:
            if alt_model != model_name:
                alt_cred = await credential_manager.get_valid_credential(
                    is_antigravity=True, model_key=alt_model
                )
                if alt_cred:
                    log.warning(
                        f"[ANTIGRAVITY] {model_name} 不可用，尝试其他 Claude 模型: {alt_model}"
                    )
                    request_body["model"] = alt_model
                    model_name = alt_model
                    cred_result = alt_cred
                    break
    
    # 如果 Claude 模型都不可用，再考虑跨池降级
    if not cred_result and enable_cross_pool_fallback:
        # ... 跨池降级逻辑
```

### 修复方案2：改进 QuotaProtection（长期方案）

**位置**: `src/quota_protection.py`

**修复思路**：
- QuotaProtection 应该基于**模型级别**，而不是凭证级别
- 只保护特定模型的配额，不影响其他模型的使用
- 需要修改数据结构，支持模型级别的禁用

**实现复杂度**：高（需要修改数据结构和存储逻辑）

## 推荐方案

**优先采用修复方案1**：
- 实现简单，风险低
- 可以立即解决当前问题
- 不需要修改 QuotaProtection 的核心逻辑

**长期考虑修复方案2**：
- 更符合设计原则
- 但需要较大的重构工作

## 验证步骤

1. **测试场景1**：所有凭证的 `claude-opus-4-5` 都被 QuotaProtection 保护
   - 应该尝试其他凭证的 `claude-opus-4-5`
   - 如果都不可用，应该尝试其他 Claude 模型（如 `claude-sonnet-4-5`）
   - 不应该直接降级到 gemini3

2. **测试场景2**：部分凭证的 `claude-opus-4-5` 被保护
   - 应该使用未被保护的凭证的 `claude-opus-4-5`
   - 不应该降级

3. **测试场景3**：所有 Claude 模型都不可用
   - 此时才应该考虑跨池降级到 Gemini

## 总结

当前的设计存在矛盾：
- QuotaProtection 基于凭证级别，但应该基于模型级别
- 降级逻辑优先考虑模型降级，但应该优先考虑凭证降级

修复方案1可以立即解决当前问题，确保 Claude 模型的优先级最高，优先尝试其他凭证的同模型，而不是降级到不同模型。
