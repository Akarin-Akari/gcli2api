# Kiro Gateway 日志问题排查指南

**日期**: 2026-01-22  
**问题**: kiro-gateway 的日志没有看到任何 sonnet 模型的记录  
**状态**: 🔍 排查中

## 诊断结果

通过 `diagnose_kiro_routing.py` 脚本检查，配置和路由逻辑都是正确的：

✅ **配置正确**:
- `claude-sonnet-4.5` 路由规则已加载
- 第一个后端是 `kiro-gateway`
- `kiro-gateway` 后端已启用
- 模型支持检查通过

✅ **路由决策正确**:
- `get_backend_and_model_for_routing("claude-sonnet-4.5")` 返回 `kiro-gateway`

## 可能的原因

### 1. 请求没有走到 `route_request_with_fallback`

**检查方法**:
- 查看应用日志中是否有 `[GATEWAY] route_request_with_fallback called` 日志
- 查看是否有 `[GATEWAY] Found routing_rule for claude-sonnet-4.5` 日志

**可能原因**:
- 请求被其他路径处理（如直接调用 `antigravity_service`）
- 请求使用的端点不是 `/chat/completions`

### 2. 请求使用的模型名称不是 `claude-sonnet-4.5`

**检查方法**:
- 查看实际请求中的 `model` 字段值
- 检查是否有模型名称映射或转换

**常见情况**:
- 请求使用 `claude-sonnet-4-5`（带连字符）而不是 `claude-sonnet-4.5`（带点）
- 请求使用 `claude-sonnet-4.5-thinking` 等变体

### 3. 日志级别设置问题

**检查方法**:
- 确认日志级别设置为 `INFO` 或更低
- 检查日志过滤器是否过滤了 `GATEWAY` 标签

**已添加的日志**:
- `[GATEWAY] route_request_with_fallback called` (DEBUG)
- `[GATEWAY] Found routing_rule for {model}` (INFO)
- `[GATEWAY] ✅ Using model_routing chain for {model}` (INFO)
- `[GATEWAY] 🔄 Trying backend: {name} ({key})` (INFO)
- `[GATEWAY] 🎯 KIRO GATEWAY REQUEST` (INFO)
- `[GATEWAY] 🎯 KIRO GATEWAY: Converting endpoint` (INFO)

### 4. Antigravity 本地服务直调

**问题**: `proxy_request_to_backend` 函数中，如果 `backend_key == "antigravity"`，会直接调用本地服务，绕过 HTTP 请求。

**检查方法**:
- 查看是否有 `Local antigravity service call` 相关日志
- 检查请求是否在到达 `route_request_with_fallback` 之前就被处理

## 排查步骤

### 步骤 1: 检查实际请求

```bash
# 查看应用日志，搜索以下关键词：
grep -i "route_request_with_fallback" logs/app.log
grep -i "claude-sonnet-4.5" logs/app.log
grep -i "kiro" logs/app.log
```

### 步骤 2: 检查模型名称

在请求处理函数中添加日志：

```python
# 在 chat_completions 函数中
model = body.get("model", "")
log.info(f"[DEBUG] Request model: {model}", tag="GATEWAY")
```

### 步骤 3: 检查路由决策

在 `route_request_with_fallback` 函数开始处添加日志：

```python
log.info(f"[GATEWAY] route_request_with_fallback: model={model}, endpoint={endpoint}", tag="GATEWAY")
```

### 步骤 4: 检查后端链构建

查看是否有以下日志：
- `[GATEWAY] Found model_routing rule for {model}`
- `[GATEWAY] ✅ Using model_routing chain for {model}`
- `[GATEWAY] 🎯 KIRO GATEWAY REQUEST`

如果没有这些日志，说明：
1. 请求没有走到 `route_request_with_fallback`
2. 或者 `model_routing` 规则没有被找到

## 已添加的调试日志

在 `route_request_with_fallback` 函数中添加了以下日志：

1. **函数入口日志**:
   ```python
   log.debug(f"[GATEWAY] route_request_with_fallback called: model={model}, endpoint={endpoint}")
   ```

2. **路由规则检查日志**:
   ```python
   log.info(f"[GATEWAY] Found routing_rule for {model}: enabled={routing_rule.enabled}")
   ```

3. **后端链构建日志**:
   ```python
   log.info(f"[GATEWAY] Found model_routing rule for {model}: enabled={routing_rule.enabled}, chain_length={len(routing_rule.backend_chain)}")
   log.debug(f"[GATEWAY] Checking backend {backend_key}: enabled={backend_enabled}, target_model={target_model}")
   log.info(f"[GATEWAY] ✅ Kiro Gateway supports {target_model}, adding to chain")
   log.info(f"[GATEWAY] ✅ Using model_routing chain for {model}: {[b[0] for b in backend_chain]}")
   ```

4. **后端尝试日志**:
   ```python
   log.info(f"[GATEWAY] 🔄 Trying backend: {backend_config['name']} ({backend_key}) for {endpoint} (model={target_model or model})")
   log.info(f"[GATEWAY] 🎯 KIRO GATEWAY REQUEST: model={target_model or model}, endpoint={endpoint}")
   ```

5. **Kiro Gateway 转换日志**:
   ```python
   log.info(f"[GATEWAY] 🎯 KIRO GATEWAY: Converting endpoint /chat/completions -> /messages (model={model_name})")
   log.info(f"[GATEWAY] 🎯 KIRO GATEWAY: Converted request body to Anthropic format (model={model_name})")
   ```

## 下一步行动

1. **重启应用**，确保新的日志代码生效
2. **发送一个测试请求**，使用 `claude-sonnet-4.5` 模型
3. **查看日志**，检查是否有上述日志输出
4. **如果仍然没有日志**，检查：
   - 请求是否真的到达了 `route_request_with_fallback` 函数
   - 是否有其他代码路径处理了请求
   - 日志配置是否正确

## 相关文件

- `gcli2api/src/unified_gateway_router.py` - 主要路由逻辑
- `gcli2api/diagnose_kiro_routing.py` - 诊断脚本
- `gcli2api/config/gateway.yaml` - 路由配置
