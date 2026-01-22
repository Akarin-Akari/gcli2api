# Token 统计功能移植开发实施文档

> **文档版本**: v1.0
> **创建日期**: 2026-01-22
> **作者**: Claude Sonnet 4
> **源项目**: Antigravity-Manager
> **目标项目**: gcli2api

---

## 1. 功能概述

### 1.1 功能描述

将 Antigravity-Manager 项目中的 Token 统计功能移植到 gcli2api，实现：

- **实时记录**: 每次 API 请求的 token 用量（输入/输出）
- **总体统计**: 总 token 数、总请求数、活跃账号数
- **按模型统计**: 各模型的 token 消耗排行
- **按账号统计**: 各账号的 token 消耗排行
- **趋势分析**: 按小时/天的使用趋势图表

### 1.2 业务价值

| 价值点 | 说明 |
|--------|------|
| 成本监控 | 了解 token 消耗情况，优化使用成本 |
| 账号管理 | 识别高消耗账号，平衡负载 |
| 模型分析 | 了解各模型使用频率，优化模型配置 |
| 异常检测 | 发现异常高消耗，及时预警 |

---

## 2. 技术架构对比

### 2.1 源项目架构 (Antigravity-Manager)

```
┌─────────────────────────────────────────────────────────┐
│                    Antigravity-Manager                   │
├─────────────────────────────────────────────────────────┤
│  前端: React + TypeScript + Tailwind CSS                │
│  后端: Rust + Tauri                                      │
│  存储: SQLite (rusqlite)                                 │
│  通信: Tauri IPC (invoke)                                │
└─────────────────────────────────────────────────────────┘
```

### 2.2 目标项目架构 (gcli2api)

```
┌─────────────────────────────────────────────────────────┐
│                       gcli2api                           │
├─────────────────────────────────────────────────────────┤
│  前端: 原生 HTML + JavaScript                            │
│  后端: Python + FastAPI                                  │
│  存储: SQLite (aiosqlite)                                │
│  通信: HTTP REST API                                     │
└─────────────────────────────────────────────────────────┘
```

### 2.3 技术映射

| 组件 | Antigravity-Manager | gcli2api |
|------|---------------------|----------|
| 数据记录 | `token_stats.rs::record_usage()` | `token_stats.py::record_usage()` |
| 数据查询 | `token_stats.rs::get_*_stats()` | `token_stats.py::get_*_stats()` |
| API 路由 | Tauri commands | FastAPI router |
| 前端调用 | `invoke<T>()` | `fetch()` |
| 图表渲染 | React 组件 | 原生 JS + CSS/Chart.js |

---

## 3. 数据库设计

### 3.1 新增数据表

#### 表1: `token_usage` (原始记录表)

```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,           -- Unix 时间戳
    account_email TEXT NOT NULL,          -- 账号邮箱
    model TEXT NOT NULL,                  -- 模型名称
    input_tokens INTEGER NOT NULL,        -- 输入 token 数
    output_tokens INTEGER NOT NULL,       -- 输出 token 数
    total_tokens INTEGER NOT NULL,        -- 总 token 数
    credential_file TEXT,                 -- 凭证文件名（可选）
    is_antigravity INTEGER DEFAULT 1      -- 是否为 Antigravity 模式
);

CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_account ON token_usage(account_email);
CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model);
```

#### 表2: `token_stats_hourly` (小时聚合表，性能优化)

```sql
CREATE TABLE IF NOT EXISTS token_stats_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_bucket TEXT NOT NULL,            -- 小时桶 'YYYY-MM-DD HH:00'
    account_email TEXT NOT NULL,          -- 账号邮箱
    model TEXT NOT NULL,                  -- 模型名称
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    request_count INTEGER DEFAULT 0,
    UNIQUE(hour_bucket, account_email, model)
);

CREATE INDEX IF NOT EXISTS idx_hourly_bucket ON token_stats_hourly(hour_bucket);
```

### 3.2 数据库文件位置

```
gcli2api/
├── data/
│   ├── credentials.db      # 现有凭证数据库
│   └── token_stats.db      # 新增统计数据库（独立文件，避免影响现有功能）
```

---

## 4. 后端实现

### 4.1 新增文件结构

```
gcli2api/src/
├── token_stats.py          # 【新增】Token 统计核心模块
├── antigravity_api.py      # 【修改】添加统计记录点
└── web_routes.py           # 【修改】添加统计 API 路由
```

### 4.2 核心模块: `src/token_stats.py`

```python
"""
Token 统计模块

功能:
- 记录每次 API 请求的 token 用量
- 提供各维度的统计查询
- 支持趋势分析

移植自: Antigravity-Manager/src-tauri/src/modules/token_stats.rs
"""

import os
import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from log import log

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "token_stats.db")


@dataclass
class TokenStatsSummary:
    """总体统计"""
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_requests: int
    unique_accounts: int
    unique_models: int


@dataclass
class ModelTokenStats:
    """按模型统计"""
    model: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    request_count: int


@dataclass
class AccountTokenStats:
    """按账号统计"""
    account_email: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    request_count: int


@dataclass
class TrendPoint:
    """趋势数据点"""
    period: str  # 时间桶 (如 '2026-01-22 16:00' 或 '2026-01-22')
    data: Dict[str, int]  # {model/account: token_count}


async def init_db():
    """初始化数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        # 原始记录表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                account_email TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                credential_file TEXT,
                is_antigravity INTEGER DEFAULT 1
            )
        """)

        # 小时聚合表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_stats_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour_bucket TEXT NOT NULL,
                account_email TEXT NOT NULL,
                model TEXT NOT NULL,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                UNIQUE(hour_bucket, account_email, model)
            )
        """)

        # 创建索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_account ON token_usage(account_email)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hourly_bucket ON token_stats_hourly(hour_bucket)")

        await db.commit()
        log.info("[TOKEN_STATS] Database initialized")


async def record_usage(
    account_email: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    credential_file: str = None,
    is_antigravity: bool = True
):
    """
    记录 token 用量

    在每次 API 请求完成后调用此函数
    """
    try:
        timestamp = int(datetime.utcnow().timestamp())
        total_tokens = input_tokens + output_tokens
        hour_bucket = datetime.utcnow().strftime("%Y-%m-%d %H:00")

        async with aiosqlite.connect(DB_PATH) as db:
            # 插入原始记录
            await db.execute("""
                INSERT INTO token_usage
                (timestamp, account_email, model, input_tokens, output_tokens, total_tokens, credential_file, is_antigravity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, account_email, model, input_tokens, output_tokens, total_tokens, credential_file, 1 if is_antigravity else 0))

            # 更新小时聚合表 (UPSERT)
            await db.execute("""
                INSERT INTO token_stats_hourly
                (hour_bucket, account_email, model, total_input_tokens, total_output_tokens, total_tokens, request_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(hour_bucket, account_email, model) DO UPDATE SET
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    total_tokens = total_tokens + ?,
                    request_count = request_count + 1
            """, (hour_bucket, account_email, model, input_tokens, output_tokens, total_tokens,
                  input_tokens, output_tokens, total_tokens))

            await db.commit()

        log.debug(f"[TOKEN_STATS] Recorded: {account_email} | {model} | in={input_tokens} out={output_tokens}")

    except Exception as e:
        log.error(f"[TOKEN_STATS] Failed to record usage: {e}")


async def get_summary_stats(hours: int = 24) -> TokenStatsSummary:
    """获取总体统计"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    cutoff_bucket = cutoff.strftime("%Y-%m-%d %H:00")

    async with aiosqlite.connect(DB_PATH) as db:
        # 获取汇总数据
        async with db.execute("""
            SELECT
                COALESCE(SUM(total_input_tokens), 0),
                COALESCE(SUM(total_output_tokens), 0),
                COALESCE(SUM(total_tokens), 0),
                COALESCE(SUM(request_count), 0)
            FROM token_stats_hourly
            WHERE hour_bucket >= ?
        """, (cutoff_bucket,)) as cursor:
            row = await cursor.fetchone()
            total_input, total_output, total, requests = row if row else (0, 0, 0, 0)

        # 获取唯一账号数
        async with db.execute("""
            SELECT COUNT(DISTINCT account_email) FROM token_stats_hourly WHERE hour_bucket >= ?
        """, (cutoff_bucket,)) as cursor:
            unique_accounts = (await cursor.fetchone())[0]

        # 获取唯一模型数
        async with db.execute("""
            SELECT COUNT(DISTINCT model) FROM token_stats_hourly WHERE hour_bucket >= ?
        """, (cutoff_bucket,)) as cursor:
            unique_models = (await cursor.fetchone())[0]

    return TokenStatsSummary(
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total,
        total_requests=requests,
        unique_accounts=unique_accounts,
        unique_models=unique_models
    )


async def get_model_stats(hours: int = 24) -> List[ModelTokenStats]:
    """按模型统计"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    cutoff_bucket = cutoff.strftime("%Y-%m-%d %H:00")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                model,
                SUM(total_input_tokens) as input,
                SUM(total_output_tokens) as output,
                SUM(total_tokens) as total,
                SUM(request_count) as count
            FROM token_stats_hourly
            WHERE hour_bucket >= ?
            GROUP BY model
            ORDER BY total DESC
        """, (cutoff_bucket,)) as cursor:
            rows = await cursor.fetchall()

    return [
        ModelTokenStats(
            model=row[0],
            total_input_tokens=row[1],
            total_output_tokens=row[2],
            total_tokens=row[3],
            request_count=row[4]
        )
        for row in rows
    ]


async def get_account_stats(hours: int = 24) -> List[AccountTokenStats]:
    """按账号统计"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    cutoff_bucket = cutoff.strftime("%Y-%m-%d %H:00")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                account_email,
                SUM(total_input_tokens) as input,
                SUM(total_output_tokens) as output,
                SUM(total_tokens) as total,
                SUM(request_count) as count
            FROM token_stats_hourly
            WHERE hour_bucket >= ?
            GROUP BY account_email
            ORDER BY total DESC
        """, (cutoff_bucket,)) as cursor:
            rows = await cursor.fetchall()

    return [
        AccountTokenStats(
            account_email=row[0],
            total_input_tokens=row[1],
            total_output_tokens=row[2],
            total_tokens=row[3],
            request_count=row[4]
        )
        for row in rows
    ]


async def get_model_trend_hourly(hours: int = 24) -> List[TrendPoint]:
    """获取模型小时趋势"""
    cutoff_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                strftime('%Y-%m-%d %H:00', datetime(timestamp, 'unixepoch')) as hour_bucket,
                model,
                SUM(total_tokens) as total
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY hour_bucket, model
            ORDER BY hour_bucket ASC
        """, (cutoff_ts,)) as cursor:
            rows = await cursor.fetchall()

    # 按时间桶分组
    trend_map = {}
    for period, model, total in rows:
        if period not in trend_map:
            trend_map[period] = {}
        trend_map[period][model] = total

    return [TrendPoint(period=k, data=v) for k, v in sorted(trend_map.items())]


async def get_account_trend_hourly(hours: int = 24) -> List[TrendPoint]:
    """获取账号小时趋势"""
    cutoff_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                strftime('%Y-%m-%d %H:00', datetime(timestamp, 'unixepoch')) as hour_bucket,
                account_email,
                SUM(total_tokens) as total
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY hour_bucket, account_email
            ORDER BY hour_bucket ASC
        """, (cutoff_ts,)) as cursor:
            rows = await cursor.fetchall()

    trend_map = {}
    for period, account, total in rows:
        if period not in trend_map:
            trend_map[period] = {}
        trend_map[period][account] = total

    return [TrendPoint(period=k, data=v) for k, v in sorted(trend_map.items())]


async def get_model_trend_daily(days: int = 7) -> List[TrendPoint]:
    """获取模型每日趋势"""
    cutoff_ts = int((datetime.utcnow() - timedelta(days=days)).timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT
                strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch')) as day_bucket,
                model,
                SUM(total_tokens) as total
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY day_bucket, model
            ORDER BY day_bucket ASC
        """, (cutoff_ts,)) as cursor:
            rows = await cursor.fetchall()

    trend_map = {}
    for period, model, total in rows:
        if period not in trend_map:
            trend_map[period] = {}
        trend_map[period][model] = total

    return [TrendPoint(period=k, data=v) for k, v in sorted(trend_map.items())]


async def clear_stats(before_hours: int = None):
    """
    清除统计数据

    Args:
        before_hours: 清除多少小时前的数据，None 表示清除全部
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if before_hours:
            cutoff_ts = int((datetime.utcnow() - timedelta(hours=before_hours)).timestamp())
            cutoff_bucket = (datetime.utcnow() - timedelta(hours=before_hours)).strftime("%Y-%m-%d %H:00")

            await db.execute("DELETE FROM token_usage WHERE timestamp < ?", (cutoff_ts,))
            await db.execute("DELETE FROM token_stats_hourly WHERE hour_bucket < ?", (cutoff_bucket,))
        else:
            await db.execute("DELETE FROM token_usage")
            await db.execute("DELETE FROM token_stats_hourly")

        await db.commit()
        log.info(f"[TOKEN_STATS] Cleared stats (before_hours={before_hours})")


async def get_stats_db_size() -> int:
    """获取统计数据库大小（字节）"""
    if os.path.exists(DB_PATH):
        return os.path.getsize(DB_PATH)
    return 0
```

### 4.3 修改: `src/antigravity_api.py`

在响应流处理完成后添加统计记录点：

```python
# 在文件开头添加导入
from src import token_stats

# 在 stream_antigravity_response() 函数中，流处理完成后添加：

# ========== 新增代码 ==========
# 记录 token 统计
try:
    # 从响应中提取 usage 信息
    if hasattr(response_context, 'usage'):
        usage = response_context.usage
        await token_stats.record_usage(
            account_email=credential_email or "unknown",
            model=model,
            input_tokens=usage.get('input_tokens', 0),
            output_tokens=usage.get('output_tokens', 0),
            credential_file=credential_filename,
            is_antigravity=True
        )
except Exception as e:
    log.warning(f"[TOKEN_STATS] Failed to record: {e}")
# ========== 新增代码结束 ==========
```

### 4.4 新增 API 路由: `src/web_routes.py`

```python
# ============ Token 统计路由 ============

@router.get("/stats/summary")
async def get_token_stats_summary(
    hours: int = 24,
    token: str = Depends(verify_panel_token)
):
    """获取 Token 统计总览"""
    try:
        from src import token_stats
        summary = await token_stats.get_summary_stats(hours)
        return JSONResponse({
            "success": True,
            "data": {
                "total_input_tokens": summary.total_input_tokens,
                "total_output_tokens": summary.total_output_tokens,
                "total_tokens": summary.total_tokens,
                "total_requests": summary.total_requests,
                "unique_accounts": summary.unique_accounts,
                "unique_models": summary.unique_models
            },
            "hours": hours
        })
    except Exception as e:
        log.error(f"获取统计总览失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/stats/by-model")
async def get_token_stats_by_model(
    hours: int = 24,
    token: str = Depends(verify_panel_token)
):
    """按模型统计 Token 用量"""
    try:
        from src import token_stats
        stats = await token_stats.get_model_stats(hours)
        return JSONResponse({
            "success": True,
            "data": [
                {
                    "model": s.model,
                    "total_input_tokens": s.total_input_tokens,
                    "total_output_tokens": s.total_output_tokens,
                    "total_tokens": s.total_tokens,
                    "request_count": s.request_count
                }
                for s in stats
            ],
            "hours": hours
        })
    except Exception as e:
        log.error(f"获取模型统计失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/stats/by-account")
async def get_token_stats_by_account(
    hours: int = 24,
    token: str = Depends(verify_panel_token)
):
    """按账号统计 Token 用量"""
    try:
        from src import token_stats
        stats = await token_stats.get_account_stats(hours)
        return JSONResponse({
            "success": True,
            "data": [
                {
                    "account_email": s.account_email,
                    "total_input_tokens": s.total_input_tokens,
                    "total_output_tokens": s.total_output_tokens,
                    "total_tokens": s.total_tokens,
                    "request_count": s.request_count
                }
                for s in stats
            ],
            "hours": hours
        })
    except Exception as e:
        log.error(f"获取账号统计失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/stats/trend/model")
async def get_model_trend(
    hours: int = 24,
    granularity: str = "hourly",  # hourly | daily
    token: str = Depends(verify_panel_token)
):
    """获取模型使用趋势"""
    try:
        from src import token_stats
        if granularity == "daily":
            days = max(1, hours // 24)
            trend = await token_stats.get_model_trend_daily(days)
        else:
            trend = await token_stats.get_model_trend_hourly(hours)

        return JSONResponse({
            "success": True,
            "data": [{"period": t.period, "data": t.data} for t in trend],
            "granularity": granularity
        })
    except Exception as e:
        log.error(f"获取模型趋势失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/stats/trend/account")
async def get_account_trend(
    hours: int = 24,
    token: str = Depends(verify_panel_token)
):
    """获取账号使用趋势"""
    try:
        from src import token_stats
        trend = await token_stats.get_account_trend_hourly(hours)
        return JSONResponse({
            "success": True,
            "data": [{"period": t.period, "data": t.data} for t in trend]
        })
    except Exception as e:
        log.error(f"获取账号趋势失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.delete("/stats/clear")
async def clear_token_stats(
    before_hours: int = None,
    token: str = Depends(verify_panel_token)
):
    """清除统计数据"""
    try:
        from src import token_stats
        await token_stats.clear_stats(before_hours)
        return JSONResponse({
            "success": True,
            "message": f"统计数据已清除 (before_hours={before_hours})"
        })
    except Exception as e:
        log.error(f"清除统计数据失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/stats/db-info")
async def get_stats_db_info(token: str = Depends(verify_panel_token)):
    """获取统计数据库信息"""
    try:
        from src import token_stats
        size = await token_stats.get_stats_db_size()
        return JSONResponse({
            "success": True,
            "db_size_bytes": size,
            "db_size_mb": round(size / 1024 / 1024, 2)
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
```

---

## 5. 前端实现

### 5.1 新增标签页

在 `control_panel.html` 的标签页区域添加：

```html
<button class="tab" onclick="switchTab('stats')">Token 统计</button>
```

### 5.2 新增 Tab 内容区域

```html
<!-- Token 统计标签页 -->
<div id="statsTab" class="tab-content">
    <h3>Token 统计</h3>

    <!-- 时间范围选择 -->
    <div class="stats-controls" style="margin-bottom: 20px;">
        <label for="statsTimeRange">统计时间范围：</label>
        <select id="statsTimeRange" onchange="loadTokenStats()">
            <option value="1">最近 1 小时</option>
            <option value="6">最近 6 小时</option>
            <option value="24" selected>最近 24 小时</option>
            <option value="72">最近 3 天</option>
            <option value="168">最近 7 天</option>
        </select>
        <button class="btn" style="width: auto; margin-left: 10px;" onclick="loadTokenStats()">
            刷新统计
        </button>
        <button class="btn" style="width: auto; margin-left: 10px; background-color: #dc3545;" onclick="clearTokenStats()">
            清除数据
        </button>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-cards" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px;">
        <div class="stats-card" style="background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 20px; border-radius: 10px;">
            <div style="font-size: 14px; opacity: 0.9;">总 Token 数</div>
            <div id="statsTotalTokens" style="font-size: 28px; font-weight: bold;">-</div>
        </div>
        <div class="stats-card" style="background: linear-gradient(135deg, #11998e, #38ef7d); color: white; padding: 20px; border-radius: 10px;">
            <div style="font-size: 14px; opacity: 0.9;">总请求数</div>
            <div id="statsTotalRequests" style="font-size: 28px; font-weight: bold;">-</div>
        </div>
        <div class="stats-card" style="background: linear-gradient(135deg, #fc4a1a, #f7b733); color: white; padding: 20px; border-radius: 10px;">
            <div style="font-size: 14px; opacity: 0.9;">活跃账号</div>
            <div id="statsUniqueAccounts" style="font-size: 28px; font-weight: bold;">-</div>
        </div>
        <div class="stats-card" style="background: linear-gradient(135deg, #4776E6, #8E54E9); color: white; padding: 20px; border-radius: 10px;">
            <div style="font-size: 14px; opacity: 0.9;">使用模型</div>
            <div id="statsUniqueModels" style="font-size: 28px; font-weight: bold;">-</div>
        </div>
    </div>

    <!-- 详细统计表格 -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
        <!-- 按模型统计 -->
        <div class="stats-table-section">
            <h4 style="margin-bottom: 15px;">📊 按模型统计</h4>
            <div id="modelStatsTable" style="max-height: 400px; overflow-y: auto;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="background: #f8f9fa; position: sticky; top: 0;">
                            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">模型</th>
                            <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6;">Token 数</th>
                            <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6;">请求数</th>
                        </tr>
                    </thead>
                    <tbody id="modelStatsBody">
                        <tr><td colspan="3" style="text-align: center; padding: 20px; color: #666;">暂无数据</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- 按账号统计 -->
        <div class="stats-table-section">
            <h4 style="margin-bottom: 15px;">👤 按账号统计</h4>
            <div id="accountStatsTable" style="max-height: 400px; overflow-y: auto;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="background: #f8f9fa; position: sticky; top: 0;">
                            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">账号</th>
                            <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6;">Token 数</th>
                            <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6;">请求数</th>
                        </tr>
                    </thead>
                    <tbody id="accountStatsBody">
                        <tr><td colspan="3" style="text-align: center; padding: 20px; color: #666;">暂无数据</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
```

### 5.3 JavaScript 函数

在 `common.js` 中添加：

```javascript
// =====================================================================
// Token 统计功能
// =====================================================================

function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

async function loadTokenStats() {
    const hours = document.getElementById('statsTimeRange').value;

    try {
        // 并行请求所有统计数据
        const [summaryResp, modelResp, accountResp] = await Promise.all([
            fetch(`./stats/summary?hours=${hours}`, { headers: getAuthHeaders() }),
            fetch(`./stats/by-model?hours=${hours}`, { headers: getAuthHeaders() }),
            fetch(`./stats/by-account?hours=${hours}`, { headers: getAuthHeaders() })
        ]);

        const [summaryData, modelData, accountData] = await Promise.all([
            summaryResp.json(),
            modelResp.json(),
            accountResp.json()
        ]);

        // 更新统计卡片
        if (summaryData.success) {
            const d = summaryData.data;
            document.getElementById('statsTotalTokens').textContent = formatNumber(d.total_tokens);
            document.getElementById('statsTotalRequests').textContent = formatNumber(d.total_requests);
            document.getElementById('statsUniqueAccounts').textContent = d.unique_accounts;
            document.getElementById('statsUniqueModels').textContent = d.unique_models;
        }

        // 更新模型统计表格
        if (modelData.success) {
            const tbody = document.getElementById('modelStatsBody');
            if (modelData.data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; padding: 20px; color: #666;">暂无数据</td></tr>';
            } else {
                tbody.innerHTML = modelData.data.map(m => `
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 10px; font-family: monospace;">${m.model}</td>
                        <td style="padding: 10px; text-align: right; font-weight: bold;">${formatNumber(m.total_tokens)}</td>
                        <td style="padding: 10px; text-align: right; color: #666;">${m.request_count}</td>
                    </tr>
                `).join('');
            }
        }

        // 更新账号统计表格
        if (accountData.success) {
            const tbody = document.getElementById('accountStatsBody');
            if (accountData.data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; padding: 20px; color: #666;">暂无数据</td></tr>';
            } else {
                tbody.innerHTML = accountData.data.map(a => `
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 10px; font-family: monospace; max-width: 200px; overflow: hidden; text-overflow: ellipsis;" title="${a.account_email}">${a.account_email}</td>
                        <td style="padding: 10px; text-align: right; font-weight: bold;">${formatNumber(a.total_tokens)}</td>
                        <td style="padding: 10px; text-align: right; color: #666;">${a.request_count}</td>
                    </tr>
                `).join('');
            }
        }

        showStatus('统计数据已加载', 'success');
    } catch (error) {
        showStatus(`加载统计失败: ${error.message}`, 'error');
    }
}

async function clearTokenStats() {
    if (!confirm('确定要清除所有 Token 统计数据吗？\n\n此操作不可撤销！')) {
        return;
    }

    try {
        const response = await fetch('./stats/clear', {
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        const data = await response.json();

        if (data.success) {
            showStatus('统计数据已清除', 'success');
            await loadTokenStats();
        } else {
            showStatus(data.error || '清除失败', 'error');
        }
    } catch (error) {
        showStatus(`清除失败: ${error.message}`, 'error');
    }
}
```

---

## 6. 初始化与启动

### 6.1 修改 `src/main.py`

在应用启动时初始化统计数据库：

```python
@app.on_event("startup")
async def startup_event():
    # ... 现有初始化代码 ...

    # 初始化 Token 统计数据库
    from src import token_stats
    await token_stats.init_db()
    log.info("[STARTUP] Token stats database initialized")
```

---

## 7. 测试计划

### 7.1 单元测试

| 测试项 | 测试内容 |
|--------|----------|
| `test_record_usage` | 验证 token 用量记录正确写入数据库 |
| `test_get_summary_stats` | 验证总体统计计算正确 |
| `test_get_model_stats` | 验证按模型统计分组正确 |
| `test_get_account_stats` | 验证按账号统计分组正确 |
| `test_trend_data` | 验证趋势数据按时间正确聚合 |

### 7.2 集成测试

| 测试项 | 测试步骤 |
|--------|----------|
| API 记录 | 发起 API 请求 → 检查数据库记录 |
| 统计 API | 调用 `/stats/summary` → 验证返回数据 |
| 前端展示 | 打开统计页面 → 验证数据正确展示 |
| 清除功能 | 点击清除 → 验证数据被清空 |

### 7.3 性能测试

| 测试项 | 预期指标 |
|--------|----------|
| 单次记录耗时 | < 10ms |
| 统计查询耗时 | < 100ms |
| 数据库大小增长 | 约 100KB / 1000 请求 |

---

## 8. 实施时间表

| 阶段 | 任务 | 预估时间 |
|------|------|----------|
| **阶段一** | 后端核心模块 (`token_stats.py`) | 2 小时 |
| **阶段二** | API 路由实现 | 1 小时 |
| **阶段三** | 埋点集成 (antigravity_api.py) | 1 小时 |
| **阶段四** | 前端页面开发 | 3 小时 |
| **阶段五** | 联调测试 | 2 小时 |
| **阶段六** | 文档与优化 | 1 小时 |
| **总计** | | **10 小时** |

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 统计记录影响请求性能 | 中 | 使用异步写入，设置超时 |
| 数据库文件过大 | 低 | 提供清理功能，定期归档 |
| Token 用量获取不准确 | 中 | 多处埋点，fallback 估算 |

---

## 10. 后续优化（可选）

1. **图表可视化**: 集成 Chart.js 展示趋势图
2. **导出功能**: 支持导出 CSV/Excel
3. **告警机制**: Token 消耗超阈值告警
4. **成本估算**: 根据模型定价估算费用
5. **数据归档**: 自动归档历史数据

---

## 附录 A: API 接口文档

| 接口 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/stats/summary` | GET | `hours=24` | 获取总体统计 |
| `/stats/by-model` | GET | `hours=24` | 按模型统计 |
| `/stats/by-account` | GET | `hours=24` | 按账号统计 |
| `/stats/trend/model` | GET | `hours=24`, `granularity=hourly` | 模型趋势 |
| `/stats/trend/account` | GET | `hours=24` | 账号趋势 |
| `/stats/clear` | DELETE | `before_hours=null` | 清除数据 |
| `/stats/db-info` | GET | - | 数据库信息 |

---

**文档结束**
