# -*- coding: utf-8 -*-
"""
Patch script for antigravity_router.py (v2)
Fix: estimated_tokens variable used before definition in Fallback logic

This patch uses line-based approach to fix the variable scope bug.

Usage: python patch_fallback_fix_v2.py
"""

import os
from datetime import datetime

# File path
FILE_PATH = os.path.join(os.path.dirname(__file__), "antigravity_router.py")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_archive", "backups")


def patch_file():
    """Apply the patch using line-based approach"""
    # Read original file
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find the problematic line and fix location
    usage_line_idx = -1
    definition_line_idx = -1
    threshold_line_idx = -1

    for i, line in enumerate(lines):
        # Find where estimated_tokens is used in the condition
        if "estimated_tokens > ESTIMATED_TOKENS_THRESHOLD" in line and usage_line_idx < 0:
            usage_line_idx = i
            print(f"[OK] Found usage at line {i + 1}: {line.strip()[:80]}...")

        # Find where estimated_tokens is defined (after usage - this is the bug)
        if "estimated_tokens = context_info.get" in line:
            definition_line_idx = i
            print(f"[OK] Found definition at line {i + 1}: {line.strip()[:80]}...")

        # Find the ESTIMATED_TOKENS_THRESHOLD line (we'll insert after this)
        if "ESTIMATED_TOKENS_THRESHOLD = " in line:
            threshold_line_idx = i
            print(f"[OK] Found threshold at line {i + 1}: {line.strip()[:80]}...")

    # Check if bug exists
    if usage_line_idx < 0:
        print("[ERROR] Cannot find estimated_tokens usage line!")
        return False

    if definition_line_idx < 0:
        print("[ERROR] Cannot find estimated_tokens definition line!")
        return False

    if definition_line_idx < usage_line_idx:
        print("[OK] Variable is already defined before usage - no fix needed!")
        return True

    if threshold_line_idx < 0:
        print("[ERROR] Cannot find ESTIMATED_TOKENS_THRESHOLD line!")
        return False

    print(f"\n[INFO] Bug confirmed: usage at line {usage_line_idx + 1}, definition at line {definition_line_idx + 1}")
    print(f"[INFO] Will insert definition after line {threshold_line_idx + 1}")

    # Get the indentation from the threshold line
    threshold_line = lines[threshold_line_idx]
    indent = ""
    for char in threshold_line:
        if char in (' ', '\t'):
            indent += char
        else:
            break

    # Create the new lines to insert
    new_lines = [
        f"\n",
        f"{indent}# [FIX] Extract estimated_tokens BEFORE usage in fallback condition (fix variable scope bug)\n",
        f"{indent}estimated_tokens = context_info.get(\"estimated_tokens\", 0) if context_info else 0\n",
    ]

    # Insert new lines after threshold line
    for j, new_line in enumerate(new_lines):
        lines.insert(threshold_line_idx + 1 + j, new_line)

    # Recalculate definition_line_idx (it shifted due to insertion)
    adjustment = len(new_lines)
    new_definition_line_idx = definition_line_idx + adjustment

    # Comment out the old definition (or update it)
    old_def_line = lines[new_definition_line_idx]
    if "estimated_tokens = context_info.get" in old_def_line:
        # Comment it out and add note
        lines[new_definition_line_idx] = f"{indent}# [FIX] Moved above - estimated_tokens already extracted before fallback condition\n"
        print(f"[OK] Commented out old definition at line {new_definition_line_idx + 1}")

    # Write the patched file
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print("\n[OK] Patch applied successfully!")
    print("Fixes applied:")
    print("  1. Added estimated_tokens extraction BEFORE its usage in fallback condition")
    print("  2. Commented out duplicate extraction")
    print("  3. Fixed NameError: estimated_tokens used before definition")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Antigravity Router Fallback Fix Patch (v2)")
    print("=" * 60)
    result = patch_file()
    print("=" * 60)
    if result:
        print("[OK] Patch completed successfully!")
    else:
        print("[ERROR] Patch failed - manual intervention required")
