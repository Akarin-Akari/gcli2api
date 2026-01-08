#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：清理死代码 _get_fallback_model
这个函数使用了未定义的 MODEL_FALLBACK_CHAIN 变量，且从未被调用

执行方式: python fix_dead_code.py
"""

import os
import shutil
from datetime import datetime

# 配置
TARGET_FILE = "src/antigravity_anthropic_router.py"
BACKUP_DIR = "_archive/backups"

def create_backup(file_path: str) -> str:
    """创建备份文件"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{os.path.basename(file_path)}.bak.{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    shutil.copy2(file_path, backup_path)
    print(f"[OK] Created backup: {backup_path}")
    return backup_path


def fix_dead_code():
    """删除死代码 _get_fallback_model 函数"""

    # 读取原文件
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 创建备份
    create_backup(TARGET_FILE)

    # 死代码：使用了未定义的 MODEL_FALLBACK_CHAIN
    dead_code = '''def _get_fallback_model(current_model: str) -> str | None:
    """获取降级模型"""
    fallback_list = MODEL_FALLBACK_CHAIN.get(current_model, [])
    return fallback_list[0] if fallback_list else None


'''

    if dead_code in content:
        content = content.replace(dead_code, '')
        print("[OK] Removed dead code: _get_fallback_model function")
        print("     - This function used undefined MODEL_FALLBACK_CHAIN variable")
        print("     - It was never called (actual code uses _get_fallback_models)")
    else:
        print("[WARN] Dead code not found or already removed")

    # 写回文件
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("")
    print("[DONE] antigravity_anthropic_router.py cleanup completed!")


if __name__ == "__main__":
    print("=" * 60)
    print("[FIX] gcli2api Dead Code Cleanup Script")
    print("=" * 60)
    print("")

    try:
        fix_dead_code()
        print("")
        print("=" * 60)
        print("[SUCCESS] Dead code removed!")
        print("=" * 60)
    except Exception as e:
        print(f"[ERROR] Fix failed: {e}")
        import traceback
        traceback.print_exc()
