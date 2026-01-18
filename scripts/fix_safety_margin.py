#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 context_truncation.py 中的安全边际系数
[FIX 2026-01-11] 降低 safety_margin 以触发截断逻辑

问题：
- 当前 claude-opus 的 safety_margin=0.80，动态阈值=143,616 tokens
- 用户输入 127,858 tokens < 143,616，截断逻辑未触发
- 导致 MAX_TOKENS 错误

解决方案：
- 降低 safety_margin 到 0.50-0.55
- 新动态阈值 = 200K * 0.50 - 16384 = 83,616 tokens
- 127,858 > 83,616，截断逻辑将触发
"""

import os
import shutil
from datetime import datetime

# 目标文件
TARGET_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "context_truncation.py")
TARGET_FILE = os.path.abspath(TARGET_FILE)

# 备份文件
BACKUP_FILE = TARGET_FILE + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# 旧内容（需要替换的部分）
OLD_CONTENT = '''# [FIX 2026-01-10] 动态阈值调整：根据模型类型设置不同的上下文限制
# 模型系列 -> (上下文限制, 安全边际系数)
MODEL_CONTEXT_LIMITS = {
    # Claude 系列：200K 上下文
    "claude": (200000, 0.80),      # 200K * 0.80 = 160K 安全限制
    "claude-opus": (200000, 0.80),
    "claude-sonnet": (200000, 0.80),
    "claude-haiku": (200000, 0.85),  # Haiku 更轻量，可以更激进'''

# 新内容
NEW_CONTENT = '''# [FIX 2026-01-10] 动态阈值调整：根据模型类型设置不同的上下文限制
# [FIX 2026-01-11] 降低安全边际，为思考模式大量输出预留更多空间
# 模型系列 -> (上下文限制, 安全边际系数)
MODEL_CONTEXT_LIMITS = {
    # Claude 系列：200K 上下文
    # 思考模式输出可能高达 40K+，需要预留更多输出空间
    "claude": (200000, 0.55),      # 200K * 0.55 = 110K 安全限制，预留 90K 给输出
    "claude-opus": (200000, 0.50), # Opus thinking 需要更多输出空间
    "claude-sonnet": (200000, 0.55),
    "claude-haiku": (200000, 0.65),  # Haiku 输出较少，可以更激进'''


def main():
    print(f"目标文件: {TARGET_FILE}")

    # 检查文件是否存在
    if not os.path.exists(TARGET_FILE):
        print(f"错误: 文件不存在 - {TARGET_FILE}")
        return False

    # 读取原文件
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否包含旧内容
    if OLD_CONTENT not in content:
        print("警告: 未找到需要替换的内容，可能已经修改过了")
        # 检查是否已经是新内容
        if "0.55" in content and "0.50" in content:
            print("检测到已经是新的安全边际值，无需修改")
            return True
        print("请手动检查文件内容")
        return False

    # 创建备份
    shutil.copy2(TARGET_FILE, BACKUP_FILE)
    print(f"已创建备份: {BACKUP_FILE}")

    # 替换内容
    new_content = content.replace(OLD_CONTENT, NEW_CONTENT)

    # 写入新内容
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("✅ 修改成功！")
    print("\n修改内容:")
    print("- claude: 0.80 -> 0.55 (动态阈值: 160K -> 93.6K)")
    print("- claude-opus: 0.80 -> 0.50 (动态阈值: 143.6K -> 83.6K)")
    print("- claude-sonnet: 0.80 -> 0.55 (动态阈值: 143.6K -> 93.6K)")
    print("- claude-haiku: 0.85 -> 0.65 (动态阈值: 153.6K -> 113.6K)")

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
