#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修复 antigravity_router.py 中缺失的 get_last_signature_with_text 导入

问题：第三次修复时使用了 get_last_signature_with_text 函数，但忘记在顶部导入。
修复：在 signature_cache 导入语句中添加该函数。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-12
"""

import os
import shutil
from datetime import datetime

# 目标文件路径
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

# 旧的导入语句
OLD_IMPORT = "from .signature_cache import get_cached_signature, cache_signature"

# 新的导入语句（添加 get_last_signature_with_text）
NEW_IMPORT = "from .signature_cache import get_cached_signature, cache_signature, get_last_signature_with_text"


def main():
    print(f"[FIX] 开始修复导入语句: {TARGET_FILE}")

    # 读取文件内容
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查新导入是否已存在
    if "get_last_signature_with_text" in content.split('\n')[16]:  # 第17行（0索引为16）
        print("[INFO] get_last_signature_with_text 已在导入语句中，无需修复。")
        return True

    # 检查旧导入是否存在
    if OLD_IMPORT not in content:
        print("[ERROR] 未找到原始导入语句！")
        print(f"[DEBUG] 期望找到: {OLD_IMPORT}")
        # 尝试更宽松的匹配
        if "from .signature_cache import" in content:
            print("[INFO] 找到 signature_cache 导入，但格式不同")
            # 显示当前的导入行
            for i, line in enumerate(content.split('\n')[:30]):
                if 'signature_cache' in line:
                    print(f"[DEBUG] 第 {i+1} 行: {line}")
        return False

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET_FILE + f".bak_{timestamp}"
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] 已创建备份: {backup_path}")

    # 替换导入语句
    new_content = content.replace(OLD_IMPORT, NEW_IMPORT)

    # 写入新内容
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 导入语句修复完成！")
    print(f"[INFO] 已添加: get_last_signature_with_text")

    # 验证
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        verify_content = f.read()

    if NEW_IMPORT in verify_content:
        print("[VERIFY] 验证通过！导入语句已正确添加。")
        return True
    else:
        print("[ERROR] 验证失败！")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
