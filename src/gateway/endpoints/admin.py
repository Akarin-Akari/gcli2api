"""
Gateway 管理端点

包含健康检查、后端配置等管理 API 端点。

从 unified_gateway_router.py 抽取的管理端点。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

import time
import secrets
from typing import Dict, Any
from fastapi import APIRouter, Request, Depends, HTTPException

# 延迟导入 log，避免循环依赖
try:
    from log import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# 延迟导入认证依赖
try:
    from src.auth import authenticate_bearer, authenticate_bearer_allow_local_dummy
except ImportError:
    # 提供默认的认证函数
    async def authenticate_bearer():
        return "dummy"
    async def authenticate_bearer_allow_local_dummy():
        return "dummy"

# 延迟导入配置和路由函数
try:
    from ..config import BACKENDS
    from ..routing import check_backend_health
except ImportError:
    BACKENDS = {}
    async def check_backend_health(backend_key: str) -> bool:
        return False

router = APIRouter()

__all__ = ["router"]


# ==================== 健康检查端点 ====================

@router.get("/health")
async def gateway_health():
    """网关健康检查 - 返回所有后端状态"""
    backend_status = {}

    for backend_key, backend_config in BACKENDS.items():
        is_healthy = await check_backend_health(backend_key)
        backend_status[backend_key] = {
            "name": backend_config.get("name", backend_key),
            "url": backend_config.get("base_url", ""),
            "priority": backend_config.get("priority", 999),
            "enabled": backend_config.get("enabled", True),
            "healthy": is_healthy,
        }

    all_healthy = any(s["healthy"] for s in backend_status.values()) if backend_status else False

    return {
        "status": "healthy" if all_healthy else "degraded",
        "backends": backend_status,
        "timestamp": time.time(),
    }


# ==================== 后端配置端点 ====================

@router.post("/config/backend/{backend_key}/toggle")
async def toggle_backend(
    backend_key: str,
    token: str = Depends(authenticate_bearer)
):
    """启用/禁用指定后端"""
    if backend_key not in BACKENDS:
        raise HTTPException(status_code=404, detail=f"Backend {backend_key} not found")

    BACKENDS[backend_key]["enabled"] = not BACKENDS[backend_key].get("enabled", True)

    return {
        "backend": backend_key,
        "enabled": BACKENDS[backend_key]["enabled"],
    }


# ==================== Augment Code 兼容端点 ====================

@router.get("/usage/api/balance")
async def get_balance(request: Request):
    """Augment Code 兼容路由：获取账户余额信息"""
    log.debug(f"Balance request received from {request.url.path}", tag="GATEWAY")

    # 尝试从凭证管理器获取用户信息（如果有的话）
    user_email = "用户"
    try:
        from web import get_credential_manager
        cred_mgr = get_credential_manager()
        if cred_mgr:
            # 获取当前使用的凭证信息
            cred_result = await cred_mgr.get_valid_credential()
            if cred_result:
                _, credential_data = cred_result
                # 尝试从凭证数据中提取用户信息
                user_email = credential_data.get("email") or credential_data.get("user_email") or "用户"
    except Exception as e:
        log.warning(f"Failed to get credential info for balance: {e}")

    # 返回余额信息（模拟数据，实际应该从数据库或配置中读取）
    # 如果系统有实际的余额系统，应该从这里查询真实余额
    balance_data = {
        "success": True,
        "data": {
            "balance": 100.00,  # 默认余额，实际应该从配置或数据库读取
            "name": user_email.split("@")[0] if "@" in user_email else user_email,  # 从邮箱提取用户名
            "plan_name": "标准套餐",  # 默认套餐名称
            "end_date": "2025-12-31"  # 默认到期日期
        }
    }

    log.debug(f"Returning balance info: {balance_data}", tag="GATEWAY")
    return balance_data


@router.get("/usage/api/getLoginToken")
async def get_login_token(request: Request):
    """Augment Code 兼容路由：获取登录令牌"""
    log.debug(f"Login token request received from {request.url.path}", tag="GATEWAY")

    # 生成一个简单的令牌（实际应该基于认证机制生成）
    # 这里生成一个基于时间戳的令牌，确保每次请求都不同
    token = secrets.token_urlsafe(32)
    timestamp = int(time.time())

    # 返回令牌信息
    token_data = {
        "success": True,
        "data": {
            "token": token,
            "expires_in": 3600,  # 1小时过期
            "token_type": "Bearer",
            "timestamp": timestamp
        }
    }

    log.debug(f"Returning login token: {token_data}", tag="GATEWAY")
    return token_data
