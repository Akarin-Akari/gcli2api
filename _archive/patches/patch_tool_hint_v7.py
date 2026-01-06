#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补丁脚本 v7：动态参数提取
- 方案3：从工具定义中提取参数名，直接告诉模型正确的参数

核心思路：不只是告诉模型"不要用错误参数"，而是直接告诉它"正确参数是什么"

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
    # 补丁 1：添加工具参数提取辅助函数
    # ============================================================
    print("[PATCH 1] 添加工具参数提取辅助函数...")

    # 在 convert_openai_tools_to_antigravity 函数之前添加辅助函数
    old_func_marker = '''def convert_openai_tools_to_antigravity(tools: List[Any]) -> Optional[List[Dict[str, Any]]]:'''

    new_func_with_helper = '''def extract_tool_params_summary(tools: List[Any]) -> str:
    """
    从工具定义中提取参数摘要，用于注入到 System Prompt
    帮助模型了解当前正确的工具参数名
    """
    if not tools:
        return ""

    # 重点关注的常用工具
    important_tools = ["read", "read_file", "terminal", "run_terminal_command", "write", "edit", "bash"]

    tool_summaries = []

    for tool in tools:
        try:
            # 获取工具信息
            if isinstance(tool, dict):
                tool_dict = tool
            elif hasattr(tool, "model_dump"):
                tool_dict = tool.model_dump()
            elif hasattr(tool, "dict"):
                tool_dict = tool.dict()
            else:
                continue

            # 提取工具名和参数
            tool_name = None
            params = {}

            # Case 1: function 类型
            if tool_dict.get("type") == "function" and "function" in tool_dict:
                func = tool_dict["function"]
                tool_name = func.get("name", "")
                func_params = func.get("parameters", {})
                if isinstance(func_params, dict):
                    params = func_params.get("properties", {})

            # Case 2: custom 类型
            elif tool_dict.get("type") == "custom" and "custom" in tool_dict:
                custom = tool_dict["custom"]
                tool_name = custom.get("name", "")
                input_schema = custom.get("input_schema", {})
                if isinstance(input_schema, dict):
                    params = input_schema.get("properties", {})

            # Case 3: 直接有 name 和 parameters
            elif "name" in tool_dict:
                tool_name = tool_dict.get("name", "")
                func_params = tool_dict.get("parameters", {}) or tool_dict.get("input_schema", {})
                if isinstance(func_params, dict):
                    params = func_params.get("properties", {})

            # 只处理重要工具
            if tool_name and any(imp in tool_name.lower() for imp in important_tools):
                param_names = list(params.keys()) if params else []
                if param_names:
                    # 标记必需参数
                    required = []
                    if isinstance(tool_dict.get("function", {}).get("parameters", {}), dict):
                        required = tool_dict["function"]["parameters"].get("required", [])
                    elif isinstance(tool_dict.get("parameters", {}), dict):
                        required = tool_dict["parameters"].get("required", [])
                    elif isinstance(tool_dict.get("input_schema", {}), dict):
                        required = tool_dict["input_schema"].get("required", [])

                    param_strs = []
                    for p in param_names[:5]:  # 最多显示5个参数
                        if p in required:
                            param_strs.append(f"`{p}` (required)")
                        else:
                            param_strs.append(f"`{p}`")

                    tool_summaries.append(f"- {tool_name}: {', '.join(param_strs)}")

        except Exception as e:
            continue

    if tool_summaries:
        return "\\n".join(tool_summaries[:10])  # 最多显示10个工具
    return ""


def convert_openai_tools_to_antigravity(tools: List[Any]) -> Optional[List[Dict[str, Any]]]:'''

    if old_func_marker in content and "extract_tool_params_summary" not in content:
        content = content.replace(old_func_marker, new_func_with_helper)
        patches_applied += 1
        print("   [OK] 补丁 1 应用成功")
    elif "extract_tool_params_summary" in content:
        print("   [SKIP] 补丁 1 已存在")
    else:
        print("   [SKIP] 补丁 1 目标代码未找到")

    # ============================================================
    # 补丁 2：更新 TOOL_FORMAT_REMINDER 模板，添加动态参数占位符
    # ============================================================
    print("[PATCH 2] 更新提示模板，支持动态参数...")

    old_reminder = '''    TOOL_FORMAT_REMINDER = """

[IMPORTANT - Tool Call Format Rules]
When calling tools, you MUST follow these rules strictly:
1. Always use the EXACT parameter names as defined in the current tool schema
2. Do NOT use parameter names from previous conversations - schemas may have changed
3. For terminal/command tools: the parameter name varies (could be `command`, `input`, `cmd`, or `shell_command`) - check the tool definition
4. Common parameter mistakes to avoid:
   - `should_read_entire_file` is INVALID -> use `target_file` with `offset`/`limit`
   - `start_line_one_indexed` / `end_line_one_indexed` are INVALID -> use `offset` / `limit`
5. When in doubt: re-read the tool definition and use ONLY the parameters listed there
"""'''

    new_reminder = '''    TOOL_FORMAT_REMINDER_TEMPLATE = """

[IMPORTANT - Tool Call Format Rules]
When calling tools, you MUST follow these rules strictly:
1. Always use the EXACT parameter names as defined in the current tool schema
2. Do NOT use parameter names from previous conversations - schemas may have changed
3. For terminal/command tools: the parameter name varies - check the tool definition
4. When in doubt: re-read the tool definition and use ONLY the parameters listed there

{tool_params_section}
"""'''

    if old_reminder in content:
        content = content.replace(old_reminder, new_reminder)
        patches_applied += 1
        print("   [OK] 补丁 2 应用成功")
    else:
        print("   [SKIP] 补丁 2 目标代码未找到（可能已应用）")

    # ============================================================
    # 补丁 3：更新 TOOL_FORMAT_REMINDER_AFTER_ERROR 模板
    # ============================================================
    print("[PATCH 3] 更新错误提示模板，支持动态参数...")

    old_error_reminder = '''    TOOL_FORMAT_REMINDER_AFTER_ERROR = """

[CRITICAL - Tool Call Error Detected]
Previous tool calls failed due to invalid arguments. You MUST:
1. STOP using parameter names from previous conversations
2. Re-read the current tool definition carefully
3. Use ONLY the exact parameter names shown in the tool schema
4. For terminal tools: verify the exact parameter name (may be `command`, `input`, `cmd`, or `shell_command`)
5. Do NOT guess parameter names - if unsure, check the tool definition first
"""'''

    new_error_reminder = '''    TOOL_FORMAT_REMINDER_AFTER_ERROR_TEMPLATE = """

[CRITICAL - Tool Call Error Detected]
Previous tool calls failed due to invalid arguments. You MUST:
1. STOP using parameter names from previous conversations
2. Use ONLY the exact parameter names shown below
3. Do NOT guess parameter names

{tool_params_section}

IMPORTANT: If a tool call fails, check the parameter names above and try again with the EXACT names listed.
"""'''

    if old_error_reminder in content:
        content = content.replace(old_error_reminder, new_error_reminder)
        patches_applied += 1
        print("   [OK] 补丁 3 应用成功")
    else:
        print("   [SKIP] 补丁 3 目标代码未找到（可能已应用）")

    # ============================================================
    # 补丁 4：更新提示注入逻辑，使用动态参数
    # ============================================================
    print("[PATCH 4] 更新提示注入逻辑...")

    old_injection = '''        # 处理 system 消息 - 合并到第一条用户消息
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

    new_injection = '''        # 处理 system 消息 - 合并到第一条用户消息
        if role == "system":
            # ✅ 方案2+3：在 system 消息末尾注入工具格式提示（包含动态参数）
            if has_tools:
                # 提取工具参数摘要
                tool_params = extract_tool_params_summary(messages[0].tool_calls if hasattr(messages[0], 'tool_calls') else [])

                # 如果没有从 messages 中提取到，尝试使用全局 tools（如果可用）
                if not tool_params:
                    tool_params_section = "Check the tool definitions in your context for exact parameter names."
                else:
                    tool_params_section = f"Current tool parameters (use EXACTLY these names):\\n{tool_params}"

                if has_tool_error:
                    # 检测到错误，注入强化提示
                    reminder = TOOL_FORMAT_REMINDER_AFTER_ERROR_TEMPLATE.format(tool_params_section=tool_params_section)
                    content = content + reminder
                    log.info(f"[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER_AFTER_ERROR with params into system message")
                else:
                    # 预防性注入基础提示
                    reminder = TOOL_FORMAT_REMINDER_TEMPLATE.format(tool_params_section=tool_params_section)
                    content = content + reminder
                    log.debug("[ANTIGRAVITY] Injected TOOL_FORMAT_REMINDER with params into system message")
            system_messages.append(content)
            continue'''

    if old_injection in content:
        content = content.replace(old_injection, new_injection)
        patches_applied += 1
        print("   [OK] 补丁 4 应用成功")
    else:
        print("   [SKIP] 补丁 4 目标代码未找到（可能已应用）")

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
        print("   - 方案3: 动态参数提取 - 从工具定义中提取参数名")
        print("   - 直接告诉模型正确的参数名，而不只是告诉它不要用错误的")
    else:
        print("[INFO] 没有补丁需要应用（可能已全部应用）")

    return patches_applied > 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
