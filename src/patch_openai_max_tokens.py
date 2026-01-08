#!/usr/bin/env python3
"""
[FIX 2026-01-08] OpenAI 格式路由 max_tokens 保护补丁

问题：
之前的修复只针对 Anthropic 格式路由 (/antigravity/v1/messages)
OpenAI 格式路由 (/antigravity/v1/chat/completions) 没有 max_tokens 保护
导致 Cursor 使用 OpenAI 格式时仍然会遇到 MAX_TOKENS 问题

解决方案：
将 Anthropic 格式的 max_tokens 保护逻辑同步到 OpenAI 格式

使用方法：
    python patch_openai_max_tokens.py
"""

import shutil
from datetime import datetime
from pathlib import Path

TARGET_FILE = Path(__file__).parent / "antigravity_router.py"
BACKUP_SUFFIX = datetime.now().strftime(".bak.%Y%m%d_%H%M%S")

# 要查找的原始代码
OLD_CODE = '''    # 生成配置参数
    parameters = {
        "temperature": getattr(request_data, "temperature", None),
        "top_p": getattr(request_data, "top_p", None),
        "max_tokens": getattr(request_data, "max_tokens", None),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    # 使用更新后的 enable_thinking（可能已被禁用）
    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)'''

# 替换后的新代码
NEW_CODE = '''    # 生成配置参数
    parameters = {
        "temperature": getattr(request_data, "temperature", None),
        "top_p": getattr(request_data, "top_p", None),
        "max_tokens": getattr(request_data, "max_tokens", None),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    # [FIX 2026-01-08] 同步 Anthropic 格式的 max_tokens 保护逻辑
    # Cursor 等客户端可能设置较小的 max_tokens（如 4096），导致长文本生成被截断
    MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值（无 thinking 时）
    MIN_OUTPUT_TOKENS = 16384  # 最小输出 token 数（有 thinking 时）
    original_max_tokens = parameters.get("max_tokens")

    # 当启用 thinking 时，确保 max_tokens 足够容纳 thinkingBudget + 实际输出
    # generate_generation_config 中硬编码 thinkingBudget=1024，但我们需要预留更多空间
    if enable_thinking:
        # 假设 thinkingBudget 可能很大（用户可能通过其他方式设置）
        # 为了安全起见，确保 max_tokens 至少为 MIN_MAX_TOKENS + MIN_OUTPUT_TOKENS
        thinking_budget_estimate = 32000  # 估算最大 thinking budget
        required_max_tokens = thinking_budget_estimate + MIN_OUTPUT_TOKENS
        current_max = parameters.get("max_tokens", 0) or 0
        if isinstance(current_max, int) and current_max < required_max_tokens:
            parameters["max_tokens"] = required_max_tokens
            log.info(
                f"[ANTIGRAVITY] max_tokens 因 thinking 自动提升: {original_max_tokens} -> {required_max_tokens} "
                f"(thinking_budget_estimate={thinking_budget_estimate}, MIN_OUTPUT_TOKENS={MIN_OUTPUT_TOKENS})"
            )
    else:
        # 无 thinking 时的基础保护
        current_max = parameters.get("max_tokens", 0) or 0
        if isinstance(current_max, int) and current_max < MIN_MAX_TOKENS:
            parameters["max_tokens"] = MIN_MAX_TOKENS
            log.info(
                f"[ANTIGRAVITY] max_tokens 自动提升: {original_max_tokens} -> {MIN_MAX_TOKENS} "
                f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
            )

    # 使用更新后的 enable_thinking（可能已被禁用）
    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)'''


def main():
    if not TARGET_FILE.exists():
        print(f"[ERROR] Target file not found: {TARGET_FILE}")
        return False

    # 读取文件内容
    content = TARGET_FILE.read_text(encoding="utf-8")

    # 检查是否已经应用过补丁
    if "MIN_OUTPUT_TOKENS" in content and "thinking_budget_estimate" in content:
        print("[OK] Patch already applied, no action needed")
        return True

    # 检查原始代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] Target code block not found, file may have been modified")
        print("Please manually check antigravity_router.py around line 1829-1839")
        return False

    # 创建备份
    backup_path = TARGET_FILE.with_suffix(TARGET_FILE.suffix + BACKUP_SUFFIX)
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] Created: {backup_path.name}")

    # 应用补丁
    new_content = content.replace(OLD_CODE, NEW_CODE)
    TARGET_FILE.write_text(new_content, encoding="utf-8")

    print("[SUCCESS] Patch applied to OpenAI format route!")
    print("   - Added MIN_MAX_TOKENS = 16384")
    print("   - Added MIN_OUTPUT_TOKENS = 16384")
    print("   - When thinking enabled: max_tokens = 32000 + 16384 = 48384")
    print("   - When thinking disabled: max_tokens >= 16384")

    return True


if __name__ == "__main__":
    main()
