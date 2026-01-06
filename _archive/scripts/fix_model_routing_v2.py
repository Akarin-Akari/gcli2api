#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix model routing - Make Claude matching more flexible.
Antigravity is preferred (token-based), Copilot is fallback (per-request billing).

Strategy:
1. Extract core model identifiers (opus, sonnet, haiku + version)
2. Match flexibly regardless of naming convention differences
3. Only fallback to Copilot if truly no match
"""

gateway_path = 'src/unified_gateway_router.py'

with open(gateway_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the entire model routing section with smarter logic
old_section = '''# Antigravity 实际支持的模型列表（用于智能路由）
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
}


def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表"""
    enabled_backends = [(k, v) for k, v in BACKENDS.items() if v.get("enabled", True)]
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])


def get_backend_for_model(model: str) -> Optional[str]:
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

new_section = '''# ==================== 智能模型路由 ====================
# 策略：Antigravity 优先（按 token 计费），Copilot 备用（按次计费）

# Copilot 专属模型（GPT/O 系列）- 这些只能用 Copilot
COPILOT_EXCLUSIVE_PREFIXES = {"gpt-", "gpt4", "gpt5", "o1", "o3"}

# Antigravity 支持的 Claude 模型核心标识
# 格式：(型号, 版本) - 用于灵活匹配各种命名变体
ANTIGRAVITY_CLAUDE_MODELS = {
    # Claude 4.x 系列
    ("sonnet", "4"), ("sonnet", "4.5"), ("sonnet", "45"),
    ("opus", "4"), ("opus", "4.5"), ("opus", "45"),
    ("haiku", "4"), ("haiku", "4.5"), ("haiku", "45"),
    # Claude 3.x 系列
    ("sonnet", "3"), ("sonnet", "3.5"), ("sonnet", "35"),
    ("opus", "3"), ("opus", "3.5"), ("opus", "35"),
    ("haiku", "3"), ("haiku", "3.5"), ("haiku", "35"),
}

# Antigravity 支持的 Gemini 模型
ANTIGRAVITY_GEMINI_MODELS = {
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
    "gemini-2.0-pro", "gemini-3-pro-preview", "gemini-pro",
    "gemini-1.5-pro", "gemini-1.5-flash",
}


def extract_claude_model_info(model: str) -> tuple:
    """
    从 Claude 模型名称中提取核心信息

    支持各种命名格式：
    - claude-4.5-sonnet-thinking
    - claude-sonnet-4.5
    - claude-3-5-sonnet-20241022
    - claude-opus-4
    等等

    Returns:
        (model_type, version) 如 ("sonnet", "4.5") 或 (None, None)
    """
    model_lower = model.lower().replace("claude-", "").replace("claude", "")

    # 移除常见后缀
    suffixes_to_remove = [
        "-thinking", "-extended", "-preview", "-latest",
        "-20241022", "-20240620", "-20250101", "-20250514",
    ]
    for suffix in suffixes_to_remove:
        model_lower = model_lower.replace(suffix, "")

    # 移除日期后缀 (格式: -YYYYMMDD)
    import re
    model_lower = re.sub(r'-\d{8}$', '', model_lower)

    # 提取型号 (sonnet, opus, haiku)
    model_type = None
    for t in ["sonnet", "opus", "haiku"]:
        if t in model_lower:
            model_type = t
            break

    if not model_type:
        return (None, None)

    # 提取版本号
    # 尝试匹配各种版本格式: 4.5, 4, 3.5, 3-5, 35 等
    version = None

    # 移除型号名称，剩下的应该包含版本
    remaining = model_lower.replace(model_type, "").replace("-", "").replace(".", "")

    # 常见版本映射
    version_patterns = {
        "45": "4.5", "4": "4", "40": "4",
        "35": "3.5", "3": "3", "30": "3",
        "25": "2.5", "2": "2", "20": "2",
    }

    for pattern, ver in version_patterns.items():
        if pattern in remaining:
            version = ver
            break

    # 如果没找到版本，尝试从原始字符串提取
    if not version:
        version_match = re.search(r'(\d+\.?\d*)', model_lower)
        if version_match:
            version = version_match.group(1)

    return (model_type, version)


def is_claude_supported_by_antigravity(model: str) -> bool:
    """
    检查 Claude 模型是否被 Antigravity 支持

    使用灵活匹配策略，支持各种命名变体
    """
    model_type, version = extract_claude_model_info(model)

    if not model_type:
        # 无法识别型号，保守起见返回 True（让 Antigravity 尝试）
        return True

    if not version:
        # 有型号但无版本，假设是最新版本，返回 True
        return True

    # 检查是否在支持列表中
    # 规范化版本号进行比较
    normalized_version = version.replace(".", "")

    for supported_type, supported_ver in ANTIGRAVITY_CLAUDE_MODELS:
        if model_type == supported_type:
            supported_normalized = supported_ver.replace(".", "")
            if normalized_version == supported_normalized:
                return True

    # 如果版本号匹配任何已知版本的前缀，也认为支持
    # 例如 "4" 匹配 "4.5"
    for supported_type, supported_ver in ANTIGRAVITY_CLAUDE_MODELS:
        if model_type == supported_type:
            if version.startswith(supported_ver.split(".")[0]):
                return True

    return False


def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表"""
    enabled_backends = [(k, v) for k, v in BACKENDS.items() if v.get("enabled", True)]
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])


def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端

    路由策略（Antigravity 优先，节省 Copilot 次数）：
    1. GPT/O1/O3 系列 -> Copilot（专属，Antigravity 不支持）
    2. Gemini 系列 -> Antigravity（专属）
    3. Claude 系列 -> Antigravity 优先（灵活匹配）
    4. 其他模型 -> 默认优先级（Antigravity 优先）
    """
    model_lower = model.lower()

    # GPT 系列 -> Copilot（专属）
    for prefix in COPILOT_EXCLUSIVE_PREFIXES:
        if model_lower.startswith(prefix):
            log.info(f"[Gateway] Model {model} is GPT/O series -> routing to Copilot (exclusive)")
            return "copilot"

    # Gemini 系列 -> Antigravity
    if model_lower.startswith("gemini"):
        log.info(f"[Gateway] Model {model} is Gemini series -> routing to Antigravity")
        return "antigravity"

    # Claude 系列 -> Antigravity 优先
    if model_lower.startswith("claude"):
        model_type, version = extract_claude_model_info(model)
        log.info(f"[Gateway] Claude model detected: {model} -> type={model_type}, version={version}")

        # 灵活匹配：只要能识别出型号，就路由到 Antigravity
        # Antigravity 会处理具体的模型映射
        if model_type:
            log.info(f"[Gateway] Model {model} is Claude {model_type} -> routing to Antigravity")
            return "antigravity"
        else:
            # 无法识别型号但是 Claude 前缀，仍然尝试 Antigravity
            log.info(f"[Gateway] Model {model} is unknown Claude variant -> trying Antigravity first")
            return "antigravity"

    # 其他模型 -> 默认使用 Antigravity（节省 Copilot 次数）
    log.info(f"[Gateway] Model {model} -> routing to Antigravity (default)")
    return "antigravity"'''

if 'extract_claude_model_info' in content:
    print("[SKIP] Smart Claude routing already implemented")
elif old_section in content:
    content = content.replace(old_section, new_section)
    print("[OK] Implemented smart Claude model routing")
else:
    print("[WARN] Could not find exact section pattern")
    # Try to find key markers
    if 'ANTIGRAVITY_SUPPORTED_MODELS' in content and 'def get_backend_for_model' in content:
        print("[INFO] Found key functions, attempting partial replacement...")

        # Find start and end positions
        start_marker = '# Antigravity 实际支持的模型列表'
        end_marker = 'def calculate_retry_delay'

        start_pos = content.find(start_marker)
        end_pos = content.find(end_marker)

        if start_pos != -1 and end_pos != -1:
            content = content[:start_pos] + new_section + '\n\n\n' + content[end_pos:]
            print("[OK] Replaced section via markers")
        else:
            print("[FAIL] Could not find section markers")

# Write back
with open(gateway_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[SUCCESS] Smart model routing implemented!")
print("\nNew routing strategy:")
print("  - GPT/O1/O3 -> Copilot (exclusive, Antigravity doesn't support)")
print("  - Gemini -> Antigravity (exclusive)")
print("  - Claude -> Antigravity (flexible matching, saves Copilot quota)")
print("  - Other -> Antigravity (default, saves Copilot quota)")
print("\nClaude model matching now supports:")
print("  - claude-4.5-sonnet-thinking")
print("  - claude-sonnet-4.5")
print("  - claude-3-5-sonnet-20241022")
print("  - Any variant with sonnet/opus/haiku + version number")
