#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁脚本 v4：预防性工具调用格式提示
- 方案2：在 System Prompt 中注入预防性工具格式提示
- 方案4增强：检测历史消息中的 "invalid arguments" 错误，动态注入纠正提示

作者：浮浮酱
日期：2024-12-24
"""

import shutil
import sys
import io
from pathlib import Path
from datetime import datetime

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============================================================
# 工具调用格式提示常量（用于注入到 System Prompt）
# ============================================================

TOOL_FORMAT_REMINDER = '''

[IMPORTANT - Tool Call Format Rules]
When calling tools, you MUST follow these rules strictly:
1. Always use the EXACT parameter names as defined in the current tool schema
2. Do NOT use parameter names from previous conversations - schemas may have changed
3. For terminal/command tools: the parameter name varies (could be `command`, `input`, `cmd`, or `shell_command`) - check the tool definition
4. Common parameter mistakes to avoid:
   - `should_read_entire_file` is INVALID → use `target_file` with `offset`/`limit`
   - `start_line_one_indexed` / `end_line_one_indexed` are INVALID → use `offset` / `limit`
5. When in doubt: re-read the tool definition and use ONLY the parameters listed there
'''

TOOL_FORMAT_REMINDER_AFTER_ERROR = '''

[CRITICAL - Tool Call Error Detected]
Previous tool calls failed due to invalid arguments. You MUST:
1. STOP using parameter names from previous conversations
2. Re-read the current tool definition carefully
3. Use ONLY the exact parameter names shown in the tool schema
4. For terminal tools: verify the exact parameter name (may be `command`, `input`, `cmd`, or `shell_command`)
5. Do NOT guess parameter names - if unsure, check the tool definition first
'''

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
    # 补丁 1：添加工具格式提示常量定义
    # ============================================================
    print("[PATCH 1] 添加工具格式提示常量...")

    # 找到合适的位置插入常量（在文件开头的 import 之后）
    import_marker = "from typing import List, Dict, Any, Optional, Union, AsyncGenerator"

    constant_definition = '''from typing import List, Dict, Any, Optional, Union, AsyncGenerator

# ============================================================
# 工具调用格式提示常量（用于预防性注入）
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
"""'''

    if import_marker in content and "TOOL_FORMAT_REMINDER" not in content:
        content = content.replace(import_marker, constant_definition)
        patches_applied += 1
        print("   [OK] 补丁 1 应用成功")
    elif "TOOL_FORMAT_REMINDER" in content:
        print("   [SKIP] 补丁 1 已存在")
    else:
        print("   [SKIP] 补丁 1 目标代码未找到")

    # ============================================================
    # 补丁 2：在消息转换函数中检测错误并注入提示
    # ============================================================
    print("[PATCH 2] 添加错误检测和预防性提示注入...")

    old_system_handling = '''    contents = []
    system_messages = []

    for i, msg in enumerate(messages):
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        # 处理 system 消息 - 合并到第一条用户消息
        if role == "system":
            system_messages.append(content)
            continue'''

    new_system_handling = '''    contents = []
    system_messages = []

    # ✅ 方案2+4：检测历史消息中是否有工具调用错误，决定是否注入强化提示
    has_tool_error = False
    has_tools = False  # 检测是否有工具调用

    for msg in messages:
        msg_content = getattr(msg, "content", "")
        msg_tool_calls = getattr(msg, "tool_calls", None)

        # 检测是否有工具调用
        if msg_tool_calls:
            has_tools = True

        # 检测错误模式
        if msg_content and isinstance(msg_content, str):
            error_patterns = [
                "invalid arguments",
                "Invalid arguments",
                "invalid parameters",
                "Invalid parameters",
                "Unexpected parameters",
                "unexpected parameters",
                "model provided invalid",
                "Tool call arguments",
                "were invalid",
            ]
            for pattern in error_patterns:
                if pattern in msg_content:
                    has_tool_error = True
                    log.info(f"[ANTIGRAVITY] Detected tool error pattern in message: '{pattern}'")
                    break
        if has_tool_error:
            break

    for i, msg in enumerate(messages):
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        # 处理 system 消息 - 合并到第一条用户消息
        if role == "system":
            # ✅ 方案2：在 system 消息末尾注入工具格式提示
            if has_tools:
                if has_tool_error:
                    # 检测到错误，注入强化提示
                    content = content + TOOL_FORMAT_REMINDER_AFTER_ERROR
                    log.info("[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER_AFTER_ERROR into system message")
                else:
                    # 预防性注入基础提示
                    content = content + TOOL_FORMAT_REMINDER
                    log.debug("[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER into system message")
            system_messages.append(content)
            continue'''

    if old_system_handling in content:
        content = content.replace(old_system_handling, new_system_handling)
        patches_applied += 1
        print("   [OK] 补丁 2 应用成功")
    else:
        print("   [SKIP] 补丁 2 目标代码未找到（可能已应用或格式不同）")

    # ============================================================
    # 写入文件
    # ============================================================
    if patches_applied > 0:
        print(f"[WRITE] 写入修改后的文件: {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[SUCCESS] 共应用 {patches_applied} 个补丁!")
        print(f"   备份文件: {backup_path}")
        print("")
        print("[INFO] 新增功能:")
        print("   - 方案2: 预防性提示 - 当检测到工具调用时，自动在 system prompt 中注入格式提示")
        print("   - 方案4增强: 错误检测 - 当检测到历史消息中有 'invalid arguments' 错误时，注入强化提示")
    else:
        print("[INFO] 没有补丁需要应用（可能已全部应用）")

    return patches_applied > 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
