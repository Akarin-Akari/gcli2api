"""
TLS 指纹伪装模块 v1.0

使用 curl_cffi 模拟真实客户端的 TLS 指纹，避免被识别为 Python 自动化工具。
支持优雅降级：如果 curl_cffi 不可用，回退到原生 httpx。

特性:
- 模拟 Chrome/Safari/Edge/Firefox 等浏览器的 TLS 指纹
- 支持异步请求 (AsyncSession)
- 自动检测 curl_cffi 可用性
- 环境变量配置

环境变量:
- TLS_IMPERSONATE_ENABLED: 是否启用 TLS 伪装 (默认: true)
- TLS_IMPERSONATE_TARGET: 伪装目标 (默认: chrome131)

作者: 浮浮酱 (Claude Opus 4.5)
日期: 2026-01-21
"""

import os
import random
from typing import Optional, Dict, Any, List
from log import log

# ====================== curl_cffi 可用性检测 ======================

# 尝试导入 curl_cffi
try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    from curl_cffi.requests import Session as CurlSession
    CURL_CFFI_AVAILABLE = True
    log.info("[TLS] curl_cffi 库已加载")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    CurlAsyncSession = None
    CurlSession = None
    log.warning("[TLS] curl_cffi 库未安装，TLS 伪装功能不可用")


# ====================== 配置 ======================

def _get_env_bool(key: str, default: bool = True) -> bool:
    """获取布尔类型环境变量"""
    value = os.getenv(key, str(default)).lower()
    return value in ("true", "1", "yes", "on")


def _get_env_str(key: str, default: str) -> str:
    """获取字符串类型环境变量"""
    return os.getenv(key, default)


# 配置项
TLS_IMPERSONATE_ENABLED = _get_env_bool("TLS_IMPERSONATE_ENABLED", True)
TLS_IMPERSONATE_TARGET = _get_env_str("TLS_IMPERSONATE_TARGET", "chrome131")

# 支持的伪装目标列表 (curl_cffi 支持的浏览器指纹)
# 参考: https://github.com/yifeikong/curl_cffi
SUPPORTED_IMPERSONATE_TARGETS = [
    # Chrome 系列
    "chrome99", "chrome100", "chrome101", "chrome104", "chrome107",
    "chrome110", "chrome116", "chrome119", "chrome120", "chrome123",
    "chrome124", "chrome126", "chrome127", "chrome128", "chrome129",
    "chrome130", "chrome131",
    # Chrome Android
    "chrome99_android", "chrome131_android",
    # Safari 系列
    "safari15_3", "safari15_5", "safari17_0", "safari17_2_ios",
    "safari18_0", "safari18_0_ios",
    # Edge
    "edge99", "edge101",
    # Firefox (实验性)
    "firefox",
]

# 随机化伪装目标池 (用于更高级的反检测)
RANDOMIZE_TARGETS = [
    "chrome131", "chrome130", "chrome129", "chrome128",
    "safari18_0", "edge101",
]


# ====================== 公共 API ======================

def is_tls_impersonate_available() -> bool:
    """
    检查 TLS 伪装是否可用

    Returns:
        True 如果 curl_cffi 已安装且 TLS 伪装已启用
    """
    return CURL_CFFI_AVAILABLE and TLS_IMPERSONATE_ENABLED


def get_impersonate_target(randomize: bool = False) -> str:
    """
    获取伪装目标

    Args:
        randomize: 是否随机选择目标（用于更高级的反检测）

    Returns:
        伪装目标字符串（如 "chrome131"）
    """
    if randomize:
        return random.choice(RANDOMIZE_TARGETS)
    return TLS_IMPERSONATE_TARGET


def get_supported_targets() -> List[str]:
    """获取支持的伪装目标列表"""
    return SUPPORTED_IMPERSONATE_TARGETS.copy()


# ====================== Go 客户端风格请求头 ======================

# Go net/http 客户端的典型请求头特征
# 这些头部用于进一步模拟 Go 客户端行为
GO_CLIENT_HEADERS = {
    # Go 默认只使用 gzip 压缩
    "accept-encoding": "gzip",
}

# Antigravity CLI 的 User-Agent
ANTIGRAVITY_USER_AGENT = "antigravity/1.11.3 windows/amd64"

# GeminiCLI 的 User-Agent
GEMINI_CLI_USER_AGENT = "GeminiCLI/0.1.5 (Windows; AMD64)"


def get_go_style_headers(user_agent: Optional[str] = None) -> Dict[str, str]:
    """
    获取 Go 客户端风格的请求头

    Args:
        user_agent: 自定义 User-Agent，默认使用 Antigravity UA

    Returns:
        Go 风格的请求头字典
    """
    headers = GO_CLIENT_HEADERS.copy()
    headers["user-agent"] = user_agent or ANTIGRAVITY_USER_AGENT
    return headers


# ====================== 辅助函数 ======================

def get_curl_async_session(**kwargs) -> Optional["CurlAsyncSession"]:
    """
    获取 curl_cffi 的 AsyncSession 实例

    Args:
        **kwargs: 传递给 AsyncSession 的参数

    Returns:
        AsyncSession 实例，如果不可用则返回 None
    """
    if not is_tls_impersonate_available():
        return None

    # 设置默认的伪装目标
    if "impersonate" not in kwargs:
        kwargs["impersonate"] = get_impersonate_target()

    return CurlAsyncSession(**kwargs)


def get_curl_session(**kwargs) -> Optional["CurlSession"]:
    """
    获取 curl_cffi 的同步 Session 实例

    Args:
        **kwargs: 传递给 Session 的参数

    Returns:
        Session 实例，如果不可用则返回 None
    """
    if not is_tls_impersonate_available():
        return None

    # 设置默认的伪装目标
    if "impersonate" not in kwargs:
        kwargs["impersonate"] = get_impersonate_target()

    return CurlSession(**kwargs)


# ====================== 状态报告 ======================

def get_tls_status() -> Dict[str, Any]:
    """
    获取 TLS 伪装模块状态

    Returns:
        状态信息字典
    """
    return {
        "curl_cffi_installed": CURL_CFFI_AVAILABLE,
        "tls_impersonate_enabled": TLS_IMPERSONATE_ENABLED,
        "is_available": is_tls_impersonate_available(),
        "current_target": get_impersonate_target() if is_tls_impersonate_available() else None,
        "supported_targets_count": len(SUPPORTED_IMPERSONATE_TARGETS),
    }


# 模块加载时输出状态
if __name__ != "__main__":
    status = get_tls_status()
    if status["is_available"]:
        log.info(f"[TLS] TLS 伪装已启用，目标: {status['current_target']}")
    else:
        if not CURL_CFFI_AVAILABLE:
            log.warning("[TLS] TLS 伪装不可用: curl_cffi 未安装")
        elif not TLS_IMPERSONATE_ENABLED:
            log.info("[TLS] TLS 伪装已禁用 (TLS_IMPERSONATE_ENABLED=false)")
