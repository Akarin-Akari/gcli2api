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
