#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 anthropic_converter.py - 添加 max_tokens 下限保护
[FIX 2026-01-11] 当 thinking=False 时，客户端传来的 max_tokens=4096 没有被保护

问题：
- 第753-763行只有上限保护（65535），没有下限保护
- 客户端（如Cursor）可能传来 max_tokens=4096
- 导致输出被截断

解决方案：
- 添加下限保护：max_tokens < 16384 时自动提升
"""

import os
import shutil
from datetime import datetime

# 目标文件
TARGET_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "anthropic_converter.py")
TARGET_FILE = os.path.abspath(TARGET_FILE)

# 备份文件
BACKUP_FILE = TARGET_FILE + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# 旧内容
OLD_CONTENT = '''    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        # 🐛 修复：添加上限保护，防止过大的 max_tokens 导致 Antigravity API 返回 429
        # 参考 gemini_router.py 和 openai_router.py 的上限设置
        MAX_OUTPUT_TOKENS_LIMIT = 65535
        if isinstance(max_tokens, int) and max_tokens > MAX_OUTPUT_TOKENS_LIMIT:
            log.warning(
                f"[ANTHROPIC CONVERTER] maxOutputTokens 超过上限: {max_tokens} -> {MAX_OUTPUT_TOKENS_LIMIT}"
            )
            max_tokens = MAX_OUTPUT_TOKENS_LIMIT
        config["maxOutputTokens"] = max_tokens'''

# 新内容
NEW_CONTENT = '''    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        # 🐛 修复：添加上限保护，防止过大的 max_tokens 导致 Antigravity API 返回 429
        # 参考 gemini_router.py 和 openai_router.py 的上限设置
        MAX_OUTPUT_TOKENS_LIMIT = 65535
        if isinstance(max_tokens, int) and max_tokens > MAX_OUTPUT_TOKENS_LIMIT:
            log.warning(
                f"[ANTHROPIC CONVERTER] maxOutputTokens 超过上限: {max_tokens} -> {MAX_OUTPUT_TOKENS_LIMIT}"
            )
            max_tokens = MAX_OUTPUT_TOKENS_LIMIT

        # [FIX 2026-01-11] 添加下限保护，防止客户端（如Cursor）传来过小的 max_tokens 导致输出被截断
        # 写 MD 文档可能需要 10K-30K tokens，4096 远远不够
        MIN_OUTPUT_TOKENS_FLOOR = 16384  # 最小输出空间保障
        if isinstance(max_tokens, int) and max_tokens < MIN_OUTPUT_TOKENS_FLOOR:
            log.info(
                f"[ANTHROPIC CONVERTER] maxOutputTokens 低于下限: {max_tokens} -> {MIN_OUTPUT_TOKENS_FLOOR}"
            )
            max_tokens = MIN_OUTPUT_TOKENS_FLOOR

        config["maxOutputTokens"] = max_tokens'''


def main():
    print(f"Target file: {TARGET_FILE}")

    # 检查文件是否存在
    if not os.path.exists(TARGET_FILE):
        print(f"Error: File not found - {TARGET_FILE}")
        return False

    # 读取原文件
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否包含旧内容
    if OLD_CONTENT not in content:
        print("Warning: Old content not found, may have been modified already")
        # 检查是否已经有下限保护
        if "MIN_OUTPUT_TOKENS_FLOOR" in content:
            print("Detected MIN_OUTPUT_TOKENS_FLOOR already in place, no modification needed")
            return True
        print("Please check file content manually")
        return False

    # 创建备份
    shutil.copy2(TARGET_FILE, BACKUP_FILE)
    print(f"Backup created: {BACKUP_FILE}")

    # 替换内容
    new_content = content.replace(OLD_CONTENT, NEW_CONTENT)

    # 写入新内容
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("Modification successful!")
    print("\nChanges:")
    print("- Added MIN_OUTPUT_TOKENS_FLOOR = 16384")
    print("- When max_tokens < 16384, automatically raise to 16384")
    print("\nEffect:")
    print("- Even when thinking=False, output space is guaranteed >= 16384 tokens")
    print("- Cursor's default max_tokens=4096 will be raised to 16384")
    print("- Sufficient for writing 10K-30K token MD documents")

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
