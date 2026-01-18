#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成脚本：将 truncation_monitor.py 监控模块集成到 antigravity_router.py

修改内容：
1. 添加 truncation_monitor 导入语句
2. 在截断事件发生时调用 record_truncation()
3. 在 MAX_TOKENS 事件发生时调用 record_max_tokens()
4. 在检测到缓存命中时调用 record_cache_hit()

运行方式：
    python integrate_truncation_monitor.py
"""

import os
import re
import shutil
from datetime import datetime

# 文件路径
TARGET_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "antigravity_router.py")
TARGET_FILE = os.path.abspath(TARGET_FILE)

def backup_file(filepath: str) -> str:
    """创建文件备份"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.bak_{timestamp}"
    shutil.copy2(filepath, backup_path)
    print(f"[BACKUP] 已创建备份: {backup_path}")
    return backup_path

def apply_fixes(content: str) -> str:
    """应用所有修复"""

    # 修复1: 添加 truncation_monitor 导入
    # 在 context_truncation 导入之后添加
    import_marker = "from .context_truncation import ("
    if import_marker in content and "from .truncation_monitor import" not in content:
        # 找到 context_truncation 导入块的结束位置
        pattern = r'(from \.context_truncation import \([^)]+\))'
        match = re.search(pattern, content, flags=re.DOTALL)
        if match:
            insert_pos = match.end()
            import_addition = """

# [FIX 2026-01-10] 导入截断监控模块
from .truncation_monitor import (
    record_truncation,
    record_max_tokens,
    record_cache_hit,
)"""
            content = content[:insert_pos] + import_addition + content[insert_pos:]
            print("[FIX 1] 已添加 truncation_monitor 导入")
    else:
        if "from .truncation_monitor import" in content:
            print("[FIX 1] truncation_monitor 导入已存在，跳过")
        else:
            print("[WARNING] 未找到 context_truncation 导入，尝试其他位置...")
            # 在文件顶部的导入区域添加
            if "from .context_analyzer import" in content:
                content = content.replace(
                    "from .context_analyzer import",
                    """# [FIX 2026-01-10] 导入截断监控模块
from .truncation_monitor import (
    record_truncation,
    record_max_tokens,
    record_cache_hit,
)
from .context_analyzer import"""
                )
                print("[FIX 1] 已在 context_analyzer 导入前添加 truncation_monitor 导入")

    # 修复2: 在截断事件发生时调用 record_truncation()
    # 查找截断日志输出的位置，在其后添加监控调用
    truncation_log_pattern = r'(if truncation_stats\.get\("truncated"\):\s+log\.info\(f"\[ANTIGRAVITY\] 智能截断完成: "\s+f"{truncation_stats\[\'original_messages\'\]} -> {truncation_stats\[\'final_messages\'\]} 消息, "\s+f"{truncation_stats\[\'original_tokens\'\]:,} -> {truncation_stats\[\'final_tokens\'\]:,} tokens, "\s+f"工具结果压缩节省 {truncation_stats\.get\(\'tool_chars_saved\', 0\):,} 字符"\))'

    if "record_truncation(" not in content or "# [MONITOR]" not in content:
        # 简化匹配：查找截断日志后的位置
        simple_pattern = r'(log\.info\(f"\[ANTIGRAVITY\] 智能截断完成: "[\s\S]*?f"工具结果压缩节省 \{truncation_stats\.get\(\'tool_chars_saved\', 0\):,\} 字符"\))'
        match = re.search(simple_pattern, content)
        if match:
            insert_pos = match.end()
            # 检查后面是否已经有 record_truncation 调用
            next_100_chars = content[insert_pos:insert_pos+200]
            if "record_truncation(" not in next_100_chars:
                monitor_call = """
            # [MONITOR] 记录截断事件
            record_truncation(
                model=actual_model,
                original_tokens=truncation_stats['original_tokens'],
                final_tokens=truncation_stats['final_tokens'],
                truncated=True,
                strategy="smart",
                messages_removed=truncation_stats.get('removed_count', 0),
                tool_chars_saved=truncation_stats.get('tool_chars_saved', 0),
                dynamic_limit=dynamic_target_limit,
            )"""
                content = content[:insert_pos] + monitor_call + content[insert_pos:]
                print("[FIX 2] 已添加截断事件监控调用")
            else:
                print("[FIX 2] 截断事件监控调用已存在，跳过")
        else:
            print("[WARNING] 未找到截断日志位置，跳过截断事件监控")
    else:
        print("[FIX 2] 截断事件监控调用已存在，跳过")

    # 修复3: 在 MAX_TOKENS 事件发生时调用 record_max_tokens()
    # 查找 MAX_TOKENS 检测代码位置
    max_tokens_pattern = r'(elif finish_reason == "MAX_TOKENS":\s+openai_finish_reason = "length")'
    if "record_max_tokens(" not in content:
        match = re.search(max_tokens_pattern, content)
        if match:
            # 查找这个块后面的日志位置
            log_pattern = r'(log\.warning\(f"\[ANTIGRAVITY STREAM\] MAX_TOKENS reached: "[\s\S]*?f"tool_calls=\{len\(state\[\'tool_calls\'\]\)\}"\))'
            log_match = re.search(log_pattern, content)
            if log_match:
                insert_pos = log_match.end()
                next_200_chars = content[insert_pos:insert_pos+300]
                if "record_max_tokens(" not in next_200_chars:
                    monitor_call = """

                    # [MONITOR] 记录 MAX_TOKENS 事件
                    record_max_tokens(
                        model=model,
                        prompt_tokens=prompt_token_count,
                        output_tokens=candidates_tokens,
                        cached_tokens=cached_content_token_count,
                        finish_reason="MAX_TOKENS",
                    )"""
                    content = content[:insert_pos] + monitor_call + content[insert_pos:]
                    print("[FIX 3] 已添加 MAX_TOKENS 事件监控调用")
                else:
                    print("[FIX 3] MAX_TOKENS 事件监控调用已存在，跳过")
            else:
                print("[WARNING] 未找到 MAX_TOKENS 日志位置")
        else:
            print("[WARNING] 未找到 MAX_TOKENS 检测代码")
    else:
        print("[FIX 3] MAX_TOKENS 事件监控调用已存在，跳过")

    # 修复4: 在检测到缓存命中时调用 record_cache_hit()
    # 查找缓存检测代码位置
    cache_pattern = r'(log\.info\(f"\[ANTIGRAVITY STREAM\] Cache detected: \{cached_content_token_count:,\} tokens cached, "[\s\S]*?f"\{actual_processed_tokens:,\} tokens actually processed \(out of \{prompt_token_count:,\} total\)"\))'
    if "record_cache_hit(" not in content:
        match = re.search(cache_pattern, content)
        if match:
            insert_pos = match.end()
            next_200_chars = content[insert_pos:insert_pos+300]
            if "record_cache_hit(" not in next_200_chars:
                monitor_call = """

                        # [MONITOR] 记录缓存命中事件
                        record_cache_hit(
                            model=model,
                            prompt_tokens=prompt_token_count,
                            cached_tokens=cached_content_token_count,
                        )"""
                content = content[:insert_pos] + monitor_call + content[insert_pos:]
                print("[FIX 4] 已添加缓存命中事件监控调用")
            else:
                print("[FIX 4] 缓存命中事件监控调用已存在，跳过")
        else:
            print("[WARNING] 未找到缓存检测代码位置")
    else:
        print("[FIX 4] 缓存命中事件监控调用已存在，跳过")

    return content

def main():
    print("=" * 60)
    print("集成脚本：truncation_monitor 监控模块集成")
    print("=" * 60)

    # 检查文件是否存在
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] 文件不存在: {TARGET_FILE}")
        return False

    print(f"[INFO] 目标文件: {TARGET_FILE}")

    # 创建备份
    backup_path = backup_file(TARGET_FILE)

    try:
        # 读取文件内容
        with open(TARGET_FILE, 'r', encoding='utf-8') as f:
            content = f.read()

        print(f"[INFO] 文件大小: {len(content):,} 字符")

        # 应用修复
        new_content = apply_fixes(content)

        # 检查是否有修改
        if new_content == content:
            print("[INFO] 文件内容未更改（可能已经应用过修复）")
            return True

        # 写入修改后的内容
        with open(TARGET_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"[SUCCESS] 文件已修改，新大小: {len(new_content):,} 字符")
        print(f"[INFO] 如需回滚，请使用备份文件: {backup_path}")

        return True

    except Exception as e:
        print(f"[ERROR] 修改失败: {e}")
        print(f"[INFO] 正在从备份恢复...")
        shutil.copy2(backup_path, TARGET_FILE)
        print(f"[INFO] 已从备份恢复")
        return False

if __name__ == "__main__":
    success = main()
    print("=" * 60)
    print(f"集成结果: {'成功' if success else '失败'}")
    print("=" * 60)
