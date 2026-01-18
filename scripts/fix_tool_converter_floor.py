#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 tool_converter.py 的 max_tokens 下限保护

根因：antigravity_router.py 使用的是 tool_converter.py 中的 generate_generation_config，
而不是 anthropic_converter.py 中的 build_generation_config。
之前的修复都在错误的文件中！

修复：在 tool_converter.py 第467行前添加下限保护
"""

import os
import shutil
from datetime import datetime

def main():
    # 目标文件
    target_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "converters", "tool_converter.py"
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
    if 'maxOutputTokens 低于下限' in content:
        print("[INFO] 已经包含下限保护修复，跳过")
        return True

    # 要查找的原始代码（精确匹配）
    old_code = '''        if isinstance(max_tokens, int) and max_tokens > MAX_ALLOWED_TOKENS:
            log.warning(
                f"[ANTIGRAVITY] maxOutputTokens 超过上限: {max_tokens} -> {MAX_ALLOWED_TOKENS}"
            )
            max_tokens = MAX_ALLOWED_TOKENS
        config_dict["maxOutputTokens"] = max_tokens'''

    # 替换后的代码
    new_code = '''        if isinstance(max_tokens, int) and max_tokens > MAX_ALLOWED_TOKENS:
            log.warning(
                f"[ANTIGRAVITY] maxOutputTokens 超过上限: {max_tokens} -> {MAX_ALLOWED_TOKENS}"
            )
            max_tokens = MAX_ALLOWED_TOKENS

        # [FIX 2026-01-12] 添加下限保护，防止客户端传来过小的 max_tokens 导致输出被截断
        # 这是之前修复都不生效的真正根因：antigravity_router 使用的是这个函数，不是 anthropic_converter 的！
        if isinstance(max_tokens, int) and max_tokens < MIN_OUTPUT_TOKENS:
            log.info(
                f"[ANTIGRAVITY] maxOutputTokens 低于下限: {max_tokens} -> {MIN_OUTPUT_TOKENS}"
            )
            max_tokens = MIN_OUTPUT_TOKENS

        config_dict["maxOutputTokens"] = max_tokens'''

    if old_code not in content:
        print("[WARNING] 未找到精确匹配的代码块，尝试正则匹配...")

        import re
        # 尝试更灵活的匹配
        pattern = r'(        if isinstance\(max_tokens, int\) and max_tokens > MAX_ALLOWED_TOKENS:\s+log\.warning\(\s+f"\[ANTIGRAVITY\] maxOutputTokens 超过上限: \{max_tokens\} -> \{MAX_ALLOWED_TOKENS\}"\s+\)\s+max_tokens = MAX_ALLOWED_TOKENS)\s+(config_dict\["maxOutputTokens"\] = max_tokens)'

        match = re.search(pattern, content)
        if match:
            print("[INFO] 找到目标代码（通过正则匹配）")

            replacement = match.group(1) + '''

        # [FIX 2026-01-12] 添加下限保护，防止客户端传来过小的 max_tokens 导致输出被截断
        # 这是之前修复都不生效的真正根因：antigravity_router 使用的是这个函数，不是 anthropic_converter 的！
        if isinstance(max_tokens, int) and max_tokens < MIN_OUTPUT_TOKENS:
            log.info(
                f"[ANTIGRAVITY] maxOutputTokens 低于下限: {max_tokens} -> {MIN_OUTPUT_TOKENS}"
            )
            max_tokens = MIN_OUTPUT_TOKENS

        ''' + match.group(2)

            new_content = content[:match.start()] + replacement + content[match.end():]
        else:
            print("[ERROR] 正则匹配也失败了，尝试按行处理...")

            # 按行处理
            lines = content.split('\n')
            new_lines = []
            found = False

            for i, line in enumerate(lines):
                new_lines.append(line)
                # 找到 "max_tokens = MAX_ALLOWED_TOKENS" 这一行
                if 'max_tokens = MAX_ALLOWED_TOKENS' in line and not found:
                    # 检查下一行是否是 config_dict["maxOutputTokens"]
                    if i + 1 < len(lines) and 'config_dict["maxOutputTokens"]' in lines[i + 1]:
                        # 在这两行之间插入下限保护代码
                        new_lines.append('')
                        new_lines.append('        # [FIX 2026-01-12] 添加下限保护，防止客户端传来过小的 max_tokens 导致输出被截断')
                        new_lines.append('        # 这是之前修复都不生效的真正根因：antigravity_router 使用的是这个函数，不是 anthropic_converter 的！')
                        new_lines.append('        if isinstance(max_tokens, int) and max_tokens < MIN_OUTPUT_TOKENS:')
                        new_lines.append('            log.info(')
                        new_lines.append('                f"[ANTIGRAVITY] maxOutputTokens 低于下限: {max_tokens} -> {MIN_OUTPUT_TOKENS}"')
                        new_lines.append('            )')
                        new_lines.append('            max_tokens = MIN_OUTPUT_TOKENS')
                        new_lines.append('')
                        found = True
                        print(f"[INFO] 在第 {i+1} 行后插入下限保护代码")

            if found:
                new_content = '\n'.join(new_lines)
            else:
                print("[ERROR] 按行处理也未能找到插入点")
                return False
    else:
        # 直接替换
        new_content = content.replace(old_code, new_code)

    # 写回文件
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("[SUCCESS] 已成功添加 tool_converter.py 下限保护修复！")

    # 验证修复
    with open(target_file, 'r', encoding='utf-8') as f:
        verify_content = f.read()

    if 'maxOutputTokens 低于下限' in verify_content:
        print("[VERIFY] 验证成功：下限保护代码已添加")
        return True
    else:
        print("[VERIFY] 验证失败：下限保护代码未找到")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
