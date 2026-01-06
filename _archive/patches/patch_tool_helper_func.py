# -*- coding: utf-8 -*-
"""
补丁脚本：添加工具参数提取辅助函数
"""
import sys
import io
import os
import shutil
from datetime import datetime

# 设置输出编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

# 要添加的辅助函数
HELPER_FUNCTION = '''
def extract_tool_params_summary(tools: Optional[List[Any]]) -> str:
    """
    从工具定义中提取参数摘要，用于注入到System Prompt
    帮助模型了解当前正确的工具参数名
    """
    if not tools:
        return ""

    # 重点关注的常用工具
    important_tools = ["read", "read_file", "terminal", "run_terminal_command",
                       "write", "edit", "bash", "str_replace_editor", "execute_command"]

    summaries = []

    for tool in tools:
        try:
            # 获取工具名
            if hasattr(tool, "function"):
                func = tool.function
                tool_name = getattr(func, "name", None) or (func.get("name") if isinstance(func, dict) else None)
                params = getattr(func, "parameters", None) or (func.get("parameters") if isinstance(func, dict) else None)
            elif isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                tool_name = func.get("name")
                params = func.get("parameters")
            else:
                continue

            if not tool_name:
                continue

            # 只处理重要工具或名称中包含关键词的工具
            is_important = any(imp in tool_name.lower() for imp in important_tools)
            if not is_important:
                continue

            # 提取参数名
            if params and isinstance(params, dict):
                properties = params.get("properties", {})
                required = params.get("required", [])

                if properties:
                    param_list = []
                    for param_name in properties.keys():
                        if param_name in required:
                            param_list.append(f"`{param_name}` (required)")
                        else:
                            param_list.append(f"`{param_name}`")

                    if param_list:
                        summaries.append(f"- {tool_name}: {', '.join(param_list)}")
        except Exception:
            continue

    if summaries:
        return "\\n\\nCurrent tool parameters (use ONLY these exact names):\\n" + "\\n".join(summaries)
    return ""


'''

# 搜索目标位置
SEARCH_PATTERN = "def convert_openai_tools_to_antigravity(tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:"

def main():
    print(f"[READ] 读取文件: {TARGET_FILE}")

    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经添加过
    if "def extract_tool_params_summary" in content:
        print("[SKIP] 辅助函数已存在，无需添加")
        return

    # 查找插入位置
    if SEARCH_PATTERN not in content:
        print(f"[ERROR] 未找到目标函数定义")
        return

    # 在目标函数之前插入辅助函数
    new_content = content.replace(SEARCH_PATTERN, HELPER_FUNCTION + SEARCH_PATTERN)

    # 备份
    backup_file = TARGET_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(TARGET_FILE, backup_file)
    print(f"[BACKUP] 备份到: {backup_file}")

    # 写入
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 辅助函数添加成功!")

if __name__ == "__main__":
    main()
