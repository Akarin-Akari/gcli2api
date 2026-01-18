# Kiro Gateway 集成说明

## 概述

gcli2api 总网关（7861端口）现已支持将请求路由到 kiro-gateway（9046端口）。

## 配置方式

### 1. 环境变量配置

#### 启用/禁用 Kiro Gateway

```bash
# 启用（默认）
export KIRO_GATEWAY_ENABLED=true

# 禁用
export KIRO_GATEWAY_ENABLED=false
```

#### 配置 Kiro Gateway 基础 URL

```bash
# 默认值: http://127.0.0.1:9046/v1
# 注意：endpoint 传入时是 /chat/completions（不带 /v1 前缀）
# 最终 URL = base_url + endpoint
# 例如：http://127.0.0.1:9046/v1 + /chat/completions = http://127.0.0.1:9046/v1/chat/completions
# 
# 如果 kiro-gateway 不使用 /v1 前缀，可以设置为：
export KIRO_GATEWAY_BASE_URL=http://127.0.0.1:9046
# 这样最终 URL = http://127.0.0.1:9046/chat/completions
```

#### 配置超时时间

```bash
# 普通请求超时（默认: 120秒）
export KIRO_GATEWAY_TIMEOUT=120.0

# 流式请求超时（默认: 600秒，10分钟）
export KIRO_GATEWAY_STREAM_TIMEOUT=600.0

# 最大重试次数（默认: 2次）
export KIRO_GATEWAY_MAX_RETRIES=2
```

#### 配置路由模型列表

通过 `KIRO_GATEWAY_MODELS` 环境变量指定哪些模型应该路由到 kiro-gateway：

```bash
# 格式：逗号分隔的模型名称列表
export KIRO_GATEWAY_MODELS="gpt-4,claude-3-opus,gemini-pro"
```

**路由优先级：**
1. **Kiro Gateway**（如果模型在 `KIRO_GATEWAY_MODELS` 列表中）
2. **Antigravity**（如果模型被 Antigravity 支持）
3. **Copilot**（其他情况）

## 使用示例

### 示例 1: 将所有 GPT-4 请求路由到 Kiro Gateway

```bash
export KIRO_GATEWAY_MODELS="gpt-4,gpt-4-turbo,gpt-4o"
```

### 示例 2: 将特定 Claude 模型路由到 Kiro Gateway

```bash
export KIRO_GATEWAY_MODELS="claude-3-opus,claude-3.5-sonnet"
```

### 示例 3: 使用自定义 API 路径

如果 kiro-gateway 不使用标准的 `/v1` 路径：

```bash
export KIRO_GATEWAY_BASE_URL="http://127.0.0.1:9046/api/v1"
```

## 路由逻辑

1. **精确匹配**：如果请求的模型名称完全匹配 `KIRO_GATEWAY_MODELS` 中的某个模型，则路由到 kiro-gateway
2. **模糊匹配**：如果规范化后的模型名称（去除后缀如 `-thinking`, `-preview` 等）匹配配置的模式，也会路由到 kiro-gateway
3. **未配置**：如果 `KIRO_GATEWAY_MODELS` 未设置或为空，则不会路由到 kiro-gateway（除非其他后端都失败）

## 故障转移

如果 kiro-gateway 不可用或返回错误，网关会：
1. 根据配置的重试次数进行重试
2. 如果所有重试都失败，会根据路由策略尝试其他后端（Antigravity 或 Copilot）

## 日志

路由决策会记录在日志中，格式如下：

```
[GATEWAY] Model gpt-4 -> Kiro Gateway (configured)
[GATEWAY] Model claude-3-opus -> Kiro Gateway (pattern match: claude-3-opus)
```

## 注意事项

1. **API 格式兼容性**：确保 kiro-gateway 支持 OpenAI 兼容的 API 格式（`/v1/chat/completions` 等）
2. **端口检查**：确保 kiro-gateway 正在 9046 端口运行
3. **模型名称**：配置模型名称时使用小写，网关会自动进行大小写不敏感匹配

## 验证配置

启动 gcli2api 后，查看启动日志，应该能看到：

```
统一网关 (自动故障转移):
   Gateway API: http://127.0.0.1:7861/gateway/v1
   优先级: Antigravity > Copilot > Kiro Gateway
   Kiro Gateway 路由模型: gpt-4, claude-3-opus
```

如果配置了 `KIRO_GATEWAY_MODELS`，会显示路由的模型列表。
