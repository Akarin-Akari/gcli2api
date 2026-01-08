#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[FIX 2026-01-08] 修复工具调用延迟发送问题

问题描述：
- 工具调用被收集到 state["tool_calls"] 列表中
- 但只有在收到 finish_reason 后才会发送
- 导致工具调用看起来"卡住"，直到流结束才显示

解决方案：
- 在收到工具调用时立即发送，而不是等到流结束
- 这样 Cursor 就能立即看到工具调用并开始执行
"""

import os
import shutil
from datetime import datetime

def main():
    target_file = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{target_file}.bak.{timestamp}"

    print(f"[INFO] 创建备份: {backup_file}")
    shutil.copy2(target_file, backup_file)

    # 读取文件内容
    print(f"[INFO] 读取文件: {target_file}")
    with open(target_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经修复过
    if "立即发送工具调用，不等待 finish_reason" in content:
        print("[INFO] 文件已经包含修复代码，跳过修改")
        return

    # 定义要查找的原始代码（工具调用只添加到列表，不立即发送）
    old_code = '''                    log.info(f"[ANTIGRAVITY STREAM] Tool call detected: name={fc.get('name')}, id={fc.get('id')}")
                    tool_call = convert_to_openai_tool_call(fc, index=tool_index)
                    state["tool_calls"].append(tool_call)
                    state["has_valid_content"] = True  # 收到了有效的工具调用
                    log.debug(f"[ANTIGRAVITY STREAM] Converted tool_call: {json.dumps(tool_call)[:200]}")'''

    # 定义修复后的代码（立即发送工具调用）
    new_code = '''                    log.info(f"[ANTIGRAVITY STREAM] Tool call detected: name={fc.get('name')}, id={fc.get('id')}")
                    tool_call = convert_to_openai_tool_call(fc, index=tool_index)
                    state["tool_calls"].append(tool_call)
                    state["has_valid_content"] = True  # 收到了有效的工具调用
                    log.debug(f"[ANTIGRAVITY STREAM] Converted tool_call: {json.dumps(tool_call)[:200]}")

                    # [FIX 2026-01-08] 立即发送工具调用，不等待 finish_reason
                    # 问题：工具调用被缓冲到 state["tool_calls"]，只有在 finish_reason 时才发送
                    # 导致 Cursor 看不到工具调用，以为卡住了
                    # 解决：收到工具调用时立即发送
                    tool_chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"tool_calls": [tool_call]},
                            "finish_reason": None
                        }]
                    }
                    log.info(f"[ANTIGRAVITY STREAM] Immediately sending tool call: {fc.get('name')}")
                    yield f"data: {json.dumps(tool_chunk)}\\n\\n"
                    state["chunks_sent"] += 1'''

    # 检查原始代码是否存在
    if old_code not in content:
        print("[ERROR] 未找到目标代码块，可能文件已被修改")
        print("[DEBUG] 搜索关键字...")
        if "Tool call detected" in content:
            print("[DEBUG] 找到 'Tool call detected'，但上下文不匹配")
        return

    # 执行替换
    new_content = content.replace(old_code, new_code)

    # 还需要修改 finish_reason 处的工具调用发送逻辑，避免重复发送
    # 查找并修改：只有在工具调用未发送时才发送
    old_finish_code = '''                # 发送工具调用
                if state["tool_calls"]:
                    log.info(f"[ANTIGRAVITY STREAM] Sending {len(state['tool_calls'])} tool calls")
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"tool_calls": state["tool_calls"]},
                            "finish_reason": None
                        }]
                    }
                    log.debug(f"[ANTIGRAVITY STREAM] Tool calls chunk: {json.dumps(chunk)[:500]}")
                    yield f"data: {json.dumps(chunk)}\\n\\n"
                    state["tool_calls_sent"] = True  # 标记工具调用已发送'''

    new_finish_code = '''                # [FIX 2026-01-08] 工具调用已在收到时立即发送，这里不再重复发送
                # 只需要标记工具调用已发送（用于后续 finish_reason 判断）
                if state["tool_calls"]:
                    log.info(f"[ANTIGRAVITY STREAM] Tool calls already sent immediately, count: {len(state['tool_calls'])}")
                    state["tool_calls_sent"] = True  # 标记工具调用已发送'''

    if old_finish_code in new_content:
        new_content = new_content.replace(old_finish_code, new_finish_code)
        print("[INFO] 已修改 finish_reason 处的工具调用发送逻辑")
    else:
        print("[WARNING] 未找到 finish_reason 处的工具调用发送代码，可能需要手动检查")

    # 写入文件
    print(f"[INFO] 写入修复后的文件: {target_file}")
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 修复完成！")
    print(f"[INFO] 备份文件: {backup_file}")

if __name__ == "__main__":
    main()
