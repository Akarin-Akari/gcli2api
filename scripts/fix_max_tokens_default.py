#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 max_tokens 默认值问题

根因：当客户端不传 max_tokens 时，if 块被跳过，config 中没有 maxOutputTokens，
下游 API 使用默认值 4096，导致输出被截断。

修复：添加 else 分支，当 max_tokens 为 None 时使用默认值 16384
"""

import os
import shutil
from datetime import datetime

def main():
    # 目标文件
    target_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "anthropic_converter.py"
    )

    print(f"[INFO] 目标文件: {target_file}")

    if not os.path.exists(target_file):
        print(f"[ERROR] 文件不存在: {target_file}")
        return False

    # 创建备份
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{target_file}.bak_{timestamp}"
    shutil.copy2(target_file, backup_file)
    print(f"[INFO] 已创建备份: {backup_file}")

    # 读取文件内容
    with open(target_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经修复
    if 'max_tokens 未指定，使用默认值' in content:
        print("[INFO] 已经包含 else 分支修复，跳过")
        return True

    # 要查找的原始代码
    old_code = '''        config["maxOutputTokens"] = max_tokens

    stop_sequences = payload.get("stop_sequences")'''

    # 替换后的代码
    new_code = '''        config["maxOutputTokens"] = max_tokens
    else:
        # [FIX 2026-01-12] 客户端未传 max_tokens 时，使用默认值保证足够输出空间
        # 这是之前修复失效的根因：if 块被跳过，config 中没有 maxOutputTokens
        DEFAULT_MAX_OUTPUT_TOKENS = 16384
        log.info(
            f"[ANTHROPIC CONVERTER] max_tokens 未指定，使用默认值: {DEFAULT_MAX_OUTPUT_TOKENS}"
        )
        config["maxOutputTokens"] = DEFAULT_MAX_OUTPUT_TOKENS

    stop_sequences = payload.get("stop_sequences")'''

    if old_code not in content:
        print("[ERROR] 未找到目标代码块，可能文件结构已变化")
        print("[DEBUG] 正在搜索相关代码...")

        # 尝试更精确的匹配
        import re
        pattern = r'(\s+config\["maxOutputTokens"\] = max_tokens)\n\n(\s+stop_sequences = payload\.get\("stop_sequences"\))'
        match = re.search(pattern, content)

        if match:
            print("[INFO] 找到目标代码（通过正则匹配）")
            # 获取缩进
            indent = "    "  # 8 spaces for the else block content

            new_content = re.sub(
                pattern,
                r'''\1
    else:
        # [FIX 2026-01-12] 客户端未传 max_tokens 时，使用默认值保证足够输出空间
        # 这是之前修复失效的根因：if 块被跳过，config 中没有 maxOutputTokens
        DEFAULT_MAX_OUTPUT_TOKENS = 16384
        log.info(
            f"[ANTHROPIC CONVERTER] max_tokens 未指定，使用默认值: {DEFAULT_MAX_OUTPUT_TOKENS}"
        )
        config["maxOutputTokens"] = DEFAULT_MAX_OUTPUT_TOKENS

\2''',
                content
            )

            # 写回文件
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)

            print("[SUCCESS] 已成功添加 else 分支修复！")
            return True
        else:
            print("[ERROR] 正则匹配也失败了")
            return False

    # 直接替换
    new_content = content.replace(old_code, new_code)

    # 写回文件
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 已成功添加 else 分支修复！")
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
