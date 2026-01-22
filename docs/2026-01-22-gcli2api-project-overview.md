# 🚀 gcli2api 项目概览文档

> **项目名称**: gcli2api - Akarin Fork 增强版 (阿卡林网关)  
> **文档类型**: 项目架构概览  
> **生成日期**: 2026-01-22  
> **生成工具**: ACE (Augment Context Engine)

---

## 📋 目录

1. [项目简介](#项目简介)
2. [核心价值](#核心价值)
3. [技术栈](#技术栈)
4. [项目结构](#项目结构)
5. [核心模块](#核心模块)
6. [API 路由系统](#api-路由系统)
7. [后端服务配置](#后端服务配置)
8. [路由策略](#路由策略)
9. [高级特性](#高级特性)
10. [部署方式](#部署方式)

---

## 项目简介

gcli2api 是 [su-kaka/gcli2api](https://github.com/su-kaka/gcli2api) 的**增强 Fork 版本**，是一个**统一 API 网关系统**，用于聚合多个 AI 模型服务后端，提供统一的 API 入口、智能路由、故障转移和负载均衡功能。

### 代码规模

| 功能模块 | 代码行数 | 说明 |
|---------|---------|------|
| **统一 API 网关** | 3,701 行 | 多后端整合、智能路由 |
| **IDE 兼容层** | 2,484 行 | Cursor/Claude Code/Windsurf/Augment 支持 |
| **Augment 兼容层** | 1,929 行 | NDJSON 流协议支持 |
| **思维签名缓存系统** | 9,013 行 | 智能缓存与降级 |
| **总计** | 21,000+ 行 | 相比官方版本增加 310% |

---

## 核心价值

- **统一入口**: 将多个后端服务整合到单一 API 端点
- **智能路由**: 根据模型名称、任务类型自动选择最优后端
- **故障转移**: 主后端失败时自动切换到备用后端
- **灵活扩展**: 支持动态添加新的后端服务
- **格式兼容**: 支持 OpenAI、Anthropic、Gemini 等多种 API 格式

---

## 技术栈

| 层级 | 技术选型 |
|------|---------|
| **Web 框架** | FastAPI (Python 3.12+) |
| **异步处理** | asyncio, httpx |
| **服务器** | Hypercorn (ASGI) |
| **日志系统** | 自定义日志系统 |
| **配置管理** | 环境变量 + YAML 配置文件 |
| **前端 (可选)** | React + TypeScript + Tauri |

---

## 项目结构

```
gcli2api/
├── web.py                    # 🚀 主入口文件，集成所有路由
├── config.py                 # ⚙️ 配置管理模块
├── log.py                    # 📝 日志系统
├── config/
│   └── gateway.yaml          # 🔧 网关配置文件
├── src/
│   ├── __init__.py
│   │
│   │── # ==================== 核心路由模块 ====================
│   ├── openai_router.py              # OpenAI 兼容路由
│   ├── gemini_router.py              # Gemini 原生路由
│   ├── antigravity_router.py         # Antigravity API 路由
│   ├── antigravity_anthropic_router.py  # Anthropic Messages 格式路由
│   ├── unified_gateway_router.py     # 统一网关路由 (核心)
│   ├── web_routes.py                 # Web 控制台路由
│   │
│   │── # ==================== 网关子模块 ====================
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── router.py         # 主路由
│   │   ├── backends.py       # 后端管理
│   │   ├── routing.py        # 路由决策
│   │   ├── proxy.py          # 代理层
│   │   ├── normalization.py  # 请求规范化
│   │   └── endpoints/        # API 端点
│   │       └── openai.py
│   │
│   │── # ==================== 格式转换模块 ====================
│   ├── anthropic_converter.py        # Anthropic 格式转换
│   ├── anthropic_streaming.py        # Anthropic 流式处理
│   ├── openai_transfer.py            # OpenAI 格式转换
│   │
│   │── # ==================== 凭证与认证 ====================
│   ├── auth.py                       # OAuth 2.0 认证
│   ├── credential_manager.py         # 多凭证管理
│   │
│   │── # ==================== IDE 兼容层 ====================
│   ├── ide_compat/
│   │   ├── __init__.py
│   │   ├── client_detector.py        # 客户端类型检测
│   │   ├── sanitizer.py              # 消息净化
│   │   ├── middleware.py             # 请求/响应中间件
│   │   ├── hash_cache.py             # 内容哈希缓存
│   │   └── state_manager.py          # SCID 状态机
│   │
│   │── # ==================== 高级特性 ====================
│   ├── anti_truncation.py            # 流式抗截断机制
│   ├── fallback_manager.py           # 降级管理器
│   ├── format_detector.py            # 格式检测
│   ├── token_estimator.py            # Token 估算
│   ├── token_stats.py                # Token 统计
│   │
│   │── # ==================== 工具模块 ====================
│   ├── httpx_client.py               # HTTP 客户端
│   ├── utils.py                      # 工具函数
│   ├── models.py                     # 数据模型
│   ├── task_manager.py               # 任务管理
│   └── state_manager.py              # 状态管理
│
├── creds/                    # 凭证文件目录
├── data/                     # 数据存储目录
├── docs/                     # 文档目录
├── front/                    # 前端资源
├── tests/                    # 测试代码
├── scripts/                  # 脚本工具
├── config/                   # 配置文件
│
├── # ==================== 启动脚本 ====================
├── 启动全部服务.bat          # Windows 启动脚本
├── start.bat                 # Windows 快速启动
├── start.sh                  # Linux/macOS 启动
│
├── # ==================== 项目配置 ====================
├── pyproject.toml            # Python 项目配置
├── requirements.txt          # 依赖列表
├── Dockerfile                # Docker 镜像
├── docker-compose.yml        # Docker Compose 配置
└── .env                      # 环境变量 (需自行创建)
```

---

## 核心模块

### 1. 认证与凭证管理

| 模块 | 路径 | 功能 |
|------|------|------|
| auth.py | `src/auth.py` | OAuth 2.0 认证流程管理 |
| credential_manager.py | `src/credential_manager.py` | 多凭证文件状态管理和轮换 |

**特性**:
- 自动故障检测和恢复
- JWT 令牌生成和验证
- 凭证冷却和轮换机制

### 2. API 路由和转换

| 模块 | 路径 | 功能 |
|------|------|------|
| openai_router.py | `src/openai_router.py` | OpenAI 格式请求处理 |
| gemini_router.py | `src/gemini_router.py` | Gemini 原生格式处理 |
| openai_transfer.py | `src/openai_transfer.py` | OpenAI/Gemini 格式双向转换 |

### 3. 统一网关

| 模块 | 路径 | 功能 |
|------|------|------|
| unified_gateway_router.py | `src/unified_gateway_router.py` | 多后端整合、故障转移 |
| gateway/routing.py | `src/gateway/routing.py` | 路由决策逻辑 |
| gateway/proxy.py | `src/gateway/proxy.py` | 请求代理和重试 |

### 4. IDE 兼容层

| 模块 | 行数 | 功能 |
|------|------|------|
| Client Detector | 305 行 | 自动检测 Cursor/Claude Code/Augment |
| Sanitizer | 608 行 | 消息净化、格式规范化 |
| Middleware | 386 行 | 请求/响应中间件 |
| Hash Cache | 620 行 | 内容哈希缓存、去重优化 |
| State Manager | 534 行 | SCID 状态机、会话管理 |

---

## API 路由系统

### 主入口 (web.py)

```python
# 挂载的路由器
app.include_router(openai_router)              # OpenAI 兼容 API
app.include_router(gemini_router)              # Gemini 原生 API
app.include_router(antigravity_router)         # Antigravity API
app.include_router(antigravity_anthropic_router)  # Anthropic Messages
app.include_router(web_router)                 # Web 控制台
app.include_router(gateway_router)             # 统一网关
```

### API 端点一览

| 端点 | 格式 | 说明 |
|------|------|------|
| `/v1/chat/completions` | OpenAI | OpenAI 聊天完成 |
| `/v1/models` | OpenAI | 模型列表 |
| `/gateway/v1/chat/completions` | OpenAI | 统一网关聊天 |
| `/gateway/v1/messages` | Anthropic | Anthropic Messages |
| `/antigravity/v1/chat/completions` | OpenAI | Antigravity 直连 |
| `/antigravity/v1/messages` | Anthropic | Antigravity Anthropic |
| `/antigravity/v1/models/{model}:generateContent` | Gemini | Gemini 生成 |
| `/antigravity/v1/models/{model}:streamGenerateContent` | Gemini | Gemini 流式 |

---

## 后端服务配置

### 支持的后端

| 后端 | 端口 | 优先级 | 支持模型 |
|------|------|--------|---------|
| **Antigravity** | 7861 | 1 (最高) | Claude 4.5, Gemini 2.5/3 |
| **Kiro Gateway** | 9046 | 2 | Claude 4.5 全家桶 |
| **AnyRouter** | 可配置 | 3 | 公益站第三方 API |
| **Copilot** | 8141 | 4 (兜底) | GPT/O1/O3 系列 |

### 配置文件 (config/gateway.yaml)

```yaml
backends:
  antigravity:
    enabled: true
    priority: 1
    base_url: "http://127.0.0.1:7861/antigravity/v1"
    timeout: 60
    stream_timeout: 300
    max_retries: 2

  copilot:
    enabled: true
    priority: 2
    base_url: "http://127.0.0.1:8141/v1"
    models: [gpt-4, gpt-4o, o1, o3, ...]

  kiro-gateway:
    enabled: true
    priority: 2
    base_url: "http://127.0.0.1:9046/v1"
```

---

## 路由策略

### 模型路由规则

```
请求模型 → 路由决策 → 后端选择 → 故障转移
```

1. **Gemini 系列** → Antigravity（按 token 计费，更经济）
2. **Claude 系列** → 优先 Antigravity，不支持时 Copilot
3. **GPT/O1/O3 系列** → Copilot（专属）
4. **自定义模型** → Kiro Gateway（可配置）

### 降级链示例 (Claude Sonnet 4.5)

```
Kiro Gateway → Antigravity → AnyRouter → Copilot → 
Copilot(Sonnet 4) → Antigravity(Gemini 3 Pro)
```

### 故障转移条件

- HTTP 状态码: 429, 502, 503, 500
- 超时 (timeout)
- 连接错误 (connection_error)

---

## 高级特性

### 1. 流式抗截断机制

```python
# src/anti_truncation.py
- 检测响应截断模式
- 自动重试和状态恢复
- 上下文连接管理
```

### 2. IDE 兼容性处理

- ✅ **请求格式规范化**: 处理 Cursor 的非标准格式
- ✅ **Responses API 转换**: `function_call` → `tool_calls`
- ✅ **工具格式清理**: `custom` → `function`
- ✅ **流式索引修复**: 添加 `tool_calls.index` 字段
- ✅ **Anthropic 格式支持**: 双向转换
- ✅ **思维签名处理**: 自动注入 `thoughtSignature`

### 3. 凭证轮换机制

```python
# 429 限流时切换凭证
max_credential_switches = 3

# 5xx 错误时同凭证重试
max_same_cred_retries = 2
```

### 4. Token 统计

```python
# src/token_stats.py
- 输入/输出 Token 统计
- 按模型/后端分组
- 持久化存储
```

---

## 部署方式

### 1. 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python web.py
```

### 2. Docker 部署

```bash
# 构建镜像
docker build -t gcli2api .

# 运行容器
docker-compose up -d
```

### 3. Windows 一键启动

```batch
# 双击运行
启动全部服务.bat
```

### 4. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | 7861 | 服务端口 |
| `HOST` | 0.0.0.0 | 监听地址 |
| `API_PASSWORD` | pwd | API 访问密码 |
| `PANEL_PASSWORD` | pwd | 控制面板密码 |
| `COPILOT_ENABLED` | true | 启用 Copilot 后端 |
| `KIRO_GATEWAY_MODELS` | - | Kiro 路由模型列表 |

---

## 数据流设计

```
用户请求 
    ↓
FastAPI 路由 (web.py)
    ↓
IDE 兼容中间件 (ide_compat/)
    ↓
统一网关路由 (unified_gateway_router.py)
    ↓
路由决策 (gateway/routing.py)
    ↓
代理请求 (gateway/proxy.py)
    ↓
后端服务 (Antigravity/Copilot/Kiro)
    ↓
响应处理 & 格式转换
    ↓
返回客户端
```

---

## 许可证

**Cooperative Non-Commercial License (CNC-1.0)**

### ✅ 允许
- 个人学习、研究、教育用途
- 非营利组织使用
- 开源项目集成

### ❌ 禁止
- 任何形式的商业使用
- 年收入超过100万美元的企业使用
- 提供付费服务或产品

---

## 相关文档

- [阿卡林网关开发文档](./akari-gateway-development.md)
- [前端开发与 Tauri 打包指南](./akari-gateway-ui-and-packaging.md)
- [官方版本对齐计划](./2026-01-21%20gcli2api_official对齐与加强计划.md)

---

> 📝 **文档生成**: 由 ACE (Augment Context Engine) 自动扫描生成  
> 🐱 **生成者**: 猫娘工程师 幽浮喵 (Claude Sonnet 4)  
> 📅 **生成时间**: 2026-01-22
