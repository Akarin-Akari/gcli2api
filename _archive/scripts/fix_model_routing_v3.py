#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix model routing based on actual Antigravity supported models.

Antigravity ONLY supports:
- Gemini 3 series: gemini-3-pro (high/low), gemini-3-flash
- Claude 4.5 series: claude-sonnet-4.5, claude-sonnet-4.5-thinking, claude-opus-4.5-thinking
- GPT: gpt-oos-120b (medium)

Everything else -> Copilot
"""

gateway_path = 'src/unified_gateway_router.py'

with open(gateway_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the model routing section
old_section_start = '# ==================== 智能模型路由 ===================='
old_section_end = 'def calculate_retry_delay'

start_pos = content.find(old_section_start)
end_pos = content.find(old_section_end)

if start_pos == -1 or end_pos == -1:
    print("[FAIL] Could not find section markers")
    exit(1)

new_section = '''# ==================== 智能模型路由 ====================
# 策略：根据 Antigravity 实际支持的模型精确路由
# Antigravity 按 token 计费，Copilot 按次计费（用一次少一次）

# Antigravity 实际支持的模型（精确列表）
# 基于用户提供的信息：
# - Gemini 3 系列: gemini-3-pro (high/low), gemini-3-flash
# - Claude 4.5 系列: claude-sonnet-4.5, claude-sonnet-4.5-thinking, claude-opus-4.5-thinking
# - GPT: gpt-oos-120b (medium)

ANTIGRAVITY_SUPPORTED_PATTERNS = {
    # Gemini 3 系列 - 只支持 3 系列
    "gemini-3", "gemini3",
    # Claude 4.5 系列 - 只支持 4.5 版本的 sonnet 和 opus
    "claude-sonnet-4.5", "claude-4.5-sonnet", "claude-45-sonnet",
    "claude-opus-4.5", "claude-4.5-opus", "claude-45-opus",
    # GPT OOS
    "gpt-oos",
}

# 用于提取模型核心信息的辅助函数
def normalize_model_name(model: str) -> str:
    """规范化模型名称，移除变体后缀"""
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
    import re
    model_lower = re.sub(r'-\d{8}$', '', model_lower)

    return model_lower.strip("-")


def is_antigravity_supported(model: str) -> bool:
    """
    检查模型是否被 Antigravity 支持

    Antigravity 只支持：
    - Gemini 3 系列 (gemini-3-pro, gemini-3-flash)
    - Claude 4.5 系列 (sonnet-4.5, opus-4.5)
    - GPT OOS 120B
    """
    normalized = normalize_model_name(model)
    model_lower = model.lower()

    # 检查 Gemini - 只支持 3 系列
    if "gemini" in model_lower:
        # 检查是否是 Gemini 3
        if "gemini-3" in normalized or "gemini3" in normalized:
            return True
        # 其他 Gemini 版本（2.5, 2.0, 1.5 等）不支持
        return False

    # 检查 Claude - 只支持 4.5 系列的 sonnet 和 opus
    if "claude" in model_lower:
        # 提取版本号
        # 支持格式: claude-sonnet-4.5, claude-4.5-sonnet, claude-45-sonnet 等
        has_45 = any(x in normalized for x in ["4.5", "45", "-4-5-"])
        has_sonnet_or_opus = "sonnet" in normalized or "opus" in normalized

        if has_45 and has_sonnet_or_opus:
            return True

        # 其他 Claude 版本不支持
        return False

    # 检查 GPT OOS
    if "gpt-oos" in model_lower or "gptoos" in model_lower:
        return True

    # 其他模型都不支持
    return False


def get_sorted_backends() -> List[Tuple[str, Dict]]:
    """获取按优先级排序的后端列表"""
    enabled_backends = [(k, v) for k, v in BACKENDS.items() if v.get("enabled", True)]
    return sorted(enabled_backends, key=lambda x: x[1]["priority"])


def get_backend_for_model(model: str) -> Optional[str]:
    """
    根据模型名称获取指定后端

    路由策略：
    1. 检查是否在 Antigravity 支持列表中
    2. 支持 -> Antigravity（按 token 计费，更经济）
    3. 不支持 -> Copilot（按次计费，但支持更多模型）

    Antigravity 支持的模型：
    - Gemini 3 系列: gemini-3-pro, gemini-3-flash
    - Claude 4.5 系列: claude-sonnet-4.5, claude-opus-4.5 (含 thinking 变体)
    - GPT: gpt-oos-120b
    """
    if is_antigravity_supported(model):
        log.info(f"[Gateway] Model {model} is supported by Antigravity -> routing to Antigravity")
        return "antigravity"
    else:
        log.info(f"[Gateway] Model {model} is NOT supported by Antigravity -> routing to Copilot")
        return "copilot"


'''

# Replace the section
content = content[:start_pos] + new_section + content[end_pos:]

# Write back
with open(gateway_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] Updated model routing with precise Antigravity support list")
print("\n[SUCCESS] Model routing updated!")
print("\nAntigravity supported models:")
print("  - Gemini 3 series: gemini-3-pro, gemini-3-flash")
print("  - Claude 4.5 series: claude-sonnet-4.5, claude-opus-4.5 (+ thinking variants)")
print("  - GPT: gpt-oos-120b")
print("\nEverything else -> Copilot")
print("\nExamples:")
print("  claude-4.5-sonnet-thinking -> Antigravity ✓")
print("  claude-sonnet-4 -> Copilot (only 4.5 supported)")
print("  gemini-3-pro -> Antigravity ✓")
print("  gemini-2.5-pro -> Copilot (only 3 series supported)")
print("  gpt-4o -> Copilot")
print("  gpt-oos-120b -> Antigravity ✓")
