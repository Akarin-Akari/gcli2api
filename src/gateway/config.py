"""
Gateway 配置模块

包含后端配置、模型路由配置、重试配置等。

从 unified_gateway_router.py 抽取的配置常量和辅助函数。

作者: 浮浮酱 (Claude Opus 4.5)
创建日期: 2026-01-18
"""

from typing import Dict, Any, Set, List, Tuple
import os
import re

__all__ = [
    # 后端配置
    "BACKENDS",
    "KIRO_GATEWAY_MODELS",
    "KIRO_GATEWAY_MODELS_ENV",
    # 重试配置
    "RETRY_CONFIG",
    # 模型路由
    "ROUTABLE_MODELS",
    "USE_PATTERN",
    "AT_PATTERN",
    "ANTIGRAVITY_SUPPORTED_PATTERNS",
    "COPILOT_MODEL_MAPPING",
    # 功能开关
    "BUGMENT_TOOL_RESULT_SHORTCIRCUIT_ENABLED",
    # 辅助函数
    "extract_model_from_prompt",
    "normalize_model_name",
    "is_antigravity_supported",
    "map_model_for_copilot",
    # AnyRouter 辅助函数
    "get_anyrouter_endpoint",
    "rotate_anyrouter_endpoint",
    "get_anyrouter_all_endpoints",
    # 模型路由规则（新增）
    "MODEL_ROUTING",
    "get_model_routing_rule",
    "reload_model_routing_config",
    "ModelRoutingRule",
    "BackendEntry",
]


# ==================== 功能开关 ====================

BUGMENT_TOOL_RESULT_SHORTCIRCUIT_ENABLED = os.getenv(
    "BUGMENT_TOOL_RESULT_SHORTCIRCUIT", ""
).strip().lower() in ("1", "true", "yes")


# ==================== 重试配置 ====================

RETRY_CONFIG: Dict[str, Any] = {
    "max_retries": 3,           # 最大重试次数
    "base_delay": 1.0,          # 基础延迟（秒）
    "max_delay": 10.0,          # 最大延迟（秒）
    "exponential_base": 2,      # 指数退避基数
    # 注意：移除 503 重试，避免把「额度/降级语义」的 503 放大成重试风暴
    "retry_on_status": [500, 502, 504],  # 需要重试的状态码
}


# ==================== 后端服务配置 ====================

BACKENDS: Dict[str, Dict[str, Any]] = {
    "antigravity": {
        "name": "Antigravity",
        "base_url": "http://127.0.0.1:7861/antigravity/v1",
        "priority": 1,  # 数字越小优先级越高
        "timeout": 60.0,  # 普通请求超时
        "stream_timeout": 300.0,  # 流式请求超时（5分钟）
        "max_retries": 2,  # 最大重试次数
        "enabled": True,
    },
    "kiro-gateway": {
        "name": "Kiro Gateway",
        # Kiro Gateway 专门用于 Claude 模型的降级
        # 优先级调整为 2，次于 Antigravity，高于 Copilot
        "base_url": os.getenv("KIRO_GATEWAY_BASE_URL", "http://127.0.0.1:9876/v1"),
        "priority": 2,  # 优先级次于 Antigravity，高于 Copilot
        "timeout": float(os.getenv("KIRO_GATEWAY_TIMEOUT", "120.0")),
        "stream_timeout": float(os.getenv("KIRO_GATEWAY_STREAM_TIMEOUT", "600.0")),
        "max_retries": int(os.getenv("KIRO_GATEWAY_MAX_RETRIES", "2")),
        "enabled": os.getenv("KIRO_GATEWAY_ENABLED", "true").lower() in ("true", "1", "yes"),
        # Kiro Gateway 支持的模型列表（Claude 4.5 全家桶 + Claude Sonnet 4）
        "supported_models": [
            "claude-sonnet-4.5", "claude-opus-4.5", "claude-haiku-4.5", "claude-sonnet-4",
        ],
    },
    "anyrouter": {
        "name": "AnyRouter",
        # AnyRouter 是公益站第三方 API，使用 Anthropic 格式
        # 优先级在 Kiro Gateway 之后，Copilot 之前
        "base_urls": [
            url.strip() for url in os.getenv(
                "ANYROUTER_BASE_URLS",
                "https://anyrouter.top,https://pmpjfbhq.cn-nb1.rainapp.top,https://a-ocnfniawgw.cn-shanghai.fcapp.run"
            ).split(",") if url.strip()
        ],
        "api_keys": [
            key.strip() for key in os.getenv(
                "ANYROUTER_API_KEYS",
                "sk-E4L18390pp12BacrKa7IJV8hgztEo8SsPKFdtSYGx6vLEbDK,sk-be7LKJwag3qXSRL77tVbxUsIHEi71UfAVOvqjGI13BJiXGD5"
            ).split(",") if key.strip()
        ],
        "priority": 3,  # 优先级次于 Kiro Gateway，高于 Copilot
        "timeout": float(os.getenv("ANYROUTER_TIMEOUT", "120.0")),
        "stream_timeout": float(os.getenv("ANYROUTER_STREAM_TIMEOUT", "600.0")),
        "max_retries": int(os.getenv("ANYROUTER_MAX_RETRIES", "1")),  # 每个端点只重试1次
        "enabled": os.getenv("ANYROUTER_ENABLED", "true").lower() in ("true", "1", "yes"),
        # AnyRouter 支持的模型列表（来自官方）
        "supported_models": [
            # Claude 4.5 系列
            "claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001",
            # Claude 4 系列
            "claude-opus-4-20250514", "claude-opus-4-1-20250805", "claude-sonnet-4-20250514",
            # Claude 3.7 系列
            "claude-3-7-sonnet-20250219",
            # Claude 3.5 系列
            "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
            # 其他模型
            "gemini-2.5-pro", "gpt-5-codex",
        ],
        # 使用 Anthropic 格式（/v1/messages 端点）
        "api_format": "anthropic",
        # 当前使用的端点和 Key 索引（运行时状态）
        "_current_url_index": 0,
        "_current_key_index": 0,
    },
    "copilot": {
        "name": "Copilot",
        "base_url": "http://127.0.0.1:8141/v1",
        "priority": 4,  # 优先级最低，作为最终兜底
        "timeout": 120.0,  # 思考模型需要更长时间
        "stream_timeout": 600.0,  # 流式请求超时（10分钟，GPT-5.2思考模型）
        "max_retries": 3,  # 最大重试次数
        "enabled": True,
    },
}


# ==================== Kiro Gateway 路由配置 ====================

# 通过环境变量 KIRO_GATEWAY_MODELS 指定哪些模型路由到 kiro-gateway
# 格式：逗号分隔的模型名称列表，例如: "gpt-4,claude-3-opus,gemini-pro"
KIRO_GATEWAY_MODELS_ENV = os.getenv("KIRO_GATEWAY_MODELS", "").strip()
KIRO_GATEWAY_MODELS: List[str] = (
    [m.strip().lower() for m in KIRO_GATEWAY_MODELS_ENV.split(",") if m.strip()]
    if KIRO_GATEWAY_MODELS_ENV
    else []
)


# ==================== AnyRouter 辅助函数 ====================

def get_anyrouter_endpoint() -> Tuple[str, str]:
    """
    获取当前 AnyRouter 的端点和 API Key

    使用轮询策略，每次调用返回下一个端点/Key 组合

    Returns:
        Tuple[str, str]: (base_url, api_key)
    """
    config = BACKENDS.get("anyrouter", {})
    base_urls = config.get("base_urls", [])
    api_keys = config.get("api_keys", [])

    if not base_urls or not api_keys:
        return "", ""

    url_index = config.get("_current_url_index", 0) % len(base_urls)
    key_index = config.get("_current_key_index", 0) % len(api_keys)

    return base_urls[url_index], api_keys[key_index]


def rotate_anyrouter_endpoint(rotate_url: bool = True, rotate_key: bool = False) -> None:
    """
    轮换 AnyRouter 端点或 API Key

    当某个端点失败时调用此函数切换到下一个

    Args:
        rotate_url: 是否轮换端点
        rotate_key: 是否轮换 API Key
    """
    config = BACKENDS.get("anyrouter", {})
    base_urls = config.get("base_urls", [])
    api_keys = config.get("api_keys", [])

    if rotate_url and base_urls:
        current = config.get("_current_url_index", 0)
        config["_current_url_index"] = (current + 1) % len(base_urls)

    if rotate_key and api_keys:
        current = config.get("_current_key_index", 0)
        config["_current_key_index"] = (current + 1) % len(api_keys)


def get_anyrouter_all_endpoints() -> List[Tuple[str, str]]:
    """
    获取所有 AnyRouter 端点和 API Key 的组合

    用于遍历所有可能的组合进行重试

    Returns:
        List[Tuple[str, str]]: [(base_url, api_key), ...]
    """
    config = BACKENDS.get("anyrouter", {})
    base_urls = config.get("base_urls", [])
    api_keys = config.get("api_keys", [])

    if not base_urls or not api_keys:
        return []

    # 返回所有端点和 Key 的组合（端点优先轮询，Key 保持不变以维持会话）
    # 策略：先尝试所有端点用同一个 Key，失败后换 Key 再试所有端点
    combinations = []
    for key in api_keys:
        for url in base_urls:
            combinations.append((url, key))

    return combinations


# ==================== Prompt Model Routing ====================

# Supported model names for routing
ROUTABLE_MODELS: Set[str] = {
    # GPT models -> Copilot
    "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5.2",
    "o1", "o1-mini", "o1-pro", "o3", "o3-mini",
    # Claude models -> Antigravity
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3.5-opus", "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
    "claude-sonnet-4.5", "claude-opus-4.5", "claude-haiku-4.5",
    # Gemini models -> Antigravity
    "gemini-pro", "gemini-ultra",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-3-pro", "gemini-3-pro-high", "gemini-3-pro-low", "gemini-3-flash",
}

# Regex patterns for model markers
# Pattern 1: [use:model-name] - High priority
USE_PATTERN = re.compile(r'\[use:([a-zA-Z0-9._-]+)\]', re.IGNORECASE)
# Pattern 2: @model-name - Low priority (at start of message or after whitespace)
AT_PATTERN = re.compile(r'(?:^|\s)@([a-zA-Z0-9._-]+)(?=\s|$)', re.IGNORECASE)


# ==================== Antigravity 支持的模型 ====================

ANTIGRAVITY_SUPPORTED_PATTERNS: Set[str] = {
    # Gemini 3 系列 - 只支持 3 系列
    "gemini-3", "gemini3",
    # Claude 4.5 系列 - 只支持 4.5 版本的 sonnet 和 opus
    "claude-sonnet-4.5", "claude-4.5-sonnet", "claude-45-sonnet",
    "claude-opus-4.5", "claude-4.5-opus", "claude-45-opus",
    # GPT OOS
    "gpt-oos",
}


# ==================== Copilot 模型名称映射 ====================

COPILOT_MODEL_MAPPING: Dict[str, str] = {
    # Claude Haiku 系列 -> claude-haiku-4.5
    "claude-3-haiku": "claude-haiku-4.5",
    "claude-3.5-haiku": "claude-haiku-4.5",
    "claude-haiku-3": "claude-haiku-4.5",
    "claude-haiku-3.5": "claude-haiku-4.5",
    "claude-haiku": "claude-haiku-4.5",

    # Claude Sonnet 系列
    "claude-3-sonnet": "claude-sonnet-4",
    "claude-3.5-sonnet": "claude-sonnet-4",
    "claude-sonnet-3": "claude-sonnet-4",
    "claude-sonnet-3.5": "claude-sonnet-4",
    "claude-sonnet": "claude-sonnet-4",

    # Claude 4 系列
    "claude-4-sonnet": "claude-sonnet-4",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-4.5-sonnet": "claude-sonnet-4.5",
    "claude-sonnet-4.5": "claude-sonnet-4.5",

    "claude-4-opus": "claude-opus-4.5",
    "claude-opus-4": "claude-opus-4.5",
    "claude-4.5-opus": "claude-opus-4.5",
    "claude-opus-4.5": "claude-opus-4.5",

    "claude-4-haiku": "claude-haiku-4.5",
    "claude-haiku-4": "claude-haiku-4.5",
    "claude-4.5-haiku": "claude-haiku-4.5",
    "claude-haiku-4.5": "claude-haiku-4.5",

    # GPT 系列
    "gpt-4-turbo": "gpt-4-0125-preview",
    "gpt-4-turbo-preview": "gpt-4-0125-preview",
    "gpt-4o-latest": "gpt-4o",
    "gpt-4o-mini-latest": "gpt-4o-mini",
}


# ==================== 辅助函数 ====================

def normalize_model_name(model: str) -> str:
    """
    规范化模型名称，移除变体后缀

    Args:
        model: 原始模型名称

    Returns:
        规范化后的模型名称
    """
    model_lower = model.lower()

    # 移除常见后缀
    suffixes = [
        "-thinking", "-think", "-extended", "-preview", "-latest",
        "-high", "-low", "-medium",
        "-20241022", "-20240620", "-20250101", "-20250514",
    ]
    for suffix in suffixes:
        model_lower = model_lower.replace(suffix, "")

    # 移除日期后缀
    model_lower = re.sub(r'-\d{8}$', '', model_lower)

    return model_lower.strip("-")


def is_antigravity_supported(model: str) -> bool:
    """
    检查模型是否被 Antigravity 支持

    Antigravity 支持：
    - Gemini 2.5 系列 (gemini-2.5-pro, gemini-2.5-flash 等)
    - Gemini 3 系列 (gemini-3-pro, gemini-3-flash)
    - Claude 4.5 系列 (sonnet-4.5, opus-4.5, haiku-4.5)
    - GPT OOS 120B

    注意：haiku 模型会被映射到 gemini-3-flash，但仍然走 Antigravity

    Args:
        model: 模型名称

    Returns:
        是否被 Antigravity 支持
    """
    normalized = normalize_model_name(model)
    model_lower = model.lower()

    # 检查 Gemini - 支持 2.5 和 3 系列
    if "gemini" in model_lower:
        # 检查是否是 Gemini 2.5 或 3
        if any(x in normalized for x in ["gemini-2.5", "gemini-2-5", "gemini2.5", "gemini25"]):
            return True
        if any(x in normalized for x in ["gemini-3", "gemini3"]):
            return True
        # 其他 Gemini 版本（2.0, 1.5 等）不支持
        return False

    # 检查 Claude - 支持 4.5 系列的 sonnet, opus, haiku
    if "claude" in model_lower:
        # 检查版本号 4.5 / 4-5
        # 支持格式: claude-sonnet-4.5, claude-4.5-sonnet, claude-opus-4-5-20251101 等
        # 使用正则匹配 4.5 或 4-5 格式
        has_45 = bool(re.search(r'4[.\-]5', normalized))

        # 检查模型类型
        has_sonnet = "sonnet" in normalized
        has_opus = "opus" in normalized
        has_haiku = "haiku" in normalized

        if has_45 and (has_sonnet or has_opus or has_haiku):
            return True

        # 其他 Claude 版本不支持
        return False

    # 检查 GPT OOS
    if "gpt-oos" in model_lower or "gptoos" in model_lower:
        return True

    # 其他模型都不支持
    return False


def map_model_for_copilot(model: str) -> str:
    """
    将模型名称映射为 Copilot API 支持的格式

    Args:
        model: 原始模型名称

    Returns:
        Copilot 兼容的模型名称
    """
    model_lower = model.lower()

    # 1. 直接映射
    if model_lower in COPILOT_MODEL_MAPPING:
        return COPILOT_MODEL_MAPPING[model_lower]

    # 2. 智能模糊匹配 Claude 模型
    if "claude" in model_lower:
        if "haiku" in model_lower:
            return "claude-haiku-4.5"
        elif "opus" in model_lower:
            return "claude-opus-4.5"
        elif "sonnet" in model_lower:
            if "4.5" in model_lower or "4-5" in model_lower:
                return "claude-sonnet-4.5"
            return "claude-sonnet-4"
        else:
            return "claude-sonnet-4"

    # 3. 智能模糊匹配 GPT 模型
    if "gpt" in model_lower:
        if "5.2" in model_lower or "5-2" in model_lower:
            return "gpt-5.2"
        elif "5.1" in model_lower or "5-1" in model_lower:
            return "gpt-5.1"
        elif "5" in model_lower:
            return "gpt-5"
        elif "4.1" in model_lower or "4-1" in model_lower:
            return "gpt-4.1"
        elif "4o-mini" in model_lower or "4o mini" in model_lower:
            return "gpt-4o-mini"
        elif "4o" in model_lower:
            return "gpt-4o"
        elif "4-turbo" in model_lower:
            return "gpt-4-0125-preview"
        elif "3.5" in model_lower:
            return "gpt-3.5-turbo"
        else:
            return "gpt-4"

    # 4. 智能模糊匹配 Gemini 模型
    if "gemini" in model_lower:
        if "3" in model_lower:
            if "flash" in model_lower:
                return "gemini-3-flash"
            return "gemini-3-pro-high"
        elif "2.5" in model_lower:
            return "gemini-2.5-pro"
        else:
            return "gemini-2.5-pro"

    # 5. O1/O3 模型
    if model_lower.startswith("o1") or model_lower.startswith("o3"):
        return model

    # 6. 返回原始模型名
    return model


def _fuzzy_match_model(model_name: str) -> bool:
    """
    Fuzzy match model name against known patterns.
    Allows variations like 'gpt4o' -> 'gpt-4o', 'claude35' -> 'claude-3.5'

    Args:
        model_name: 模型名称

    Returns:
        是否匹配已知模型
    """
    # Normalize: remove dashes and dots for comparison
    normalized = model_name.replace('-', '').replace('.', '').replace('_', '')

    for known_model in ROUTABLE_MODELS:
        known_normalized = known_model.replace('-', '').replace('.', '').replace('_', '')
        if normalized == known_normalized:
            return True

    # Check prefixes for model families
    model_prefixes = ['gpt', 'claude', 'gemini', 'o1', 'o3']
    for prefix in model_prefixes:
        if normalized.startswith(prefix):
            return True

    return False


def _extract_and_clean(text: str, current_model: str = None) -> Tuple[str, str]:
    """
    Extract model marker from text and return cleaned text.

    Args:
        text: The text to search
        current_model: Currently extracted model (for priority)

    Returns:
        Tuple of (model_name or None, cleaned_text)
    """
    extracted_model = current_model
    cleaned_text = text

    # Priority 1: [use:model-name]
    use_match = USE_PATTERN.search(text)
    if use_match:
        model_name = use_match.group(1).lower()
        if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
            extracted_model = model_name
            # Remove the marker from text
            cleaned_text = USE_PATTERN.sub('', cleaned_text).strip()

    # Priority 2: @model-name (only if no [use:] found)
    if not use_match:
        at_match = AT_PATTERN.search(text)
        if at_match:
            model_name = at_match.group(1).lower()
            if model_name in ROUTABLE_MODELS or _fuzzy_match_model(model_name):
                extracted_model = model_name
                # Remove the marker from text
                cleaned_text = AT_PATTERN.sub(' ', cleaned_text).strip()

    return extracted_model, cleaned_text


def extract_model_from_prompt(messages: list) -> Tuple[str, list]:
    """
    Extract model name from prompt markers in messages.

    Priority:
    1. [use:model-name] - Highest priority
    2. @model-name - Lower priority

    Args:
        messages: List of message dicts with 'role' and 'content'

    Returns:
        Tuple of (extracted_model_name or None, cleaned_messages)
    """
    if not messages:
        return None, messages

    extracted_model = None
    cleaned_messages = []

    for msg in messages:
        if not isinstance(msg, dict):
            cleaned_messages.append(msg)
            continue

        content = msg.get("content", "")

        # Handle different content types
        if isinstance(content, list):
            # Multi-modal content (text + images)
            new_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    model, cleaned_text = _extract_and_clean(text, extracted_model)
                    if model:
                        extracted_model = model
                    new_content.append({**item, "text": cleaned_text})
                else:
                    new_content.append(item)
            cleaned_messages.append({**msg, "content": new_content})
        elif isinstance(content, str):
            model, cleaned_content = _extract_and_clean(content, extracted_model)
            if model:
                extracted_model = model
            cleaned_messages.append({**msg, "content": cleaned_content})
        else:
            cleaned_messages.append(msg)

    return extracted_model, cleaned_messages


# ==================== 模型特定路由规则 ====================

# 延迟导入，避免循环依赖
from .config_loader import (
    load_model_routing_config,
    get_model_routing_rule,
    reload_model_routing_config,
    ModelRoutingRule,
    BackendEntry,
)

# 全局模型路由配置（启动时加载）
MODEL_ROUTING: Dict[str, "ModelRoutingRule"] = {}

def _init_model_routing():
    """初始化模型路由配置"""
    global MODEL_ROUTING
    try:
        MODEL_ROUTING = load_model_routing_config()
    except Exception:
        # 配置加载失败时使用空配置
        MODEL_ROUTING = {}

# 模块加载时初始化
_init_model_routing()
