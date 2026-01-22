## 从 Antigravity-Manager 移植的关键思路（估算校准 & 上下文压缩 & 429/预热）

> 目标：在 Python 版 gcli2api 中，吸收 Antigravity-Manager（Rust/Tauri 后端）在「Token 估算校准」「三层上下文压缩」「429/Quota 重试」「内部 Warmup API」上的成熟设计，形成一套可持续演进的网关内核。

---

## 一、Context Estimation Calibrator（核心移植点）

### 1.1 Antigravity-Manager 的实现要点

- 位置：
  - `src-tauri/src/proxy/mappers/context_manager.rs`
  - `src-tauri/src/proxy/mappers/estimation_calibrator.rs`
  - README 中 v3.3.47 相关说明（解释了「动态校准器」的设计）。

- 关键思想：
  - 估算与实际分离：
    - 请求发出前：通过 `ContextManager::estimate_token_usage` 估算上下文 token 数；
    - 请求返回后：从 Google API 响应中解析真实 token 用量（prompt/completion/total）；
  - 动态校准：
    - 使用 `EstimationCalibrator` 跟踪：
      - `total_estimated`：累计估算值
      - `total_actual`：累计真实值
      - `sample_count`：样本数量
    - 每次调用 `update(estimated, actual)` 时：
      - 计算 `new_factor = actual / estimated`
      - 限制在 `[0.8, 4.0]` 区间，避免极端值
      - 使用指数移动平均：`factor = 0.6 * old + 0.4 * clamped`
    - `calibrate(estimated)` 返回 `ceil(estimated * factor)`，作为后续决策依据。
  - 使用场景：
    - 在 Claude handler 里，所有「上下文压力」计算都基于 `calibrate(estimate)` 而非裸估算：
      - Layer 1/2/3 压缩前后都会重新估算，并记录压缩效果（saved tokens）。

### 1.2 在 gcli2api 的 Python 版本设计

- 新增模块：`src/context_calibrator.py`
  - 目标：提供一个线程安全的全局校准器，接口对齐 Rust 设计，但实现符合 Python 风格。
  - 设计草案：
    - 类：`EstimationCalibrator`
      - 字段：
        - `total_estimated: int`
        - `total_actual: int`
        - `sample_count: int`
        - `calibration_factor: float`（默认 2.0，与 Rust 一致）
        - `lock: threading.Lock`（保证线程安全）
      - 方法：
        - `update(estimated: int, actual: int) -> None`：
          - 忽略 `estimated <= 0` 或 `actual <= 0` 的样本；
          - 计算 `new_factor = actual / estimated`，clamp 到 `[0.8, 4.0]`；
          - 使用 `self.calibration_factor = old * 0.6 + clamped * 0.4`；
          - 更新累计统计与样本数量；
          - 打 info/debug 日志，类似：
            - `[Calibrator] updated factor: old -> new (raw=..., samples=...)`
        - `calibrate(estimated: int) -> int`：
          - 返回 `ceil(estimated * factor)`，factor 默认 2.0。
        - `get_factor() -> float` / `get_stats() -> dict`：用于调试与监控。
    - 单例访问：
      - 提供 `get_global_calibrator()` 或模块级单例 `GLOBAL_CALIBRATOR`。
  - 持久化（第一阶段可以先不做）：
    - 后续可将 `factor` 和累计统计写入 `data/context_calibrator.json`，在启动时加载。

- 与 `context_truncation.py` 的集成（初始版本）：
  - 现有逻辑中，`estimate_messages_tokens(messages)` 会返回一个估算值。
  - 修改点：
    - 在 `truncate_context_for_api` 等入口中：
      - 先得到 `raw_estimated = estimate_messages_tokens(messages)`；
      - 再得到 `calibrated = calibrator.calibrate(raw_estimated)`；
      - 在返回的 `stats` 中同时记录：
        - `original_tokens`（raw）
        - `estimated_tokens`（calibrated）
        - 当前 `calibration_factor`。
  - 实际 `update(estimated, actual)` 的调用：
    - 短期内可以先留 TODO：在处理上游响应时（例如在某个统一响应转换器中），从 usage/usageMetadata 中提取真实 tokens，并调用 `update`。
    - 这样可以先完成「可用的校准器及其集成」，后续再逐步接入真实数据。

---

## 二、三层渐进式上下文压缩（Layer 1/2/3）

### 2.1 Antigravity-Manager 的三层设计

- 文档位置：`docs/testing/context_compression_test_plan.md`
  - 场景 1：Layer 1 工具消息裁剪（60% 压力）
  - 场景 2：Layer 2 Thinking 压缩 + 签名保留（75% 压力）
  - 场景 3：Layer 3 Fork + XML 摘要（90% 压力）
  - 每层都有清晰的触发条件、日志模式与压缩率预期。
- 代码位置：
  - `src-tauri/src/proxy/mappers/context_manager.rs`
  - `src-tauri/src/proxy/handlers/claude.rs` 中对 Layer 1/2/3 的调用与日志。

### 2.2 迁移到 gcli2api 的规划

- 目标：在保持现有 Python `context_truncation.py` 的基础上，引入“多层压缩”的清晰框架。
- 建议分层：
  - Layer 0：当前已有的 `truncate_messages_smart` / `truncate_messages_aggressive`，作为基础策略。
  - Layer 1（工具裁剪）：
    - 引入函数：`trim_tool_messages(messages, pressure, limit)`，保留最近 N 轮工具消息。
    - 触发条件：上下文压力 > 某阈值（例如 0.6），但尚未接近极限。
  - Layer 2（Thinking 压缩保留签名）：
    - 在已有 Thinking 相关处理基础上，增加：
      - 对带 `thought` / `thoughtSignature` 的块，只压缩文本为占位符 `"..."`，保留签名字段；
      - 保护最近几轮消息不被压缩（可配置）。
  - Layer 3（摘要 Fork）：
    - 抽象出接口：`fork_with_summary(messages, model_hint) -> messages`：
      - 调用某个“便宜模型”（可配置，如 Gemini Flash）生成对话摘要（文本或结构化 XML）；
      - 构造新的消息列表：`[system/summary] + [assistant 确认] + [最近用户消息]`；
      - 保证签名链的兼容性（参考 Rust 实现中的注释）。

- 分阶段实施：
  1. 先在 `context_truncation.py` 中建立「pressure → layer」决策骨架，日志对齐 Antigravity-Manager（Layer-1/2/3）。
  2. 再逐步填充各层的具体实现逻辑（可复用现有工具压缩 / MAX_TOKENS 重试逻辑）。

---

## 三、429 / Quota 重试与 RetryInfo 解析

- Rust 实现位置：`src-tauri/src/proxy/upstream/retry.rs`
  - `parse_duration_ms(duration_str)`：解析 `"1.5s"`, `"200ms"`, `"1h16m0.667s"` 等字符串为毫秒。
  - `parse_retry_delay(error_text)`：
    - 解析 Google RPC `RetryInfo.retryDelay`；
    - 或从 `metadata.quotaResetDelay` 中提取延迟。

- gcli2api 迁移思路：
  - 新增模块：`src/retry_utils.py`（命名可再斟酌）：
    - 提供：
      - `parse_duration_ms(str) -> Optional[int]`
      - `parse_retry_delay(error_text: str) -> Optional[int]`
    - Python 版实现与 Rust 逻辑对齐。
  - 与 `antigravity_api.py` 的结合：
    - 在处理 429/Quota 错误时：
      - 优先从 error JSON 中解析 retryDelay/quotaResetDelay；
      - 将结果喂给现有的 `_tiered_quota_lockout_seconds` 或替代其部分逻辑；
      - 在日志中记录「从上游提取的 retry delay」和「最终冷却时长」。

---

## 四、内部 Warmup API 与 SmartWarmup 协同

- Rust 实现位置：`src-tauri/src/proxy/handlers/warmup.rs`
  - 暴露 `/internal/warmup` 端点，参数包含：
    - `email`
    - `model`（原始名称，不做映射）
    - 可选的 `access_token` / `project_id`
  - 复用 TokenManager/UpstreamClient，调用上游一次极小请求实现“预热”。

- gcli2api 侧规划：
  - 在 FastAPI 应用中新增一个内部路由模块（例如 `src/internal_routes.py` 或并入 `web_routes.py` 的「internal」分组）：
    - `POST /internal/warmup`：
      - 参数结构与 Rust 版本对齐；
      - 内部调用当前的 SmartWarmup / HTTP 客户端逻辑，完成一次“ping”式调用。
  - 与 `smart_warmup.py` 协作：
    - SmartWarmup 的周期性扫描仍然存在；
    - 内部 API 提供“人为触发某账号/模型预热”的入口，方便从 Web 控制台或脚本直接操作。

---

## 五、优先级与落地顺序

> 结合 gcli2api 当前状态与收益/复杂度评估，推荐如下顺序：

1. **Context Estimation Calibrator（本次任务先实现）**
   - 新建 `src/context_calibrator.py`；
   - 在 `context_truncation.py` 中集成 `calibrate()` 调用；
   - 在日志与 stats 中暴露原始/校准后 token 值与 factor。
2. **三层压缩框架骨架**
   - 在 `context_truncation.py` 中建立「pressure → layer」决策与日志输出；
   - 初期可仅映射到现有 smart/aggressive 策略。
3. **RetryInfo 429 重试解析模块**
   - 新建 `src/retry_utils.py`，在 `antigravity_api.py` 中使用；
   - 增强当前的 429 配额冷却逻辑。
4. **Warmup 内部 API**
   - 为 SmartWarmup 增加 `/internal/warmup` 入口，便于控制面板和脚本调用。
5. **进一步签名敏感的 Layer 2/Layer 3 实现**
   - 在已有 SignatureCache 架构上，细化“压缩但保留签名”的逻辑，以及 summary fork 方案。

---

## 六、与当前任务的接口约定

- 本文档与《2026-01-21 gcli2api_official对齐与加强计划》共同作为本次网关内核演进的设计基线。
- 当前具体实现任务：
  1. 在 `gcli2api` 中实现 Python 版 `EstimationCalibrator`（`src/context_calibrator.py`）；
  2. 在 `context_truncation.py` 中集成校准器的估算流程；
  3. 在后续响应路径中寻找适合的位置接入真实 token 更新（预留 TODO）。

