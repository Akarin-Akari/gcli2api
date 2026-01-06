"""
Tool Cleaner - 工具格式清理和规范化
处理来自不同客户端（Cursor、Claude Code 等）的工具格式差异

这是自定义功能模块，原版 gcli2api 不包含此功能
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from log import log


def clean_json_schema_for_tool(schema: Any) -> Dict[str, Any]:
    """
    清理 JSON Schema，确保符合 Antigravity API 要求

    Args:
        schema: 原始 schema（可能是 dict 或其他类型）

    Returns:
        清理后的 schema 字典
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    # 导入 clean_json_schema 函数
    try:
        from src.anthropic_converter import clean_json_schema
        cleaned = clean_json_schema(schema)
    except ImportError:
        # 如果导入失败，使用简单的清理逻辑
        cleaned = _simple_clean_schema(schema)

    # 确保有 type 字段
    if "type" not in cleaned:
        cleaned["type"] = "object"

    return cleaned


def _simple_clean_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """简单的 schema 清理逻辑（备用）"""
    EXCLUDED_KEYS = {'$schema', 'additionalProperties', 'minLength', 'maxLength',
                     'minItems', 'maxItems', 'uniqueItems'}

    def clean_recursive(obj):
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in EXCLUDED_KEYS:
                    continue
                cleaned[key] = clean_recursive(value)
            return cleaned
        elif isinstance(obj, list):
            return [clean_recursive(item) for item in obj]
        else:
            return obj

    return clean_recursive(schema)


def convert_pydantic_to_dict(obj: Any) -> Dict[str, Any]:
    """
    将 Pydantic 模型转换为字典

    Args:
        obj: Pydantic 模型或字典

    Returns:
        字典
    """
    if isinstance(obj, dict):
        return obj

    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    elif hasattr(obj, "dict"):
        return obj.dict()
    else:
        # 尝试使用 getattr 获取属性
        result = {}
        for attr in dir(obj):
            if not attr.startswith("_"):
                try:
                    value = getattr(obj, attr)
                    if not callable(value):
                        result[attr] = value
                except Exception:
                    pass
        return result


def normalize_tool_to_function_format(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    将工具规范化为标准的 function 格式

    支持的输入格式：
    1. 标准格式: {"type": "function", "function": {...}}
    2. Custom 格式: {"type": "custom", "custom": {...}}
    3. 扁平格式: {"name": "...", "description": "...", "parameters": {...}}
    4. Cursor 格式: {"name": "...", "input_schema": {...}}

    Args:
        tool: 原始工具定义

    Returns:
        标准格式的工具定义，如果无法转换则返回 None
    """
    if not isinstance(tool, dict):
        return None

    tool_type = tool.get("type", "")
    has_function = "function" in tool
    has_name = "name" in tool
    has_custom = "custom" in tool

    # Case 1: 标准格式 - 已经有 type 和 function
    if tool_type == "function" and has_function:
        return tool

    # Case 2: Custom 类型 - 需要转换
    if tool_type == "custom" and has_custom:
        custom_tool = tool.get("custom", {})
        if not isinstance(custom_tool, dict):
            custom_tool = convert_pydantic_to_dict(custom_tool)

        if not custom_tool or "name" not in custom_tool:
            log.warning(f"[TOOL CLEANER] Skipping invalid custom tool: missing name")
            return None

        input_schema = custom_tool.get("input_schema", {}) or {}
        if not isinstance(input_schema, dict):
            input_schema = convert_pydantic_to_dict(input_schema)

        cleaned_schema = clean_json_schema_for_tool(input_schema)

        log.warning(f"[TOOL CLEANER] Converted custom tool '{custom_tool.get('name')}' to function format")

        return {
            "type": "function",
            "function": {
                "name": custom_tool.get("name", ""),
                "description": custom_tool.get("description", ""),
                "parameters": cleaned_schema
            }
        }

    # Case 3: 扁平格式 - 只有 name，没有 type 和 function 包装
    if has_name and not has_function:
        # 优先使用 parameters，如果没有则使用 input_schema（Cursor 可能使用 input_schema）
        parameters = tool.get("parameters")
        used_input_schema = False

        if parameters is None:
            input_schema = tool.get("input_schema")
            if input_schema is not None:
                parameters = clean_json_schema_for_tool(input_schema)
                used_input_schema = True
            else:
                parameters = {"type": "object", "properties": {}}

        if not isinstance(parameters, dict):
            log.warning(f"[TOOL CLEANER] Tool '{tool.get('name')}' has non-dict parameters: {type(parameters)}")
            parameters = {"type": "object", "properties": {}}
        else:
            parameters = clean_json_schema_for_tool(parameters)

        log.warning(f"[TOOL CLEANER] Converted flat format tool '{tool.get('name')}' to standard format. "
                   f"Tool keys: {list(tool.keys())}, used_input_schema={used_input_schema}")

        return {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": parameters
            }
        }

    # Case 4: 其他格式 - 尝试修复
    if has_name:
        log.warning(f"[TOOL CLEANER] Unknown tool format: type={tool_type}, keys={list(tool.keys())}")

        parameters = tool.get("parameters")
        if parameters is None:
            input_schema = tool.get("input_schema")
            if input_schema is not None:
                parameters = clean_json_schema_for_tool(input_schema)
            else:
                parameters = {"type": "object", "properties": {}}

        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        else:
            parameters = clean_json_schema_for_tool(parameters)

        return {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": parameters
            }
        }

    log.warning(f"[TOOL CLEANER] Skipping tool without name field: {list(tool.keys())}")
    return None


def clean_tools_list(tools: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """
    清理工具列表，确保所有工具都是标准格式

    Args:
        tools: 原始工具列表

    Returns:
        清理后的工具列表
    """
    if not tools:
        return []

    cleaned_tools = []

    for tool in tools:
        # 首先将 Pydantic 模型转换为字典
        if not isinstance(tool, dict):
            tool = convert_pydantic_to_dict(tool)

        # 规范化为标准格式
        normalized = normalize_tool_to_function_format(tool)
        if normalized:
            cleaned_tools.append(normalized)

    return cleaned_tools


# ==================== User-Agent 检测增强 ====================

# 客户端匹配规则（按优先级排序）
# 格式: (client_type, patterns, version_regex, display_name)
CLIENT_PATTERNS: List[Tuple[str, List[str], Optional[str], str]] = [
    # 高优先级：专用 AI 编程工具（精确匹配）
    ("cursor", ["cursor/", "cursor-"], r"cursor[/-]?(\d+(?:\.\d+)*)", "Cursor IDE"),
    ("cline", ["cline/", "cline-", "claude-dev", "claudedev"], r"cline[/-]?(\d+(?:\.\d+)*)", "Cline"),
    ("claude_code", ["claude-code/", "claude-code-", "anthropic-claude"], r"claude-code[/-]?(\d+(?:\.\d+)*)", "Claude Code"),
    ("windsurf", ["windsurf/", "windsurf-"], r"windsurf[/-]?(\d+(?:\.\d+)*)", "Windsurf IDE"),
    ("aider", ["aider/", "aider-"], r"aider[/-]?(\d+(?:\.\d+)*)", "Aider"),
    ("continue_dev", ["continue/", "continue-dev"], r"continue[/-]?(\d+(?:\.\d+)*)", "Continue.dev"),
    ("zed", ["zed/", "zed-editor"], r"zed[/-]?(\d+(?:\.\d+)*)", "Zed Editor"),
    ("copilot", ["github-copilot", "copilot/"], r"copilot[/-]?(\d+(?:\.\d+)*)", "GitHub Copilot"),

    # 中优先级：通用关键词（可能误匹配，放在后面）
    ("cursor", ["cursor"], None, "Cursor IDE"),
    ("claude_code", ["claude", "anthropic"], None, "Claude Code"),

    # 低优先级：SDK 和通用客户端
    ("openai_api", ["openai-python/", "openai-node/", "openai/"], r"openai[/-]?(\d+(?:\.\d+)*)", "OpenAI SDK"),
    ("openai_api", ["python-requests/", "httpx/", "aiohttp/"], r"(?:python-requests|httpx|aiohttp)[/-]?(\d+(?:\.\d+)*)", "HTTP Client"),
    ("openai_api", ["node-fetch/", "axios/", "got/"], r"(?:node-fetch|axios|got)[/-]?(\d+(?:\.\d+)*)", "Node.js Client"),
]


def extract_version(user_agent: str, version_regex: Optional[str]) -> str:
    """
    从 User-Agent 中提取版本号

    Args:
        user_agent: HTTP User-Agent 头
        version_regex: 版本号正则表达式

    Returns:
        版本号字符串，如果未找到则返回空字符串
    """
    if not version_regex:
        return ""

    try:
        match = re.search(version_regex, user_agent, re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass

    return ""


def detect_client_type_with_version(user_agent: str) -> Tuple[str, str, str]:
    """
    检测客户端类型并提取版本号（增强版）

    Args:
        user_agent: HTTP User-Agent 头

    Returns:
        (client_type, version, display_name)
    """
    if not user_agent:
        return "unknown", "", "Unknown Client"

    user_agent_lower = user_agent.lower()

    # 按优先级顺序匹配
    for client_type, patterns, version_regex, display_name in CLIENT_PATTERNS:
        for pattern in patterns:
            if pattern in user_agent_lower:
                version = extract_version(user_agent, version_regex) if version_regex else ""
                log.debug(f"[TOOL CLEANER] Matched client: {display_name} (type={client_type}, version={version or 'unknown'})")
                return client_type, version, display_name

    return "unknown", "", "Unknown Client"


def detect_client_type(user_agent: str) -> str:
    """
    检测请求来源客户端类型

    支持的客户端类型：
    - claude_code: Claude Code (官方 Claude 客户端)
    - cursor: Cursor IDE
    - cline: Cline VSCode 扩展
    - continue_dev: Continue.dev
    - aider: Aider
    - windsurf: Windsurf IDE
    - zed: Zed 编辑器
    - copilot: GitHub Copilot
    - openai_api: 标准 OpenAI API 调用
    - unknown: 未知客户端

    Args:
        user_agent: HTTP User-Agent 头

    Returns:
        客户端类型标识符
    """
    if not user_agent:
        return "unknown"
    
    user_agent_lower = user_agent.lower()

    # Cline VSCode 扩展 (之前叫 Claude Dev) - 需要在 Claude Code 之前检测
    # 因为 "claude-dev" 包含 "claude"
    if any(keyword in user_agent_lower for keyword in ["cline", "claude-dev", "claudedev"]):
        return "cline"

    # Claude Code / Anthropic 官方客户端
    if any(keyword in user_agent_lower for keyword in ["claude", "anthropic"]):
        return "claude_code"

    # Cursor IDE
    if "cursor" in user_agent_lower:
        return "cursor"
    
    # Continue.dev
    if "continue" in user_agent_lower:
        return "continue_dev"
    
    # Aider
    if "aider" in user_agent_lower:
        return "aider"
    
    # Windsurf IDE
    if "windsurf" in user_agent_lower:
        return "windsurf"
    
    # Zed 编辑器
    if "zed" in user_agent_lower:
        return "zed"
    
    # GitHub Copilot
    if any(keyword in user_agent_lower for keyword in ["copilot", "github-copilot"]):
        return "copilot"
    
    # 标准 OpenAI API 调用 (Python SDK, Node SDK 等)
    if any(keyword in user_agent_lower for keyword in ["openai", "python-requests", "node-fetch", "axios"]):
        return "openai_api"

    return "unknown"


def get_client_info(user_agent: str) -> dict:
    """
    获取客户端详细信息（增强版：包含版本号）

    Args:
        user_agent: HTTP User-Agent 头

    Returns:
        包含客户端类型、版本等信息的字典
    """
    # 使用增强版检测函数获取类型、版本和显示名称
    client_type, version, display_name = detect_client_type_with_version(user_agent)

    info = {
        "type": client_type,
        "name": display_name,
        "version": version,
        "user_agent": user_agent,
        "supports_tools": True,  # 默认支持工具调用
        "supports_streaming": True,  # 默认支持流式响应
        "enable_cross_pool_fallback": False,  # 默认不启用跨池降级
    }

    # 根据客户端类型设置特定属性
    if client_type == "claude_code":
        info["enable_cross_pool_fallback"] = True
    elif client_type == "cursor":
        # Cursor 不启用跨池降级，因为它有自己的 fallback 机制
        pass
    elif client_type == "cline":
        info["enable_cross_pool_fallback"] = True
    elif client_type == "continue_dev":
        info["enable_cross_pool_fallback"] = True
    elif client_type == "aider":
        info["enable_cross_pool_fallback"] = True
    elif client_type == "windsurf":
        pass  # Windsurf 不启用跨池降级
    elif client_type == "zed":
        pass  # Zed 不启用跨池降级
    elif client_type == "copilot":
        pass  # Copilot 不启用跨池降级
    elif client_type == "openai_api":
        info["enable_cross_pool_fallback"] = True

    return info


def should_enable_cross_pool_fallback(user_agent: str) -> bool:
    """
    判断是否应该启用跨池降级

    基于客户端类型决定是否启用跨池降级：
    - Claude Code, Cline, Continue.dev, Aider, OpenAI API: 启用
    - Cursor, Windsurf, Zed, Copilot: 不启用（有自己的 fallback 机制）
    - Unknown: 不启用（保守策略）

    Args:
        user_agent: HTTP User-Agent 头

    Returns:
        是否启用跨池降级
    """
    client_info = get_client_info(user_agent)
    client_type = client_info["type"]
    enable_fallback = client_info["enable_cross_pool_fallback"]

    if enable_fallback:
        log.info(f"[TOOL CLEANER] Detected {client_info['name']} ({client_type}) - cross-pool fallback ENABLED")
    else:
        log.debug(f"[TOOL CLEANER] Detected {client_info['name']} ({client_type}) - cross-pool fallback DISABLED")

    return enable_fallback


def log_tools_info(tools: List[Any], prefix: str = "ANTIGRAVITY"):
    """
    记录工具信息（用于调试）

    Args:
        tools: 工具列表
        prefix: 日志前缀
    """
    if not tools:
        return

    # 将 Pydantic 模型转换为字典以便检查
    tool_dicts = []
    for tool in tools:
        if not isinstance(tool, dict):
            tool_dicts.append(convert_pydantic_to_dict(tool))
        else:
            tool_dicts.append(tool)

    tool_types = [tool.get("type", "unknown") for tool in tool_dicts]
    log.info(f"[{prefix}] Received {len(tools)} tools, types={tool_types[:10]}... (showing first 10)")

    # 检查是否有 custom 格式的工具
    has_custom = any(tool.get("type") == "custom" for tool in tool_dicts)
    if has_custom:
        log.warning(f"[{prefix}] WARNING: Received custom tool format! This should have been normalized by gateway.")
        first_custom = next((tool for tool in tool_dicts if tool.get("type") == "custom"), None)
        if first_custom:
            custom_tool = first_custom.get("custom", {})
            if isinstance(custom_tool, dict):
                input_schema = custom_tool.get("input_schema", {})
                schema_type = input_schema.get("type") if isinstance(input_schema, dict) else type(input_schema).__name__
                has_properties = "properties" in input_schema if isinstance(input_schema, dict) else False
                log.warning(f"[{prefix}] First custom tool: name={custom_tool.get('name')}, "
                           f"input_schema_type={schema_type}, has_properties={has_properties}")
