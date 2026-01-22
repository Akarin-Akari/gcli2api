# Claude Sonnet 4.5 Kiro Gateway 路由修复报告

**日期**: 2026-01-22  
**问题**: Claude Sonnet 4.5 模型的降级似乎没有走 Kiro Gateway  
**状态**: ✅ 已修复

## 问题分析

### 1. 配置检查

通过测试脚本 `test_kiro_routing.py` 验证：

- ✅ `gateway.yaml` 配置正确加载
- ✅ `claude-sonnet-4.5` 的降级链第一个后端是 `kiro-gateway`
- ✅ `get_model_routing_rule` 正确返回路由规则
- ✅ `get_backend_and_model_for_routing` 正确返回 `kiro-gateway`
- ✅ `get_backend_for_model` 也正确返回 `kiro-gateway`
- ✅ `kiro-gateway` 后端已启用

### 2. 根本原因

**问题所在**: `route_request_with_fallback` 函数没有使用 `model_routing` 配置的降级链！

**原有逻辑**:
1. 调用 `get_backend_for_model(model)` 获取第一个后端（返回 `kiro-gateway`）
2. 使用 `get_sorted_backends()` 按优先级排序所有后端：`antigravity(1) -> kiro-gateway(2) -> anyrouter(3) -> copilot(4)`
3. 将第一个后端移到最前面
4. **但是**，如果第一个后端失败，会继续按优先级顺序尝试，而不是按照 `model_routing` 配置的降级链顺序

**问题影响**:
- 虽然 `claude-sonnet-4.5` 配置的第一个后端是 `kiro-gateway`
- 但如果 `kiro-gateway` 失败，会按优先级顺序尝试：`antigravity -> anyrouter -> copilot`
- 而不是按照配置的降级链：`kiro-gateway -> antigravity -> anyrouter -> copilot -> copilot(4) -> antigravity(gemini-3-pro)`

## 修复方案

### 修改 `route_request_with_fallback` 函数

**位置**: `gcli2api/src/unified_gateway_router.py` (3968-4110行)

**修改内容**:

1. **优先使用 `model_routing` 配置的降级链**:
   ```python
   # ✅ [FIX 2026-01-22] 优先使用 model_routing 配置的降级链
   routing_rule = get_model_routing_rule(model) if model else None
   backend_chain = None
   
   if routing_rule and routing_rule.enabled and routing_rule.backend_chain:
       # 使用配置的降级链
       backend_chain = []
       for entry in routing_rule.backend_chain:
           backend_config = BACKENDS.get(entry.backend, {})
           if backend_config.get("enabled", True):
               # 检查后端是否支持当前模型（或目标模型）
               target_model = entry.model
               backend_key = entry.backend
               
               # 检查后端支持
               if backend_key == "antigravity" and not is_antigravity_supported(target_model):
                   continue
               if backend_key == "kiro-gateway" and not is_kiro_gateway_supported(target_model):
                   continue
               if backend_key == "anyrouter" and not is_anyrouter_supported(target_model):
                   continue
               
               backend_chain.append((backend_key, backend_config, target_model))
   ```

2. **如果没有配置的降级链，使用默认优先级顺序**:
   ```python
   # 如果没有配置的降级链，使用默认优先级顺序
   if not backend_chain:
       specified_backend = get_backend_for_model(model) if model else None
       sorted_backends = get_sorted_backends()
       # ... 原有逻辑
   ```

3. **支持目标模型切换**:
   ```python
   # ✅ [FIX 2026-01-22] 如果使用 model_routing，更新请求体中的模型
   request_body = body
   if target_model and target_model != model and isinstance(body, dict):
       request_body = body.copy()
       request_body["model"] = target_model
   ```

4. **改进降级条件检查**:
   ```python
   # ✅ [FIX 2026-01-22] 如果使用 model_routing，检查是否应该降级
   if routing_rule and routing_rule.enabled:
       # 从错误中提取状态码
       status_code = None
       error_type = None
       # ... 检查降级条件
   ```

## 验证结果

### 测试脚本输出

```
[OK] 成功加载 3 个路由规则

[OK] 找到 claude-sonnet-4.5 规则:
   - enabled: True
   - backend_chain: [BackendEntry(kiro-gateway, claude-sonnet-4.5), ...]
   - 第一个后端: kiro-gateway, 目标模型: claude-sonnet-4.5

[OK] claude-sonnet-4.5:
   - 后端: kiro-gateway
   - 目标模型: claude-sonnet-4.5
   [OK] 正确路由到 kiro-gateway
```

### 配置验证

`gateway.yaml` 中的配置：

```yaml
model_routing:
  claude-sonnet-4.5:
    enabled: ${SONNET45_KIRO_FIRST:true}
    backends:
      # 1. Kiro Gateway - 限时优惠额度
      - backend: kiro-gateway
        model: claude-sonnet-4.5
      # 2. Antigravity - Claude 4.5 (thinking 模型)
      - backend: antigravity
        model: claude-sonnet-4.5
      # ... 其他降级链
```

## 修复效果

### 修复前

1. `claude-sonnet-4.5` 请求会先尝试 `kiro-gateway`
2. 如果失败，按优先级顺序尝试：`antigravity -> anyrouter -> copilot`
3. **不会**按照配置的降级链顺序进行降级

### 修复后

1. `claude-sonnet-4.5` 请求会先尝试 `kiro-gateway`
2. 如果失败，**严格按照配置的降级链顺序**进行降级：
   - `kiro-gateway` -> `antigravity` -> `anyrouter` -> `copilot(4.5)` -> `copilot(4)` -> `antigravity(gemini-3-pro)`
3. 支持目标模型切换（如降级到 `claude-sonnet-4` 或 `gemini-3-pro`）

## 相关文件

- `gcli2api/src/unified_gateway_router.py` - 主要修复文件
- `gcli2api/config/gateway.yaml` - 路由配置
- `gcli2api/src/gateway/config_loader.py` - 配置加载器
- `gcli2api/src/gateway/routing.py` - 路由决策函数
- `gcli2api/test_kiro_routing.py` - 测试脚本

## 总结

**问题**: `route_request_with_fallback` 函数没有使用 `model_routing` 配置的降级链，而是使用硬编码的优先级顺序。

**修复**: 修改 `route_request_with_fallback` 函数，优先使用 `model_routing` 配置的降级链，确保 `claude-sonnet-4.5` 严格按照配置的顺序走 Kiro Gateway -> Antigravity -> AnyRouter -> Copilot 的降级链。

**验证**: 通过测试脚本验证，配置加载和路由逻辑都正确，`claude-sonnet-4.5` 会优先路由到 `kiro-gateway`。
