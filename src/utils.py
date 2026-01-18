import base64
import re
import platform
import time
from datetime import datetime, timezone
from typing import List, Optional

from config import get_api_password, get_panel_password
from fastapi import Depends, HTTPException, Header, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from log import log

# HTTP Bearer security scheme
security = HTTPBearer()

CLI_VERSION = "0.1.5"  # Match current gemini-cli version

# ====================== OAuth Configuration ======================

# OAuth Configuration - 标准模式
CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
CLIENT_SECRET = "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"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Antigravity OAuth Configuration
ANTIGRAVITY_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_CLIENT_SECRET = "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"
ANTIGRAVITY_SCOPES = [
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/cclog',
    'https://www.googleapis.com/auth/experimentsandconfigs'
]

# 统一的 Token URL（两种模式相同）
TOKEN_URL = "https://oauth2.googleapis.com/token"

# 回调服务器配置
CALLBACK_HOST = "localhost"

# ====================== API Configuration ======================

STANDARD_USER_AGENT = "GeminiCLI/0.1.5 (Windows; AMD64)"

ANTIGRAVITY_USER_AGENT = "antigravity/1.11.3 windows/amd64"

# ====================== Model Configuration ======================

# Default Safety Settings for Google API
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_JAILBREAK", "threshold": "BLOCK_NONE"},
]

# Model name lists for different features
BASE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview"
]


# ====================== Model Helper Functions ======================

def get_base_model_name(model_name: str) -> str:
    """Convert variant model name to base model name."""
    # Remove all possible suffixes (supports multiple suffixes in any order)
    suffixes = ["-maxthinking", "-nothinking", "-search"]
    result = model_name
    # Keep removing suffixes until no more matches
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[: -len(suffix)]
                changed = True
                break
    return result


def is_search_model(model_name: str) -> bool:
    """Check if model name indicates search grounding should be enabled."""
    return "-search" in model_name


def is_nothinking_model(model_name: str) -> bool:
    """Check if model name indicates thinking should be disabled."""
    return "-nothinking" in model_name


def is_maxthinking_model(model_name: str) -> bool:
    """Check if model name indicates maximum thinking budget should be used."""
    return "-maxthinking" in model_name


def get_thinking_budget(model_name: str) -> Optional[int]:
    """Get the appropriate thinking budget for a model based on its name and variant."""
    if is_nothinking_model(model_name):
        return 128  # Limited thinking for pro
    elif is_maxthinking_model(model_name):
        base_model = get_base_model_name(get_base_model_from_feature_model(model_name))
        if "flash" in base_model:
            return 24576
        return 32768
    else:
        # Default thinking budget for regular models
        return None  # Default for all models


def should_include_thoughts(model_name: str) -> bool:
    """Check if thoughts should be included in the response."""
    if is_nothinking_model(model_name):
        # For nothinking mode, still include thoughts if it's a pro model
        base_model = get_base_model_name(model_name)
        return "pro" in base_model
    else:
        # For all other modes, include thoughts
        return True


def is_fake_streaming_model(model_name: str) -> bool:
    """Check if model name indicates fake streaming should be used."""
    return model_name.startswith("假流式/")


def is_anti_truncation_model(model_name: str) -> bool:
    """Check if model name indicates anti-truncation should be used."""
    return model_name.startswith("流式抗截断/")


def get_base_model_from_feature_model(model_name: str) -> str:
    """Get base model name from feature model name."""
    # Remove feature prefixes
    for prefix in ["假流式/", "流式抗截断/"]:
        if model_name.startswith(prefix):
            return model_name[len(prefix) :]
    return model_name


def get_available_models(router_type: str = "openai") -> List[str]:
    """
    Get available models with feature prefixes.

    Args:
        router_type: "openai" or "gemini"

    Returns:
        List of model names with feature prefixes
    """
    models = []

    for base_model in BASE_MODELS:
        # 基础模型
        models.append(base_model)

        # 假流式模型 (前缀格式)
        models.append(f"假流式/{base_model}")

        # 流式抗截断模型 (仅在流式传输时有效，前缀格式)
        models.append(f"流式抗截断/{base_model}")

        # 支持thinking模式后缀与功能前缀组合
        # 新增: 支持多后缀组合 (thinking + search)
        thinking_suffixes = ["-maxthinking", "-nothinking"]
        search_suffix = "-search"

        # 1. 单独的 thinking 后缀
        for thinking_suffix in thinking_suffixes:
            models.append(f"{base_model}{thinking_suffix}")
            models.append(f"假流式/{base_model}{thinking_suffix}")
            models.append(f"流式抗截断/{base_model}{thinking_suffix}")

        # 2. 单独的 search 后缀
        models.append(f"{base_model}{search_suffix}")
        models.append(f"假流式/{base_model}{search_suffix}")
        models.append(f"流式抗截断/{base_model}{search_suffix}")

        # 3. thinking + search 组合后缀
        for thinking_suffix in thinking_suffixes:
            combined_suffix = f"{thinking_suffix}{search_suffix}"
            models.append(f"{base_model}{combined_suffix}")
            models.append(f"假流式/{base_model}{combined_suffix}")
            models.append(f"流式抗截断/{base_model}{combined_suffix}")

    return models


def get_model_group(model_name: str) -> str:
    """
    获取模型组，用于 GCLI CD 机制。

    Args:
        model_name: 模型名称

    Returns:
        "pro" 或 "flash"

    说明:
        - pro 组: gemini-2.5-pro, gemini-3-pro-preview 共享额度
        - flash 组: gemini-2.5-flash 单独额度
    """
    # 去除功能前缀和后缀，获取基础模型名
    base_model = get_base_model_from_feature_model(model_name)
    base_model = get_base_model_name(base_model)

    # 判断模型组
    if "flash" in base_model.lower():
        return "flash"
    else:
        # pro 模型（包括 gemini-2.5-pro 和 gemini-3-pro-preview）
        return "pro"


# ====================== User Agent ======================


def get_user_agent():
    """Generate User-Agent string matching gemini-cli format."""
    version = CLI_VERSION
    system = platform.system()
    arch = platform.machine()
    return f"GeminiCLI/{version} ({system}; {arch})"


def parse_quota_reset_timestamp(
    error_response: dict,
    response_headers: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> Optional[float]:
    """
    从Google API错误响应中提取quota重置时间戳

    对齐 CLIProxyAPI 的完整解析策略：
    1. 优先解析 HTTP 响应头 Retry-After
    2. 解析 Google RPC RetryInfo.retryDelay（标准格式）
    3. 解析 ErrorInfo.metadata.quotaResetTimeStamp（ISO 时间戳）
    4. 解析 ErrorInfo.metadata.quotaResetDelay（duration 字符串）
    5. 从错误消息中正则提取（最后备用）

    Args:
        error_response: Google API返回的错误响应字典
        response_headers: HTTP 响应头（可选，用于解析 Retry-After）
        error_message: 错误消息文本（可选，用于正则提取）

    Returns:
        Unix时间戳（秒），如果无法解析则返回None

    示例错误响应:
    {
      "error": {
        "code": 429,
        "message": "You have exhausted your capacity...",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "QUOTA_EXHAUSTED",
            "metadata": {
              "quotaResetTimeStamp": "2025-11-30T14:57:24Z",
              "quotaResetDelay": "13h19m1.20964964s"
            }
          }
        ]
      }
    }
    """
    def _parse_duration_seconds(duration_str: str) -> Optional[float]:
        """
        解析 duration 字符串，支持形如：
        - "1.5s"
        - "200ms"
        - "1h16m0.667s"
        """
        if not duration_str:
            return None

        total_ms = 0.0
        matched = False
        for value_str, unit in re.findall(r"([\d.]+)\s*(ms|s|m|h)", duration_str):
            matched = True
            try:
                value = float(value_str)
            except ValueError:
                return None

            if unit == "ms":
                total_ms += value
            elif unit == "s":
                total_ms += value * 1000.0
            elif unit == "m":
                total_ms += value * 60.0 * 1000.0
            elif unit == "h":
                total_ms += value * 60.0 * 60.0 * 1000.0

        if not matched:
            return None
        return total_ms / 1000.0

    try:
        # ✅ [FIX 2026-01-17] 方式 0：HTTP 响应头 Retry-After（最高优先级）
        if response_headers:
            retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
            if retry_after:
                try:
                    # 尝试解析为整数（秒数）
                    seconds = int(retry_after)
                    return time.time() + seconds
                except ValueError:
                    # 尝试解析为 HTTP-date 格式
                    try:
                        from email.utils import parsedate_to_datetime
                        retry_dt = parsedate_to_datetime(retry_after)
                        return retry_dt.timestamp()
                    except Exception:
                        pass

        details = error_response.get("error", {}).get("details", []) or []

        # 方式 1：google.rpc.RetryInfo.retryDelay
        for detail in details:
            if not isinstance(detail, dict):
                continue
            type_str = detail.get("@type")
            if isinstance(type_str, str) and "RetryInfo" in type_str:
                retry_delay = detail.get("retryDelay")
                if isinstance(retry_delay, str):
                    seconds = _parse_duration_seconds(retry_delay)
                    if seconds is not None:
                        return time.time() + seconds

        # 方式 2：google.rpc.ErrorInfo.metadata.quotaResetTimeStamp（ISO 时间）
        for detail in details:
            if not isinstance(detail, dict):
                continue
            if detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
                reset_timestamp_str = (detail.get("metadata", {}) or {}).get("quotaResetTimeStamp")

                if isinstance(reset_timestamp_str, str) and reset_timestamp_str:
                    if reset_timestamp_str.endswith("Z"):
                        reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                    reset_dt = datetime.fromisoformat(reset_timestamp_str)
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                    return reset_dt.astimezone(timezone.utc).timestamp()

        # 方式 3：metadata.quotaResetDelay（duration）
        for detail in details:
            if not isinstance(detail, dict):
                continue
            quota_delay = (detail.get("metadata", {}) or {}).get("quotaResetDelay")
            if isinstance(quota_delay, str) and quota_delay:
                seconds = _parse_duration_seconds(quota_delay)
                if seconds is not None:
                    return time.time() + seconds

        # ✅ [FIX 2026-01-17] 方式 4：从错误消息中正则提取（最后备用）
        if error_message:
            # 匹配 "Your quota will reset after Xs." 或 "retry after Xs"
            match = re.search(r"(?:reset|retry)\s+(?:after|in)\s+([\d.]+)\s*s(?:econds?)?", error_message, re.IGNORECASE)
            if match:
                try:
                    seconds = float(match.group(1))
                    return time.time() + seconds
                except ValueError:
                    pass

            # 匹配 "Please try again in Xm" 或 "retry in Xh"
            match = re.search(r"(?:try again|retry)\s+in\s+([\d.]+)\s*([mh])", error_message, re.IGNORECASE)
            if match:
                try:
                    value = float(match.group(1))
                    unit = match.group(2).lower()
                    if unit == "m":
                        return time.time() + value * 60.0
                    elif unit == "h":
                        return time.time() + value * 3600.0
                except ValueError:
                    pass

        return None

    except Exception:
        return None


# ====================== Authentication Functions ======================

async def authenticate_bearer(
    authorization: Optional[str] = Header(None)
) -> str:
    """
    Bearer Token 认证

    此函数可以直接用作 FastAPI 的 Depends 依赖

    Args:
        authorization: Authorization 头部值（自动注入）

    Returns:
        验证通过的token

    Raises:
        HTTPException: 认证失败时抛出401或403异常

    使用示例:
        @router.post("/endpoint")
        async def endpoint(token: str = Depends(authenticate_bearer)):
            # token 已验证通过
            pass
    """

    password = await get_api_password()

    # 检查是否提供了 Authorization 头
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查是否是 Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Use 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 提取 token
    token = authorization[7:]  # 移除 "Bearer " 前缀

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 验证 token
    if token != password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="密码错误"
        )

    return token


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


async def authenticate_bearer_allow_local_dummy(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Bearer Token 认证（兼容本地 Bugment/Augment）。

    背景：部分客户端固定发送 `Authorization: Bearer dummy`，而网关口令可配置，重构后会导致入口直接 403。
    策略：仅对 localhost 请求放行 dummy token，其它保持原有严格校验。
    """

    password = await get_api_password()

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Use 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = token.strip()

    if token == password:
        return token

    if _is_local_request(request):
        # Some hosts (e.g. Cursor) can end up sending placeholder strings if secret storage fails.
        # We only tolerate these placeholders for localhost requests.
        if token in ("dummy", "undefined", "null"):
            return "dummy"
        # IDE-integrated clients (VSCode/Cursor) run locally and may provide their own per-host token.
        # To avoid brittle coupling between the IDE's token storage and the gateway's API_PASSWORD,
        # accept any non-empty Bearer token for localhost requests coming from the Augment extension UA.
        user_agent_lower = (request.headers.get("user-agent") or request.headers.get("User-Agent") or "").lower()
        if "augment.vscode-augment/" in user_agent_lower:
            return token

    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent") or request.headers.get("User-Agent") or ""
    if token in ("dummy", "undefined", "null"):
        masked = token
    elif len(token) <= 8:
        masked = f"{token[:2]}***{token[-2:]}" if len(token) > 3 else "***"
    else:
        masked = f"{token[:4]}***{token[-4:]}"
    log.warning(
        f"[AUTH] Rejecting bearer token (client_ip={client_ip}, ua={user_agent}, token={masked})",
        tag="AUTH",
    )

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="密码错误")


async def authenticate_gemini_flexible(
    request: Request,
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    key: Optional[str] = Query(None)
) -> str:
    """
    Gemini 灵活认证：支持 x-goog-api-key 头部、URL 参数 key 或 Authorization Bearer

    此函数可以直接用作 FastAPI 的 Depends 依赖

    Args:
        request: FastAPI Request 对象
        x_goog_api_key: x-goog-api-key 头部值（自动注入）
        key: URL 参数 key（自动注入）

    Returns:
        验证通过的API密钥

    Raises:
        HTTPException: 认证失败时抛出400异常

    使用示例:
        @router.post("/endpoint")
        async def endpoint(api_key: str = Depends(authenticate_gemini_flexible)):
            # api_key 已验证通过
            pass
    """

    password = await get_api_password()

    # 尝试从URL参数key获取（Google官方标准方式）
    if key:
        log.debug("Using URL parameter key authentication")
        if key == password:
            return key

    # 尝试从Authorization头获取（兼容旧方式）
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # 移除 "Bearer " 前缀
        log.debug("Using Bearer token authentication")
        if token == password:
            return token

    # 尝试从x-goog-api-key头获取（新标准方式）
    if x_goog_api_key:
        log.debug("Using x-goog-api-key authentication")
        if x_goog_api_key == password:
            return x_goog_api_key

    log.error(f"Authentication failed. Headers: {dict(request.headers)}, Query params: key={key}")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing or invalid authentication. Use 'key' URL parameter, 'x-goog-api-key' header, or 'Authorization: Bearer <token>'",
    )


async def authenticate_sdwebui_flexible(request: Request) -> str:
    """
    SD-WebUI 灵活认证：支持 Authorization Basic/Bearer

    此函数可以直接用作 FastAPI 的 Depends 依赖

    Args:
        request: FastAPI Request 对象

    Returns:
        验证通过的密码

    Raises:
        HTTPException: 认证失败时抛出403异常

    使用示例:
        @router.post("/endpoint")
        async def endpoint(pwd: str = Depends(authenticate_sdwebui_flexible)):
            # pwd 已验证通过
            pass
    """


    password = await get_api_password()

    # 尝试从 Authorization 头获取
    auth_header = request.headers.get("authorization")

    if auth_header:
        # 支持 Bearer token 认证
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]  # 移除 "Bearer " 前缀
            log.debug("Using Bearer token authentication")
            if token == password:
                return token

        # 支持 Basic 认证
        elif auth_header.startswith("Basic "):
            try:
                # 解码 Base64
                encoded_credentials = auth_header[6:]  # 移除 "Basic " 前缀
                decoded_bytes = base64.b64decode(encoded_credentials)
                decoded_str = decoded_bytes.decode('utf-8')

                # Basic 认证格式: username:password 或者只有 password
                # SD-WebUI 可能只发送密码
                if ':' in decoded_str:
                    _, pwd = decoded_str.split(':', 1)
                else:
                    pwd = decoded_str

                log.debug(f"Using Basic authentication, decoded: {decoded_str}")
                if pwd == password:
                    return pwd
            except Exception as e:
                log.error(f"Failed to decode Basic auth: {e}")

    log.error(f"SD-WebUI authentication failed. Headers: {dict(request.headers)}")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing or invalid authentication. Use 'Authorization: Basic <base64>' or 'Bearer <token>'",
    )


# ====================== Panel Authentication Functions ======================

async def verify_panel_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    简化的控制面板密码验证函数

    直接验证Bearer token是否等于控制面板密码

    Args:
        credentials: HTTPAuthorizationCredentials 自动注入

    Returns:
        验证通过的token

    Raises:
        HTTPException: 密码错误时抛出401异常
    """

    password = await get_panel_password()
    if credentials.credentials != password:
        raise HTTPException(status_code=401, detail="密码错误")
    return credentials.credentials
