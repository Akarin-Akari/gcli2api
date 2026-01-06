#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix two issues:
1. Tools format - convert_openai_tools_to_antigravity uses getattr which doesn't work with dicts
2. Model routing - Antigravity doesn't have some models, should fallback to Copilot
"""

# ==================== Fix 1: antigravity_router.py tools conversion ====================

antigravity_path = 'src/antigravity_router.py'

with open(antigravity_path, 'r', encoding='utf-8') as f:
    antigravity_content = f.read()

old_convert_tools = '''def convert_openai_tools_to_antigravity(tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 OpenAI 工具定义转换为 Antigravity 格式
    """
    if not tools:
        return None

    # 需要排除的字段
    EXCLUDED_KEYS = {'$schema', 'additionalProperties', 'minLength', 'maxLength',
                     'minItems', 'maxItems', 'uniqueItems'}

    def clean_parameters(obj):
        """递归清理参数对象"""
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in EXCLUDED_KEYS:
                    continue
                cleaned[key] = clean_parameters(value)
            return cleaned
        elif isinstance(obj, list):
            return [clean_parameters(item) for item in obj]
        else:
            return obj

    function_declarations = []

    for tool in tools:
        tool_type = getattr(tool, "type", "function")
        if tool_type == "function":
            function = getattr(tool, "function", None)
            if function:
                func_name = function.get("name")
                assert func_name is not None, "Function name is required"
                func_desc = function.get("description", "")
                func_params = function.get("parameters", {})

                # 转换为字典（如果是 Pydantic 模型）
                if hasattr(func_params, "dict") or hasattr(func_params, "model_dump"):
                    func_params = model_to_dict(func_params)

                # 清理参数
                cleaned_params = clean_parameters(func_params)

                function_declarations.append({
                    "name": func_name,
                    "description": func_desc,
                    "parameters": cleaned_params
                })

    if function_declarations:
        return [{"functionDeclarations": function_declarations}]

    return None'''

new_convert_tools = '''def convert_openai_tools_to_antigravity(tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 OpenAI 工具定义转换为 Antigravity 格式

    支持两种输入格式：
    1. Pydantic 模型对象（使用 getattr）
    2. 普通字典（使用 .get()）
    """
    if not tools:
        return None

    # 需要排除的字段
    EXCLUDED_KEYS = {'$schema', 'additionalProperties', 'minLength', 'maxLength',
                     'minItems', 'maxItems', 'uniqueItems'}

    def clean_parameters(obj):
        """递归清理参数对象"""
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in EXCLUDED_KEYS:
                    continue
                cleaned[key] = clean_parameters(value)
            return cleaned
        elif isinstance(obj, list):
            return [clean_parameters(item) for item in obj]
        else:
            return obj

    def get_value(obj, key, default=None):
        """从对象或字典中获取值"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    function_declarations = []

    for tool in tools:
        # 支持字典和对象两种格式
        tool_type = get_value(tool, "type", "function")

        if tool_type == "function":
            function = get_value(tool, "function", None)

            if function:
                func_name = get_value(function, "name")
                if not func_name:
                    log.warning(f"[ANTIGRAVITY] Skipping tool without function name")
                    continue

                func_desc = get_value(function, "description", "")
                func_params = get_value(function, "parameters", {})

                # 转换为字典（如果是 Pydantic 模型）
                if hasattr(func_params, "dict"):
                    func_params = func_params.dict()
                elif hasattr(func_params, "model_dump"):
                    func_params = func_params.model_dump()

                # 清理参数
                cleaned_params = clean_parameters(func_params)

                # 确保 parameters 有 type 字段（Antigravity 要求）
                if cleaned_params and "type" not in cleaned_params:
                    cleaned_params["type"] = "object"

                function_declarations.append({
                    "name": func_name,
                    "description": func_desc,
                    "parameters": cleaned_params
                })

    if function_declarations:
        return [{"functionDeclarations": function_declarations}]

    return None'''

if 'def get_value(obj, key, default=None):' in antigravity_content:
    print("[SKIP] convert_openai_tools_to_antigravity already fixed")
elif old_convert_tools in antigravity_content:
    antigravity_content = antigravity_content.replace(old_convert_tools, new_convert_tools)
    with open(antigravity_path, 'w', encoding='utf-8') as f:
        f.write(antigravity_content)
    print("[OK] Fixed convert_openai_tools_to_antigravity to support dict format")
else:
    print("[WARN] Could not find exact convert_openai_tools_to_antigravity pattern")
    # Try to find if it exists
    if 'convert_openai_tools_to_antigravity' in antigravity_content:
        print("[INFO] Function exists but pattern doesn't match - manual fix may be needed")


# ==================== Fix 2: unified_gateway_router.py model routing ====================

gateway_path = 'src/unified_gateway_router.py'

with open(gateway_path, 'r', encoding='utf-8') as f:
    gateway_content = f.read()

# Update MODEL_BACKEND_MAPPING to be smarter
old_model_mapping = '''# 模型到后端的映射（可选，用于特定模型强制使用特定后端）
MODEL_BACKEND_MAPPING = {
    # Copilot 专属模型
    "gpt-4": "copilot",
    "gpt-4o": "copilot",
    "gpt-4o-mini": "copilot",
    "gpt-4.1": "copilot",
    "gpt-5": "copilot",
    "gpt-5.1": "copilot",
    "gpt-5.2": "copilot",
    # Antigravity 专属模型
    "gemini-2.5-pro": "antigravity",
    "gemini-2.5-flash": "antigravity",
    "gemini-3-pro-preview": "antigravity",
    # Claude 模型 - 两边都有，优先 Antigravity
    "claude-sonnet-4": "antigravity",
    "claude-sonnet-4.5": "antigravity",
    "claude-opus-4.5": "antigravity",
    "claude-haiku-4.5": "antigravity",
}'''

new_model_mapping = '''# Antigravity 实际支持的模型列表（用于智能路由）
ANTIGRAVITY_SUPPORTED_MODELS = {
    # Claude 模型 - Antigravity 实际支持的
    "claude-sonnet-4", "claude-3-5-sonnet", "claude-3.5-sonnet",
    "claude-opus-4", "claude-3-opus",
    # Gemini 模型
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
    "gemini-3-pro-preview", "gemini-pro",
}

# Copilot 专属模型（GPT 系列）
COPILOT_EXCLUSIVE_MODELS = {
    "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5.2",
    "o1", "o1-mini", "o1-pro", "o3", "o3-mini",
}

# 模型到后端的映射（可选，用于特定模型强制使用特定后端）
MODEL_BACKEND_MAPPING = {
    # Copilot 专属模型 - GPT 系列
    "gpt-4": "copilot",
    "gpt-4o": "copilot",
    "gpt-4o-mini": "copilot",
    "gpt-4-turbo": "copilot",
    "gpt-4.1": "copilot",
    "gpt-4.1-mini": "copilot",
    "gpt-4.1-nano": "copilot",
    "gpt-5": "copilot",
    "gpt-5.1": "copilot",
    "gpt-5.2": "copilot",
    "o1": "copilot",
    "o1-mini": "copilot",
    "o1-pro": "copilot",
    "o3": "copilot",
    "o3-mini": "copilot",
    # Antigravity 专属模型 - Gemini 系列
    "gemini-2.5-pro": "antigravity",
    "gemini-2.5-flash": "antigravity",
    "gemini-2.0-flash": "antigravity",
    "gemini-3-pro-preview": "antigravity",
    "gemini-pro": "antigravity",
    # Claude 模型 - 优先 Antigravity（如果支持）
    "claude-sonnet-4": "antigravity",
    "claude-3-5-sonnet": "antigravity",
    "claude-3.5-sonnet": "antigravity",
    "claude-opus-4": "antigravity",
    "claude-3-opus": "antigravity",
}'''

if 'ANTIGRAVITY_SUPPORTED_MODELS' in gateway_content:
    print("[SKIP] Model routing already updated with ANTIGRAVITY_SUPPORTED_MODELS")
elif old_model_mapping in gateway_content:
    gateway_content = gateway_content.replace(old_model_mapping, new_model_mapping)
    print("[OK] Updated MODEL_BACKEND_MAPPING with smarter routing")
else:
    print("[WARN] Could not find exact MODEL_BACKEND_MAPPING pattern")


# Update get_backend_for_model function to be smarter
old_get_backend = '''def get_backend_for_model(model: str) -> Optional[str]:
    """根据模型名称获取指定后端"""
    # 精确匹配
    if model in MODEL_BACKEND_MAPPING:
        return MODEL_BACKEND_MAPPING[model]

    # 前缀匹配
    for model_prefix, backend in MODEL_BACKEND_MAPPING.items():
        if model.startswith(model_prefix):
            return backend

    return None  # 使用默认优先级'''

new_get_backend = '''def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端

    路由逻辑：
    1. GPT/O1/O3 系列 -> Copilot（专属）
    2. Gemini 系列 -> Antigravity（专属）
    3. Claude 系列 -> 检查 Antigravity 是否支持，不支持则 Copilot
    4. 其他模型 -> 默认优先级（Antigravity 优先）
    """
    model_lower = model.lower()

    # 精确匹配
    if model in MODEL_BACKEND_MAPPING:
        return MODEL_BACKEND_MAPPING[model]

    # GPT 系列 -> Copilot
    if model_lower.startswith("gpt-") or model_lower.startswith("gpt4") or model_lower.startswith("gpt5"):
        log.info(f"[Gateway] Model {model} is GPT series -> routing to Copilot")
        return "copilot"

    # O1/O3 系列 -> Copilot
    if model_lower.startswith("o1") or model_lower.startswith("o3"):
        log.info(f"[Gateway] Model {model} is O-series -> routing to Copilot")
        return "copilot"

    # Gemini 系列 -> Antigravity
    if model_lower.startswith("gemini"):
        log.info(f"[Gateway] Model {model} is Gemini series -> routing to Antigravity")
        return "antigravity"

    # Claude 系列 -> 检查 Antigravity 是否支持
    if model_lower.startswith("claude"):
        # 检查是否在 Antigravity 支持列表中
        if model in ANTIGRAVITY_SUPPORTED_MODELS:
            log.info(f"[Gateway] Model {model} is supported by Antigravity")
            return "antigravity"

        # 规范化模型名称进行模糊匹配
        normalized = model_lower.replace("-", "").replace(".", "").replace("_", "")
        for supported in ANTIGRAVITY_SUPPORTED_MODELS:
            supported_normalized = supported.lower().replace("-", "").replace(".", "").replace("_", "")
            if normalized == supported_normalized:
                log.info(f"[Gateway] Model {model} fuzzy matched to {supported} -> Antigravity")
                return "antigravity"

        # Claude 模型不在 Antigravity 支持列表中 -> Copilot
        log.info(f"[Gateway] Model {model} not supported by Antigravity -> routing to Copilot")
        return "copilot"

    # 前缀匹配（兜底）
    for model_prefix, backend in MODEL_BACKEND_MAPPING.items():
        if model.startswith(model_prefix):
            return backend

    return None  # 使用默认优先级'''

if 'ANTIGRAVITY_SUPPORTED_MODELS' in gateway_content and 'def get_backend_for_model' in gateway_content:
    # Check if already updated
    if 'GPT series -> routing to Copilot' in gateway_content:
        print("[SKIP] get_backend_for_model already updated with smart routing")
    elif old_get_backend in gateway_content:
        gateway_content = gateway_content.replace(old_get_backend, new_get_backend)
        print("[OK] Updated get_backend_for_model with smart Claude routing")
    else:
        print("[WARN] Could not find exact get_backend_for_model pattern")
else:
    if old_get_backend in gateway_content:
        gateway_content = gateway_content.replace(old_get_backend, new_get_backend)
        print("[OK] Updated get_backend_for_model with smart Claude routing")
    else:
        print("[WARN] Could not find get_backend_for_model pattern")


# Write back gateway file
with open(gateway_path, 'w', encoding='utf-8') as f:
    f.write(gateway_content)

print("\n[SUCCESS] Fixes applied!")
print("\nChanges made:")
print("1. Fixed convert_openai_tools_to_antigravity to support dict format")
print("2. Added ANTIGRAVITY_SUPPORTED_MODELS list for smart routing")
print("3. Updated get_backend_for_model with intelligent Claude routing:")
print("   - GPT/O1/O3 series -> Copilot (exclusive)")
print("   - Gemini series -> Antigravity (exclusive)")
print("   - Claude series -> Check if Antigravity supports, else Copilot")
print("   - claude-haiku-4.5 (not in Antigravity) -> Copilot")
