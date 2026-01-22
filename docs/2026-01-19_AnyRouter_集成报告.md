# AnyRouter 网关集成报告

**日期**: 2026-01-19
**作者**: 浮浮酱 (Claude Opus 4.5)
**状态**: ✅ 完成

---

## 概述

本报告记录了将 AnyRouter 公益站第三方 API 集成到 gcli2api 网关系统的完整过程。

### 需求

- 添加 AnyRouter 作为新的后端网关
- 优先级：Antigravity (1) → Kiro Gateway (2) → **AnyRouter (3)** → Copilot (4)
- 支持多端点轮询（3个端点）
- 支持多 API Key 轮换（2个 Key）
- 使用 Anthropic API 格式

---

## 配置详情

### 后端配置 (`gateway/config.py`)

```python
"anyrouter": {
    "name": "AnyRouter",
    "base_urls": [
        "https://anyrouter.top",
        "https://pmpjfbhq.cn-nb1.rainapp.top",
        "https://a-ocnfniawgw.cn-shanghai.fcapp.run"
    ],
    "api_keys": [
        "sk-E4L18390pp12BacrKa7IJV8hgztEo8SsPKFdtSYGx6vLEbDK",
        "sk-be7LKJwag3qXSRL77tVbxUsIHEi71UfAVOvqjGI13BJiXGD5"
    ],
    "priority": 3,
    "timeout": 120.0,
    "stream_timeout": 600.0,
    "max_retries": 1,
    "enabled": True,
    "api_format": "anthropic",
}
```

### 环境变量配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ANYROUTER_BASE_URLS` | 见上方 | 逗号分隔的端点列表 |
| `ANYROUTER_API_KEYS` | 见上方 | 逗号分隔的 API Key 列表 |
| `ANYROUTER_TIMEOUT` | 120.0 | 普通请求超时（秒） |
| `ANYROUTER_STREAM_TIMEOUT` | 600.0 | 流式请求超时（秒） |
| `ANYROUTER_MAX_RETRIES` | 1 | 最大重试次数 |
| `ANYROUTER_ENABLED` | true | 是否启用 |

---

## 支持的模型

来自 AnyRouter 官方：

```
claude-3-5-haiku-20241022
claude-3-5-sonnet-20241022
claude-3-7-sonnet-20250219
claude-haiku-4-5-20251001
claude-opus-4-1-20250805
claude-opus-4-20250514
claude-opus-4-5-20251101
claude-sonnet-4-20250514
claude-sonnet-4-5-20250929
gemini-2.5-pro
gpt-5-codex
```

### 模型匹配规则

1. **Claude 模型**：任何包含 `claude` 的模型名称
2. **Gemini 2.5**：包含 `gemini` 和 `2.5` 的模型
3. **GPT-5 Codex**：包含 `gpt-5` 和 `codex` 的模型

---

## 降级链路图

```
请求到达
    ↓
[Antigravity] ──失败──→ [Kiro Gateway] ──失败──→ [AnyRouter] ──失败──→ [Copilot] ──失败──→ 503
      ↓                       ↓                       ↓                    ↓
   成功返回                成功返回                成功返回             成功返回
```

### AnyRouter 模型过滤

```
非 Claude/Gemini2.5/GPT5Codex 模型
    ↓
[跳过 AnyRouter] ──→ [Copilot]
```

---

## 技术实现

### 1. 多端点轮询机制

```python
# 获取当前端点
get_anyrouter_endpoint() -> (base_url, api_key)

# 轮换到下一个端点
rotate_anyrouter_endpoint(rotate_url=True, rotate_key=False)

# 获取所有端点组合
get_anyrouter_all_endpoints() -> [(url, key), ...]
```

**轮询策略**：
- 端点失败时自动轮换到下一个
- API Key 保持不变以维持会话
- 如果所有端点都失败，降级到 Copilot

### 2. 请求格式转换

AnyRouter 使用 Anthropic API 格式，需要转换：

**请求转换** (OpenAI → Anthropic)：
- 端点：`/chat/completions` → `/v1/messages`
- 请求体：使用 `_convert_openai_to_anthropic_body()` 函数
- 请求头：添加 `x-api-key` 和 `anthropic-version`

**响应转换** (Anthropic → OpenAI)：
- 非流式：使用 `_convert_anthropic_to_openai_response()` 函数
- 流式：使用 `_convert_anthropic_stream_to_openai()` 异步生成器

### 3. 会话保持策略

为保持会话和缓存的连续性：
- 优先轮换端点，不轮换 API Key
- 同一个 API Key 会尝试所有端点
- 只有在所有端点都失败时才考虑换 Key

---

## 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `gateway/config.py` | 添加 AnyRouter 后端配置和辅助函数 |
| `gateway/routing.py` | 添加 `is_anyrouter_supported()` 函数 |
| `unified_gateway_router.py` | 添加 AnyRouter 请求处理和格式转换 |

---

## 使用示例

### 禁用 AnyRouter

```bash
export ANYROUTER_ENABLED=false
```

### 修改端点列表

```bash
export ANYROUTER_BASE_URLS="https://new-endpoint1.com,https://new-endpoint2.com"
```

### 修改 API Key

```bash
export ANYROUTER_API_KEYS="sk-new-key-1,sk-new-key-2"
```

---

## 注意事项

1. **公益站特性**：
   - 每日签到领取额度
   - 不稳定，需要多端点轮询
   - 三个端点都失败也无所谓，会降级到 Copilot

2. **安全提醒**：
   - API Key 已硬编码在配置中（按用户要求）
   - 生产环境建议使用环境变量覆盖

3. **会话保持**：
   - 尽量保持同一个 API Key 以维持会话
   - 端点切换不影响会话状态

---

## 测试建议

1. **基本功能测试**：
   ```bash
   curl -X POST http://localhost:7861/gateway/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model": "claude-sonnet-4.5", "messages": [{"role": "user", "content": "Hello"}]}'
   ```

2. **强制使用 AnyRouter**：
   暂时禁用 Antigravity 和 Kiro Gateway 来测试

3. **端点轮换测试**：
   模拟端点失败，观察是否自动切换

---

_记录完成喵～ ฅ'ω'ฅ_
