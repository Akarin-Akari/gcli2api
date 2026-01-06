#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁脚本：在错误提示 prompt 中添加工具调用格式提示
帮助 Cursor agent 自我纠正参数格式问题
"""

import shutil
import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def main():
    file_path = Path(__file__).parent / "antigravity_router.py"
    backup_path = file_path.with_suffix(".py.bak")

    # 备份原文件
    print(f"[BACKUP] 备份原文件到: {backup_path}")
    shutil.copy2(file_path, backup_path)

    # 读取文件内容
    print(f"[READ] 读取文件: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 要查找的原始代码
    old_code = '''            error_parts.append("")
            error_parts.append("💡 **Action Required**: You need to compress the context before retrying:")
            error_parts.append("")
            error_parts.append("1. **Summarize tool results**: Extract only essential information (errors, summaries, key findings)")
            error_parts.append("2. **Remove old tool results**: Keep only the most recent and relevant tool results")
            error_parts.append("3. **Truncate large results**: For large tool results, keep only the beginning and end, or extract key sections")
            error_parts.append("4. **Reduce tool calls**: Use fewer tool calls in the next request if possible")
            error_parts.append("")
            if cached_content_token_count > 0:'''

    # 新代码（添加工具调用格式提示）
    new_code = '''            error_parts.append("")
            error_parts.append("💡 **Action Required**: You need to compress the context before retrying:")
            error_parts.append("")
            error_parts.append("1. **Summarize tool results**: Extract only essential information (errors, summaries, key findings)")
            error_parts.append("2. **Remove old tool results**: Keep only the most recent and relevant tool results")
            error_parts.append("3. **Truncate large results**: For large tool results, keep only the beginning and end, or extract key sections")
            error_parts.append("4. **Reduce tool calls**: Use fewer tool calls in the next request if possible")
            error_parts.append("")

            # ✅ 新增：工具调用格式提示，帮助 agent 自我纠正参数格式问题
            error_parts.append("⚠️ **Tool Call Format Reminder** (IMPORTANT - Read carefully before making tool calls):")
            error_parts.append("")
            error_parts.append("If you encounter 'invalid arguments' errors when calling tools, please note:")
            error_parts.append("- **Always use the EXACT parameter names** as defined in the current tool schema")
            error_parts.append("- **Do NOT use parameters from previous conversations** - tool schemas may have changed")
            error_parts.append("- **Common mistakes to avoid**:")
            error_parts.append("  - `should_read_entire_file` → Use `target_file` with `offset`/`limit` instead")
            error_parts.append("  - `start_line_one_indexed` / `end_line_one_indexed` → Use `offset` / `limit` instead")
            error_parts.append("  - Unknown parameters → Check the tool definition in current context")
            error_parts.append("- **When in doubt**: Re-read the tool definition and use only the parameters listed there")
            error_parts.append("")

            if cached_content_token_count > 0:'''

    # 检查是否找到目标代码
    if old_code not in content:
        print("[ERROR] 未找到目标代码，可能文件已被修改或格式不同")
        print("请手动检查 antigravity_router.py 中的错误消息构建部分")
        return False

    # 替换代码
    new_content = content.replace(old_code, new_code)

    # 写入文件
    print(f"[WRITE] 写入修改后的文件: {file_path}")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 补丁应用成功!")
    print(f"   备份文件: {backup_path}")
    print("   新增内容: 工具调用格式提示 (Tool Call Format Reminder)")
    return True

if __name__ == "__main__":
    main()
