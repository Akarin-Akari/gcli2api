"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import (
    get_antigravity_api_url,
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_return_thoughts_to_frontend,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from .fallback_manager import get_cross_pool_fallback, get_model_pool, is_quota_exhausted_error


class NonRetryableError(Exception):
    """不可重试的错误，用于 400 等客户端错误，避免外层 except 继续重试"""
    pass


class AntigravityUpstreamError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        tag: str | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.tag = tag
        super().__init__(message)


def _default_429_lockout_seconds(error_text: str) -> float:
    """
    429 无法解析 reset time 时的保底锁定时间：
    - 速率限制：30s
    - 配额耗尽：1h
    - 其他：60s
    """
    text = (error_text or "").lower()
    if any(k in text for k in ("rate limit", "rate_limit", "per minute", "rpm", "qps")):
        return 30.0
    if "quota" in text:
        return 3600.0
    return 60.0


_fallback_429_failure_counts: dict[tuple[str, str], int] = {}


# ==================== [FIX 2026-01-21] BaseURL 健康状态管理 ====================
# 记录每个 BaseURL 的健康状态，优先使用最近成功的 URL

class BaseURLHealthManager:
    """
    BaseURL 健康状态管理器
    - 记录每个 URL 的成功/失败次数
    - 记录最后成功时间
    - 提供智能排序，优先使用健康的 URL
    """

    def __init__(self):
        # {url: {"success_count": int, "failure_count": int, "last_success": float, "last_failure": float}}
        self._health_data: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def record_success(self, url: str, latency_ms: float = 0) -> None:
        """记录成功请求"""
        async with self._lock:
            if url not in self._health_data:
                self._health_data[url] = {
                    "success_count": 0,
                    "failure_count": 0,
                    "last_success": 0,
                    "last_failure": 0,
                    "total_latency_ms": 0,
                }
            self._health_data[url]["success_count"] += 1
            self._health_data[url]["last_success"] = time.time()
            self._health_data[url]["total_latency_ms"] += latency_ms

    async def record_failure(self, url: str, error_code: int = 0) -> None:
        """记录失败请求"""
        async with self._lock:
            if url not in self._health_data:
                self._health_data[url] = {
                    "success_count": 0,
                    "failure_count": 0,
                    "last_success": 0,
                    "last_failure": 0,
                    "total_latency_ms": 0,
                }
            self._health_data[url]["failure_count"] += 1
            self._health_data[url]["last_failure"] = time.time()

    def get_health_score(self, url: str) -> float:
        """
        计算 URL 的健康度评分 (0-100)
        - 成功率占 50%
        - 最近成功时间占 30%
        - 最近失败惩罚占 20%
        """
        if url not in self._health_data:
            return 50.0  # 未知 URL 给中等分数

        data = self._health_data[url]
        total = data["success_count"] + data["failure_count"]

        if total == 0:
            return 50.0

        # 成功率 (0-100)
        success_rate = (data["success_count"] / total) * 100

        # 最近成功时间奖励 (0-100)
        # 最近 5 分钟内成功过给满分，超过 1 小时给 0 分
        current_time = time.time()
        time_since_success = current_time - data["last_success"] if data["last_success"] else 3600
        recency_score = max(0, 100 - (time_since_success / 36))  # 1小时 = 3600s，每36s减1分

        # 最近失败惩罚 (0-100)
        # 最近 1 分钟内失败过减分
        time_since_failure = current_time - data["last_failure"] if data["last_failure"] else 3600
        failure_penalty = 100 if time_since_failure > 60 else max(0, time_since_failure / 0.6)

        # 综合评分
        health_score = success_rate * 0.5 + recency_score * 0.3 + failure_penalty * 0.2

        return min(100, max(0, health_score))

    def get_sorted_urls(self, urls: List[str]) -> List[str]:
        """
        按健康度排序 URL 列表
        健康度高的排在前面
        """
        if not urls:
            return urls

        # 计算每个 URL 的健康度
        url_scores = [(url, self.get_health_score(url)) for url in urls]

        # 按健康度降序排序
        url_scores.sort(key=lambda x: x[1], reverse=True)

        sorted_urls = [url for url, _ in url_scores]

        # 日志输出排序结果
        if len(urls) > 1:
            log.debug(
                f"[BaseURL Health] Sorted URLs: "
                + ", ".join([f"{url.split('//')[-1].split('/')[0]}({self.get_health_score(url):.0f})" for url in sorted_urls[:3]])
                + ("..." if len(sorted_urls) > 3 else "")
            )

        return sorted_urls


# 全局 BaseURL 健康管理器实例
_baseurl_health_manager = BaseURLHealthManager()


def get_baseurl_health_manager() -> BaseURLHealthManager:
    """获取 BaseURL 健康管理器实例"""
    return _baseurl_health_manager


def _tiered_quota_lockout_seconds(consecutive_failures: int) -> float:
    """
    对齐 Antigravity-Manager 的 QUOTA_EXHAUSTED 保底锁定阶梯：
    1 次 60s，2 次 5min，3 次 30min，4+ 次 2h。
    """
    n = max(1, int(consecutive_failures))
    if n == 1:
        return 60.0
    if n == 2:
        return 300.0
    if n == 3:
        return 1800.0
    return 7200.0


def _reset_429_fallback_failure_count(*, credential_name: str, model_name: str) -> None:
    try:
        _fallback_429_failure_counts.pop((credential_name, model_name), None)
    except Exception:
        pass


async def _resolve_429_cooldown_until(
    *,
    credential_name: str,
    access_token: str,
    model_name: str,
    error_text: str,
) -> float | None:
    """
    解析 429 冷却时间

    [FIX 2026-01-21] 增强解析逻辑：
    1. 优先尝试从 error_text 解析精确的 RetryInfo.retryDelay 或 metadata.quotaResetDelay
    2. 其次尝试实时拉取 quota resetTime (针对配额耗尽)
    3. 最后使用阶梯锁定或默认锁定时间
    """
    # 1. 尝试精确解析 (RetryInfo / quotaResetDelay)
    precise_delay = parse_retry_delay_seconds(error_text)
    if precise_delay is not None:
        log.info(f"[ANTIGRAVITY] 429 精确解析延迟: {precise_delay}s")
        return time.time() + precise_delay

    text = (error_text or "").lower()
    should_try_quota_refresh = "quota" in text or "exhaust" in text

    if should_try_quota_refresh:
        try:
            quota = await fetch_quota_info(access_token, cache_key=credential_name)
            if quota.get("success"):
                models = quota.get("models") or {}
                model_info = models.get(model_name) or {}
                reset_raw = model_info.get("resetTimeRaw")
                if reset_raw and isinstance(reset_raw, str):
                    reset_dt = datetime.fromisoformat(reset_raw.replace("Z", "+00:00"))
                    reset_ts = reset_dt.timestamp()
                    return max(time.time(), reset_ts) + 5.0
        except Exception as e:
            log.warning(f"[ANTIGRAVITY] 429 quota refresh failed, fallback lockout: {e}")

    # 兜底：对于 QUOTA 类型使用阶梯锁定，避免“立即复用 -> 立即 429”振荡
    if should_try_quota_refresh:
        key = (credential_name, model_name)
        count = _fallback_429_failure_counts.get(key, 0) + 1
        _fallback_429_failure_counts[key] = count
        lockout = _tiered_quota_lockout_seconds(count)
        log.warning(
            f"[ANTIGRAVITY] 429 配额类兜底锁定 {lockout:.0f}s "
            f"(consecutive_failures={count}, cred={credential_name}, model={model_name})"
        )
        return time.time() + lockout

    return time.time() + _default_429_lockout_seconds(error_text)


# [FIX 2026-01-21] 使用新的 RateLimiter 模块
from .rate_limiter import get_global_rate_limiter

async def _throttle_antigravity_upstream() -> None:
    """
    对上游请求进行防抖限流
    """
    try:
        min_interval = float(os.getenv("ANTIGRAVITY_MIN_REQUEST_INTERVAL_SECONDS", "0.5"))
    except Exception:
        min_interval = 0.5

    # 转换为毫秒
    min_interval_ms = int(min_interval * 1000)
    limiter = get_global_rate_limiter(min_interval_ms=min_interval_ms)
    await limiter.wait()


# [FIX 2026-01-22] Quota 查询专用限流器 - 防止短时间内大量查询导致 429
# [FIX 2026-01-22] 使用独立的 RateLimiter 实例，不使用全局限流器（避免和普通 API 请求冲突）
from .rate_limiter import RateLimiter as QuotaRateLimiter
_quota_rate_limiter: QuotaRateLimiter | None = None

async def _throttle_quota_query() -> None:
    """
    对 quota 查询进行限流（比普通 API 请求更严格）

    默认最小间隔 2 秒，避免短时间内大量查询
    """
    global _quota_rate_limiter

    try:
        min_interval = float(os.getenv("ANTIGRAVITY_QUOTA_MIN_INTERVAL_SECONDS", "2.0"))
    except Exception:
        min_interval = 2.0

    # 转换为毫秒
    min_interval_ms = int(min_interval * 1000)

    if _quota_rate_limiter is None:
        # [FIX 2026-01-22] 直接创建独立的 RateLimiter 实例，不使用 get_global_rate_limiter
        # 因为 quota 查询需要独立的限流器，和普通 API 请求分开
        _quota_rate_limiter = QuotaRateLimiter(min_interval_ms=min_interval_ms)

    await _quota_rate_limiter.wait()


def _compute_429_retry_delay(
    *,
    attempt: int,
    base_delay: float,
    cooldown_until: float | None = None,
    max_delay: float = 1800.0,  # ✅ [FIX 2026-01-17] 对齐 CLIProxyAPI：最大延迟 30 分钟
    jitter_ratio: float = 0.2,
) -> float:
    """
    429 重试延迟：指数退避 + 抖动（并可参考 quota cooldown，但不做长等待）。

    ✅ [FIX 2026-01-17] 对齐 CLIProxyAPI 的完善退避策略：
    - 基础延迟：1 秒
    - 最大延迟：30 分钟（1800 秒）
    - 公式：delay = 1秒 × 2^attempt
    - 退避时间序列：1s → 2s → 4s → 8s → 16s → ... → 最大 30 分钟

    Args:
        attempt: 当前重试次数（从 0 开始）
        base_delay: 基础延迟（秒）
        cooldown_until: 冷却截止时间戳（可选）
        max_delay: 最大延迟（秒），默认 1800 秒（30 分钟）
        jitter_ratio: 抖动比例，默认 0.2（±20%）

    Returns:
        延迟时间（秒）
    """
    attempt = max(0, int(attempt))
    base_delay = float(base_delay)
    max_delay = float(max_delay)

    delay = min(max_delay, base_delay * (2 ** attempt))

    if cooldown_until:
        remaining = float(cooldown_until) - time.time()
        if remaining > 0:
            delay = max(delay, min(remaining, max_delay))

    if jitter_ratio and jitter_ratio > 0:
        jitter_ratio = float(jitter_ratio)
        delay *= random.uniform(1.0 - jitter_ratio, 1.0 + jitter_ratio)

    return max(0.0, delay)


# ====================== 429 错误处理辅助函数 ======================
# [FIX 2026-01-21] 抽取公共的 429 错误处理逻辑

def _check_capacity_exhausted(error_text: str) -> bool:
    """
    检查错误文本是否表示 MODEL_CAPACITY_EXHAUSTED

    Args:
        error_text: 错误响应文本

    Returns:
        True 表示额度用尽，不应重试
    """
    try:
        error_data = json.loads(error_text)
        details = error_data.get("error", {}).get("details", [])
        for detail in details:
            if detail.get("reason") == "MODEL_CAPACITY_EXHAUSTED":
                return True
    except Exception:
        pass
    return False


async def _record_429_failure(
    baseurl_health_mgr,
    antigravity_url: str,
    model_name: str,
    credential_name: str = "",
    error_text: str = "",
    cooldown_until: float | None = None,
) -> None:
    """
    记录 429 失败到各个健康管理器

    [FIX 2026-01-21] 增加限流状态池集成

    Args:
        baseurl_health_mgr: BaseURL 健康管理器
        antigravity_url: 当前使用的 BaseURL
        model_name: 模型名称
        credential_name: 凭证名称
        error_text: 错误文本
        cooldown_until: 冷却截止时间戳
    """
    # 记录 BaseURL 失败
    await baseurl_health_mgr.record_failure(antigravity_url, error_code=429)

    # 记录池失败
    from src.fallback_manager import get_model_pool, record_pool_failure
    pool_name = get_model_pool(model_name)
    if pool_name != "unknown":
        record_pool_failure(pool_name)

    # [FIX 2026-01-21] 记录到限流状态池
    if credential_name and model_name:
        # 判断限流原因
        reason = "rate_limit"
        if "quota" in error_text.lower() or "exhaust" in error_text.lower():
            reason = "quota_exhausted"

        await mark_rate_limited(
            credential_name,
            model_name,
            status_code=429,
            error_text=error_text[:200],
            cooldown_until=cooldown_until,
            reason=reason,
        )


async def _record_success(
    baseurl_health_mgr,
    antigravity_url: str,
    model_name: str,
    credential_name: str = "",
) -> None:
    """
    记录成功到各个健康管理器

    [FIX 2026-01-21] 增加限流状态池集成

    Args:
        baseurl_health_mgr: BaseURL 健康管理器
        antigravity_url: 当前使用的 BaseURL
        model_name: 模型名称
        credential_name: 凭证名称
    """
    # 记录 BaseURL 成功
    await baseurl_health_mgr.record_success(antigravity_url)

    # 记录池成功
    from src.fallback_manager import get_model_pool, record_pool_success
    pool_name = get_model_pool(model_name)
    if pool_name != "unknown":
        record_pool_success(pool_name)

    # [FIX 2026-01-21] 清除限流状态
    if credential_name and model_name:
        await clear_rate_limit(credential_name, model_name)


from .credential_manager import CredentialManager
from .httpx_client import create_streaming_client_with_kwargs, http_client, safe_close_client
from .models import Model, model_to_dict
from .api.utils import check_should_auto_ban, handle_auto_ban, parse_and_log_cooldown, record_api_call_error
from .utils import ANTIGRAVITY_USER_AGENT, parse_quota_reset_timestamp

# [FIX 2026-01-21] 引入新的风控/限流模块
from .retry_utils import parse_retry_delay_seconds
from .rate_limit_registry import mark_rate_limited, clear_rate_limit, get_rate_limit_registry
from .antigravity_retry_policies import determine_retry_strategy, get_retry_delay_from_error

_antigravity_concurrency_semaphore: asyncio.Semaphore | None = None


def _get_antigravity_max_concurrency() -> int:
    try:
        v = int(os.getenv("ANTIGRAVITY_MAX_CONCURRENCY", "2"))
    except Exception:
        v = 2
    return max(1, v)


def _get_antigravity_concurrency_semaphore() -> asyncio.Semaphore:
    global _antigravity_concurrency_semaphore
    if _antigravity_concurrency_semaphore is None:
        _antigravity_concurrency_semaphore = asyncio.Semaphore(_get_antigravity_max_concurrency())
    return _antigravity_concurrency_semaphore


class _AntigravityPermit:
    def __init__(self) -> None:
        self._sem = _get_antigravity_concurrency_semaphore()
        self._released = False

    async def acquire(self) -> None:
        await self._sem.acquire()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._sem.release()
        except Exception:
            pass


class _StreamCtxWrapper:
    """
    将 permit 的释放绑定到 stream_ctx.__aexit__，以便在流式链路结束时释放并发额度。
    """

    def __init__(self, inner: Any, permit: _AntigravityPermit) -> None:
        self._inner = inner
        self._permit = permit

    async def __aenter__(self) -> Any:
        return await self._inner.__aenter__()

    async def __aexit__(self, exc_type, exc, tb) -> Any:
        try:
            return await self._inner.__aexit__(exc_type, exc, tb)
        finally:
            self._permit.release()


_quota_cache_lock: asyncio.Lock | None = None
_quota_cache: dict[str, dict[str, Any]] = {}
_quota_cache_expires_at: dict[str, float] = {}
_quota_cache_fail_until: dict[str, float] = {}


def _get_quota_cache_lock() -> asyncio.Lock:
    global _quota_cache_lock
    if _quota_cache_lock is None:
        _quota_cache_lock = asyncio.Lock()
    return _quota_cache_lock


def _get_quota_cache_settings() -> tuple[float, float, float]:
    """
    Returns: (ttl_seconds, failure_cooldown_seconds, stale_max_seconds)
    """
    try:
        ttl = float(os.getenv("ANTIGRAVITY_QUOTA_CACHE_TTL_SECONDS", "600"))
    except Exception:
        ttl = 600.0
    try:
        fail_cd = float(os.getenv("ANTIGRAVITY_QUOTA_CACHE_FAILURE_COOLDOWN_SECONDS", "120"))
    except Exception:
        fail_cd = 120.0
    try:
        stale = float(os.getenv("ANTIGRAVITY_QUOTA_CACHE_STALE_MAX_SECONDS", "3600"))
    except Exception:
        stale = 3600.0
    return max(0.0, ttl), max(0.0, fail_cd), max(0.0, stale)

async def _check_should_auto_ban(status_code: int) -> bool:
    """检查是否应该触发自动封禁"""
    return (
        await get_auto_ban_enabled()
        and status_code in await get_auto_ban_error_codes()
    )


async def _handle_auto_ban(
    credential_manager: CredentialManager,
    status_code: int,
    credential_name: str
) -> None:
    """处理自动封禁：直接禁用凭证"""
    if credential_manager and credential_name:
        log.warning(
            f"[ANTIGRAVITY AUTO_BAN] Status {status_code} triggers auto-ban for credential: {credential_name}"
        )
        await credential_manager.set_cred_disabled(credential_name, True, is_antigravity=True)


def build_antigravity_headers(access_token: str, model_name: str = "") -> Dict[str, str]:
    """
    构建 Antigravity API 请求头
    
    [FIX 2026-01-15] 与官方 gcli2api 对齐：
    - 将 requestId 添加到 headers（而非 body）
    - 将 requestType 添加到 headers（而非 body）
    """
    import uuid
    headers = {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip',
        'requestId': f"req-{uuid.uuid4()}"  # [FIX] 添加到 headers
    }
    
    # [FIX] 根据模型名称判断 requestType 并添加到 headers
    if model_name:
        if "image" in model_name.lower():
            headers['requestType'] = "image_gen"
        else:
            headers['requestType'] = "agent"
    
    return headers





def build_antigravity_request_body(
    contents: List[Dict[str, Any]],
    model: str,
    project_id: str,
    session_id: str,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_config: Optional[Dict[str, Any]] = None,
    generation_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 Antigravity 请求体

    Args:
        contents: 消息内容列表
        model: 模型名称
        project_id: 项目 ID
        session_id: 会话 ID
        system_instruction: 系统指令
        tools: 工具定义列表
        generation_config: 生成配置

    Returns:
        Antigravity 格式的请求体
    Raises:
        ValueError: 如果 contents 为空或包含空 parts
    """
    # ✅ 新增：验证 contents 不为空（防御性检查，最后一道防线）
    if not contents:
        raise ValueError("contents cannot be empty. At least one message with valid content is required. "
                        "This is a defensive check - the issue should be caught earlier in chat_completions.")

    # 验证每个 content 的 parts 不为空
    for i, content_item in enumerate(contents):
        parts = content_item.get("parts", [])
        if not parts:
            raise ValueError(f"Content at index {i} (role={content_item.get('role')}) has empty parts. "
                           f"Each content must have at least one part (text, image, or functionCall).")

    # [FIX 2026-01-15] requestId, userAgent, requestType 已迁移到 HTTP Headers 中
    request_body = {
        "project": project_id,
        "model": model,
        "request": {
            "contents": contents,
            "session_id": session_id,
        }
    }

    # [FIX 2026-01-09] 上游同步：添加 custom_prompt 到 systemInstruction
    # Antigravity API 要求必须包含特定的系统提示词
    custom_prompt = "You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.**Absolute paths only****Proactiveness**"
    
    if system_instruction:
        # 存在 systemInstruction，将 custom_prompt 插入到 parts[0]，原有内容后移
        if isinstance(system_instruction, dict):
            parts = system_instruction.get("parts", [])
            if parts:
                # 将 custom_prompt 插入到位置0，原有内容后移
                system_instruction["parts"] = [{"text": custom_prompt}] + parts
            else:
                # parts 为空，创建新的
                system_instruction["parts"] = [{"text": custom_prompt}]
        request_body["request"]["systemInstruction"] = system_instruction
    else:
        # 不存在 systemInstruction，创建新的
        request_body["request"]["systemInstruction"] = {
            "parts": [{"text": custom_prompt}]
        }

    # 添加工具定义
    if tools:
        request_body["request"]["tools"] = tools
        if tool_config:
            request_body["request"]["toolConfig"] = tool_config
        else:
            request_body["request"]["toolConfig"] = {
                "functionCallingConfig": {"mode": "VALIDATED"}
            }

    # 添加生成配置
    if generation_config:
        request_body["request"]["generationConfig"] = generation_config

    return request_body


async def _filter_thinking_from_stream(lines, return_thoughts: bool):
    """过滤流式响应中的思维链（如果配置禁用）"""
    async for line in lines:
        # ✅ [FIX 2026-01-22] 修复类型错误：处理 bytes 和 str 两种类型
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="ignore")
        
        if not line or not line.startswith("data: "):
            yield line
            continue

        raw = line[6:].strip()
        if raw == "[DONE]":
            yield line
            continue

        if not return_thoughts:
            try:
                data = json.loads(raw)
                response = data.get("response", {}) or {}
                candidate = (response.get("candidates", []) or [{}])[0] or {}
                parts = (candidate.get("content", {}) or {}).get("parts", []) or []

                # 过滤掉思维链部分
                filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]

                # 如果过滤后为空，跳过这一行
                if not filtered_parts and parts:
                    continue

                # 更新parts
                if filtered_parts != parts:
                    candidate["content"]["parts"] = filtered_parts
                    yield f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n"
                    continue
            except Exception:
                pass

        yield line


async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
    max_retry_attempts: int = 0,  # ✅ [FIX 2026-01-22] IDE 客户端增强重试：0 表示使用默认配置
    is_ide_client: bool = False,  # ✅ [FIX 2026-01-22] 是否为 IDE 客户端（需要特殊处理）
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    发送 Antigravity 流式请求

    重试策略优化（对齐 CLIProxyAPI）：
    - 5xx 错误：用同一个凭证重试（服务端临时问题，切换凭证没意义）
    - 429 限流：先尝试切换 BaseURL，如果所有 BaseURL 都失败，再切换凭证
    - 400 错误：不重试（客户端参数错误）
    - 额度用尽：不重试（让上层处理降级）

    ✅ [FIX 2026-01-17] 新增 BaseURL 故障转移机制：
    - 遇到 429 时，按顺序尝试：沙箱 → Daily
    - 提高请求成功率，减少因单点故障导致的请求失败

    ✅ [FIX 2026-01-22] IDE 客户端增强重试机制：
    - Cursor/Augment/Windsurf 等 IDE 客户端在 429 时会直接中断会话
    - 增加重试次数到 10 次（模仿 Claude Code）
    - 在重试期间保持 SSE 连接活跃，避免客户端超时

    Args:
        request_body: 请求体
        credential_manager: 凭证管理器
        enable_cross_pool_fallback: 是否启用跨池降级
        max_retry_attempts: 最大重试次数（0 表示使用默认配置）
        is_ide_client: 是否为 IDE 客户端

    Returns:
        (response, credential_name, credential_data)
    """
    retry_enabled = await get_retry_429_enabled()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # ✅ [FIX 2026-01-17] BaseURL 故障转移 - 获取所有可用的 BaseURL
    from config import get_antigravity_fallback_urls
    fallback_urls = await get_antigravity_fallback_urls()

    # ✅ [FIX 2026-01-21] BaseURL 健康状态持久化 - 按健康度排序 URL
    # 优先使用最近成功的 BaseURL，减少首次失败的浪费
    baseurl_health_mgr = get_baseurl_health_manager()
    fallback_urls = baseurl_health_mgr.get_sorted_urls(fallback_urls)

    current_url_index = 0
    base_url_switch_count = 0

    # ✅ [FIX 2026-01-22] IDE 客户端增强重试机制
    # 429/5xx 时切换凭证的最大次数：
    # - 如果调用方指定了 max_retry_attempts，使用指定值
    # - 否则使用统一的 RETRY_429_MAX_RETRIES 配置作为上限（默认 5）
    if max_retry_attempts > 0:
        max_credential_switches = max_retry_attempts
        log.info(f"[ANTIGRAVITY] IDE 客户端增强重试模式: max_credential_switches={max_credential_switches}, is_ide_client={is_ide_client}")
    else:
        max_credential_switches = max(1, await get_retry_429_max_retries())
    credential_switch_count = 0

    # ✅ [FIX 2026-01-22] 5xx 错误时用同一凭证重试的最大次数
    # 400 thinking 格式错误时也用同一凭证重试（仅 IDE 客户端）
    max_same_cred_retries = 2
    same_cred_retry_count = 0  # ✅ [FIX 2026-01-22] 同一凭证重试计数（用于 400 thinking 错误和 5xx 错误）

    # 当前使用的凭证（5xx 错误时复用）
    current_cred_result = None

    # [FIX 2026-01-15] 凭证预热 - 并行获取下一个凭证
    next_cred_task = None

    # [FIX 2026-01-08] 添加 attempt 计数器，使日志更清晰
    attempt_count = 0
    waited_for_credential = False

    while True:
        attempt_count += 1
        
        # 决定是否需要获取新凭证
        need_new_credential = current_cred_result is None

        if need_new_credential:
            # [FIX 2026-01-15] 优先使用预热的凭证
            if next_cred_task is not None:
                try:
                    cred_result = await next_cred_task
                    next_cred_task = None  # 重置任务
                    if cred_result:
                        log.info(f"[ANTIGRAVITY] 使用预热的凭证 (model={model_name})")
                    else:
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=model_name
                        )
                except Exception as e:
                    log.warning(f"[ANTIGRAVITY] 预热凭证任务失败: {e}")
                    next_cred_task = None
                    cred_result = await credential_manager.get_valid_credential(
                        is_antigravity=True, model_key=model_name
                    )
            else:
                # 获取可用凭证（传递模型名称）
                cred_result = await credential_manager.get_valid_credential(
                    is_antigravity=True, model_key=model_name
                )

            # 根据 enable_cross_pool_fallback 参数决定降级策略
            if not cred_result:
                # 对齐 Antigravity-Manager 的 max_wait_seconds：
                # 如果“最早冷却”即将到期，短暂等待后再试一次，避免不必要的跨池降级/503。
                if not waited_for_credential and model_name:
                    try:
                        max_wait = float(os.getenv("ANTIGRAVITY_CREDENTIAL_MAX_WAIT_SECONDS", "10"))
                    except Exception:
                        max_wait = 10.0

                    if max_wait > 0:
                        earliest = await credential_manager.get_earliest_model_cooldown(
                            is_antigravity=True,
                            model_key=model_name,
                        )
                        if earliest:
                            remaining = earliest - time.time()
                            if 0 < remaining <= max_wait:
                                waited_for_credential = True
                                log.info(
                                    f"[ANTIGRAVITY] 当前无可用凭证，等待最早冷却 {remaining:.1f}s 后重试 (model={model_name})"
                                )
                                await asyncio.sleep(remaining)
                                continue

                # [FIX 2026-01-17] 修改降级逻辑：只有所有凭证的该模型都低于20%时才降级
                # 修复原因：
                # 1. sqlite_manager.py 已修复为基于配额百分比判定可用性（20%阈值）
                # 2. 当某个凭证的模型配额<20%时，会自动换号（而不是换模型）
                # 3. 只有当所有凭证的该模型都<20%时，get_valid_credential 才会返回 None
                # 4. 此时才应该降级到其他模型（避免被谷歌盯上）
                if model_name and not cred_result:
                    log.warning(
                        f"[ANTIGRAVITY] {model_name} 所有凭证配额不足（<20%），开始降级到其他模型"
                    )

                    # 先尝试其他 Claude 模型（如果当前是 Claude 模型）
                    from src.fallback_manager import get_model_pool, CLAUDE_THIRD_PARTY_POOL

                    current_pool = get_model_pool(model_name)
                    if current_pool == "claude":
                        # 按优先级尝试其他 Claude 模型
                        claude_models_priority = [
                            "claude-opus-4-5-thinking",
                            "claude-sonnet-4-5-thinking",
                            "claude-sonnet-4-5",
                            "claude-opus-4-5",
                        ]

                        for alt_model in claude_models_priority:
                            if alt_model != model_name:
                                try:
                                    alt_cred = await credential_manager.get_valid_credential(
                                        is_antigravity=True, model_key=alt_model
                                    )
                                    if alt_cred:
                                        log.warning(
                                            f"[ANTIGRAVITY] 降级到其他 Claude 模型: {alt_model}"
                                        )
                                        request_body["model"] = alt_model
                                        model_name = alt_model
                                        cred_result = alt_cred
                                        break
                                except Exception:
                                    continue

                    # 如果 Claude 模型都不可用，再尝试任意凭证（作为最后的兜底）
                    if not cred_result:
                        try:
                            relaxed = await credential_manager.get_valid_credential(
                                is_antigravity=True, model_key=None
                            )
                        except Exception:
                            relaxed = None
                        if relaxed:
                            log.warning(
                                f"[ANTIGRAVITY] model_key={model_name} 无可用凭证，已退化为任意凭证选择"
                            )
                            cred_result = relaxed

                # 只有在所有尝试都失败后，才考虑跨池降级
                if enable_cross_pool_fallback and not cred_result:
                    # Claude Code 模式：尝试跨池降级（仅在真正无法获取凭证时）
                    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                    if fallback_model:
                        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 所有凭证尝试均失败，尝试跨池降级到 {fallback_model}")
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=fallback_model
                        )
                        if cred_result:
                            # 更新请求体中的模型名
                            request_body["model"] = fallback_model
                            model_name = fallback_model  # 更新本地变量
                            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

                # 如果仍然没有凭证，报错
                if not cred_result:
                    log.error(f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback")
                    raise Exception(f"No valid antigravity credentials available for model {model_name}")

            current_cred_result = cred_result
            same_cred_retry_count = 0  # 重置同凭证重试计数

        current_file, credential_data = current_cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            current_cred_result = None  # 强制获取新凭证
            continue

        # [FIX 2026-01-08] 改进日志：显示 attempt 次数和已切换凭证次数
        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, attempt={attempt_count}, cred_switched={credential_switch_count}/{max_credential_switches})")

        # 构建请求头
        headers = build_antigravity_headers(access_token, model_name)

        try:
            # 发送流式请求
            client = await create_streaming_client_with_kwargs()
            # ✅ [FIX 2026-01-17] 使用 BaseURL 故障转移列表中的 URL
            antigravity_url = fallback_urls[current_url_index]
            log.debug(f"[ANTIGRAVITY] Using BaseURL: {antigravity_url} (index={current_url_index}/{len(fallback_urls)-1})")
            permit: _AntigravityPermit | None = None
            permit_transferred = False

            try:
                # 使用stream方法但不在async with块中消费数据
                await _throttle_antigravity_upstream()
                permit = _AntigravityPermit()
                await permit.acquire()
                stream_ctx = client.stream(
                    "POST",
                    f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse",
                    json=request_body,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    # ✅ [FIX 2026-01-23] IDE 客户端详细日志：记录重试成功
                    if is_ide_client and (base_url_switch_count > 0 or credential_switch_count > 0):
                        log.info(
                            f"[ANTIGRAVITY] [IDE客户端] ✅ 重试成功，连接已恢复 "
                            f"(模型: {model_name}, "
                            f"BaseURL: {fallback_urls[current_url_index].split('//')[-1].split('/')[0]}, "
                            f"凭证: {current_file}, "
                            f"BaseURL轮次: {base_url_switch_count}/{len(fallback_urls)-1}, "
                            f"凭证轮次: {credential_switch_count}/{max_credential_switches})"
                        )
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    _reset_429_fallback_failure_count(credential_name=current_file, model_name=model_name)

                    # ✅ [FIX 2026-01-17] 自动恢复机制：请求成功后记录成功调用
                    # 这会重置模型级别的 cooldown 状态，避免长期累积的退避等级影响后续请求
                    from src.api.utils import record_api_call_success
                    await record_api_call_success(
                        credential_manager,
                        current_file,
                        mode="antigravity",
                        model_key=model_name,
                    )

                    # ✅ [FIX 2026-01-21] 使用辅助函数记录成功
                    await _record_success(baseurl_health_mgr, antigravity_url, model_name, credential_name=current_file)

                    # 对齐 gcli2api_official：增加"首 chunk 超时/空流"保护，避免偶发 200 但无内容导致不稳定。
                    # 做法：先从原始 aiter_lines() 里 peek 一个事件行，成功后再接回过滤器。
                    try:
                        first_chunk_timeout = float(os.getenv("ANTIGRAVITY_STREAM_FIRST_CHUNK_TIMEOUT_SECONDS", "15"))
                    except Exception:
                        first_chunk_timeout = 15.0

                    try:
                        stall_cd = float(os.getenv("ANTIGRAVITY_STREAM_STALL_COOLDOWN_SECONDS", "5"))
                    except Exception:
                        stall_cd = 5.0

                    raw_iter = response.aiter_lines()
                    try:
                        first_line = await asyncio.wait_for(raw_iter.__anext__(), timeout=first_chunk_timeout)
                    except (asyncio.TimeoutError, StopAsyncIteration):
                        log.warning(
                            f"[ANTIGRAVITY] 流式响应首 chunk 超时/空流，触发重试 "
                            f"(timeout={first_chunk_timeout}s, cred={current_file}, model={model_name})"
                        )
                        cooldown_until = time.time() + max(0.0, stall_cd)
                        await credential_manager.record_api_call_result(
                            current_file,
                            False,
                            200,
                            cooldown_until=cooldown_until,
                            is_antigravity=True,
                            model_key=model_name,
                        )
                        try:
                            if permit is not None:
                                permit.release()
                            await stream_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                        await safe_close_client(client)
                        current_cred_result = None
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )
                        await asyncio.sleep(retry_interval)
                        continue

                    async def raw_iter_with_first():
                        yield first_line
                        async for line in raw_iter:
                            yield line

                    # 注意: 不在这里记录成功,在流式生成器中第一次收到数据时记录
                    # 获取配置并包装响应流，在源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    filtered_lines = _filter_thinking_from_stream(raw_iter_with_first(), return_thoughts)
                    # 返回过滤后的行生成器和资源管理对象,让调用者管理资源生命周期
                    if permit is not None:
                        stream_ctx = _StreamCtxWrapper(stream_ctx, permit)
                        permit_transferred = True
                    return (filtered_lines, stream_ctx, client), current_file, credential_data

                # 处理错误
                # ✅ [FIX 2026-01-22] 修复：检查响应对象是否有 aread() 方法
                # 在某些情况下（如流式响应），响应对象可能没有 aread() 方法
                if hasattr(response, "aread"):
                    error_body = await response.aread()
                elif hasattr(response, "read"):
                    error_body = await response.read()
                elif hasattr(response, "content"):
                    error_body = response.content
                else:
                    # 如果都没有，尝试从流中读取
                    error_body = b""
                    try:
                        async for chunk in response.aiter_bytes():
                            error_body += chunk
                            if len(error_body) > 10000:  # 限制读取大小
                                break
                    except Exception:
                        pass
                
                if isinstance(error_body, bytes):
                    error_text = error_body.decode('utf-8', errors='ignore')
                else:
                    error_text = str(error_body)
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_text[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    # 优先尊重服务端 Retry-After（如果存在），减少“猜测型 sleep”
                    try:
                        ra = response.headers.get("Retry-After")
                        if ra:
                            ra_s = float(str(ra).strip())
                            if ra_s > 0:
                                cooldown_until = time.time() + ra_s
                                log.info(
                                    f"[ANTIGRAVITY] 429 Retry-After={ra_s:.2f}s (cred={current_file}, model={model_name})"
                                )
                    except Exception:
                        pass

                    if cooldown_until is None:
                        cooldown_until = await parse_and_log_cooldown(error_text, mode="antigravity")

                    if cooldown_until is None:
                        cooldown_until = await _resolve_429_cooldown_until(
                            credential_name=current_file,
                            access_token=access_token,
                            model_name=model_name,
                            error_text=error_text,
                        )
                        if cooldown_until:
                            log.info(
                                f"[ANTIGRAVITY] 429 无重试指令，使用保底/配额刷新冷却: "
                                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                            )

                # 对齐 Antigravity-Manager：500/503/529 做短暂隔离（避免持续打到同一账号/同一模型）
                if response.status_code in (500, 503, 529):
                    try:
                        server_cd = float(os.getenv("ANTIGRAVITY_SERVER_ERROR_COOLDOWN_SECONDS", "20"))
                    except Exception:
                        server_cd = 20.0
                    if server_cd > 0:
                        cooldown_until = time.time() + server_cd
                        log.warning(
                            f"[ANTIGRAVITY] 上游 {response.status_code}，设置短隔离 {server_cd:.0f}s "
                            f"(cred={current_file}, model={model_name})"
                        )

                await record_api_call_error(
                    credential_manager,
                    current_file,
                    response.status_code,
                    cooldown_until,
                    mode="antigravity",
                    model_key=model_name,
                )

                # 检查自动封禁
                if await check_should_auto_ban(response.status_code):
                    await handle_auto_ban(credential_manager, response.status_code, current_file, mode="antigravity")

                # ✅ [FIX 2026-01-22] 清理资源 - 使用 safe_close_client
                try:
                    if permit is not None:
                        permit.release()
                    await stream_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await safe_close_client(client)

                # ✅ [FIX 2026-01-22] 401 错误 - 认证失败，尝试切换凭证
                if response.status_code == 401:
                    log.warning(f"[ANTIGRAVITY] 401 认证失败，尝试切换凭证 (model={model_name})")
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        # ✅ [FIX 2026-01-22] 清理旧资源后再切换凭证
                        try:
                            if permit is not None and not permit_transferred:
                                permit.release()
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception as cleanup_err:
                            log.debug(f"[ANTIGRAVITY] Error cleaning up resources before 401 credential switch: {cleanup_err}")
                        finally:
                            try:
                                await safe_close_client(client)
                            except Exception as cleanup_err:
                                log.debug(f"[ANTIGRAVITY] Error closing client before 401 credential switch: {cleanup_err}")
                        
                        credential_switch_count += 1
                        current_cred_result = None  # 强制获取新凭证
                        current_url_index = 0  # 重置 BaseURL 索引
                        base_url_switch_count = 0  # 重置 BaseURL 切换计数
                        
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )
                        
                        log.warning(f"[ANTIGRAVITY] 401 认证失败，切换凭证重试 ({credential_switch_count}/{max_credential_switches})")
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.error(f"[ANTIGRAVITY] 401 认证失败，已达到最大凭证切换次数 ({max_credential_switches})")
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            message=f"Antigravity API authentication failed ({response.status_code}): {error_text[:200]}",
                        )

                # ✅ [FIX 2026-01-22] 400 错误 - 客户端参数错误，但某些 400 可能是临时问题（如 thinking 格式错误）
                # 对于 IDE 客户端，增加一次重试机会（可能是 thinking 格式问题）
                if response.status_code == 400:
                    # 检查是否是 thinking 格式相关的错误
                    is_thinking_error = (
                        "thinking" in error_text.lower() or
                        "redacted_thinking" in error_text.lower() or
                        "must start with a thinking block" in error_text.lower()
                    )
                    
                    if is_thinking_error and is_ide_client and same_cred_retry_count < 1:
                        # IDE 客户端的 thinking 格式错误，允许重试一次
                        same_cred_retry_count += 1
                        log.warning(
                            f"[ANTIGRAVITY] 400 thinking 格式错误，IDE 客户端重试一次 "
                            f"(model={model_name}, attempt={same_cred_retry_count})"
                        )
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 400 客户端错误，不重试 (model={model_name})")
                        raise NonRetryableError(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

                # 429 错误处理
                if response.status_code == 429:
                    # ✅ [FIX 2026-01-23] IDE 客户端详细日志：记录 429 错误初始检测
                    if is_ide_client:
                        log.warning(
                            f"[ANTIGRAVITY] [IDE客户端] ⚠️  检测到 429 限流错误 "
                            f"(模型: {model_name}, "
                            f"当前 BaseURL: {fallback_urls[current_url_index].split('//')[-1].split('/')[0]}, "
                            f"凭证: {current_file}, "
                            f"BaseURL轮次: {base_url_switch_count}/{len(fallback_urls)-1}, "
                            f"凭证轮次: {credential_switch_count}/{max_credential_switches})"
                        )
                    
                    # ✅ [FIX 2026-01-21] 使用辅助函数检查额度用尽
                    is_capacity_exhausted = _check_capacity_exhausted(error_text)

                    # ✅ [FIX 2026-01-22] IDE 客户端增强重试：即使 MODEL_CAPACITY_EXHAUSTED 也要重试
                    # Cursor/Augment 等 IDE 没有自己的重试机制，429 会直接中断会话
                    # 对于这些客户端，我们需要在 gcli2api 层面进行重试
                    if is_capacity_exhausted:
                        if is_ide_client and credential_switch_count < max_credential_switches:
                            # IDE 客户端：继续重试，尝试换号
                            log.warning(
                                f"[ANTIGRAVITY] [IDE客户端] MODEL_CAPACITY_EXHAUSTED 检测，启用 IDE 客户端重试模式 "
                                f"(轮次: {credential_switch_count + 1}/{max_credential_switches}, 模型: {model_name})"
                            )
                            # 不抛出异常，继续走下面的重试逻辑
                        else:
                            # 非 IDE 客户端或已达到最大重试次数：让上层处理降级
                            log.warning(f"[ANTIGRAVITY] MODEL_CAPACITY_EXHAUSTED detected, not retrying (model={model_name})")
                            raise AntigravityUpstreamError(
                                status_code=response.status_code,
                                message=f"Antigravity API error ({response.status_code}): {error_text[:200]}",
                            )

                    # ✅ [FIX 2026-01-21] 使用辅助函数记录失败
                    await _record_429_failure(
                        baseurl_health_mgr, antigravity_url, model_name,
                        credential_name=current_file,
                        error_text=error_text,
                        cooldown_until=cooldown_until,
                    )

                    # ✅ [FIX 2026-01-17] BaseURL 故障转移 - 先尝试切换 BaseURL
                    if current_url_index < len(fallback_urls) - 1:
                        # ✅ [FIX 2026-01-22] 清理旧资源后再切换 BaseURL
                        try:
                            if permit is not None and not permit_transferred:
                                permit.release()
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception as cleanup_err:
                            log.debug(f"[ANTIGRAVITY] Error cleaning up resources before BaseURL switch: {cleanup_err}")
                        finally:
                            try:
                                await safe_close_client(client)
                            except Exception as cleanup_err:
                                log.debug(f"[ANTIGRAVITY] Error closing client before BaseURL switch: {cleanup_err}")
                        
                        current_url_index += 1
                        base_url_switch_count += 1
                        
                        # ✅ [FIX 2026-01-23] IDE 客户端（Cursor/Augment）保持重试至少 3 分钟
                        # IDE 客户端在 429 时会直接中断会话，需要保持连接至少 3 分钟
                        if is_ide_client:
                            # IDE 客户端：保持重试至少 3 分钟（180 秒），与 retry_keepalive_seconds 对齐
                            delay = _compute_429_retry_delay(
                                attempt=base_url_switch_count - 1,
                                base_delay=retry_interval,
                                cooldown_until=cooldown_until,
                                max_delay=180.0,  # IDE 客户端最大延迟 180 秒（3 分钟）
                            )
                        else:
                            # 非 IDE 客户端：使用标准延迟
                            delay = _compute_429_retry_delay(
                                attempt=base_url_switch_count - 1,
                                base_delay=retry_interval,
                                cooldown_until=cooldown_until,
                            )
                        
                        # ✅ [FIX 2026-01-23] IDE 客户端详细日志：添加重试时间、轮次、状态提示
                        next_url = fallback_urls[current_url_index]
                        url_domain = next_url.split("//")[-1].split("/")[0] if "//" in next_url else next_url
                        
                        if is_ide_client:
                            # IDE 客户端详细日志（类似 Claude Code 重连风格）
                            log.warning(
                                f"[ANTIGRAVITY] [IDE客户端] 429 限流，切换 BaseURL 重试 "
                                f"轮次: {base_url_switch_count}/{len(fallback_urls)-1}, "
                                f"目标: {url_domain}, "
                                f"等待时间: {delay:.1f}秒, "
                                f"模型: {model_name}, "
                                f"尝试连接中..."
                            )
                            log.info(
                                f"[ANTIGRAVITY] [IDE客户端] ⏱️  重试延迟: {delay:.2f}秒 "
                                f"(轮次 {base_url_switch_count}/{len(fallback_urls)-1}, "
                                f"BaseURL: {url_domain})"
                            )
                        else:
                            # 非 IDE 客户端标准日志
                            log.warning(
                                f"[ANTIGRAVITY] 429 限流，切换 BaseURL 重试 "
                                f"({base_url_switch_count}/{len(fallback_urls)-1}): {next_url}"
                            )
                        
                        await asyncio.sleep(delay)
                        continue

                    # 所有 BaseURL 都失败了，尝试切换凭证
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        # ✅ [FIX 2026-01-22] 清理旧资源后再切换凭证
                        try:
                            if permit is not None and not permit_transferred:
                                permit.release()
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception as cleanup_err:
                            log.debug(f"[ANTIGRAVITY] Error cleaning up resources before credential switch: {cleanup_err}")
                        finally:
                            try:
                                await safe_close_client(client)
                            except Exception as cleanup_err:
                                log.debug(f"[ANTIGRAVITY] Error closing client before credential switch: {cleanup_err}")
                        
                        credential_switch_count += 1
                        current_cred_result = None  # 强制获取新凭证
                        current_url_index = 0  # 重置 BaseURL 索引
                        base_url_switch_count = 0  # 重置 BaseURL 切换计数

                        # [FIX 2026-01-15] 并行预热下一个凭证
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )

                        # ✅ [FIX 2026-01-23] IDE 客户端（Cursor/Augment）保持重试至少 3 分钟
                        # IDE 客户端在 429 时会直接中断会话，需要保持连接至少 3 分钟
                        if is_ide_client:
                            # IDE 客户端：保持重试至少 3 分钟（180 秒），与 retry_keepalive_seconds 对齐
                            delay = _compute_429_retry_delay(
                                attempt=credential_switch_count - 1,
                                base_delay=retry_interval,
                                cooldown_until=cooldown_until,
                                max_delay=180.0,  # IDE 客户端最大延迟 180 秒（3 分钟）
                            )
                        else:
                            # 非 IDE 客户端：使用标准延迟
                            delay = _compute_429_retry_delay(
                                attempt=credential_switch_count - 1,
                                base_delay=retry_interval,
                                cooldown_until=cooldown_until,
                            )
                        
                        # ✅ [FIX 2026-01-23] IDE 客户端详细日志：添加重试时间、轮次、状态提示
                        if is_ide_client:
                            # IDE 客户端详细日志（类似 Claude Code 重连风格）
                            log.warning(
                                f"[ANTIGRAVITY] [IDE客户端] 429 限流，所有 BaseURL 失败，切换凭证重试 "
                                f"轮次: {credential_switch_count}/{max_credential_switches}, "
                                f"等待时间: {delay:.1f}秒, "
                                f"模型: {model_name}, "
                                f"尝试连接中..."
                            )
                            log.info(
                                f"[ANTIGRAVITY] [IDE客户端] ⏱️  重试延迟: {delay:.2f}秒 "
                                f"(凭证轮次 {credential_switch_count}/{max_credential_switches}, "
                                f"模型: {model_name})"
                            )
                        else:
                            # 非 IDE 客户端标准日志
                            log.warning(f"[ANTIGRAVITY] 429 限流，所有 BaseURL 失败，切换凭证重试 ({credential_switch_count}/{max_credential_switches})")
                        
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # 只有"明确的 QUOTA_EXHAUSTED"才视为配额耗尽；否则按临时限流处理
                        quota_exhausted = is_quota_exhausted_error(error_text)
                        tag = "QUOTA_EXHAUSTED" if quota_exhausted else "RATE_LIMITED"

                        # ✅ [FIX 2026-01-22] 自动校验：所有凭证轮换用尽仍然 429 时触发
                        # 触发条件：连续 5 次"所有凭证用尽"，20 分钟冷却期
                        # 校验失败则返回 should_fallback=True，让上层降级到其他后端
                        try:
                            from .auto_verify import record_all_credentials_exhausted
                            verify_result, should_fallback = await record_all_credentials_exhausted(
                                credential_name=current_file,
                                model_name=model_name,
                                error_text=error_text,
                                is_antigravity=True,
                            )
                            if verify_result is True:
                                # 校验成功，重置并重试
                                log.info(f"[ANTIGRAVITY] Auto verify SUCCESS, resetting and retrying...")
                                credential_switch_count = 0
                                current_cred_result = None
                                current_url_index = 0
                                base_url_switch_count = 0
                                await asyncio.sleep(1.0)  # 短暂等待后重试
                                continue
                            elif should_fallback:
                                # 校验失败，标记需要降级
                                tag = "RATE_LIMITED_FALLBACK"
                                log.warning(f"[ANTIGRAVITY] Auto verify failed, marking for fallback to other backend")
                        except Exception as auto_verify_err:
                            log.debug(f"[ANTIGRAVITY] Auto verify error (non-critical): {auto_verify_err}")

                        log.warning(
                            f"[ANTIGRAVITY] 429 限流，已达到最大凭证切换次数 ({max_credential_switches})，"
                            f"标记为 {tag}"
                        )
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            tag=tag,
                            message=(
                                f"Antigravity API error ({response.status_code}) [{tag}]: "
                                f"All credentials exhausted. {error_text[:200]}"
                            ),
                        )

                # 503/529：Google 现在用这些错误码进行限流，优先热切换账号（并写入短隔离）
                # ✅ [FIX 2026-01-17] 保持指数退避策略，但仍然切换凭证
                if response.status_code in (503, 529):
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        # ✅ [FIX 2026-01-22] 清理旧资源后再切换凭证
                        try:
                            if permit is not None and not permit_transferred:
                                permit.release()
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception as cleanup_err:
                            log.debug(f"[ANTIGRAVITY] Error cleaning up resources before 503/529 credential switch: {cleanup_err}")
                        finally:
                            try:
                                await safe_close_client(client)
                            except Exception as cleanup_err:
                                log.debug(f"[ANTIGRAVITY] Error closing client before 503/529 credential switch: {cleanup_err}")
                        
                        credential_switch_count += 1
                        current_cred_result = None
                        current_url_index = 0  # 重置 BaseURL 索引
                        base_url_switch_count = 0  # 重置 BaseURL 切换计数
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )
                        # ✅ [FIX 2026-01-17] 使用指数退避而不是固定延迟
                        delay = retry_interval * (2 ** (credential_switch_count - 1))
                        log.warning(
                            f"[ANTIGRAVITY] {response.status_code} 触发热切换凭证重试 "
                            f"({credential_switch_count}/{max_credential_switches}), "
                            f"指数退避延迟 {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        continue

                # ✅ [FIX 2026-01-17] 错误码差异化处理：其他 5xx 错误用同一凭证重试
                # 原因：标准 5xx（500/502/504等）是服务端临时问题，切换凭证没有意义
                # 策略：指数退避重试，最多重试 3 次
                if response.status_code >= 500:
                    same_cred_retry_count += 1
                    if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                        # 使用指数退避：1s → 2s → 4s
                        delay = retry_interval * (2 ** (same_cred_retry_count - 1))
                        log.warning(
                            f"[ANTIGRAVITY] 5xx 服务端错误 ({response.status_code})，"
                            f"用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})，"
                            f"延迟 {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，已达到最大重试次数 ({max_same_cred_retries})，不再重试")
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            message=f"Antigravity API error ({response.status_code}): {error_text[:200]}",
                        )

                # 其他错误，直接抛出
                raise AntigravityUpstreamError(
                    status_code=response.status_code,
                    message=f"Antigravity API error ({response.status_code}): {error_text[:200]}",
                )

            except NonRetryableError:
                # ✅ [FIX 2026-01-22] 不可重试的错误，确保清理资源后直接向上抛出
                try:
                    if permit is not None and not permit_transferred:
                        permit.release()
                    await safe_close_client(client)
                except Exception:
                    pass
                raise
            except Exception as stream_error:
                # ✅ [FIX 2026-01-22] 确保在异常情况下也清理资源
                try:
                    if permit is not None and not permit_transferred:
                        permit.release()
                    await safe_close_client(client)
                except Exception:
                    pass
                raise stream_error

        except (NonRetryableError, AntigravityUpstreamError):
            # 明确不可重试的上游/客户端错误，直接向上抛出，避免被误判为“网络错误”回声室重试
            raise
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            # 网络错误 - 用同一凭证重试
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            same_cred_retry_count = getattr(locals(), 'same_cred_retry_count', 0) + 1
            if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                log.warning(f"[ANTIGRAVITY] 网络错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                await asyncio.sleep(retry_interval)
                continue
            raise
        except Exception as e:
            # 未知错误：不要盲目重试，避免扩大上游压力
            log.error(f"[ANTIGRAVITY] Unexpected error (no retry): {e}")
            raise


async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
    enable_cross_pool_fallback: bool = False,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    发送 Antigravity 非流式请求 (内部使用 Auto-Stream Conversion)

    ⚠️ 函数名与内部行为不一致说明 ⚠️
    =========================================
    此函数名为 "no_stream"，但内部实际使用流式 API (streamGenerateContent)。
    这是一个 **有意为之** 的设计决策，原因如下：

    1. **配额差异**：Google API 对流式请求 (streamGenerateContent) 的配额限制
       比非流式请求 (generateContent) 宽松得多。使用流式 API 可以显著减少 429 错误。

    2. **向后兼容**：保留原函数名和签名，确保所有调用方无需修改代码。
       调用方仍然认为它在发送"非流式请求"，并获得完整的 JSON 响应。

    3. **代理层透明转换**：
       - 客户端发送非流式请求 → 代理层转换为流式请求发送给 Google
       - 代理层收集 SSE 响应 → 重组为完整 JSON 返回给客户端
       - 客户端感知不到这个转换过程

    此功能移植自 Antigravity_Tools 项目的 Auto-Stream Conversion 功能，
    该功能在实践中证明可以将请求成功率从 10-20% 提升到 95%+，几乎消除 429 错误。
    代价是响应时间增加约 100-200ms（SSE 收集开销）。

    参考：Antigravity_Tools/src-tauri/src/proxy/handlers/claude.rs 第 622-700 行

    重试策略优化（对齐 CLIProxyAPI）：
    - 5xx 错误：用同一个凭证重试（服务端临时问题，切换凭证没意义）
    - 429 限流：先尝试切换 BaseURL，如果所有 BaseURL 都失败，再切换凭证
    - 400 错误：不重试（客户端参数错误）
    - 额度用尽：不重试（让上层处理降级）

    ✅ [FIX 2026-01-17] 新增 BaseURL 故障转移机制：
    - 遇到 429 时，按顺序尝试：沙箱 → Daily → 生产
    - 提高请求成功率，减少因单点故障导致的请求失败

    Returns:
        (response_data, credential_name, credential_data)
    """

    retry_enabled = await get_retry_429_enabled()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # ✅ [FIX 2026-01-17] BaseURL 故障转移 - 获取所有可用的 BaseURL
    from config import get_antigravity_fallback_urls
    fallback_urls = await get_antigravity_fallback_urls()

    # ✅ [FIX 2026-01-21] BaseURL 健康状态持久化 - 按健康度排序 URL
    # 优先使用最近成功的 BaseURL，减少首次失败的浪费
    baseurl_health_mgr = get_baseurl_health_manager()
    fallback_urls = baseurl_health_mgr.get_sorted_urls(fallback_urls)

    current_url_index = 0
    base_url_switch_count = 0

    # 429/5xx 时切换凭证的最大次数：使用统一配置上限（默认 5）
    max_credential_switches = max(1, await get_retry_429_max_retries())
    credential_switch_count = 0

    # 5xx 错误时用同一凭证重试的最大次数
    max_same_cred_retries = 2

    # 当前使用的凭证（5xx 错误时复用）
    current_cred_result = None
    same_cred_retry_count = 0

    # [FIX 2026-01-15] 凭证预热 - 并行获取下一个凭证
    next_cred_task = None

    # [FIX 2026-01-08] 添加 attempt 计数器，使日志更清晰
    attempt_count = 0
    waited_for_credential = False

    while True:
        attempt_count += 1
        
        # 决定是否需要获取新凭证
        need_new_credential = current_cred_result is None

        if need_new_credential:
            # [FIX 2026-01-15] 优先使用预热的凭证
            if next_cred_task is not None:
                try:
                    cred_result = await next_cred_task
                    next_cred_task = None  # 重置任务
                    if cred_result:
                        log.info(f"[ANTIGRAVITY] 使用预热的凭证 (model={model_name})")
                    else:
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=model_name
                        )
                except Exception as e:
                    log.warning(f"[ANTIGRAVITY] 预热凭证任务失败: {e}")
                    next_cred_task = None
                    cred_result = await credential_manager.get_valid_credential(
                        is_antigravity=True, model_key=model_name
                    )
            else:
                # 获取可用凭证（传递模型名称）
                cred_result = await credential_manager.get_valid_credential(
                    is_antigravity=True, model_key=model_name
                )

            # 根据 enable_cross_pool_fallback 参数决定降级策略
            if not cred_result:
                # 对齐 Antigravity-Manager 的 max_wait_seconds：短暂等待最早冷却后再试一次
                if not waited_for_credential and model_name:
                    try:
                        max_wait = float(os.getenv("ANTIGRAVITY_CREDENTIAL_MAX_WAIT_SECONDS", "10"))
                    except Exception:
                        max_wait = 10.0

                    if max_wait > 0:
                        earliest = await credential_manager.get_earliest_model_cooldown(
                            is_antigravity=True,
                            model_key=model_name,
                        )
                        if earliest:
                            remaining = earliest - time.time()
                            if 0 < remaining <= max_wait:
                                waited_for_credential = True
                                log.info(
                                    f"[ANTIGRAVITY] 当前无可用凭证，等待最早冷却 {remaining:.1f}s 后重试 (model={model_name})"
                                )
                                await asyncio.sleep(remaining)
                                continue

                # [FIX 2026-01-15] 优先尝试其他凭证的同模型，而不是降级到不同模型
                if model_name and not cred_result:
                    # 先尝试其他 Claude 模型（如果当前是 Claude 模型）
                    from src.fallback_manager import get_model_pool, CLAUDE_THIRD_PARTY_POOL
                    
                    current_pool = get_model_pool(model_name)
                    if current_pool == "claude":
                        # 按优先级尝试其他 Claude 模型
                        claude_models_priority = [
                            "claude-opus-4-5-thinking",
                            "claude-sonnet-4-5-thinking",
                            "claude-sonnet-4-5",
                            "claude-opus-4-5",
                        ]
                        
                        for alt_model in claude_models_priority:
                            if alt_model != model_name:
                                try:
                                    alt_cred = await credential_manager.get_valid_credential(
                                        is_antigravity=True, model_key=alt_model
                                    )
                                    if alt_cred:
                                        log.warning(
                                            f"[ANTIGRAVITY] {model_name} 所有凭证不可用，尝试其他 Claude 模型: {alt_model}"
                                        )
                                        request_body["model"] = alt_model
                                        model_name = alt_model
                                        cred_result = alt_cred
                                        break
                                except Exception:
                                    continue
                    
                    # 如果 Claude 模型都不可用，再尝试任意凭证（作为最后的兜底）
                    if not cred_result:
                        try:
                            relaxed = await credential_manager.get_valid_credential(
                                is_antigravity=True, model_key=None
                            )
                        except Exception:
                            relaxed = None
                        if relaxed:
                            log.warning(
                                f"[ANTIGRAVITY] model_key={model_name} 无可用凭证，已退化为任意凭证选择"
                            )
                            cred_result = relaxed

                # 只有在所有尝试都失败后，才考虑跨池降级
                if enable_cross_pool_fallback and not cred_result:
                    # Claude Code 模式：尝试跨池降级（仅在真正无法获取凭证时）
                    fallback_model = get_cross_pool_fallback(model_name, log_level="info")
                    if fallback_model:
                        log.warning(f"[ANTIGRAVITY FALLBACK] 模型 {model_name} 所有凭证尝试均失败，尝试跨池降级到 {fallback_model}")
                        cred_result = await credential_manager.get_valid_credential(
                            is_antigravity=True, model_key=fallback_model
                        )
                        if cred_result:
                            # 更新请求体中的模型名
                            request_body["model"] = fallback_model
                            model_name = fallback_model  # 更新本地变量
                            log.info(f"[ANTIGRAVITY FALLBACK] 成功降级到 {fallback_model}")

                # 如果仍然没有凭证，报错
                if not cred_result:
                    log.error(
                        f"[ANTIGRAVITY] No valid credentials for model {model_name} - let Gateway handle fallback"
                    )
                    raise AntigravityUpstreamError(
                        status_code=503,
                        tag="NO_CREDENTIAL",
                        message=f"No valid antigravity credentials available for model {model_name}",
                    )

            current_cred_result = cred_result
            same_cred_retry_count = 0  # 重置同凭证重试计数

        current_file, credential_data = current_cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            current_cred_result = None  # 强制获取新凭证
            continue

        # [FIX 2026-01-08] 改进日志：显示 attempt 次数和已切换凭证次数
        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, attempt={attempt_count}, cred_switched={credential_switch_count}/{max_credential_switches})")

        # 构建请求头
        headers = build_antigravity_headers(access_token, model_name)

        try:
            # =========================================
            # [AUTO-STREAM CONVERSION] 2026-01-11
            # =========================================
            # 虽然客户端请求的是非流式响应，但内部使用流式 API 以享受更宽松的配额限制。
            # 流程：POST streamGenerateContent → 收集 SSE 流 → 重组为 JSON → 返回
            #
            # 参考 Antigravity_Tools 的实现：
            # - src-tauri/src/proxy/handlers/claude.rs:622-700
            # - src-tauri/src/proxy/mappers/claude/collector.rs
            # =========================================

            # ✅ [FIX 2026-01-17] 使用 BaseURL 故障转移列表中的 URL
            antigravity_url = fallback_urls[current_url_index]
            log.debug(f"[ANTIGRAVITY] Using BaseURL: {antigravity_url} (index={current_url_index}/{len(fallback_urls)-1})")

            log.info(f"[ANTIGRAVITY] 🔄 Auto-converting non-stream to stream for better quota (model={model_name})")

            # 使用流式客户端发送请求
            client = await create_streaming_client_with_kwargs()
            stream_ctx = None
            permit: _AntigravityPermit | None = None
             
            try:
                # 使用 streamGenerateContent 代替 generateContent
                await _throttle_antigravity_upstream()
                permit = _AntigravityPermit()
                await permit.acquire()
                stream_ctx = client.stream(
                    "POST",
                    f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse",
                    json=request_body,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Stream started successfully with credential: {current_file}")

                    # ✅ [FIX 2026-01-17] 自动恢复机制：请求成功后记录成功调用
                    # 这会重置模型级别的 cooldown 状态，避免长期累积的退避等级影响后续请求
                    from src.api.utils import record_api_call_success
                    await record_api_call_success(
                        credential_manager,
                        current_file,
                        mode="antigravity",
                        model_key=model_name,
                    )

                    # ✅ [FIX 2026-01-21] 使用辅助函数记录成功
                    await _record_success(baseurl_health_mgr, antigravity_url, model_name, credential_name=current_file)

                    # 导入 SSE 收集器并收集完整响应（带超时保护）
                    # ✅ [FIX 2026-01-17] 使用带超时的收集器，防止 SSE 流卡住导致对话中断
                    from .sse_collector import collect_sse_to_json_with_timeout

                    try:
                        # 从环境变量获取超时时间，默认 300 秒（5 分钟）
                        # Thinking 模型可能需要更长时间，所以默认值较大
                        try:
                            sse_timeout = float(os.getenv("ANTIGRAVITY_SSE_COLLECT_TIMEOUT_SECONDS", "300"))
                        except Exception:
                            sse_timeout = 300.0

                        log.debug(f"[ANTIGRAVITY] Starting SSE collection with timeout={sse_timeout}s (model={model_name})")
                        # ✅ [FIX 2026-01-17] 修复日志级别检查：使用正确的日志级别获取方法
                        from log import _get_current_log_level, LOG_LEVELS
                        debug_mode = _get_current_log_level() <= LOG_LEVELS["debug"]
                        response_data = await collect_sse_to_json_with_timeout(
                            response.aiter_lines(),
                            timeout_seconds=sse_timeout,
                            debug=debug_mode,
                        )
                        log.info(f"[ANTIGRAVITY] ✓ SSE collected and converted to JSON (model={model_name})")
                    except asyncio.TimeoutError:
                        log.error(f"[ANTIGRAVITY] SSE collection timeout after {sse_timeout}s (model={model_name})")
                        raise Exception(f"SSE collection timeout after {sse_timeout}s for model {model_name}")
                    except Exception as collect_err:
                        log.error(f"[ANTIGRAVITY] SSE collection failed: {collect_err} (model={model_name})")
                        raise Exception(f"SSE collection failed: {collect_err}")

                    # ✅ [FIX 2026-01-17] Auto-Stream Conversion 路径的 Signature 缓存写入
                    # 问题：之前只有流式路径（antigravity_router.py, anthropic_streaming.py）会写入缓存
                    # 但 send_antigravity_request_no_stream 使用 Auto-Stream Conversion 时没有写入
                    # 这导致 thinking 模型的 signature 丢失，后续对话无法恢复
                    try:
                        from .signature_cache import cache_signature, cache_tool_signature
                        candidate = (response_data.get("response", {}) or {}).get("candidates", [{}])[0] or {}
                        parts = (candidate.get("content", {}) or {}).get("parts", []) or []

                        # 用于工具签名缓存的 thinking signature（从 thinking block 中提取）
                        current_thinking_signature = None
                        
                        for part in parts:
                            if not isinstance(part, dict):
                                continue
                            
                            # 处理 thinking block - 缓存 thinking 签名
                            if part.get("thought") is True:
                                thinking_text = part.get("text", "")
                                thought_signature = part.get("thoughtSignature")
                                if thinking_text and thought_signature:
                                    success = cache_signature(
                                        thinking_text=thinking_text,
                                        signature=thought_signature,
                                        model=model_name
                                    )
                                    if success:
                                        log.info(
                                            f"[SIGNATURE_CACHE] Auto-Stream 路径 thinking 缓存写入成功: "
                                            f"thinking_len={len(thinking_text)}, model={model_name}"
                                        )
                                    # 保存 thinking signature 用于后续工具调用
                                    current_thinking_signature = thought_signature
                                    break  # 只缓存第一个 thinking block
                            
                            # ✅ [FIX 2026-01-17] 处理 functionCall - 缓存工具签名
                            # 问题：Auto-Stream Conversion 路径没有缓存工具签名，导致工具调用时无法恢复签名
                            elif "functionCall" in part:
                                function_call = part.get("functionCall", {})
                                tool_id = function_call.get("id")
                                tool_signature = part.get("thoughtSignature") or current_thinking_signature
                                
                                if tool_id and tool_signature:
                                    try:
                                        cache_tool_signature(tool_id, tool_signature)
                                        log.info(
                                            f"[SIGNATURE_CACHE] Auto-Stream 路径工具签名缓存写入成功: "
                                            f"tool_id={tool_id}, model={model_name}"
                                        )
                                    except Exception as tool_cache_err:
                                        log.warning(f"[SIGNATURE_CACHE] Auto-Stream 路径工具签名缓存失败: {tool_cache_err}")
                    except Exception as cache_err:
                        log.warning(f"[SIGNATURE_CACHE] Auto-Stream 路径缓存写入失败: {cache_err}")

                    await credential_manager.record_api_call_result(
                        current_file, True, is_antigravity=True, model_key=model_name
                    )

                    # 从源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    if not return_thoughts:
                        try:
                            candidate = (response_data.get("response", {}) or {}).get("candidates", [{}])[0] or {}
                            parts = (candidate.get("content", {}) or {}).get("parts", []) or []
                            # 过滤掉思维链部分
                            filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]
                            if filtered_parts != parts:
                                candidate["content"]["parts"] = filtered_parts
                        except Exception as e:
                            log.debug(f"[ANTIGRAVITY] Failed to filter thinking from response: {e}")

                    _reset_429_fallback_failure_count(credential_name=current_file, model_name=model_name)

                    # ========== [NEW 2026-01-22] Token 统计记录 ==========
                    try:
                        from src import token_stats
                        # 从响应中提取 usage 信息
                        usage_metadata = (response_data.get("response", {}) or {}).get("usageMetadata", {})
                        if usage_metadata:
                            input_tokens = usage_metadata.get("promptTokenCount", 0)
                            output_tokens = usage_metadata.get("candidatesTokenCount", 0)
                            await token_stats.record_usage(
                                account_email=credential_data.get("email", "unknown"),
                                model=model_name,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                credential_file=current_file,
                                is_antigravity=True
                            )
                    except Exception as e:
                        log.warning(f"[TOKEN_STATS] Failed to record usage: {e}")
                    # ========== Token 统计记录结束 ==========

                    return response_data, current_file, credential_data

                # 处理错误
                # ✅ [FIX 2026-01-22] 修复：安全地获取错误响应体
                # 流式响应对象可能没有 text 属性，需要尝试多种方法
                if hasattr(response, "text"):
                    error_body = response.text
                elif hasattr(response, "aread"):
                    error_body_bytes = await response.aread()
                    error_body = error_body_bytes.decode('utf-8', errors='ignore') if isinstance(error_body_bytes, bytes) else str(error_body_bytes)
                elif hasattr(response, "read"):
                    error_body_bytes = await response.read()
                    error_body = error_body_bytes.decode('utf-8', errors='ignore') if isinstance(error_body_bytes, bytes) else str(error_body_bytes)
                elif hasattr(response, "content"):
                    content = response.content
                    error_body = content.decode('utf-8', errors='ignore') if isinstance(content, bytes) else str(content)
                else:
                    # 如果都没有，尝试从流中读取
                    error_body = ""
                    try:
                        async for chunk in response.aiter_bytes():
                            error_body += chunk.decode('utf-8', errors='ignore')
                            if len(error_body) > 10000:  # 限制读取大小
                                break
                    except Exception:
                        error_body = f"Failed to read error response (status={response.status_code})"
                
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_body[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    try:
                        ra = response.headers.get("Retry-After")
                        if ra:
                            ra_s = float(str(ra).strip())
                            if ra_s > 0:
                                cooldown_until = time.time() + ra_s
                                log.info(
                                    f"[ANTIGRAVITY] 429 Retry-After={ra_s:.2f}s (cred={current_file}, model={model_name})"
                                )
                    except Exception:
                        pass

                    if cooldown_until is None:
                        cooldown_until = await parse_and_log_cooldown(error_body, mode="antigravity")

                    if cooldown_until is None:
                        cooldown_until = await _resolve_429_cooldown_until(
                            credential_name=current_file,
                            access_token=access_token,
                            model_name=model_name,
                            error_text=error_body,
                        )
                        if cooldown_until:
                            log.info(
                                f"[ANTIGRAVITY] 429 无重试指令，使用保底/配额刷新冷却: "
                                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                            )

                # 对齐 Antigravity-Manager：500/503/529 做短暂隔离（避免持续打到同一账号/同一模型）
                if response.status_code in (500, 503, 529):
                    try:
                        server_cd = float(os.getenv("ANTIGRAVITY_SERVER_ERROR_COOLDOWN_SECONDS", "20"))
                    except Exception:
                        server_cd = 20.0
                    if server_cd > 0:
                        cooldown_until = time.time() + server_cd
                        log.warning(
                            f"[ANTIGRAVITY] 上游 {response.status_code}，设置短隔离 {server_cd:.0f}s "
                            f"(cred={current_file}, model={model_name})"
                        )

                await record_api_call_error(
                    credential_manager,
                    current_file,
                    response.status_code,
                    cooldown_until,
                    mode="antigravity",
                    model_key=model_name,
                )

                # 检查自动封禁
                if await check_should_auto_ban(response.status_code):
                    await handle_auto_ban(credential_manager, response.status_code, current_file, mode="antigravity")

                # 400 错误 - 客户端参数错误，不重试
                if response.status_code == 400:
                    log.warning(f"[ANTIGRAVITY] 400 客户端错误，不重试 (model={model_name})")
                    raise NonRetryableError(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

                # 429 错误处理
                if response.status_code == 429:
                    # ✅ [FIX 2026-01-21] 使用辅助函数检查额度用尽
                    is_capacity_exhausted = _check_capacity_exhausted(error_body)
                    if is_capacity_exhausted:
                        log.warning(f"[ANTIGRAVITY] MODEL_CAPACITY_EXHAUSTED detected, not retrying (model={model_name})")
                        # 额度用尽，不重试，让上层处理降级
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            message=f"Antigravity API error ({response.status_code}): {error_body[:200]}",
                        )

                    # ✅ [FIX 2026-01-21] 使用辅助函数记录失败
                    await _record_429_failure(
                        baseurl_health_mgr, antigravity_url, model_name,
                        credential_name=current_file,
                        error_text=error_body,
                        cooldown_until=cooldown_until,
                    )

                    # ✅ [FIX 2026-01-17] BaseURL 故障转移 - 先尝试切换 BaseURL
                    if current_url_index < len(fallback_urls) - 1:
                        current_url_index += 1
                        base_url_switch_count += 1
                        log.warning(
                            f"[ANTIGRAVITY] 429 限流，切换 BaseURL 重试 "
                            f"({base_url_switch_count}/{len(fallback_urls)-1}): {fallback_urls[current_url_index]}"
                        )
                        # 清理流式资源
                        try:
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                        try:
                            await safe_close_client(client)
                        except Exception:
                            pass
                        # ✅ [FIX 2026-01-23] 非流式请求也使用标准延迟（非 IDE 客户端场景）
                        delay = _compute_429_retry_delay(
                            attempt=base_url_switch_count - 1,
                            base_delay=retry_interval,
                            cooldown_until=cooldown_until,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # 所有 BaseURL 都失败了，尝试切换凭证
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        credential_switch_count += 1
                        current_cred_result = None  # 强制获取新凭证
                        current_url_index = 0  # 重置 BaseURL 索引
                        base_url_switch_count = 0  # 重置 BaseURL 切换计数

                        # [FIX 2026-01-15] 并行预热下一个凭证
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )

                        log.warning(f"[ANTIGRAVITY] 429 限流，所有 BaseURL 失败，切换凭证重试 ({credential_switch_count}/{max_credential_switches})")
                        # 清理流式资源
                        try:
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                        try:
                            await safe_close_client(client)
                        except Exception:
                            pass
                        delay = _compute_429_retry_delay(
                            attempt=credential_switch_count - 1,
                            base_delay=retry_interval,
                            cooldown_until=cooldown_until,
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        quota_exhausted = is_quota_exhausted_error(error_body)
                        tag = "QUOTA_EXHAUSTED" if quota_exhausted else "RATE_LIMITED"
                        log.warning(
                            f"[ANTIGRAVITY] 429 限流，已达到最大凭证切换次数 ({max_credential_switches})，标记为 {tag}"
                        )
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            tag=tag,
                            message=(
                                f"Antigravity API error ({response.status_code}) [{tag}]: "
                                f"All credentials exhausted. {error_body[:200]}"
                            ),
                        )

                # 503/529：Google 现在用这些错误码进行限流，优先热切换账号（并写入短隔离）
                # ✅ [FIX 2026-01-17] 保持指数退避策略，但仍然切换凭证
                if response.status_code in (503, 529):
                    if retry_enabled and credential_switch_count < max_credential_switches:
                        credential_switch_count += 1
                        current_cred_result = None
                        if next_cred_task is None:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    is_antigravity=True, model_key=model_name
                                )
                            )
                        # ✅ [FIX 2026-01-17] 使用指数退避而不是固定延迟
                        delay = retry_interval * (2 ** (credential_switch_count - 1))
                        log.warning(
                            f"[ANTIGRAVITY] {response.status_code} 触发热切换凭证重试 "
                            f"({credential_switch_count}/{max_credential_switches}), "
                            f"指数退避延迟 {delay:.1f}s"
                        )
                        # 清理流式资源
                        try:
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                        try:
                            await safe_close_client(client)
                        except Exception:
                            pass
                        await asyncio.sleep(delay)
                        continue

                # ✅ [FIX 2026-01-17] 错误码差异化处理：其他 5xx 错误用同一凭证重试
                # 原因：标准 5xx（500/502/504等）是服务端临时问题，切换凭证没有意义
                # 策略：指数退避重试，最多重试 3 次
                if response.status_code >= 500:
                    same_cred_retry_count += 1
                    if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                        # 使用指数退避：1s → 2s → 4s
                        delay = retry_interval * (2 ** (same_cred_retry_count - 1))
                        log.warning(
                            f"[ANTIGRAVITY] 5xx 服务端错误 ({response.status_code})，"
                            f"用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})，"
                            f"延迟 {delay:.1f}s"
                        )
                        # 清理流式资源
                        try:
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                        try:
                            await safe_close_client(client)
                        except Exception:
                            pass
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        log.warning(f"[ANTIGRAVITY] 5xx 服务端错误，已达到最大重试次数 ({max_same_cred_retries})，不再重试")
                        raise AntigravityUpstreamError(
                            status_code=response.status_code,
                            message=f"Antigravity API error ({response.status_code}): {error_body[:200]}",
                        )

                # 其他错误，直接抛出
                raise AntigravityUpstreamError(
                    status_code=response.status_code,
                    message=f"Antigravity API error ({response.status_code}): {error_body[:200]}",
                )

            finally:
                try:
                    if permit is not None:
                        permit.release()
                except Exception:
                    pass
                # 确保清理流式资源
                try:
                    if stream_ctx:
                        await stream_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    if client:
                        await safe_close_client(client)
                except Exception:
                    pass

        except (NonRetryableError, AntigravityUpstreamError):
            # 明确不可重试的上游/客户端错误，直接向上抛出，避免被误判为“网络错误”回声室重试
            raise
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            # 网络错误 - 用同一凭证重试
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            same_cred_retry_count += 1
            if retry_enabled and same_cred_retry_count <= max_same_cred_retries:
                log.warning(f"[ANTIGRAVITY] 网络错误，用同一凭证重试 ({same_cred_retry_count}/{max_same_cred_retries})")
                await asyncio.sleep(retry_interval)
                continue
            raise
        except Exception as e:
            # 未知错误：不要盲目重试，避免扩大上游压力
            log.error(f"[ANTIGRAVITY] Unexpected error (no retry): {e}")
            raise


async def fetch_available_models(
    credential_manager: CredentialManager,
) -> List[Dict[str, Any]]:
    """
    获取可用模型列表，返回符合 OpenAI API 规范的格式

    Returns:
        模型列表，格式为字典列表（用于兼容现有代码）
    """
    # 获取可用凭证
    cred_result = await credential_manager.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("[ANTIGRAVITY] No valid credentials available for fetching models")
        return []

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return []

    # 构建请求头
    headers = build_antigravity_headers(access_token, model_name="")

    try:
        # 使用 POST 请求获取模型列表（根据 buildAxiosConfig，method 是 POST）
        antigravity_url = await get_antigravity_api_url()

        # 使用上下文管理器确保正确的资源管理
        async with http_client.get_client(timeout=30.0) as client:
            await _throttle_antigravity_upstream()
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},  # 空的请求体
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY] Raw models response: {json.dumps(data, ensure_ascii=False)[:500]}")

                # 转换为 OpenAI 格式的模型列表，使用 Model 类
                model_list = []
                current_timestamp = int(datetime.now(timezone.utc).timestamp())

                if 'models' in data and isinstance(data['models'], dict):
                    # 遍历模型字典
                    for model_id in data['models'].keys():
                        model = Model(
                            id=model_id,
                            object='model',
                            created=current_timestamp,
                            owned_by='google'
                        )
                        model_list.append(model_to_dict(model))

                # 添加额外的 claude-opus-4-5 模型
                claude_opus_model = Model(
                    id='claude-opus-4-5',
                    object='model',
                    created=current_timestamp,
                    owned_by='google'
                )
                model_list.append(model_to_dict(claude_opus_model))

                log.info(f"[ANTIGRAVITY] Fetched {len(model_list)} available models")
                return model_list
            else:
                log.error(f"[ANTIGRAVITY] Failed to fetch models ({response.status_code}): {response.text[:500]}")
                return []

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY] Failed to fetch models: {e}")
        log.error(f"[ANTIGRAVITY] Traceback: {traceback.format_exc()}")
        return []


async def fetch_quota_info(
    access_token: str,
    *,
    cache_key: str | None = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    获取指定凭证的额度信息

    Args:
        access_token: Antigravity 访问令牌
        cache_key: 缓存键（可选，默认使用 access_token）
        force_refresh: 是否强制刷新缓存（忽略缓存有效期）

    Returns:
        包含额度信息的字典，格式为：
        {
            "success": True,
            "models": {
                "gemini-2.0-flash-exp": {
                    "remaining": 0.95,
                    "resetTime": "12-20 10:30",
                    "resetTimeRaw": "2025-12-20T02:30:00Z"
                }
            }
        }
    """

    headers = build_antigravity_headers(access_token, model_name="")
    key = cache_key or access_token
    ttl_seconds, failure_cooldown_seconds, stale_max_seconds = _get_quota_cache_settings()
    now = time.time()

    lock = _get_quota_cache_lock()
    async with lock:
        # [FIX 2026-01-17] 支持 force_refresh 参数，用于后台刷新时强制更新缓存
        if not force_refresh:
            expires_at = _quota_cache_expires_at.get(key, 0.0)
            if expires_at > now:
                cached = _quota_cache.get(key)
                if isinstance(cached, dict):
                    return cached

        fail_until = _quota_cache_fail_until.get(key, 0.0)
        if fail_until > now and not force_refresh:
            cached = _quota_cache.get(key)
            if isinstance(cached, dict):
                cached_expires = _quota_cache_expires_at.get(key, 0.0)
                if stale_max_seconds > 0 and (now - cached_expires) <= stale_max_seconds:
                    log.warning(f"[ANTIGRAVITY QUOTA] Fetch cooldown active, serving stale cache (key={key})")
                    return cached
            return {"success": False, "error": "quota_fetch_in_cooldown"}

    try:
        antigravity_url = await get_antigravity_api_url()

        async with http_client.get_client(timeout=30.0) as client:
            # [FIX 2026-01-22] Quota 查询限流 - 防止短时间大量请求导致 429
            await _throttle_quota_query()

            await _throttle_antigravity_upstream()
            permit = _AntigravityPermit()
            await permit.acquire()
            try:
                response = await client.post(
                    f"{antigravity_url}/v1internal:fetchAvailableModels",
                    json={},
                    headers=headers,
                )
            finally:
                permit.release()

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY QUOTA] Raw response: {json.dumps(data, ensure_ascii=False)[:500]}")

                quota_info = {}

                if 'models' in data and isinstance(data['models'], dict):
                    for model_id, model_data in data['models'].items():
                        if isinstance(model_data, dict) and 'quotaInfo' in model_data:
                            quota = model_data['quotaInfo']
                            remaining = quota.get('remainingFraction', 0)
                            reset_time_raw = quota.get('resetTime', '')

                            # 转换为北京时间
                            reset_time_beijing = 'N/A'
                            if reset_time_raw:
                                try:
                                    utc_date = datetime.fromisoformat(reset_time_raw.replace('Z', '+00:00'))
                                    # 转换为北京时间 (UTC+8)
                                    from datetime import timedelta
                                    beijing_date = utc_date + timedelta(hours=8)
                                    reset_time_beijing = beijing_date.strftime('%m-%d %H:%M')
                                except Exception as e:
                                    log.warning(f"[ANTIGRAVITY QUOTA] Failed to parse reset time: {e}")

                            quota_info[model_id] = {
                                "remaining": remaining,
                                "resetTime": reset_time_beijing,
                                "resetTimeRaw": reset_time_raw
                            }

                result = {
                    "success": True,
                    "models": quota_info
                }
                async with lock:
                    _quota_cache[key] = result
                    _quota_cache_expires_at[key] = time.time() + ttl_seconds
                    _quota_cache_fail_until.pop(key, None)
                return result
            else:
                log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota ({response.status_code}): {response.text[:500]}")
                async with lock:
                    _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
                return {
                    "success": False,
                    "error": f"API返回错误: {response.status_code}"
                }

    except httpx.ReadError as e:
        # ✅ [FIX 2026-01-17] 网络读取错误：连接中断或服务器关闭连接
        # 这是临时性网络问题，不应该打印完整 traceback
        log.warning(f"[ANTIGRAVITY QUOTA] Network read error (connection interrupted): {e}")
        async with lock:
            _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
        return {
            "success": False,
            "error": "network_read_error",
            "message": "Connection interrupted during quota fetch"
        }
    except httpx.ConnectError as e:
        # ✅ [FIX 2026-01-17] 连接错误：无法连接到服务器
        log.warning(f"[ANTIGRAVITY QUOTA] Connection error: {e}")
        async with lock:
            _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
        return {
            "success": False,
            "error": "connection_error",
            "message": "Failed to connect to quota API"
        }
    except httpx.TimeoutException as e:
        # ✅ [FIX 2026-01-17] 超时错误：请求超时
        log.warning(f"[ANTIGRAVITY QUOTA] Request timeout: {e}")
        async with lock:
            _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
        return {
            "success": False,
            "error": "timeout",
            "message": "Quota fetch request timed out"
        }
    except httpx.RequestError as e:
        # ✅ [FIX 2026-01-17] 其他 httpx 请求错误
        log.warning(f"[ANTIGRAVITY QUOTA] Request error: {e}")
        async with lock:
            _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
        return {
            "success": False,
            "error": "request_error",
            "message": str(e)
        }
    except Exception as e:
        # ✅ [FIX 2026-01-17] 其他未知错误：打印完整 traceback 用于调试
        import traceback
        log.error(f"[ANTIGRAVITY QUOTA] Unexpected error: {e}")
        log.error(f"[ANTIGRAVITY QUOTA] Traceback: {traceback.format_exc()}")
        async with lock:
            _quota_cache_fail_until[key] = time.time() + failure_cooldown_seconds
        return {
            "success": False,
            "error": "unknown_error",
            "message": str(e)
        }
