#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修复 antigravity_router.py 中的 thinking block fallback 逻辑

问题：当 Cursor 发送的历史消息中没有 thinking block 时，代码直接禁用了 thinking 模式。
但实际上应该从缓存中获取 signature 和 thinking text！

修复：重新启用 fallback 机制，使用 get_last_signature_with_text() 从缓存恢复。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-12
"""

import os
import shutil
from datetime import datetime

# 目标文件路径
TARGET_FILE = r"F:\antigravity2api\gcli2api\src\antigravity_router.py"

# 旧代码（需要替换的部分）
OLD_CODE = '''                        if thinking_part:
                            # 在开头插入 thinking block
                            parts.insert(0, thinking_part)
                            log.info(f"[ANTIGRAVITY] Added thinking block to last assistant message from previous message")
                        else:
                            # [FIX 2026-01-09] 禁用不安全的 fallback 机制
                            # 问题：get_last_signature_with_text() 返回的是全局最近缓存的 signature，
                            # 这个 signature 对应的 thinking 内容可能与当前消息完全无关，
                            # 导致 Claude API 返回 400 错误：Invalid signature in thinking block
                            #
                            # 错误场景：
                            # 1. 第一轮对话：模型返回 thinking block A + 工具调用
                            # 2. Cursor 保存历史消息，可能截断或修改 thinking 内容为 A'
                            # 3. 第二轮对话：Cursor 发送历史消息（包含 A'）
                            # 4. message_converter.py 查询缓存，A' 与 A 不匹配，缓存未命中
                            # 5. 移除 thinking 标签，消息不以 thinking block 开头
                            # 6. Fallback 机制使用 A 的 signature 和内容
                            # 7. 发送请求：消息包含 A，但 Cursor 期望的是 A'
                            # 8. Claude API 验证失败 → 400 错误
                            #
                            # 正确做法：当无法找到匹配的 thinking block 时，禁用 thinking 模式
                            # [FIX 2026-01-08] 无法找到有效的 thinking block with signature
                            # 必须禁用 thinking 模式，否则 API 会返回 400 错误：
                            # "Expected 'thinking' or 'redacted_thinking', but found 'text'"
                            # API 明确指示："To avoid this requirement, disable 'thinking'."
                            log.warning(f"[ANTIGRAVITY] Last assistant message does not start with thinking block, "
                                       f"cannot find previous thinking block with valid signature. "
                                       f"DISABLING thinking mode to avoid 400 error.")
                            enable_thinking = False
                            # 重新清理消息中的 thinking 内容
                            messages = strip_thinking_from_openai_messages(messages)
                            # 重新转换消息格式（不带 thinking）
                            contents = openai_messages_to_antigravity_contents(
                                messages,
                                enable_thinking=False,
                                tools=tools,
                                recommend_sequential_thinking=recommend_sequential
                            )'''

# 新代码（替换后的内容）
NEW_CODE = '''                        if thinking_part:
                            # 在开头插入 thinking block
                            parts.insert(0, thinking_part)
                            log.info(f"[ANTIGRAVITY] Added thinking block to last assistant message from previous message")
                        else:
                            # [FIX 2026-01-12] 重新启用 fallback 机制，正确使用缓存
                            #
                            # 核心理解：Cursor **从不**在历史消息中发送 thinking block！
                            # Cursor 会过滤掉 thinking 内容，只发送纯文本响应。
                            # 因此，我们必须从缓存中获取 signature 和 thinking text。
                            #
                            # 之前的 [FIX 2026-01-09] 禁用了 fallback，因为担心 signature 与内容不匹配。
                            # 但现在 get_last_signature_with_text() 返回的是**配对的** (signature, thinking_text)，
                            # 这两者是一起缓存的，所以不会出现不匹配的问题。
                            #
                            # 关键点：使用缓存返回的 thinking_text，而不是历史消息中的内容！
                            cached_result = get_last_signature_with_text()
                            if cached_result:
                                cached_signature, cached_thinking_text = cached_result
                                thinking_part = {
                                    "text": cached_thinking_text,
                                    "thought": True,
                                    "thoughtSignature": cached_signature
                                }
                                parts.insert(0, thinking_part)
                                log.info(f"[ANTIGRAVITY] 从缓存恢复 thinking block (fallback): "
                                        f"thinking_len={len(cached_thinking_text)}, "
                                        f"signature_len={len(cached_signature)}")
                            else:
                                # 缓存为空，确实无法恢复 thinking 模式
                                log.warning(f"[ANTIGRAVITY] Last assistant message does not start with thinking block, "
                                           f"cache is empty, cannot recover. "
                                           f"DISABLING thinking mode to avoid 400 error.")
                                enable_thinking = False
                                # 重新清理消息中的 thinking 内容
                                messages = strip_thinking_from_openai_messages(messages)
                                # 重新转换消息格式（不带 thinking）
                                contents = openai_messages_to_antigravity_contents(
                                    messages,
                                    enable_thinking=False,
                                    tools=tools,
                                    recommend_sequential_thinking=recommend_sequential
                                )'''


def main():
    print(f"[FIX] 开始修复 {TARGET_FILE}")

    # 读取文件内容
    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查旧代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] 未找到需要替换的代码段！可能已经修复过了。")
        # 检查新代码是否已经存在
        if "从缓存恢复 thinking block (fallback)" in content:
            print("[INFO] 检测到新代码已存在，修复已完成。")
            return True
        else:
            print("[ERROR] 既没有找到旧代码，也没有找到新代码，请手动检查文件。")
            return False

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET_FILE + f".bak_{timestamp}"
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] 已创建备份: {backup_path}")

    # 替换代码
    new_content = content.replace(OLD_CODE, NEW_CODE)

    # 写入新内容
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 修复完成！")
    print(f"[INFO] 修改内容: 重新启用 fallback 机制，使用 get_last_signature_with_text() 从缓存恢复")

    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
