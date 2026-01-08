#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[FIX 2026-01-08] 修复流式输出被缓冲导致"卡住"的问题

问题描述：
- 工具调用时，流式输出看起来像卡住了
- 实际上后台在处理，但事件被缓冲在 pending_output 中
- 只有在收到 usageMetadata.promptTokenCount 后才发送 message_start
- 如果上游延迟发送 usageMetadata，所有事件都被缓冲直到流结束

解决方案：
- 在收到第一个有效内容时，如果 message_start 还没发送，就使用估算值发送
- 这样可以确保流式输出立即开始，不会被缓冲
"""

import os
import shutil
from datetime import datetime

def main():
    target_file = r"F:\antigravity2api\gcli2api\src\anthropic_streaming.py"

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{target_file}.bak.{timestamp}"

    print(f"[INFO] 创建备份: {backup_file}")
    shutil.copy2(target_file, backup_file)

    # 读取文件内容
    print(f"[INFO] 读取文件: {target_file}")
    with open(target_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 定义要查找的原始代码
    old_code = '''            # 为保证 message_start 永远是首个事件：在拿到真实值之前，把所有事件暂存到 pending_output。
            if state.has_input_tokens and not message_start_sent:
                send_message_start(ready_output, input_tokens=state.input_tokens)

            for part in parts:'''

    # 定义修复后的代码
    new_code = '''            # 为保证 message_start 永远是首个事件：在拿到真实值之前，把所有事件暂存到 pending_output。
            if state.has_input_tokens and not message_start_sent:
                send_message_start(ready_output, input_tokens=state.input_tokens)

            # [FIX 2026-01-08] 提前发送 message_start 以避免流式输出被缓冲
            # 问题：如果上游延迟发送 usageMetadata，所有事件都会被 enqueue 到 pending_output
            # 导致流式输出看起来"卡住"，直到流结束才一次性发送
            # 解决：在收到第一个有效内容时，如果 message_start 还没发送，就使用估算值发送
            if parts and not message_start_sent:
                # 有有效内容到来，立即发送 message_start（使用估算的 token 数）
                send_message_start(ready_output, input_tokens=initial_input_tokens_int)

            for part in parts:'''

    # 检查是否已经修复过
    if "提前发送 message_start 以避免流式输出被缓冲" in content:
        print("[INFO] 文件已经包含修复代码，跳过修改")
        return

    # 检查原始代码是否存在
    if old_code not in content:
        print("[ERROR] 未找到目标代码块，可能文件已被修改")
        print("[DEBUG] 搜索关键字 'for part in parts:' ...")
        if "for part in parts:" in content:
            print("[DEBUG] 找到 'for part in parts:'，但上下文不匹配")
        else:
            print("[DEBUG] 未找到 'for part in parts:'")
        return

    # 执行替换
    new_content = content.replace(old_code, new_code)

    # 写入文件
    print(f"[INFO] 写入修复后的文件: {target_file}")
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 修复完成！")
    print(f"[INFO] 备份文件: {backup_file}")

if __name__ == "__main__":
    main()
