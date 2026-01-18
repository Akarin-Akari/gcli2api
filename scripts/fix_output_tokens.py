#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 tool_converter.py 中的输出 token 限制
[FIX 2026-01-11] 提高 MIN_OUTPUT_TOKENS 和 DEFAULT_MAX_OUTPUT_TOKENS

问题：
- MIN_OUTPUT_TOKENS = 4096 太小，写 MD 文档需要 10K-30K tokens
- 导致 finishReason=MAX_TOKENS，工具调用被截断

解决方案：
- MIN_OUTPUT_TOKENS: 4096 -> 16384
- DEFAULT_MAX_OUTPUT_TOKENS: 16384 -> 32768
"""

import os
import shutil
from datetime import datetime

# 目标文件
TARGET_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "converters", "tool_converter.py")
TARGET_FILE = os.path.abspath(TARGET_FILE)

# 备份文件
BACKUP_FILE = TARGET_FILE + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# 旧内容
OLD_CONTENT = '''# ==================== 双向限制策略常量（移植自 anthropic_converter.py）====================
# [FIX 2026-01-10] 双向限制策略：既要保证足够的输出空间，又不能让 max_tokens 过大触发 429
MAX_ALLOWED_TOKENS = 65535   # max_tokens 的绝对上限（Claude 最大值）
MIN_OUTPUT_TOKENS = 4096     # 实际输出的最小保障空间
DEFAULT_MAX_OUTPUT_TOKENS = 16384  # 默认的 maxOutputTokens（当客户端未指定时）'''

# 新内容
NEW_CONTENT = '''# ==================== 双向限制策略常量（移植自 anthropic_converter.py）====================
# [FIX 2026-01-10] 双向限制策略：既要保证足够的输出空间，又不能让 max_tokens 过大触发 429
# [FIX 2026-01-11] 提高输出空间限制，支持写长文档（MD文档可能需要 10K-30K tokens）
MAX_ALLOWED_TOKENS = 65535   # max_tokens 的绝对上限（Claude 最大值）
MIN_OUTPUT_TOKENS = 16384    # 实际输出的最小保障空间（4096 -> 16384，支持长文档输出）
DEFAULT_MAX_OUTPUT_TOKENS = 32768  # 默认的 maxOutputTokens（16384 -> 32768，确保足够的输出空间）'''


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
        # 检查是否已经是新内容
        if "MIN_OUTPUT_TOKENS = 16384" in content:
            print("Detected new values already in place, no modification needed")
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
    print("- MIN_OUTPUT_TOKENS: 4096 -> 16384 (4x increase)")
    print("- DEFAULT_MAX_OUTPUT_TOKENS: 16384 -> 32768 (2x increase)")
    print("\nEffect:")
    print("- Thinking mode: output space 4096 -> 16384 tokens")
    print("- Non-thinking mode: output space 16384 -> 32768 tokens")
    print("- Sufficient for writing 10K-30K token MD documents")

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
