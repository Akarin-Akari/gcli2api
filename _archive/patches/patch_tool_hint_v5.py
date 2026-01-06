#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁脚本 v5：添加工具格式提示常量
"""

import shutil
import sys
import io
from pathlib import Path
from datetime import datetime

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    file_path = Path(__file__).parent / "antigravity_router.py"
    backup_path = file_path.with_suffix(f".py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    # 备份原文件
    print(f"[BACKUP] 备份原文件到: {backup_path}")
    shutil.copy2(file_path, backup_path)

    # 读取文件内容
    print(f"[READ] 读取文件: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    patches_applied = 0

    # ============================================================
    # 补丁：添加工具格式提示常量定义
    # ============================================================
    print("[PATCH] 添加工具格式提示常量...")

    old_import = '''from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request'''

    new_import = '''from typing import Any, Dict, List, Optional

# ============================================================
# 工具调用格式提示常量（用于预防性注入到 System Prompt）
# ============================================================

TOOL_FORMAT_REMINDER = """

[IMPORTANT - Tool Call Format Rules]
When calling tools, you MUST follow these rules strictly:
1. Always use the EXACT parameter names as defined in the current tool schema
2. Do NOT use parameter names from previous conversations - schemas may have changed
3. For terminal/command tools: the parameter name varies (could be `command`, `input`, `cmd`, or `shell_command`) - check the tool definition
4. Common parameter mistakes to avoid:
   - `should_read_entire_file` is INVALID -> use `target_file` with `offset`/`limit`
   - `start_line_one_indexed` / `end_line_one_indexed` are INVALID -> use `offset` / `limit`
5. When in doubt: re-read the tool definition and use ONLY the parameters listed there
"""

TOOL_FORMAT_REMINDER_AFTER_ERROR = """

[CRITICAL - Tool Call Error Detected]
Previous tool calls failed due to invalid arguments. You MUST:
1. STOP using parameter names from previous conversations
2. Re-read the current tool definition carefully
3. Use ONLY the exact parameter names shown in the tool schema
4. For terminal tools: verify the exact parameter name (may be `command`, `input`, `cmd`, or `shell_command`)
5. Do NOT guess parameter names - if unsure, check the tool definition first
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Request'''

    if old_import in content and "TOOL_FORMAT_REMINDER" not in content:
        content = content.replace(old_import, new_import)
        patches_applied += 1
        print("   [OK] 补丁应用成功")
    elif "TOOL_FORMAT_REMINDER" in content:
        print("   [SKIP] 常量已存在")
    else:
        print("   [SKIP] 目标代码未找到")

    # ============================================================
    # 写入文件
    # ============================================================
    if patches_applied > 0:
        print(f"[WRITE] 写入修改后的文件: {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[SUCCESS] 共应用 {patches_applied} 个补丁!")
        print(f"   备份文件: {backup_path}")
    else:
        print("[INFO] 没有补丁需要应用")

    return patches_applied > 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
