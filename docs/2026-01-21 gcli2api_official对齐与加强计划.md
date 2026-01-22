## gcli2api 与 gcli2api_official 对齐与加强计划

> 目标：在保持现有自研增强能力（SignatureCache、SmartWarmup、Unified Gateway 等）的前提下，系统性对齐 gcli2api_official 的工程化实践与模块边界，让本仓库既像“官方版超集”，又容易被后来者看懂和维护。

---

## 一、整体架构对齐思路

- **参考来源**
  - 官方文档：`gcli2api_official/README.md`、`gcli2api_official/docs/README_EN.md`
  - 核心模块说明（官方）：
    - 认证与凭证管理：`src/auth.py`, `src/credential_manager.py`
    - API 路由与转换：`src/openai_router.py`, `src/gemini_router.py`, `src/openai_transfer.py`
    - 网络与代理：`src/httpx_client.py`, `src/google_chat_api.py`
    - 状态管理：`src/state_manager.py`, `src/usage_stats.py`
    - 任务管理：`src/task_manager.py`
    - Web 控制台：`src/web_routes.py`
    - 流式抗截断：`src/anti_truncation.py`
    - 格式检测：`src/format_detector.py`

- **gcli2api 当前对应关系（规划命名，而不是立即搬文件）**
  - 认证与凭证管理：
    - 现有：`src/credential_manager.py` + 若干辅助函数散落在 `config.py` / `web_routes.py`
    - 计划：收敛到 `src/auth.py` + `src/credential_manager.py`，并在 README 中用同一术语描述
  - API 路由与转换：
    - 现有：`src/antigravity_router.py`, `src/openai_router.py`, `src/unified_gateway_router.py`, `src/converters/*`
    - 计划：保留现有模块，但在 README 中显式分层：
      - 「OpenAI/Gemini/Claude 前端路由」
      - 「统一网关路由 (`unified_gateway_router.py`)」
      - 「Antigravity 后端适配 (`antigravity_api.py`, `converters/*`)」
  - 网络与代理：
    - 现有：`src/httpx_client.py`
    - 计划：对齐官方描述，明确这是“统一 HTTP 客户端 + 代理/重试策略”的唯一入口。
  - 状态管理 / 任务管理：
    - 现有：多处零散逻辑（usage、冷却、warmup 调度、缓存文件）
    - 计划：逐步抽象出：
      - `src/state_manager.py`：统一处理全局状态与锁
      - `src/usage_stats.py`：记录网关侧使用统计（请求量、错误率、截断/重试情况等）
      - `src/task_manager.py`：收拢后台任务（SmartWarmup、周期性 quota 刷新等）

> 行动项：先通过文档和命名对齐（不急着大搬家），等新模块稳定后再逐步迁移实现。

---

## 二、API 设计与控制面板对齐

### 2.1 Web 控制台 API

- **官方能力（参考 `README.md` / `docs/README_EN.md`）**
  - 认证端点：
    - `POST /auth/login`
    - `POST /auth/start` / `/auth/antigravity/start`
    - `POST /auth/callback` / `GET /auth/status/{project_id}`
  - 凭证管理端点（支持 `mode=geminicli|antigravity`）：
    - 上传 / 下载 / 批量操作 / quota 查询 / 邮箱获取
  - 配置管理：
    - `GET /config/get` / `POST /config/save`
  - 日志管理：
    - 清空 / 下载 / WebSocket 实时流
  - 版本信息：
    - `GET /version/info?check_update=true`

- **gcli2api 侧对齐建议**
  - 统一控制面板 API 的分组与命名，文档上与官方保持同一章节结构：
    - 「认证端点」：对齐 `/auth/*` 的语义，哪怕内部实现不同；
    - 「凭证管理端点」：明确 `mode` 参数含义，与官方说明一致；
    - 「配置管理 / 日志 / 版本信息」：尽量使用同名/同语义端点，方便脚本与文档通用。
  - 所有控制台端点应统一走：
    - 密码配置：`API_PASSWORD` / `PANEL_PASSWORD` / `PASSWORD`
    - 认证逻辑：对齐 `src/utils.py` 中 `authenticate_flexible` / `verify_panel_token` 的使用模式。

### 2.2 Chat API 与多格式支持

- **官方约定**
  - GCLI OpenAI 兼容端点：`/v1/chat/completions`
  - Gemini 原生端点：`/v1/models/{model}:generateContent` / `streamGenerateContent`
  - Antigravity 端点：`/antigravity/v1/*`（OpenAI/Gemini/Claude 三套）

- **gcli2api 侧对齐方向**
  - 将当前所有 OpenAI/Gemini/Claude/Antigravity 端点的路径与行为整理成“**三组兼容端点 + 统一网关端点**”的结构描述，放进 README：
    - 组 1：直接转官方 GCLI / Gemini；
    - 组 2：转本地 Antigravity 后端（你的网关增强）；
    - 组 3：统一网关 `/gateway/*`（多后端路由）。
  - 在文档中明确每组端点的：
    - 请求/响应格式；
    - 所需认证方式（Bearer / key / x-goog-api-key 等）；
    - 典型使用示例。

---

## 三、环境变量与配置对齐

- **官方环境变量分层（见 README「环境变量配置」）**
  - 基础：`PORT`, `HOST`
  - 密码：`API_PASSWORD`, `PANEL_PASSWORD`, `PASSWORD`
  - 性能稳定性：`CALLS_PER_ROTATION`, `RETRY_429_*`, `ANTI_TRUNCATION_MAX_ATTEMPTS`
  - 网络与代理：`PROXY`, `OAUTH_PROXY_URL`, `GOOGLEAPIS_PROXY_URL`, `METADATA_SERVICE_URL`
  - 自动化：`AUTO_BAN`, `AUTO_LOAD_ENV_CREDS`
  - 兼容性：`COMPATIBILITY_MODE`
  - 日志：`LOG_LEVEL`, `LOG_FILE`
  - 存储：`MONGODB_URI`, `MONGODB_DATABASE`, …

- **gcli2api 对齐计划**
  - 整理当前所有环境变量（包括已有和隐式使用的），按照官方分组方式重写一节「环境变量配置」到 `gcli2api/README.md` 中。
  - 尽量采用与官方相同的变量名与语义：
    - 对于完全相同的行为（如密码、代理），直接复用；
    - 对于扩展行为（如三层签名缓存、SmartWarmup），以 `AKARI_*` 前缀做增强配置，避免与官方冲突。
  - 明确优先级规则（环境变量 > 配置文件 > UI / 默认值），与官方 README 一致。

---

## 四、状态管理 / 任务管理 / 监控

- **官方提供的模式**
  - 状态管理：`src/state_manager.py`, `src/usage_stats.py`
  - 任务管理：`src/task_manager.py`
  - Web 控制台中的使用统计与监控面板。

- **gcli2api 可对齐/增强的部分**
  - 将当前分散在：
    - SmartWarmup（`src/smart_warmup.py`）
    - SignatureCache 迁移状态
    - Quota 缓存与冷却期
    - 统一网关状态（会话状态、fallback 统计）
    的逻辑抽象到：
    - `src/state_manager.py`：全局状态读写与锁；
    - `src/usage_stats.py`：网关级请求/错误/截断/重试统计；
    - `src/task_manager.py`：统一登记/关闭后台任务。
  - 在 Web 控制台新增（或开放）类似官方的：
    - `/usage/stats`, `/usage/aggregated`, `/usage/update-limits`, `/usage/reset`；
    - 作为你现有 log.jsonl 的结构化补充。

---

## 五、流式抗截断与上下文截断机制

- **官方模块：** `src/anti_truncation.py`（流式抗截断），`docs/README_EN.md` 中有简要介绍。
- **gcli2api 现状：**
  - `src/context_truncation.py` 已经实现按模型家族的 Token 限制与多策略截断；
  - 抗截断重试逻辑分散在多处 Router/Handler 中。
- **对齐/加强方向：**
  - 文档层面：在 `gcli2api/README.md` 新增一节「流式抗截断与上下文管理」，参考官方描述：
    - 描述：检测截断、自动重试、上下文拼接、智能删历史。
    - 配置：类似 `ANTI_TRUNCATION_MAX_ATTEMPTS` 的环境变量。
  - 代码层面：将“检测截断 + 触发重试 + 截断/压缩上下文”的逻辑统一封装到一个模块（例如 `src/anti_truncation.py`），Router 只调用统一入口。

---

## 六、对齐落地路线图（针对 gcli2api）

1. **文档先行（本文件 + README 重构）**
   - 在本仓 README 中引入与官方一致的章节结构与术语。
2. **模块命名与入口统一**
   - 新增/收拢：`auth.py`、`state_manager.py`、`usage_stats.py`、`task_manager.py`、`anti_truncation.py`。
3. **Web 控制台 API 对齐**
   - 整理并实现 `/auth/*`、`/creds/*`、`/config/*`、`/logs/*`、`/usage/*` 等结构化端点。
4. **环境变量配置对齐**
   - 支持官方所有关键变量，并用文档标明扩展变量（如 AKARI_*）。
5. **监控与运维能力提升**
   - 在现有面板基础上，增加 usage 统计接口与看板，使 gcli2api 成为“官方版的超集实现”。

