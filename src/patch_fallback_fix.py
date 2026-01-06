# -*- coding: utf-8 -*-
"""
Patch script for antigravity_router.py
Fix: estimated_tokens variable used before definition in Fallback logic

This patch moves the estimated_tokens extraction BEFORE its usage in the
should_try_non_streaming_fallback condition.

Usage: python patch_fallback_fix.py
"""

import shutil
import os
from datetime import datetime

# File path
FILE_PATH = os.path.join(os.path.dirname(__file__), "antigravity_router.py")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_archive", "backups")


def backup_file(file_path: str) -> str:
    """Create file backup"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"antigravity_router.py.bak.{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(file_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return backup_path


def patch_file():
    """Apply the patch"""
    # Read original file
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Create backup
    backup_file(FILE_PATH)

    # ============================================================
    # FIX 1: Move estimated_tokens extraction before its usage
    # ============================================================

    # Find the problematic section - the old code that uses estimated_tokens before definition
    old_fallback_condition = '''            # 增强：基于多种条件进行动态 fallback
            # 1. 如果实际处理的 tokens 超过阈值（50K），尝试非流式 fallback
            # 2. 如果没有 token 信息但估算 tokens 超过阈值，也尝试 fallback
            # 3. 如果收到了空 parts 但没有有效内容，也尝试 fallback
            ACTUAL_PROCESSED_TOKENS_THRESHOLD = 50000  # 50K tokens 阈值
            ESTIMATED_TOKENS_THRESHOLD = 60000  # 60K tokens 估算阈值

            # 判断是否应该尝试 fallback
            should_try_non_streaming_fallback = (
                request_body and
                cred_mgr and
                not state.get("fallback_attempted", False) and  # 避免重复尝试
                (
                    # 条件1：实际处理的 tokens 超过阈值
                    actual_processed_tokens > ACTUAL_PROCESSED_TOKENS_THRESHOLD or
                    # 条件2：估算 tokens 超过阈值（当没有实际 token 信息时）
                    (actual_processed_tokens == 0 and estimated_tokens > ESTIMATED_TOKENS_THRESHOLD) or
                    # 条件3：收到了空 parts 且 SSE 行数 > 0（表示 API 有响应但内容为空）
                    (state.get("empty_parts_count", 0) > 0 and state["sse_lines_received"] > 0)
                )
            )'''

    new_fallback_condition = '''            # 增强：基于多种条件进行动态 fallback
            # 1. 如果实际处理的 tokens 超过阈值（50K），尝试非流式 fallback
            # 2. 如果没有 token 信息但估算 tokens 超过阈值，也尝试 fallback
            # 3. 如果收到了空 parts 但没有有效内容，也尝试 fallback
            ACTUAL_PROCESSED_TOKENS_THRESHOLD = 50000  # 50K tokens 阈值
            ESTIMATED_TOKENS_THRESHOLD = 60000  # 60K tokens 估算阈值

            # [FIX] 提前从 context_info 获取 estimated_tokens（修复变量作用域 bug）
            estimated_tokens = context_info.get("estimated_tokens", 0) if context_info else 0
            tool_result_count = context_info.get("tool_result_count", 0) if context_info else 0
            total_tool_results_length = context_info.get("total_tool_results_length", 0) if context_info else 0

            # 判断是否应该尝试 fallback
            should_try_non_streaming_fallback = (
                request_body and
                cred_mgr and
                not state.get("fallback_attempted", False) and  # 避免重复尝试
                (
                    # 条件1：实际处理的 tokens 超过阈值
                    actual_processed_tokens > ACTUAL_PROCESSED_TOKENS_THRESHOLD or
                    # 条件2：估算 tokens 超过阈值（当没有实际 token 信息时）
                    (actual_processed_tokens == 0 and estimated_tokens > ESTIMATED_TOKENS_THRESHOLD) or
                    # 条件3：收到了空 parts 且 SSE 行数 > 0（表示 API 有响应但内容为空）
                    (state.get("empty_parts_count", 0) > 0 and state["sse_lines_received"] > 0)
                )
            )'''

    if old_fallback_condition not in content:
        print("[WARNING] Cannot find exact fallback condition block!")
        print("Trying alternative approach...")

        # Alternative: Find just the problematic line and fix context
        # Look for the line that uses estimated_tokens before definition
        alt_marker = "(actual_processed_tokens == 0 and estimated_tokens > ESTIMATED_TOKENS_THRESHOLD)"

        if alt_marker in content:
            # Check if estimated_tokens is defined before this usage
            lines = content.split('\n')
            usage_line_idx = -1
            definition_line_idx = -1

            for i, line in enumerate(lines):
                if alt_marker in line:
                    usage_line_idx = i
                if "estimated_tokens = context_info.get" in line and i < usage_line_idx:
                    definition_line_idx = i

            if usage_line_idx > 0 and definition_line_idx < 0:
                print(f"[OK] Found usage at line {usage_line_idx + 1}, no prior definition")
                print("[INFO] The variable scope bug exists - manual fix required")
            else:
                print("[OK] Variable is properly defined before usage - no fix needed")
                return
        else:
            print("[INFO] Fallback condition might already be fixed or different format")
            return
    else:
        # Apply the fix
        new_content = content.replace(old_fallback_condition, new_fallback_condition)

        # ============================================================
        # FIX 2: Remove duplicate variable extraction (now done earlier)
        # ============================================================

        # Find the old duplicate extraction that happens after the condition
        old_extraction = '''            # 从 context_info 获取上下文信息（如果可用）
            estimated_tokens = context_info.get("estimated_tokens", 0) if context_info else 0
            tool_result_count = context_info.get("tool_result_count", 0) if context_info else 0
            total_tool_results_length = context_info.get("total_tool_results_length", 0) if context_info else 0'''

        new_extraction = '''            # 从 context_info 获取上下文信息（已在前面提取，这里用于错误消息）
            # estimated_tokens, tool_result_count, total_tool_results_length 已在 fallback 判断前提取'''

        if old_extraction in new_content:
            new_content = new_content.replace(old_extraction, new_extraction)
            print("[OK] Removed duplicate variable extraction")

        # Write the patched file
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)

        print("[OK] Patch applied successfully!")
        print("Fixes applied:")
        print("  1. Moved estimated_tokens extraction BEFORE its usage in fallback condition")
        print("  2. Removed duplicate variable extraction")
        print("  3. Fixed NameError: estimated_tokens used before definition")


if __name__ == "__main__":
    print("=" * 60)
    print("Antigravity Router Fallback Fix Patch")
    print("=" * 60)
    patch_file()
    print("=" * 60)
