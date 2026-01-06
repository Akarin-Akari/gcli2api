#!/usr/bin/env python3
"""
修复模型名称映射问题

问题：用户请求的模型名如 claude-3-5-haiku-20241022 无法被 Copilot API 识别
原因：Copilot API 需要特定的模型ID格式，如 claude-haiku-4.5
解决：在发送请求到 Copilot 之前，转换模型名称为 Copilot 能识别的格式

Copilot 支持的模型:
- Claude: claude-sonnet-4, claude-sonnet-4.5, claude-opus-4.5, claude-haiku-4.5
- GPT: gpt-5, gpt-5-mini, gpt-5.1, gpt-5.2, gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4
- Gemini: gemini-3-pro-preview, gemini-3-flash-preview, gemini-2.5-pro
"""

import re

# 模型名称映射函数代码
MODEL_MAPPING_CODE = '''
# ==================== Copilot 模型名称映射 ====================
# Copilot API 需要特定格式的模型ID

COPILOT_MODEL_MAPPING = {
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

    # Gemini 系列
    "gemini-2.5-pro-latest": "gemini-2.5-pro",
    "gemini-2.5-pro-preview": "gemini-2.5-pro",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3-flash": "gemini-3-flash-preview",
}


def map_model_for_copilot(model: str) -> str:
    """
    将模型名称映射为 Copilot API 能识别的格式

    Args:
        model: 原始模型名称

    Returns:
        Copilot 能识别的模型ID
    """
    if not model:
        return "gpt-4o"  # 默认模型

    model_lower = model.lower()

    # 移除常见后缀进行匹配
    base_model = model_lower
    for suffix in ["-thinking", "-think", "-extended", "-preview", "-latest",
                   "-20241022", "-20240620", "-20250101", "-20250514"]:
        base_model = base_model.replace(suffix, "")

    # 移除日期后缀
    base_model = re.sub(r'-\\d{8}$', '', base_model).strip("-")

    # 1. 直接匹配原始名称
    if model_lower in COPILOT_MODEL_MAPPING:
        return COPILOT_MODEL_MAPPING[model_lower]

    # 2. 匹配去除后缀的名称
    if base_model in COPILOT_MODEL_MAPPING:
        return COPILOT_MODEL_MAPPING[base_model]

    # 3. 智能模糊匹配 Claude 模型
    if "claude" in model_lower:
        # 检测模型类型
        if "haiku" in model_lower:
            return "claude-haiku-4.5"
        elif "opus" in model_lower:
            return "claude-opus-4.5"
        elif "sonnet" in model_lower:
            # 检查版本号
            if "4.5" in model_lower or "45" in model_lower:
                return "claude-sonnet-4.5"
            else:
                return "claude-sonnet-4"
        else:
            # 默认 Claude -> sonnet
            return "claude-sonnet-4"

    # 4. 智能模糊匹配 GPT 模型
    if "gpt" in model_lower:
        if "5.2" in model_lower:
            return "gpt-5.2"
        elif "5.1" in model_lower:
            if "codex" in model_lower:
                if "mini" in model_lower:
                    return "gpt-5.1-codex-mini"
                elif "max" in model_lower:
                    return "gpt-5.1-codex-max"
                return "gpt-5.1-codex"
            return "gpt-5.1"
        elif "gpt-5" in model_lower or "gpt5" in model_lower:
            if "mini" in model_lower:
                return "gpt-5-mini"
            return "gpt-5"
        elif "4.1" in model_lower or "41" in model_lower:
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

    # 5. 智能模糊匹配 Gemini 模型
    if "gemini" in model_lower:
        if "3" in model_lower:
            if "flash" in model_lower:
                return "gemini-3-flash-preview"
            return "gemini-3-pro-preview"
        elif "2.5" in model_lower:
            return "gemini-2.5-pro"
        else:
            return "gemini-2.5-pro"  # 默认

    # 6. O1/O3 模型 (如果 Copilot 支持)
    if model_lower.startswith("o1") or model_lower.startswith("o3"):
        # 目前 Copilot 可能不支持，返回原名尝试
        return model

    # 7. 返回原始模型名（可能 Copilot 直接支持）
    return model

'''

def main():
    file_path = "F:/antigravity2api/gcli2api/src/unified_gateway_router.py"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 在 ANTIGRAVITY_SUPPORTED_PATTERNS 之前添加模型映射代码
    marker = "ANTIGRAVITY_SUPPORTED_PATTERNS = {"
    if marker in content and "COPILOT_MODEL_MAPPING" not in content:
        content = content.replace(marker, MODEL_MAPPING_CODE + "\n\n" + marker)
        print("[OK] Added COPILOT_MODEL_MAPPING and map_model_for_copilot function")
    else:
        if "COPILOT_MODEL_MAPPING" in content:
            print("[SKIP] COPILOT_MODEL_MAPPING already exists")
        else:
            print("[ERROR] Could not find insertion marker")
            return

    # 2. 在 proxy_request_to_backend 函数中应用模型映射
    # 找到发送请求的地方，在发送到 copilot 时映射模型名

    # 查找 proxy_request_to_backend 函数并在其中添加模型映射逻辑
    old_pattern = '''    # 构建完整URL
    url = f"{backend_config['base_url']}{endpoint}"'''

    new_pattern = '''    # 对 Copilot 后端应用模型名称映射
    if backend_key == "copilot" and body and isinstance(body, dict) and "model" in body:
        original_model = body.get("model", "")
        mapped_model = map_model_for_copilot(original_model)
        if mapped_model != original_model:
            log.info(f"[Gateway] Mapping model for Copilot: {original_model} -> {mapped_model}")
            body = {**body, "model": mapped_model}

    # 构建完整URL
    url = f"{backend_config['base_url']}{endpoint}"'''

    if old_pattern in content and "Mapping model for Copilot" not in content:
        content = content.replace(old_pattern, new_pattern)
        print("[OK] Added model mapping in proxy_request_to_backend")
    else:
        if "Mapping model for Copilot" in content:
            print("[SKIP] Model mapping already exists in proxy_request_to_backend")
        else:
            print("[WARNING] Could not find proxy_request_to_backend insertion point")

    # 保存文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("\n[SUCCESS] Model name mapping added!")
    print("\nThis fix handles model name translation for Copilot API:")
    print("  - claude-3-5-haiku-20241022 -> claude-haiku-4.5")
    print("  - claude-3.5-sonnet -> claude-sonnet-4")
    print("  - gpt-4-turbo -> gpt-4-0125-preview")
    print("  - And many more...")

if __name__ == "__main__":
    main()
