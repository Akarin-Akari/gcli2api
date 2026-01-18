#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：为 antigravity_router.py 添加动态阈值调整功能

修改内容：
1. 导入语句添加 get_dynamic_target_limit 和 get_model_context_limit
2. 截断逻辑使用动态计算的阈值替代固定的 TARGET_TOKEN_LIMIT

运行方式：
    python fix_antigravity_router_dynamic_limit.py
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

def apply_fix(content: str) -> str:
    """应用修复"""

    # 修复1: 修改导入语句，添加 get_dynamic_target_limit 和 get_model_context_limit
    old_import = """from .context_truncation import (
    truncate_context_for_api,
    estimate_messages_tokens,
    TARGET_TOKEN_LIMIT,
    prepare_retry_after_max_tokens,  # [FIX 2026-01-10] MAX_TOKENS 自动重试
    truncate_messages_aggressive,     # [FIX 2026-01-10] 激进截断策略
    smart_preemptive_truncation,      # [FIX 2026-01-10] 智能预防性截断
    should_retry_with_aggressive_truncation,  # [FIX 2026-01-10] 重试判断
)"""

    new_import = """from .context_truncation import (
    truncate_context_for_api,
    estimate_messages_tokens,
    TARGET_TOKEN_LIMIT,
    prepare_retry_after_max_tokens,  # [FIX 2026-01-10] MAX_TOKENS 自动重试
    truncate_messages_aggressive,     # [FIX 2026-01-10] 激进截断策略
    smart_preemptive_truncation,      # [FIX 2026-01-10] 智能预防性截断
    should_retry_with_aggressive_truncation,  # [FIX 2026-01-10] 重试判断
    get_dynamic_target_limit,         # [FIX 2026-01-10] 动态阈值计算
    get_model_context_limit,          # [FIX 2026-01-10] 获取模型上下文限制
)"""

    if old_import in content:
        content = content.replace(old_import, new_import)
        print("[FIX 1] 已添加 get_dynamic_target_limit 和 get_model_context_limit 导入")
    else:
        print("[WARNING] 未找到预期的导入语句，可能已被修改或格式不同")
        # 尝试更灵活的匹配
        if "get_dynamic_target_limit" not in content:
            # 在 context_truncation 导入块末尾添加
            pattern = r'(from \.context_truncation import \([^)]+)(should_retry_with_aggressive_truncation[^\)]+\))'
            replacement = r'\1should_retry_with_aggressive_truncation,  # [FIX 2026-01-10] 重试判断\n    get_dynamic_target_limit,         # [FIX 2026-01-10] 动态阈值计算\n    get_model_context_limit,          # [FIX 2026-01-10] 获取模型上下文限制\n)'
            new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
            if new_content != content:
                content = new_content
                print("[FIX 1] 使用正则表达式添加导入")

    # 修复2: 修改截断逻辑，使用动态阈值
    # 查找并替换截断逻辑
    old_truncation = """    # ✅ [FIX 2026-01-10] 智能消息截断 - 防止 promptTokenCount 超限导致工具调用失败
    # 问题：长对话会导致 token 累积到 120K-148K+，超出 API 限制
    # 解决方案：在转换消息前进行智能截断
    pre_truncate_tokens = estimate_messages_tokens(messages)
    if pre_truncate_tokens > TARGET_TOKEN_LIMIT:
        log.warning(f"[ANTIGRAVITY] 检测到长对话: ~{pre_truncate_tokens:,} tokens (目标: {TARGET_TOKEN_LIMIT:,})")
        messages, truncation_stats = truncate_context_for_api(
            messages,
            target_tokens=TARGET_TOKEN_LIMIT,
            compress_tools=True,
            tool_max_length=5000,
        )
        if truncation_stats.get("truncated"):
            log.info(f"[ANTIGRAVITY] 智能截断完成: "
                    f"{truncation_stats['original_messages']} -> {truncation_stats['final_messages']} 消息, "
                    f"{truncation_stats['original_tokens']:,} -> {truncation_stats['final_tokens']:,} tokens, "
                    f"工具结果压缩节省 {truncation_stats.get('tool_chars_saved', 0):,} 字符")
    else:
        log.debug(f"[ANTIGRAVITY] Token 数量在限制内: ~{pre_truncate_tokens:,} tokens")"""

    new_truncation = """    # ✅ [FIX 2026-01-10] 智能消息截断 - 防止 promptTokenCount 超限导致工具调用失败
    # 问题：长对话会导致 token 累积到 120K-148K+，超出 API 限制
    # 解决方案：在转换消息前进行智能截断，使用动态阈值
    # [ENHANCED 2026-01-10] 动态阈值：根据模型类型设置不同的上下文限制
    dynamic_target_limit = get_dynamic_target_limit(actual_model)
    pre_truncate_tokens = estimate_messages_tokens(messages)
    if pre_truncate_tokens > dynamic_target_limit:
        log.warning(f"[ANTIGRAVITY] 检测到长对话: ~{pre_truncate_tokens:,} tokens (动态目标: {dynamic_target_limit:,}, 模型: {actual_model})")
        messages, truncation_stats = truncate_context_for_api(
            messages,
            target_tokens=dynamic_target_limit,
            compress_tools=True,
            tool_max_length=5000,
        )
        if truncation_stats.get("truncated"):
            log.info(f"[ANTIGRAVITY] 智能截断完成: "
                    f"{truncation_stats['original_messages']} -> {truncation_stats['final_messages']} 消息, "
                    f"{truncation_stats['original_tokens']:,} -> {truncation_stats['final_tokens']:,} tokens, "
                    f"工具结果压缩节省 {truncation_stats.get('tool_chars_saved', 0):,} 字符")
    else:
        log.debug(f"[ANTIGRAVITY] Token 数量在限制内: ~{pre_truncate_tokens:,} tokens (动态限制: {dynamic_target_limit:,})")"""

    if old_truncation in content:
        content = content.replace(old_truncation, new_truncation)
        print("[FIX 2] 已将截断逻辑改为使用动态阈值")
    else:
        print("[WARNING] 未找到预期的截断逻辑，尝试使用正则匹配...")
        # 尝试更灵活的匹配
        pattern = r'(pre_truncate_tokens = estimate_messages_tokens\(messages\)\s+if pre_truncate_tokens > )TARGET_TOKEN_LIMIT:'
        if re.search(pattern, content):
            # 首先添加动态阈值计算
            insert_pattern = r'(# ✅ \[FIX 2026-01-10\] 智能消息截断.*?# 解决方案：在转换消息前进行智能截断\s+)'
            insert_replacement = r'\1# [ENHANCED 2026-01-10] 动态阈值：根据模型类型设置不同的上下文限制\n    dynamic_target_limit = get_dynamic_target_limit(actual_model)\n    '
            content = re.sub(insert_pattern, insert_replacement, content, flags=re.DOTALL)

            # 替换 TARGET_TOKEN_LIMIT 为 dynamic_target_limit
            content = re.sub(
                r'if pre_truncate_tokens > TARGET_TOKEN_LIMIT:',
                'if pre_truncate_tokens > dynamic_target_limit:',
                content
            )
            content = re.sub(
                r'target_tokens=TARGET_TOKEN_LIMIT,',
                'target_tokens=dynamic_target_limit,',
                content
            )
            content = re.sub(
                r'\(目标: \{TARGET_TOKEN_LIMIT:,\}\)',
                '(动态目标: {dynamic_target_limit:,}, 模型: {actual_model})',
                content
            )
            print("[FIX 2] 使用正则表达式修改截断逻辑")

    return content

def main():
    print("=" * 60)
    print("修复脚本：antigravity_router.py 动态阈值调整")
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
        new_content = apply_fix(content)

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
    print(f"修复结果: {'成功' if success else '失败'}")
    print("=" * 60)
