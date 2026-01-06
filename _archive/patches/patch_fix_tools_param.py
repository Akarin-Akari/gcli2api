# -*- coding: utf-8 -*-
"""
补丁脚本：修复工具参数提取逻辑
问题：extract_tool_params_summary 需要接收工具定义(tools)，而不是工具调用结果(tool_calls)
"""
import sys
import io
import shutil
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

PATCHES = [
    # 补丁1：修改函数签名，添加 tools 参数
    {
        "name": "修改函数签名，添加 tools 参数",
        "old": 'def openai_messages_to_antigravity_contents(messages: List[Any], enable_thinking: bool = False) -> List[Dict[str, Any]]:',
        "new": 'def openai_messages_to_antigravity_contents(messages: List[Any], enable_thinking: bool = False, tools: Optional[List[Any]] = None) -> List[Dict[str, Any]]:'
    },
    # 补丁2：修复工具参数提取逻辑
    {
        "name": "修复工具参数提取逻辑",
        "old": """            if has_tools:
                # 提取工具参数摘要
                tool_params = extract_tool_params_summary(messages[0].tool_calls if hasattr(messages[0], 'tool_calls') else [])

                # 如果没有从 messages 中提取到，尝试使用全局 tools（如果可用）
                if not tool_params:
                    tool_params_section = "Check the tool definitions in your context for exact parameter names."
                else:
                    tool_params_section = f"Current tool parameters (use EXACTLY these names):\\n{tool_params}\"""",
        "new": """            if has_tools:
                # 提取工具参数摘要（从传入的 tools 参数中提取）
                tool_params = extract_tool_params_summary(tools) if tools else ""

                if not tool_params:
                    tool_params_section = "Check the tool definitions in your context for exact parameter names."
                else:
                    tool_params_section = tool_params"""
    },
    # 补丁3：修改第一个调用点，传入 tools 参数
    {
        "name": "修改第一个调用点，传入 tools 参数",
        "old": "        contents = openai_messages_to_antigravity_contents(messages, enable_thinking=enable_thinking)",
        "new": "        contents = openai_messages_to_antigravity_contents(messages, enable_thinking=enable_thinking, tools=tools)"
    },
    # 补丁4：修改第二个调用点，传入 tools 参数
    {
        "name": "修改第二个调用点，传入 tools 参数",
        "old": "                            contents = openai_messages_to_antigravity_contents(messages, enable_thinking=False)",
        "new": "                            contents = openai_messages_to_antigravity_contents(messages, enable_thinking=False, tools=tools)"
    }
]

def main():
    print(f"[READ] 读取文件: {TARGET_FILE}")

    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 备份
    backup_file = TARGET_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(TARGET_FILE, backup_file)
    print(f"[BACKUP] 备份到: {backup_file}")

    applied = 0
    for patch in PATCHES:
        if patch["old"] in content:
            content = content.replace(patch["old"], patch["new"], 1)
            print(f"   [OK] {patch['name']}")
            applied += 1
        else:
            print(f"   [SKIP] {patch['name']} - 目标未找到")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[SUCCESS] 共应用 {applied} 个补丁!")

if __name__ == "__main__":
    main()
