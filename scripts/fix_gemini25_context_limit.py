#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：将 Gemini 2.5 系列的上下文限制从 128K 改为 1M

问题：context_truncation.py 中 Gemini 2.5 系列配置错误
- 当前配置：128K 上下文
- 正确配置：1M 上下文（与 Gemini 2.0/3.0 一致）

运行方式：
    python fix_gemini25_context_limit.py
"""

import os
import re
import shutil
from datetime import datetime

# 文件路径
TARGET_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "context_truncation.py")
TARGET_FILE = os.path.abspath(TARGET_FILE)


def backup_file(filepath: str) -> str:
    """创建文件备份"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.bak_{timestamp}"
    shutil.copy2(filepath, backup_path)
    print(f"[BACKUP] 已创建备份: {backup_path}")
    return backup_path


def apply_fixes(content: str) -> str:
    """应用修复：将 Gemini 2.5 系列上下文限制从 128K 改为 1M"""

    # 修复1: 修改注释
    content = content.replace(
        "# Gemini 2.5 系列：128K 上下文",
        "# Gemini 2.5 系列：1M 上下文（与 Gemini 2.0/3.0 一致）"
    )
    print("[FIX 1] 已修改 Gemini 2.5 注释")

    # 修复2: 修改 gemini-2.5 配置
    old_gemini25 = '"gemini-2.5": (128000, 0.80),'
    new_gemini25 = '"gemini-2.5": (1000000, 0.70),  # 1M * 0.70 = 700K 安全限制'
    if old_gemini25 in content:
        content = content.replace(old_gemini25, new_gemini25)
        print("[FIX 2] 已修改 gemini-2.5 配置: 128K -> 1M")
    else:
        # 尝试带注释的版本
        pattern = r'"gemini-2\.5": \(128000, 0\.80\),.*'
        if re.search(pattern, content):
            content = re.sub(pattern, new_gemini25, content)
            print("[FIX 2] 已修改 gemini-2.5 配置（正则匹配）: 128K -> 1M")
        else:
            print("[WARNING] 未找到 gemini-2.5 配置，可能已修改")

    # 修复3: 修改 gemini-2.5-flash 配置
    old_flash = '"gemini-2.5-flash": (128000, 0.85),'
    new_flash = '"gemini-2.5-flash": (1000000, 0.75),  # 1M * 0.75 = 750K 安全限制'
    if old_flash in content:
        content = content.replace(old_flash, new_flash)
        print("[FIX 3] 已修改 gemini-2.5-flash 配置: 128K -> 1M")
    else:
        pattern = r'"gemini-2\.5-flash": \(128000, 0\.85\),.*'
        if re.search(pattern, content):
            content = re.sub(pattern, new_flash, content)
            print("[FIX 3] 已修改 gemini-2.5-flash 配置（正则匹配）: 128K -> 1M")
        else:
            print("[WARNING] 未找到 gemini-2.5-flash 配置，可能已修改")

    # 修复4: 修改 gemini-2.5-pro 配置
    old_pro = '"gemini-2.5-pro": (128000, 0.80),'
    new_pro = '"gemini-2.5-pro": (1000000, 0.70),  # 1M * 0.70 = 700K 安全限制'
    if old_pro in content:
        content = content.replace(old_pro, new_pro)
        print("[FIX 4] 已修改 gemini-2.5-pro 配置: 128K -> 1M")
    else:
        pattern = r'"gemini-2\.5-pro": \(128000, 0\.80\),.*'
        if re.search(pattern, content):
            content = re.sub(pattern, new_pro, content)
            print("[FIX 4] 已修改 gemini-2.5-pro 配置（正则匹配）: 128K -> 1M")
        else:
            print("[WARNING] 未找到 gemini-2.5-pro 配置，可能已修改")

    return content


def main():
    print("=" * 60)
    print("修复脚本：Gemini 2.5 系列上下文限制修正")
    print("修改内容：128K -> 1M (与 Gemini 2.0/3.0 一致)")
    print("=" * 60)

    # 检查文件是否存在
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] 文件不存在: {TARGET_FILE}")
        return False

    print(f"[INFO] 目标文件: {TARGET_FILE}")

    # 创建备份
    backup_path = backup_file(TARGET_FILE)

    try:
        # 读取文件内容
        with open(TARGET_FILE, 'r', encoding='utf-8') as f:
            content = f.read()

        print(f"[INFO] 文件大小: {len(content):,} 字符")

        # 应用修复
        new_content = apply_fixes(content)

        # 检查是否有修改
        if new_content == content:
            print("[INFO] 文件内容未更改（可能已经应用过修复）")
            return True

        # 写入修改后的内容
        with open(TARGET_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"[SUCCESS] 文件已修改，新大小: {len(new_content):,} 字符")
        print(f"[INFO] 如需回滚，请使用备份文件: {backup_path}")

        return True

    except Exception as e:
        print(f"[ERROR] 修改失败: {e}")
        print(f"[INFO] 正在从备份恢复...")
        shutil.copy2(backup_path, TARGET_FILE)
        print(f"[INFO] 已从备份恢复")
        return False


if __name__ == "__main__":
    success = main()
    print("=" * 60)
    print(f"修复结果: {'成功' if success else '失败'}")
    print("=" * 60)
