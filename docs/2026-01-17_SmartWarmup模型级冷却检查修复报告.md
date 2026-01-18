# SmartWarmup 模型级冷却检查修复报告

**日期**: 2026-01-17
**版本**: v7.1
**修复者**: 浮浮酱 (Claude Opus 4.5)

## 问题描述

用户反馈：**"已经进入冷却时间的凭证，即使额度为100%，还是会被预热"**

每到定时周期，SmartWarmup 就会触发预热，完全忽略了凭证的模型级冷却状态。

## 问题根因

### 概念混淆

系统中存在两种不同的"冷却"概念：

| 概念 | 来源 | 作用 | 存储位置 |
|------|------|------|----------|
| **模型级冷却** (`model_cooldowns`) | 429 QUOTA_EXHAUSTED 触发 | 阻止凭证被选中使用 | SQLite/MongoDB `model_cooldowns` 字段 |
| **预热冷却** (`COOLDOWN_SECONDS`) | SmartWarmup 内部逻辑 | 防止同一周期重复预热 | `warmup_history.json` |

### 缺失的检查

在 `_scan_and_warmup()` 方法中，SmartWarmup 只检查了：
1. ✅ 凭证是否被禁用 (`is_disabled`)
2. ✅ 配额是否为 100%
3. ✅ 预热历史记录（防止同周期重复预热）

但**缺少**：
- ❌ **没有检查 `model_cooldowns` 中的模型级冷却时间！**

## 修复方案

### 修改文件

`gcli2api/src/smart_warmup.py`

### 修改内容

#### 1. 在凭证遍历时获取 model_cooldowns 状态

```python
# [FIX 2026-01-17 v7.1] 获取模型级冷却状态
# 如果凭证的某个模型在冷却期内，即使配额为100%也不应该预热
cred_state = await self.credential_manager._storage_adapter.get_credential_state(
    cred_name, is_antigravity=True
)
model_cooldowns = cred_state.get("model_cooldowns", {}) or {}
```

#### 2. 在模型遍历时检查冷却状态

```python
# [FIX 2026-01-17 v7.1] 检查模型级冷却
# 如果该模型在冷却期内，即使配额为100%也不应该预热
model_cooldown_until = model_cooldowns.get(model_name)
if model_cooldown_until is not None and now < model_cooldown_until:
    remaining_cooldown = model_cooldown_until - now
    log.debug(
        f"[SmartWarmup] {key} 模型级冷却中，跳过预热 "
        f"(剩余 {remaining_cooldown/3600:.1f}h)"
    )
    skipped_cooldown += 1
    continue
```

### 修改后的检查流程

现在 SmartWarmup 使用**三重保险**机制：

1. **检查1（新增）**: 模型级冷却检查 (`model_cooldowns`)
   - 如果模型在冷却期内，直接跳过

2. **检查2（优先）**: 基于 `resetTimeRaw` 判断当前周期是否已预热
   - 解析 API 返回的下次重置时间，判断是否在同一周期

3. **检查3（保底）**: 基于本地计时，5小时内只允许一次
   - 当 `resetTimeRaw` 解析失败时的兜底方案

## 验证

- ✅ Python 语法检查通过
- ✅ 逻辑流程正确
- ✅ 日志输出清晰

## 影响范围

- 仅影响 SmartWarmup 模块的预热判断逻辑
- 不影响凭证选择、配额查询等其他功能
- 向后兼容，无需数据库迁移

## 总结

此修复确保了：
- 当凭证的某个模型因 429 QUOTA_EXHAUSTED 进入冷却期时
- 即使该模型的配额显示为 100%
- SmartWarmup 也不会对其进行预热
- 避免了无效的预热请求和潜在的配额浪费
