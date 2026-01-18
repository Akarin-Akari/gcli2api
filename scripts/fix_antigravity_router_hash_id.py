#!/usr/bin/env python3
"""
[FIX 2026-01-12] 修复 antigravity_router.py 中的工具调用 ID 问题

问题：流式传输中每个 chunk 生成不同的随机 UUID，导致客户端无法正确拼接工具调用
解决：使用 MD5 哈希生成稳定 ID（ID = MD5(函数名 + 参数内容)）

修改位置：convert_to_openai_tool_call 函数（约第 244-265 行）
"""

import os
import shutil
from datetime import datetime

# 配置
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"
BACKUP_SUFFIX = datetime.now().strftime("_%Y%m%d_%H%M%S")

# 需要查找的旧代码
OLD_CODE = '''def convert_to_openai_tool_call(function_call: Dict[str, Any], index: int = None) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call，使用 OpenAIToolCall 模型

    Args:
        function_call: Antigravity 格式的函数调用
        index: 工具调用索引（流式响应必需）
    """
    tool_call = OpenAIToolCall(
        index=index,
        id=function_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
        type="function",
        function=OpenAIToolFunction(
            name=function_call.get("name", ""),
            arguments=json.dumps(function_call.get("args", {}))
        )
    )'''

# 替换为的新代码（使用 MD5 哈希生成稳定 ID）
NEW_CODE = '''def convert_to_openai_tool_call(function_call: Dict[str, Any], index: int = None) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call，使用 OpenAIToolCall 模型

    Args:
        function_call: Antigravity 格式的函数调用
        index: 工具调用索引（流式响应必需）
    """
    # [FIX 2026-01-12] 使用哈希生成确定性 ID，解决流式传输 ID 不一致导致客户端卡顿问题
    # 问题：随机 UUID 导致每个 chunk 的 tool_call.id 不同，客户端无法拼接
    # 解决：ID = MD5(函数名 + 参数内容)，确保同一工具调用的 ID 稳定一致
    import hashlib
    func_name = function_call.get("name", "")
    func_args = function_call.get("args", {})
    unique_string = f"{func_name}{json.dumps(func_args, sort_keys=True)}"
    hash_object = hashlib.md5(unique_string.encode())
    stable_call_id = f"call_{hash_object.hexdigest()[:24]}"

    tool_call = OpenAIToolCall(
        index=index,
        id=function_call.get("id", stable_call_id),  # 优先使用已有 ID，否则使用哈希 ID
        type="function",
        function=OpenAIToolFunction(
            name=func_name,
            arguments=json.dumps(func_args)
        )
    )'''


def main():
    print(f"=" * 60)
    print(f"[FIX] 修复 antigravity_router.py 工具调用 ID 问题")
    print(f"=" * 60)

    # 检查文件是否存在
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] 目标文件不存在: {TARGET_FILE}")
        return False

    # 创建备份
    backup_file = TARGET_FILE + ".bak" + BACKUP_SUFFIX
    print(f"[INFO] 创建备份: {backup_file}")
    shutil.copy2(TARGET_FILE, backup_file)

    # 读取文件内容
    print(f"[INFO] 读取文件: {TARGET_FILE}")
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 检查是否已经修复过
    if "使用哈希生成确定性 ID" in content:
        print(f"[WARN] 文件似乎已经包含修复代码，跳过修复")
        # 删除不需要的备份
        os.remove(backup_file)
        return True

    # 检查旧代码是否存在
    if OLD_CODE not in content:
        print(f"[ERROR] 未找到需要替换的代码块！")
        print(f"[DEBUG] 正在搜索函数定义...")

        # 尝试更宽松的匹配
        if "def convert_to_openai_tool_call" in content:
            print(f"[INFO] 找到函数定义，尝试定位...")
            # 找到函数开始位置
            start_idx = content.find("def convert_to_openai_tool_call")
            if start_idx != -1:
                # 显示周围的代码
                snippet = content[start_idx:start_idx + 500]
                print(f"[DEBUG] 函数代码片段:\n{snippet[:300]}...")

        print(f"[ERROR] 请手动检查并修复")
        return False

    # 执行替换
    print(f"[INFO] 执行代码替换...")
    new_content = content.replace(OLD_CODE, NEW_CODE)

    # 验证替换成功
    if new_content == content:
        print(f"[ERROR] 替换失败，内容未改变")
        return False

    # 写入新内容
    print(f"[INFO] 写入修复后的文件...")
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"[SUCCESS] 修复完成！")
    print(f"[INFO] 备份文件: {backup_file}")
    print(f"")
    print(f"修改摘要:")
    print(f"  - 将随机 UUID (uuid.uuid4().hex[:24]) 改为 MD5 哈希 ID")
    print(f"  - ID = MD5(函数名 + 参数内容)")
    print(f"  - 确保流式传输中同一工具调用的 ID 稳定一致")
    print(f"")
    print(f"⚠️ 请重启服务使修改生效")

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
