"""
Configuration constants for the Geminicli2api proxy server.
Centralizes all configuration to avoid duplication across modules.

- 启动时加载一次配置到内存
- 修改配置时调用 reload_config() 重新从数据库加载
"""

import os
from typing import Any, List, Optional

# 全局配置缓存
_config_cache: dict[str, Any] = {}
_config_initialized = False

# Client Configuration

# 需要自动封禁的错误码 (默认值，可通过环境变量或配置覆盖)
AUTO_BAN_ERROR_CODES = [403]


# ====================== 配置系统 ======================

async def init_config():
    """初始化配置缓存（启动时调用一次）"""
    global _config_cache, _config_initialized

    if _config_initialized:
        return

    try:
        from src.storage_adapter import get_storage_adapter
        storage_adapter = await get_storage_adapter()
        _config_cache = await storage_adapter.get_all_config()
        _config_initialized = True
    except Exception:
        # 初始化失败时使用空缓存
        _config_cache = {}
        _config_initialized = True


async def reload_config():
    """重新加载配置（修改配置后调用）"""
    global _config_cache, _config_initialized

    try:
        from src.storage_adapter import get_storage_adapter
        storage_adapter = await get_storage_adapter()

        # 如果后端支持 reload_config_cache，调用它
        if hasattr(storage_adapter._backend, 'reload_config_cache'):
            await storage_adapter._backend.reload_config_cache()

        # 重新加载配置缓存
        _config_cache = await storage_adapter.get_all_config()
        _config_initialized = True
    except Exception:
        pass


def _get_cached_config(key: str, default: Any = None) -> Any:
    """从内存缓存获取配置（同步）"""
    return _config_cache.get(key, default)


async def get_config_value(key: str, default: Any = None, env_var: Optional[str] = None) -> Any:
    """Get configuration value with priority: ENV > Storage > default."""
    # 确保配置已初始化
    if not _config_initialized:
        await init_config()

    # Priority 1: Environment variable
    if env_var and os.getenv(env_var):
        return os.getenv(env_var)

    # Priority 2: Memory cache
    value = _get_cached_config(key)
    if value is not None:
        return value

    return default


# Configuration getters - all async
async def get_proxy_config():
    """Get proxy configuration."""
    proxy_url = await get_config_value("proxy", env_var="PROXY")
    return proxy_url if proxy_url else None


async def get_auto_ban_enabled() -> bool:
    """Get auto ban enabled setting."""
    env_value = os.getenv("AUTO_BAN")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("auto_ban_enabled", False))


async def get_auto_ban_error_codes() -> list:
    """
    Get auto ban error codes.

    Environment variable: AUTO_BAN_ERROR_CODES (comma-separated, e.g., "400,403")
    Database config key: auto_ban_error_codes
    Default: [400, 403]
    """
    env_value = os.getenv("AUTO_BAN_ERROR_CODES")
    if env_value:
        try:
            return [int(code.strip()) for code in env_value.split(",") if code.strip()]
        except ValueError:
            pass

    codes = await get_config_value("auto_ban_error_codes")
    if codes and isinstance(codes, list):
        return codes
    return AUTO_BAN_ERROR_CODES


async def get_retry_429_max_retries() -> int:
    """Get max retries for 429 errors."""
    env_value = os.getenv("RETRY_429_MAX_RETRIES")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("retry_429_max_retries", 5))


async def get_retry_429_enabled() -> bool:
    """Get 429 retry enabled setting."""
    env_value = os.getenv("RETRY_429_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("retry_429_enabled", True))


async def get_retry_429_interval() -> float:
    """Get 429 retry interval in seconds."""
    env_value = os.getenv("RETRY_429_INTERVAL")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass

    return float(await get_config_value("retry_429_interval", 0.1))


async def get_anti_truncation_max_attempts() -> int:
    """
    Get maximum attempts for anti-truncation continuation.

    Environment variable: ANTI_TRUNCATION_MAX_ATTEMPTS
    Database config key: anti_truncation_max_attempts
    Default: 3
    """
    env_value = os.getenv("ANTI_TRUNCATION_MAX_ATTEMPTS")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("anti_truncation_max_attempts", 3))


# Server Configuration
async def get_server_host() -> str:
    """
    Get server host setting.

    Environment variable: HOST
    Database config key: host
    Default: 0.0.0.0
    """
    return str(await get_config_value("host", "0.0.0.0", "HOST"))


async def get_server_port() -> int:
    """
    Get server port setting.

    Environment variable: PORT
    Database config key: port
    Default: 7861
    """
    env_value = os.getenv("PORT")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("port", 7861))


async def get_api_password() -> str:
    """
    Get API password setting for chat endpoints.

    Environment variable: API_PASSWORD
    Database config key: api_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 API_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    api_password = await get_config_value("api_password", None, "API_PASSWORD")
    if api_password is not None:
        return str(api_password)

    # 兼容性：使用通用密码
    return str(await get_config_value("password", "pwd", "PASSWORD"))


async def get_panel_password() -> str:
    """
    Get panel password setting for web interface.

    Environment variable: PANEL_PASSWORD
    Database config key: panel_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 PANEL_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    panel_password = await get_config_value("panel_password", None, "PANEL_PASSWORD")
    if panel_password is not None:
        return str(panel_password)

    # 兼容性：使用通用密码
    return str(await get_config_value("password", "pwd", "PASSWORD"))


async def get_server_password() -> str:
    """
    Get server password setting (deprecated, use get_api_password or get_panel_password).

    Environment variable: PASSWORD
    Database config key: password
    Default: pwd
    """
    return str(await get_config_value("password", "pwd", "PASSWORD"))


async def get_credentials_dir() -> str:
    """
    Get credentials directory setting.

    Environment variable: CREDENTIALS_DIR
    Database config key: credentials_dir
    Default: ./creds
    """
    return str(await get_config_value("credentials_dir", "./creds", "CREDENTIALS_DIR"))


async def get_code_assist_endpoint() -> str:
    """
    Get Code Assist endpoint setting.

    Environment variable: CODE_ASSIST_ENDPOINT
    Database config key: code_assist_endpoint
    Default: https://daily-cloudcode-pa.sandbox.googleapis.com (沙箱环境，容量更宽松)
    """
    return str(
        await get_config_value(
            "code_assist_endpoint", "https://daily-cloudcode-pa.sandbox.googleapis.com", "CODE_ASSIST_ENDPOINT"
        )
    )


async def get_compatibility_mode_enabled() -> bool:
    """
    Get compatibility mode setting.

    兼容性模式：启用后所有system消息全部转换成user，停用system_instructions。
    该选项可能会降低模型理解能力，但是能避免流式空回的情况。

    Environment variable: COMPATIBILITY_MODE
    Database config key: compatibility_mode_enabled
    Default: True
    """
    env_value = os.getenv("COMPATIBILITY_MODE")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("compatibility_mode_enabled", True))


async def get_return_thoughts_to_frontend() -> bool:
    """
    Get return thoughts to frontend setting.

    控制是否将思维链返回到前端。
    启用后，思维链会在响应中返回；禁用后，思维链会在响应中被过滤掉。

    Environment variable: RETURN_THOUGHTS_TO_FRONTEND
    Database config key: return_thoughts_to_frontend
    Default: True
    """
    env_value = os.getenv("RETURN_THOUGHTS_TO_FRONTEND")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("return_thoughts_to_frontend", True))


async def get_oauth_proxy_url() -> str:
    """
    Get OAuth proxy URL setting.

    用于Google OAuth2认证的代理URL。

    Environment variable: OAUTH_PROXY_URL
    Database config key: oauth_proxy_url
    Default: https://oauth2.googleapis.com
    """
    return str(
        await get_config_value(
            "oauth_proxy_url", "https://oauth2.googleapis.com", "OAUTH_PROXY_URL"
        )
    )


async def get_googleapis_proxy_url() -> str:
    """
    Get Google APIs proxy URL setting.

    用于Google APIs调用的代理URL。

    Environment variable: GOOGLEAPIS_PROXY_URL
    Database config key: googleapis_proxy_url
    Default: https://www.googleapis.com
    """
    return str(
        await get_config_value(
            "googleapis_proxy_url", "https://www.googleapis.com", "GOOGLEAPIS_PROXY_URL"
        )
    )


async def get_resource_manager_api_url() -> str:
    """
    Get Google Cloud Resource Manager API URL setting.

    用于Google Cloud Resource Manager API的URL。

    Environment variable: RESOURCE_MANAGER_API_URL
    Database config key: resource_manager_api_url
    Default: https://cloudresourcemanager.googleapis.com
    """
    return str(
        await get_config_value(
            "resource_manager_api_url",
            "https://cloudresourcemanager.googleapis.com",
            "RESOURCE_MANAGER_API_URL",
        )
    )


async def get_service_usage_api_url() -> str:
    """
    Get Google Cloud Service Usage API URL setting.

    用于Google Cloud Service Usage API的URL。

    Environment variable: SERVICE_USAGE_API_URL
    Database config key: service_usage_api_url
    Default: https://serviceusage.googleapis.com
    """
    return str(
        await get_config_value(
            "service_usage_api_url", "https://serviceusage.googleapis.com", "SERVICE_USAGE_API_URL"
        )
    )


async def get_antigravity_api_url() -> str:
    """
    Get Antigravity API URL setting.

    用于Google Antigravity API的URL。

    Environment variable: ANTIGRAVITY_API_URL
    Database config key: antigravity_api_url
    Default: https://daily-cloudcode-pa.sandbox.googleapis.com
    """
    return str(
        await get_config_value(
            "antigravity_api_url",
            "https://daily-cloudcode-pa.sandbox.googleapis.com",
            "ANTIGRAVITY_API_URL",
        )
    )


async def get_antigravity_fallback_urls() -> List[str]:
    """
    Get Antigravity API fallback URLs for BaseURL failover.

    对齐 CLIProxyAPI 的多层级故障转移策略：
    1. 沙箱环境（Sandbox）- 最高优先级，容量最大
    2. Daily 环境（Daily）- 备用

    ⚠️ [FIX 2026-01-22] 移除生产端点 cloudcode-pa.googleapis.com
    该端点容量极小，频繁触发 MODEL_CAPACITY_EXHAUSTED 429 错误，
    导致 Cursor/Augment Code 等 IDE 的会话直接中断。

    Returns:
        按优先级排序的 BaseURL 列表（不含生产端点）
    """
    # 获取主 URL（用户配置的优先级最高）
    primary_url = await get_antigravity_api_url()

    # ✅ [FIX 2026-01-22] 只保留沙箱和 Daily 端点，移除生产端点
    # 生产端点 cloudcode-pa.googleapis.com 容量太小，基本无法使用
    # ✅ [FIX 2026-01-23] 确保至少有 2 个备用 URL（不包括主 URL），总共至少 3 个 URL
    all_urls = [
        "https://daily-cloudcode-pa.sandbox.googleapis.com",  # 沙箱（容量最大）
        "https://daily-cloudcode-pa.googleapis.com",          # Daily（备用）
        # ❌ 已移除: "https://cloudcode-pa.googleapis.com"    # 生产（容量极小，已禁用）
    ]

    # 如果主 URL 不在列表中，将其作为最高优先级
    # 但要排除生产端点（即使用户手动配置了也不允许）
    production_endpoint = "https://cloudcode-pa.googleapis.com"
    if primary_url == production_endpoint:
        # 用户配置了生产端点，强制替换为沙箱
        primary_url = "https://daily-cloudcode-pa.sandbox.googleapis.com"

    if primary_url not in all_urls:
        # 主 URL 不在列表中，添加到列表开头
        all_urls.insert(0, primary_url)
    else:
        # 主 URL 已在列表中，移到列表开头
        all_urls.remove(primary_url)
        all_urls.insert(0, primary_url)
    
    # ✅ [FIX 2026-01-23] 确保至少有 3 个 URL（主 URL + 2 个备用）
    # 如果主 URL 已经在列表中，最终只有 2 个 URL，需要添加更多备用 URL
    # 由于生产端点已移除，我们确保至少有 2 个不同的 URL（沙箱和 Daily）
    # 如果主 URL 是其中一个，则确保另一个也在列表中
    sandbox_url = "https://daily-cloudcode-pa.sandbox.googleapis.com"
    daily_url = "https://daily-cloudcode-pa.googleapis.com"
    
    # 确保沙箱和 Daily 都在列表中（如果它们不是主 URL）
    if sandbox_url not in all_urls:
        all_urls.append(sandbox_url)
    if daily_url not in all_urls:
        all_urls.append(daily_url)
    
    # 去重并保持主 URL 在第一位
    seen = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    
    # 确保主 URL 在第一位
    if unique_urls[0] != primary_url:
        unique_urls.remove(primary_url)
        unique_urls.insert(0, primary_url)

    return unique_urls


# ==================== 后台刷新配置 ====================

async def get_background_refresh_enabled() -> bool:
    """
    Get background refresh enabled setting.
    
    启用后台自动刷新配额功能。
    
    Environment variable: BACKGROUND_REFRESH_ENABLED
    Database config key: background_refresh_enabled
    Default: False
    """
    env_value = os.getenv("BACKGROUND_REFRESH_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(await get_config_value("background_refresh_enabled", False))


async def get_refresh_interval() -> int:
    """
    Get refresh interval in minutes.
    
    后台刷新间隔（分钟）。
    
    Environment variable: REFRESH_INTERVAL_MINUTES
    Database config key: refresh_interval
    Default: 15
    """
    env_value = os.getenv("REFRESH_INTERVAL_MINUTES")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(await get_config_value("refresh_interval", 15))


# ==================== 配额保护配置 ====================

async def get_quota_protection_enabled() -> bool:
    """
    Get quota protection enabled setting.
    
    启用配额保护功能，当高级模型配额低于阈值时自动禁用账号。
    
    Environment variable: QUOTA_PROTECTION_ENABLED
    Database config key: quota_protection_enabled
    Default: False
    """
    env_value = os.getenv("QUOTA_PROTECTION_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(await get_config_value("quota_protection_enabled", False))


async def get_quota_protection_threshold() -> int:
    """
    Get quota protection threshold percentage.
    
    配额保护阈值（百分比），低于此值时禁用账号。
    
    Environment variable: QUOTA_PROTECTION_THRESHOLD
    Database config key: quota_protection_threshold
    Default: 10
    """
    env_value = os.getenv("QUOTA_PROTECTION_THRESHOLD")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(await get_config_value("quota_protection_threshold", 10))


async def get_quota_protection_models() -> list:
    """
    Get quota protection monitored models.
    
    需要监控配额的模型列表。
    
    Environment variable: QUOTA_PROTECTION_MODELS (comma-separated)
    Database config key: quota_protection_models
    Default: ["claude-sonnet-4-5", "claude-opus-4-5", "gemini-3-pro"]
    """
    env_value = os.getenv("QUOTA_PROTECTION_MODELS")
    if env_value:
        return [model.strip() for model in env_value.split(",") if model.strip()]
    
    models = await get_config_value("quota_protection_models")
    if models and isinstance(models, list):
        return models
    
    return ["claude-sonnet-4-5", "claude-opus-4-5", "gemini-3-pro"]


# ==================== 智能预热配置 ====================

async def get_smart_warmup_enabled() -> bool:
    """
    Get smart warmup enabled setting.
    
    启用智能预热功能，当配额恢复到 100% 时自动预热。
    
    Environment variable: SMART_WARMUP_ENABLED
    Database config key: smart_warmup_enabled
    Default: False
    """
    env_value = os.getenv("SMART_WARMUP_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(await get_config_value("smart_warmup_enabled", False))


async def get_warmup_models() -> list:
    """
    Get warmup monitored models.
    
    需要预热的模型列表。
    
    Environment variable: WARMUP_MODELS (comma-separated)
    Database config key: warmup_models
    Default: ["gemini-3-flash", "claude-sonnet-4-5", "gemini-3-pro-high", "gemini-3-pro-image"]
    """
    env_value = os.getenv("WARMUP_MODELS")
    if env_value:
        return [model.strip() for model in env_value.split(",") if model.strip()]
    
    models = await get_config_value("warmup_models")
    if models and isinstance(models, list):
        return models
    
    return ["gemini-3-flash", "claude-sonnet-4-5", "gemini-3-pro-high", "gemini-3-pro-image"]

