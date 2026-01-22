"""
Fallback Manager - 智能模型降级管理器
处理额度用尽时的跨池降级和 Copilot 路由

额度池划分：
- Claude/第三方池：Claude 系列 + GPT 系列 + 其他非 Google 模型
- Gemini 池：Google 自家的 Gemini 系列

降级策略：
- Claude/第三方模型额度用完 → 切换到 Gemini 池
- Gemini 模型额度用完 → 切换到 Claude/第三方池
- 两个池都用完 或 模型不存在 → 路由到 Copilot
- 特例：haiku 模型 → Antigravity 不支持，直接走 Kiro -> Copilot，全部失败后用 gemini-3-flash
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Union
from log import log


# ====================== 模型池定义 ======================

# Gemini 池（Google 自家模型）
GEMINI_POOL = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-thinking",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-image",
    "gemini-2.5-pro",
    "gemini-3-flash",
    "gemini-3-pro-low",
    "gemini-3-pro-high",
    "gemini-3-pro-image",
}

# Claude/第三方池（非 Google 模型）
CLAUDE_THIRD_PARTY_POOL = {
    "claude-sonnet-4-5",
    "claude-sonnet-4-5-thinking",
    "claude-opus-4-5-thinking",
    "claude-opus-4-5",  # 可能被映射
    "gpt-oss-120b-medium",
    # 内部测试模型
    "rev19-uic3-1p",
    "chat_20706",
    "chat_23310",
}

# 所有支持的模型
ALL_SUPPORTED_MODELS = GEMINI_POOL | CLAUDE_THIRD_PARTY_POOL

# Haiku 模型（特殊处理，不走 Copilot）
HAIKU_MODELS = {
    "claude-haiku-4-5",
    "claude-haiku-4.5",
    "haiku-4.5",
    "haiku-4-5",
    "claude-3-haiku",
    "claude-3-haiku-20240307",
}


# ====================== 跨池降级映射 ======================

# Claude/第三方池 → Gemini 池的降级映射
# [FIX 2026-01-19] 优先使用 Gemini 3 Pro，Gemini 2.5 优先级更低
CLAUDE_TO_GEMINI_FALLBACK = {
    "claude-opus-4-5-thinking": "gemini-3-pro-high",
    "claude-opus-4-5": "gemini-3-pro-high",
    "claude-sonnet-4-5-thinking": "gemini-3-pro-high",  # 改为 Gemini 3 Pro
    "claude-sonnet-4-5": "gemini-3-pro-high",  # 改为 Gemini 3 Pro
    "gpt-oss-120b-medium": "gemini-3-pro-high",
    # 默认降级目标 - 使用 Gemini 3 Pro（优先级高于 Gemini 2.5）
    "_default": "gemini-3-pro-high",
}

# Gemini 池 → Claude/第三方池的降级映射
GEMINI_TO_CLAUDE_FALLBACK = {
    "gemini-3-pro-high": "claude-opus-4-5-thinking",
    "gemini-3-pro-low": "claude-sonnet-4-5",
    "gemini-3-pro-image": "claude-sonnet-4-5",
    "gemini-2.5-pro": "claude-sonnet-4-5",
    "gemini-2.5-flash": "claude-sonnet-4-5",
    "gemini-2.5-flash-thinking": "claude-sonnet-4-5-thinking",
    "gemini-2.5-flash-lite": "claude-sonnet-4-5",
    "gemini-2.5-flash-image": "claude-sonnet-4-5",
    "gemini-3-flash": "claude-sonnet-4-5",
    # 默认降级目标
    "_default": "claude-sonnet-4-5",
}

# Haiku 模型的降级策略
# Antigravity 不支持 Haiku，所以 Haiku 直接走 Kiro -> Copilot 链路
# 只有 Kiro 和 Copilot 都失败后，才最终降级到 Gemini 3 Flash
# 注意：HAIKU_FALLBACK_TARGET 设为 None 表示保持原模型名，只切换后端
HAIKU_FALLBACK_TARGET = None  # 保持 haiku 模型名，走 Kiro/Copilot
HAIKU_FINAL_FALLBACK = "gemini-3-flash"  # 最终兜底

# Copilot API 地址
COPILOT_URL = "http://localhost:4141/"


# ====================== 额度用尽检测关键词 ======================

QUOTA_EXHAUSTED_KEYWORDS = [
    'model_capacity_exhausted',   # ✅ API 明确返回的容量耗尽
    'quota exhausted',            # ✅ 明确的配额用尽
    'account quota',              # ✅ 账户配额相关
    'billing quota',              # ✅ 计费配额相关
    'no capacity available',      # ✅ 无可用容量
    'all credentials exhausted',  # ✅ [FIX 2026-01-08] 凭证切换用尽
    'quota_exhausted',            # ✅ [FIX 2026-01-08] 显式标记的配额用尽
    # ❌ 移除这些过于宽泛的关键词：
    # 'quota exceeded',           # 太宽泛
    # 'limit exceeded',           # 太宽泛，可能是速率限制
    # 'capacity exhausted',       # 太宽泛
    # 'rate limit exceeded',      # 这是速率限制，不是配额用尽！
    # 'resource_exhausted',       # 太宽泛，"Resource has been exhausted"只是临时限流！
]

# 这些关键词表示临时的速率限制，应该重试
RATE_LIMIT_KEYWORDS = [
    'rate limit',
    'too many requests',
    'resource has been exhausted',  # ← 这个是临时限制！
    'retry after',
]


# ====================== 错误类型判断 ======================

def get_status_code_from_error(error_msg: str) -> Optional[int]:
    """从错误消息中提取状态码"""
    match = re.search(r'\((\d{3})\)', str(error_msg))
    if match:
        return int(match.group(1))
    return None


def is_quota_exhausted_error(error_msg: str) -> bool:
    """
    判断是否是真正的额度用尽错误（不是临时的速率限制）
    
    🐛 优化逻辑：
    1. 先排除临时速率限制（这些应该重试）
    2. 再检查真正的配额用尽关键词
    
    Returns:
        True: 真正的配额用尽，不应该重试
        False: 临时限制或其他错误，可以重试
    """
    error_str = str(error_msg)
    error_lower = error_str.lower()

    status_code = get_status_code_from_error(error_str)

    # 如果不是 429 错误，肯定不是配额问题
    if status_code is not None and status_code != 429:
        return False

    # 🐛 优化：先检查是否是临时速率限制（这些不应该判为配额用尽）
    for rate_keyword in RATE_LIMIT_KEYWORDS:
        if rate_keyword in error_lower:
            # "Resource has been exhausted" 是临时限制，不是真正的配额用尽
            return False

    # 429 + 配额用尽关键词 = 真正的配额用尽
    if status_code == 429:
        for keyword in QUOTA_EXHAUSTED_KEYWORDS:
            if keyword in error_lower:
                return True

    # 没有状态码时，检查关键词
    if status_code is None:
        for keyword in QUOTA_EXHAUSTED_KEYWORDS:
            if keyword in error_lower:
                return True

    return False


def is_retryable_error(error_msg: str) -> bool:
    """
    判断是否是可重试的错误

    可重试的错误：
    - 429 普通限流（非额度用尽）
    - 5xx 错误（服务端临时问题）

    不可重试的错误：
    - 400 错误（客户端参数错误，重试没有意义，只会浪费 token）
    """
    error_str = str(error_msg)
    status_code = get_status_code_from_error(error_str)

    if status_code is None:
        return False

    # 400 错误 - 不重试（客户端参数错误，重试没有意义）
    if status_code == 400:
        log.debug(f"[FALLBACK] 400 客户端错误，不可重试")
        return False

    # 429 但不是额度用尽 - 重试
    if status_code == 429 and not is_quota_exhausted_error(error_str):
        log.debug(f"[FALLBACK] 429 普通限流，标记为可重试")
        return True

    # 5xx 错误 - 重试
    if status_code >= 500:
        log.debug(f"[FALLBACK] 5xx 错误 ({status_code})，标记为可重试")
        return True

    return False


def is_403_error(error_msg: str) -> bool:
    """判断是否是 403 错误（需要验证）"""
    status_code = get_status_code_from_error(str(error_msg))
    return status_code == 403


def is_credential_unavailable_error(error_msg: str) -> bool:
    """
    判断是否是凭证不可用错误

    这类错误表示模型的凭证池已耗尽（所有凭证都在冷却中），
    应该触发 Gateway 层的 fallback 到 Copilot
    """
    error_lower = str(error_msg).lower()

    # 检测凭证不可用的关键词
    credential_unavailable_keywords = [
        'no valid antigravity credentials',
        'no valid credentials',
        'credentials unavailable',
        'credential pool exhausted',
    ]

    for keyword in credential_unavailable_keywords:
        if keyword in error_lower:
            return True

    return False


# ====================== 跨池熔断机制 ======================
# [FIX 2026-01-21] 池级别熔断器
# 当某个池的凭证连续失败超过阈值时，开启熔断，快速切换到另一个池

import time

class PoolCircuitBreaker:
    """
    池级别熔断器

    功能：
    - 跟踪每个池（Gemini/Claude）的连续失败次数
    - 当连续失败超过阈值时，开启熔断
    - 熔断期间跳过该池，直接使用另一个池
    - 熔断超时后自动恢复（半开状态）
    """

    def __init__(
        self,
        failure_threshold: int = 5,  # 连续失败多少次开启熔断
        recovery_timeout: float = 300.0,  # 熔断恢复超时（秒）
        half_open_max_tries: int = 1,  # 半开状态最多尝试次数
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_tries = half_open_max_tries

        # 池状态: {pool_name: {"failures": int, "open_time": float, "state": str}}
        # state: "closed" (正常), "open" (熔断), "half_open" (半开)
        self._pool_states: Dict[str, Dict] = {}

    def _get_or_create(self, pool_name: str) -> Dict:
        """获取或创建池状态"""
        if pool_name not in self._pool_states:
            self._pool_states[pool_name] = {
                "failures": 0,
                "open_time": 0.0,
                "state": "closed",
                "half_open_tries": 0,
            }
        return self._pool_states[pool_name]

    def record_success(self, pool_name: str) -> None:
        """记录池请求成功"""
        state = self._get_or_create(pool_name)
        state["failures"] = 0
        state["state"] = "closed"
        state["half_open_tries"] = 0
        log.debug(f"[POOL CIRCUIT] {pool_name} 成功，熔断器关闭")

    def record_failure(self, pool_name: str) -> None:
        """记录池请求失败"""
        state = self._get_or_create(pool_name)
        state["failures"] += 1

        if state["state"] == "half_open":
            # 半开状态下失败，重新开启熔断
            state["state"] = "open"
            state["open_time"] = time.time()
            log.warning(f"[POOL CIRCUIT] {pool_name} 半开状态失败，重新开启熔断")
        elif state["failures"] >= self.failure_threshold and state["state"] == "closed":
            # 达到阈值，开启熔断
            state["state"] = "open"
            state["open_time"] = time.time()
            log.warning(
                f"[POOL CIRCUIT] {pool_name} 连续失败 {state['failures']} 次，开启熔断 "
                f"(恢复超时={self.recovery_timeout}s)"
            )

    def is_open(self, pool_name: str) -> bool:
        """检查池熔断器是否开启"""
        state = self._get_or_create(pool_name)

        if state["state"] == "closed":
            return False

        if state["state"] == "open":
            # 检查是否超时，进入半开状态
            if time.time() - state["open_time"] >= self.recovery_timeout:
                state["state"] = "half_open"
                state["half_open_tries"] = 0
                log.info(f"[POOL CIRCUIT] {pool_name} 熔断超时，进入半开状态")
                return False
            return True

        if state["state"] == "half_open":
            # 半开状态，允许有限次数的尝试
            if state["half_open_tries"] < self.half_open_max_tries:
                state["half_open_tries"] += 1
                return False
            return True

        return False

    def get_state(self, pool_name: str) -> str:
        """获取池熔断器状态"""
        state = self._get_or_create(pool_name)
        return state["state"]

    def reset(self, pool_name: str) -> None:
        """重置池熔断器"""
        if pool_name in self._pool_states:
            self._pool_states[pool_name] = {
                "failures": 0,
                "open_time": 0.0,
                "state": "closed",
                "half_open_tries": 0,
            }
            log.info(f"[POOL CIRCUIT] {pool_name} 熔断器已重置")


# 全局池熔断器实例
_pool_circuit_breaker = PoolCircuitBreaker()


def get_pool_circuit_breaker() -> PoolCircuitBreaker:
    """获取池熔断器实例"""
    return _pool_circuit_breaker


def is_pool_circuit_open(pool_name: str) -> bool:
    """检查指定池的熔断器是否开启"""
    return _pool_circuit_breaker.is_open(pool_name)


def record_pool_success(pool_name: str) -> None:
    """记录池请求成功"""
    _pool_circuit_breaker.record_success(pool_name)


def record_pool_failure(pool_name: str) -> None:
    """记录池请求失败"""
    _pool_circuit_breaker.record_failure(pool_name)


# ====================== 模型池判断 ======================

def get_model_pool(model_name: str) -> str:
    """
    获取模型所属的池

    Returns:
        "gemini" | "claude" | "unknown"
    """
    if model_name is None:
        return "unknown"
    
    # 标准化模型名
    model_lower = model_name.lower()

    # 检查是否在 Gemini 池
    if model_name in GEMINI_POOL:
        return "gemini"

    # 模糊匹配 Gemini
    if "gemini" in model_lower:
        return "gemini"

    # 检查是否在 Claude/第三方池
    if model_name in CLAUDE_THIRD_PARTY_POOL:
        return "claude"

    # 模糊匹配 Claude/GPT
    if "claude" in model_lower or "gpt" in model_lower:
        return "claude"

    return "unknown"


def is_haiku_model(model_name: str) -> bool:
    """判断是否是 Haiku 模型"""
    if model_name is None:
        return False
    
    model_lower = model_name.lower()

    if model_name in HAIKU_MODELS:
        return True

    if "haiku" in model_lower:
        return True

    return False


def is_opus_model(model_name: str) -> bool:
    """
    判断是否是 Opus 模型

    [FIX 2026-01-21] Opus 模型需要特殊的降级策略：
    - 不在 Antigravity 层自动跨池降级到 Gemini
    - 必须先尝试所有后端: AG (所有凭证) -> Kiro -> AnyRouter -> Copilot
    - 只有所有后端都失败后，才在 Gateway 层进行跨模型降级
    """
    if model_name is None:
        return False
    
    model_lower = model_name.lower()
    return "opus" in model_lower


def is_model_supported(model_name: str) -> bool:
    """判断模型是否在 Antigravity 支持列表中"""
    if model_name is None:
        return False
    
    if model_name in ALL_SUPPORTED_MODELS:
        return True

    # 模糊匹配
    model_lower = model_name.lower()
    for supported in ALL_SUPPORTED_MODELS:
        if supported.lower() in model_lower or model_lower in supported.lower():
            return True

    return False


# ====================== 降级目标获取 ======================

def get_cross_pool_fallback(model_name: str, log_level: str = "debug") -> Optional[str]:
    """
    获取跨池降级目标

    Args:
        model_name: 当前模型名
        log_level: 日志级别，"debug" 用于预计算，"info" 用于实际降级时

    Returns:
        降级目标模型名，如果无法降级则返回 None
    """
    if model_name is None:
        return None
    
    pool = get_model_pool(model_name)

    # 根据 log_level 选择日志函数
    # debug 级别用于预计算，fallback 级别用于实际降级
    log_func = log.debug if log_level == "debug" else log.fallback

    if pool == "claude":
        # Claude/第三方池 → Gemini 池
        fallback = CLAUDE_TO_GEMINI_FALLBACK.get(model_name)
        if fallback:
            log_func(f"Claude池 -> Gemini池: {model_name} -> {fallback}")
            return fallback
        # 使用默认降级
        default_fallback = CLAUDE_TO_GEMINI_FALLBACK.get("_default")
        log_func(f"Claude池 -> Gemini池 (默认): {model_name} -> {default_fallback}")
        return default_fallback

    elif pool == "gemini":
        # Gemini 池 → Claude/第三方池
        fallback = GEMINI_TO_CLAUDE_FALLBACK.get(model_name)
        if fallback:
            log_func(f"Gemini池 -> Claude池: {model_name} -> {fallback}")
            return fallback
        # 使用默认降级
        default_fallback = GEMINI_TO_CLAUDE_FALLBACK.get("_default")
        log_func(f"Gemini池 -> Claude池 (默认): {model_name} -> {default_fallback}")
        return default_fallback

    else:
        # 未知池，尝试降级到 Gemini
        log.warning(f"未知模型池: {model_name}，尝试降级到 Gemini")
        return CLAUDE_TO_GEMINI_FALLBACK.get("_default")


def should_route_to_copilot(model_name: str, both_pools_exhausted: bool = False, all_backends_failed: bool = False) -> Tuple[bool, Optional[str]]:
    """
    判断是否应该路由到 Copilot

    Args:
        model_name: 模型名
        both_pools_exhausted: 两个池是否都用完了
        all_backends_failed: 所有后端是否都失败了（用于 Haiku 最终降级）

    Returns:
        (should_route, alternative_model)
        - should_route: 是否应该路由到 Copilot
        - alternative_model: 如果不路由到 Copilot，使用的替代模型
    """
    # Haiku 模型特殊处理
    # Antigravity 不支持 Haiku，所以直接走 Kiro -> Copilot 链路
    # 只有所有后端都失败后，才最终降级到 gemini-3-flash
    if is_haiku_model(model_name):
        if all_backends_failed:
            # 所有后端都失败了，使用最终兜底 gemini-3-flash
            log.fallback(f"Haiku {model_name} -> {HAIKU_FINAL_FALLBACK} (所有后端失败，最终兜底)")
            return False, HAIKU_FINAL_FALLBACK
        else:
            # 保持 haiku 模型名，让 Gateway 路由到 Kiro/Copilot
            # 返回 True 表示应该路由到其他后端（Kiro/Copilot 都支持 haiku）
            log.fallback(f"Haiku {model_name} -> Kiro/Copilot (Antigravity 不支持)")
            return True, None

    # 模型不在支持列表中
    if not is_model_supported(model_name):
        log.fallback(f"模型 {model_name} -> Copilot (不在AG列表)")
        return True, None

    # 两个池都用完了
    if both_pools_exhausted:
        log.fallback(f"两个额度池都用完 -> Copilot")
        return True, None

    return False, None


# ====================== 额度检查 ======================

async def check_pool_quota(
    credential_manager,
    pool: str,
) -> Tuple[bool, Dict[str, float]]:
    """
    检查指定池的额度状态

    Args:
        credential_manager: 凭证管理器
        pool: "gemini" 或 "claude"

    Returns:
        (has_quota, quota_info)
        - has_quota: 池中是否还有可用额度
        - quota_info: 各模型的额度信息 {model: remaining_fraction}
    """
    try:
        # 获取凭证
        cred_result = await credential_manager.get_valid_credential(is_antigravity=True)
        if not cred_result:
            log.warning(f"[FALLBACK] 无法获取凭证来检查额度")
            return True, {}  # 无法检查，假设有额度

        _, credential_data = cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.warning(f"[FALLBACK] 凭证中没有 access_token")
            return True, {}

        # 获取额度信息
        from .antigravity_api import fetch_quota_info
        quota_result = await fetch_quota_info(access_token)

        if not quota_result.get("success"):
            log.warning(f"[FALLBACK] 获取额度信息失败: {quota_result.get('error')}")
            return True, {}  # 无法检查，假设有额度

        models_quota = quota_result.get("models", {})

        # 根据池筛选模型
        pool_models = GEMINI_POOL if pool == "gemini" else CLAUDE_THIRD_PARTY_POOL

        quota_info = {}
        has_quota = False

        for model in pool_models:
            if model in models_quota:
                remaining = models_quota[model].get("remaining", 0)
                quota_info[model] = remaining
                if remaining > 0.01:  # 大于 1% 认为有额度
                    has_quota = True

        log.info(f"[FALLBACK] {pool}池额度检查: has_quota={has_quota}, models={list(quota_info.keys())}")
        return has_quota, quota_info

    except Exception as e:
        log.error(f"[FALLBACK] 检查额度时出错: {e}")
        return True, {}  # 出错时假设有额度


# ====================== 403 验证请求 ======================

async def trigger_credential_verification(
    credential_name: str,
    is_antigravity: bool = True,
) -> bool:
    """
    触发凭证验证请求（用于 403 错误恢复）

    Args:
        credential_name: 凭证文件名
        is_antigravity: 是否是 Antigravity 凭证

    Returns:
        验证是否成功
    """
    try:
        import httpx

        # 构建验证请求 URL
        if is_antigravity:
            url = f"http://127.0.0.1:7861/antigravity/creds/verify-project/{credential_name}"
        else:
            url = f"http://127.0.0.1:7861/creds/verify-project/{credential_name}"

        log.info(f"[FALLBACK] 触发凭证验证: {url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url)

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    log.info(f"[FALLBACK] 凭证验证成功: {credential_name}")
                    return True
                else:
                    log.warning(f"[FALLBACK] 凭证验证失败: {result.get('message')}")
                    return False
            else:
                log.warning(f"[FALLBACK] 凭证验证请求失败: {response.status_code}")
                return False

    except Exception as e:
        log.error(f"[FALLBACK] 凭证验证出错: {e}")
        return False


# ====================== 主要降级决策函数 ======================

class FallbackDecision:
    """降级决策结果"""

    def __init__(
        self,
        action: str,  # "retry" | "fallback" | "copilot" | "verify" | "fail"
        target_model: Optional[str] = None,
        copilot_url: Optional[str] = None,
        message: str = "",
        fallback_history: Optional[List[str]] = None,  # ✅ [FIX 2026-01-22] 降级历史跟踪
    ):
        self.action = action
        self.target_model = target_model
        self.copilot_url = copilot_url
        self.message = message
        self.fallback_history = fallback_history or []  # ✅ [FIX 2026-01-22] 降级历史跟踪

    def __repr__(self):
        history_str = " -> ".join(self.fallback_history) if self.fallback_history else ""
        return f"FallbackDecision(action={self.action}, target={self.target_model}, msg={self.message}, history=[{history_str}])"


async def decide_fallback_action(
    error_msg: str,
    current_model: str,
    credential_name: Optional[str] = None,
    credential_manager = None,
    already_tried_fallback: Union[bool, List[str]] = False,  # ✅ [FIX 2026-01-22] 支持降级历史列表
    copilot_url: str = "http://localhost:4141/",
) -> FallbackDecision:
    """
    根据错误类型和当前状态决定降级动作

    Args:
        error_msg: 错误消息
        current_model: 当前使用的模型
        credential_name: 凭证文件名（用于 403 验证）
        credential_manager: 凭证管理器（用于检查额度）
        already_tried_fallback: 是否已经尝试过降级
        copilot_url: Copilot API 地址

    Returns:
        FallbackDecision 对象
    """
    error_str = str(error_msg)
    status_code = get_status_code_from_error(error_str)
    
    # ✅ [FIX 2026-01-22] 处理降级历史（兼容 bool 和 List）
    fallback_history: List[str] = []
    if isinstance(already_tried_fallback, list):
        fallback_history = already_tried_fallback
    elif already_tried_fallback:
        # 如果已经是 bool=True，转换为列表（向后兼容）
        fallback_history = []

    log.info(f"[FALLBACK] 分析错误: status={status_code}, model={current_model}, tried_fallback={already_tried_fallback}, history={fallback_history}")

    # 1. 403 错误 - 触发验证
    if is_403_error(error_str):
        log.info(f"[FALLBACK] 检测到 403 错误，需要触发凭证验证")
        return FallbackDecision(
            action="verify",
            message="403 错误，需要验证凭证"
        )

    # 2. 可重试错误（400, 普通429, 5xx）
    if is_retryable_error(error_str):
        log.info(f"[FALLBACK] 检测到可重试错误 (status={status_code})")
        return FallbackDecision(
            action="retry",
            message=f"状态码 {status_code}，建议重试"
        )

    # 3. 额度用尽错误 - 跨池降级
    if is_quota_exhausted_error(error_str):
        log.info(f"[FALLBACK] 检测到额度用尽错误")

        # 检查是否是 Haiku 模型
        should_copilot, alt_model = should_route_to_copilot(current_model, both_pools_exhausted=False)
        if alt_model:
            # Haiku 特殊处理
            return FallbackDecision(
                action="fallback",
                target_model=alt_model,
                message=f"Haiku 模型降级到 {alt_model}"
            )

        # ✅ [FIX 2026-01-22] 检查是否形成循环降级
        # 如果当前模型已在降级历史中，说明形成了循环
        if fallback_history and current_model in fallback_history:
            log.error(
                f"[FALLBACK] 检测到循环降级: {current_model} 已在降级历史中 "
                f"(history: {fallback_history})"
            )
            return FallbackDecision(
                action="copilot",
                copilot_url=copilot_url,
                message="检测到循环降级，路由到 Copilot",
                fallback_history=fallback_history + [current_model]
            )
        
        # 已经尝试过降级，检查是否两个池都用完了
        if already_tried_fallback or fallback_history:
            # 检查另一个池的额度
            current_pool = get_model_pool(current_model)
            other_pool = "claude" if current_pool == "gemini" else "gemini"

            if credential_manager:
                has_quota, _ = await check_pool_quota(credential_manager, other_pool)
                if not has_quota:
                    log.warning(f"[FALLBACK] 两个池都用完了，路由到 Copilot")
                    return FallbackDecision(
                        action="copilot",
                        copilot_url=copilot_url,
                        message="两个额度池都用完，路由到 Copilot",
                        fallback_history=fallback_history + [current_model]
                    )

        # ✅ [FIX 2026-01-22] 检查是否形成循环降级
        # 如果当前模型已在降级历史中，说明形成了循环
        if fallback_history and current_model in fallback_history:
            log.error(
                f"[FALLBACK] 检测到循环降级: {current_model} 已在降级历史中 "
                f"(history: {fallback_history})"
            )
            return FallbackDecision(
                action="copilot",
                copilot_url=copilot_url,
                message="检测到循环降级，路由到 Copilot",
                fallback_history=fallback_history + [current_model]
            )
        
        # 获取跨池降级目标
        fallback_model = get_cross_pool_fallback(current_model)
        if fallback_model:
            # ✅ [FIX 2026-01-22] 更新降级历史
            new_history = fallback_history.copy()
            new_history.append(current_model)
            
            return FallbackDecision(
                action="fallback",
                target_model=fallback_model,
                message=f"额度用尽，跨池降级到 {fallback_model}",
                fallback_history=new_history
            )
        else:
            # 无法降级，路由到 Copilot
            return FallbackDecision(
                action="copilot",
                copilot_url=copilot_url,
                message="无法找到降级目标，路由到 Copilot"
            )

    # 4. 模型不支持 - 路由到 Copilot
    if not is_model_supported(current_model):
        should_copilot, alt_model = should_route_to_copilot(current_model)
        if should_copilot:
            return FallbackDecision(
                action="copilot",
                copilot_url=copilot_url,
                message=f"模型 {current_model} 不支持，路由到 Copilot"
            )
        elif alt_model:
            return FallbackDecision(
                action="fallback",
                target_model=alt_model,
                message=f"模型 {current_model} 降级到 {alt_model}"
            )

    # 5. 其他错误 - 失败
    return FallbackDecision(
        action="fail",
        message=f"无法处理的错误: {error_str[:200]}"
    )
