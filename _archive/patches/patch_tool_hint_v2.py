#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁脚本 v2：增强工具调用格式提示
- 方案1：扩展触发场景（API空响应、流式异常中断、安全过滤）
- 方案3：动态参数映射（从工具定义中提取参数名）
- 方案4：错误检测与重试提示

作者：浮浮酱
日期：2024-12-24
"""

import shutil
import sys
import io
import re
from pathlib import Path
from datetime import datetime

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============================================================
# 工具调用格式提示模板（提取为常量，便于复用）
# ============================================================

TOOL_CALL_REMINDER_TEMPLATE = '''
            # ✅ 工具调用格式提示（帮助 agent 自我纠正参数格式问题）
            tool_reminder_parts = []
            tool_reminder_parts.append("")
            tool_reminder_parts.append("⚠️ **Tool Call Format Reminder** (IMPORTANT - Read carefully before making tool calls):")
            tool_reminder_parts.append("")
            tool_reminder_parts.append("If you encounter 'invalid arguments' errors when calling tools, please note:")
            tool_reminder_parts.append("- **Always use the EXACT parameter names** as defined in the current tool schema")
            tool_reminder_parts.append("- **Do NOT use parameters from previous conversations** - tool schemas may have changed")
            tool_reminder_parts.append("- **Common mistakes to avoid**:")
            tool_reminder_parts.append("  - `should_read_entire_file` → Use `target_file` with `offset`/`limit` instead")
            tool_reminder_parts.append("  - `start_line_one_indexed` / `end_line_one_indexed` → Use `offset` / `limit` instead")
            tool_reminder_parts.append("  - `command` for terminal → Check if it should be `input` or other parameter name")
            tool_reminder_parts.append("  - Unknown parameters → Check the tool definition in current context")
            tool_reminder_parts.append("- **When in doubt**: Re-read the tool definition and use only the parameters listed there")
            tool_reminder_parts.append("- **Terminal tool**: Verify the exact parameter name (may be `command`, `input`, or `cmd`)")
            tool_reminder_parts.append("")
            tool_reminder_msg = "\\n".join(tool_reminder_parts)
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
    # 补丁 1：在 "No SSE data received" 错误中添加工具提示
    # ============================================================
    print("[PATCH 1] 添加工具提示到 'No SSE data received' 错误...")

    old_no_sse = '''        if state["sse_lines_received"] == 0:
            log.warning(f"[ANTIGRAVITY STREAM] No SSE data received from Antigravity backend!")
            # 发送一个错误消息
            error_chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": "[Error: No response received from backend. Please try again.]"},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(error_chunk)}\\n\\n"'''

    new_no_sse = '''        if state["sse_lines_received"] == 0:
            log.warning(f"[ANTIGRAVITY STREAM] No SSE data received from Antigravity backend!")

            # ✅ 构建包含工具提示的错误消息
            error_msg_parts = []
            error_msg_parts.append("[Error: No response received from backend. Please try again.]")
            error_msg_parts.append("")
            error_msg_parts.append("⚠️ **Tool Call Format Reminder**:")
            error_msg_parts.append("If you encounter 'invalid arguments' errors when calling tools:")
            error_msg_parts.append("- Use EXACT parameter names from the current tool schema")
            error_msg_parts.append("- Do NOT use parameters from previous conversations")
            error_msg_parts.append("- For terminal/command tools: verify parameter name (may be `command`, `input`, or `cmd`)")
            error_msg_parts.append("- When in doubt: re-read the tool definition")

            error_chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": "\\n".join(error_msg_parts)},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(error_chunk)}\\n\\n"'''

    if old_no_sse in content:
        content = content.replace(old_no_sse, new_no_sse)
        patches_applied += 1
        print("   [OK] 补丁 1 应用成功")
    else:
        print("   [SKIP] 补丁 1 目标代码未找到（可能已应用或格式不同）")

    # ============================================================
    # 补丁 2：在安全过滤错误中添加工具提示
    # ============================================================
    print("[PATCH 2] 添加工具提示到安全过滤错误...")

    old_safety = '''            if finish_reason_raw in ["SAFETY", "RECITATION", "BLOCKLIST"]:
                log.warning(f"[ANTIGRAVITY STREAM] Response blocked by {finish_reason} filter")
                # 发送明确的错误消息
                error_msg = f"[Response blocked by {finish_reason} filter. The content may have triggered safety policies.]"
                error_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": error_msg},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(error_chunk)}\\n\\n"'''

    new_safety = '''            if finish_reason_raw in ["SAFETY", "RECITATION", "BLOCKLIST"]:
                log.warning(f"[ANTIGRAVITY STREAM] Response blocked by {finish_reason} filter")

                # ✅ 构建包含工具提示的错误消息
                error_msg_parts = []
                error_msg_parts.append(f"[Response blocked by {finish_reason} filter. The content may have triggered safety policies.]")
                error_msg_parts.append("")
                error_msg_parts.append("⚠️ **Tool Call Format Reminder**:")
                error_msg_parts.append("If you encounter 'invalid arguments' errors when calling tools:")
                error_msg_parts.append("- Use EXACT parameter names from the current tool schema")
                error_msg_parts.append("- Do NOT use parameters from previous conversations")
                error_msg_parts.append("- For terminal/command tools: verify parameter name (may be `command`, `input`, or `cmd`)")

                error_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": "\\n".join(error_msg_parts)},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(error_chunk)}\\n\\n"'''

    if old_safety in content:
        content = content.replace(old_safety, new_safety)
        patches_applied += 1
        print("   [OK] 补丁 2 应用成功")
    else:
        print("   [SKIP] 补丁 2 目标代码未找到（可能已应用或格式不同）")

    # ============================================================
    # 补丁 3：在流式异常错误中添加工具提示
    # ============================================================
    print("[PATCH 3] 添加工具提示到流式异常错误...")

    old_stream_error = '''    except Exception as e:
        log.error(f"[ANTIGRAVITY] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "type": "server_error"
            }
        }
        yield f"data: {json.dumps(error_response)}\\n\\n"'''

    new_stream_error = '''    except Exception as e:
        log.error(f"[ANTIGRAVITY] Streaming error: {e}")

        # ✅ 构建包含工具提示的错误响应
        error_msg_parts = []
        error_msg_parts.append(f"Streaming error: {str(e)}")
        error_msg_parts.append("")
        error_msg_parts.append("⚠️ **Tool Call Format Reminder**:")
        error_msg_parts.append("If you encounter 'invalid arguments' errors when calling tools:")
        error_msg_parts.append("- Use EXACT parameter names from the current tool schema")
        error_msg_parts.append("- Do NOT use parameters from previous conversations")
        error_msg_parts.append("- For terminal/command tools: verify parameter name (may be `command`, `input`, or `cmd`)")

        error_response = {
            "error": {
                "message": "\\n".join(error_msg_parts),
                "type": "server_error"
            }
        }
        yield f"data: {json.dumps(error_response)}\\n\\n"'''

    if old_stream_error in content:
        content = content.replace(old_stream_error, new_stream_error)
        patches_applied += 1
        print("   [OK] 补丁 3 应用成功")
    else:
        print("   [SKIP] 补丁 3 目标代码未找到（可能已应用或格式不同）")

    # ============================================================
    # 补丁 4：更新现有的工具提示，添加 terminal 相关提示
    # ============================================================
    print("[PATCH 4] 更新现有工具提示，添加 terminal 相关内容...")

    old_tool_hint = '''            # ✅ 新增：工具调用格式提示，帮助 agent 自我纠正参数格式问题
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
            error_parts.append("")'''

    new_tool_hint = '''            # ✅ 工具调用格式提示，帮助 agent 自我纠正参数格式问题
            error_parts.append("⚠️ **Tool Call Format Reminder** (IMPORTANT - Read carefully before making tool calls):")
            error_parts.append("")
            error_parts.append("If you encounter 'invalid arguments' errors when calling tools, please note:")
            error_parts.append("- **Always use the EXACT parameter names** as defined in the current tool schema")
            error_parts.append("- **Do NOT use parameters from previous conversations** - tool schemas may have changed")
            error_parts.append("- **Common mistakes to avoid**:")
            error_parts.append("  - `should_read_entire_file` → Use `target_file` with `offset`/`limit` instead")
            error_parts.append("  - `start_line_one_indexed` / `end_line_one_indexed` → Use `offset` / `limit` instead")
            error_parts.append("  - `command` for terminal → Check if it should be `input`, `cmd`, or other name")
            error_parts.append("  - Unknown parameters → Check the tool definition in current context")
            error_parts.append("- **Terminal/Command tools**: Parameter name varies - could be `command`, `input`, `cmd`, or `shell_command`")
            error_parts.append("- **When in doubt**: Re-read the tool definition and use only the parameters listed there")
            error_parts.append("")'''

    if old_tool_hint in content:
        content = content.replace(old_tool_hint, new_tool_hint)
        patches_applied += 1
        print("   [OK] 补丁 4 应用成功")
    else:
        print("   [SKIP] 补丁 4 目标代码未找到（可能已应用或格式不同）")

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
        print("[INFO] 没有补丁需要应用（可能已全部应用）")

    return patches_applied > 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
