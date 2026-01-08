#!/usr/bin/env python3
"""
[FIX 2026-01-08 Part2] thinkingBudget vs max_tokens 关系修复补丁

问题：
当 thinkingBudget >= max_tokens 时，下游会将 budget 调整为 max_tokens - 1
这会导致几乎所有 token 都给 thinking，只留 1 个 token 给实际输出

解决方案：
确保 max_tokens >= thinkingBudget + MIN_OUTPUT_TOKENS

使用方法：
    python patch_thinking_budget.py
"""

import shutil
from datetime import datetime
from pathlib import Path

TARGET_FILE = Path(__file__).parent / "antigravity_anthropic_router.py"
BACKUP_SUFFIX = datetime.now().strftime(".bak.%Y%m%d_%H%M%S")

# 要查找的原始代码
OLD_CODE = '''    # [FIX 2026-01-08] 设置最小 max_tokens 保护
    # Cursor 等客户端可能设置较小的 max_tokens（如 4096），导致长文本生成被截断
    # 对于需要生成长文本（如研究报告）的场景，自动提升到更合理的值
    MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值
    original_max_tokens = max_tokens
    if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
        max_tokens = MIN_MAX_TOKENS
        payload["max_tokens"] = max_tokens
        log.info(
            f"[ANTHROPIC] max_tokens 自动提升: {original_max_tokens} -> {max_tokens} "
            f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
        )'''

# 替换后的新代码
NEW_CODE = '''    # [FIX 2026-01-08] 设置最小 max_tokens 保护
    # Cursor 等客户端可能设置较小的 max_tokens（如 4096），导致长文本生成被截断
    # 对于需要生成长文本（如研究报告）的场景，自动提升到更合理的值
    MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值（无 thinking 时）
    MIN_OUTPUT_TOKENS = 16384  # 最小输出 token 数（有 thinking 时，确保实际输出有足够空间）
    original_max_tokens = max_tokens

    # [FIX 2026-01-08 Part2] 当启用 thinking 时，确保 max_tokens 足够容纳 thinkingBudget + 实际输出
    # 问题：如果 thinkingBudget >= max_tokens，下游会将 budget 调整为 max_tokens - 1
    # 这会导致几乎所有 token 都给 thinking，只留 1 个 token 给实际输出
    thinking_budget = 0
    if thinking_present and isinstance(thinking_value, dict):
        thinking_budget = thinking_value.get("budget_tokens", 0) or 0
        if isinstance(thinking_budget, int) and thinking_budget > 0:
            # 确保 max_tokens >= thinkingBudget + MIN_OUTPUT_TOKENS
            required_max_tokens = thinking_budget + MIN_OUTPUT_TOKENS
            if isinstance(max_tokens, int) and max_tokens < required_max_tokens:
                max_tokens = required_max_tokens
                payload["max_tokens"] = max_tokens
                log.info(
                    f"[ANTHROPIC] max_tokens 因 thinking 自动提升: {original_max_tokens} -> {max_tokens} "
                    f"(thinkingBudget={thinking_budget}, MIN_OUTPUT_TOKENS={MIN_OUTPUT_TOKENS})"
                )

    # 无 thinking 时的基础保护
    if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
        max_tokens = MIN_MAX_TOKENS
        payload["max_tokens"] = max_tokens
        log.info(
            f"[ANTHROPIC] max_tokens 自动提升: {original_max_tokens} -> {max_tokens} "
            f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
        )'''


def main():
    if not TARGET_FILE.exists():
        print(f"[ERROR] Target file not found: {TARGET_FILE}")
        return False

    # 读取文件内容
    content = TARGET_FILE.read_text(encoding="utf-8")

    # 检查是否已经应用过补丁
    if "MIN_OUTPUT_TOKENS" in content:
        print("[OK] Patch already applied, no action needed")
        return True

    # 检查原始代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] Target code block not found, file may have been modified")
        print("Please manually check antigravity_anthropic_router.py")
        return False

    # 创建备份
    backup_path = TARGET_FILE.with_suffix(TARGET_FILE.suffix + BACKUP_SUFFIX)
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] Created: {backup_path.name}")

    # 应用补丁
    new_content = content.replace(OLD_CODE, NEW_CODE)
    TARGET_FILE.write_text(new_content, encoding="utf-8")

    print("[SUCCESS] Patch applied!")
    print("   - Added MIN_OUTPUT_TOKENS = 16384")
    print("   - When thinking is enabled: max_tokens >= thinkingBudget + MIN_OUTPUT_TOKENS")
    print("   - Example: thinkingBudget=31999 -> max_tokens=48383 (31999+16384)")

    return True


if __name__ == "__main__":
    main()
