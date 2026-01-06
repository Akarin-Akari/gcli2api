# -*- coding: utf-8 -*-
"""
Patch script for antigravity_router.py
Enhancement: Integrate tool_converter.py validation functions into validate_tool_call

This patch:
1. Imports validate_tool_name from tool_converter
2. Enhances validate_tool_call to use the imported validation function
3. Adds more detailed validation logging

Usage: python patch_tool_validation.py
"""

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
    import shutil
    shutil.copy2(file_path, backup_path)
    print(f"[OK] Backup created: {backup_path}")
    return backup_path


def patch_file():
    """Apply the patch"""
    # Read original file
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Create backup
    backup_file(FILE_PATH)

    # Track changes
    changes_made = []

    # ============================================================
    # FIX 1: Add import for validate_tool_name from tool_converter
    # ============================================================

    # Find the import section for converters
    import_line_idx = -1
    for i, line in enumerate(lines):
        if "from .converters import" in line:
            import_line_idx = i
            break

    if import_line_idx < 0:
        print("[ERROR] Cannot find converters import section!")
        return False

    # Check if validate_tool_name is already imported
    import_block_end = import_line_idx
    for i in range(import_line_idx, min(import_line_idx + 20, len(lines))):
        if lines[i].strip() == ")":
            import_block_end = i
            break

    import_block = "".join(lines[import_line_idx:import_block_end + 1])

    if "validate_tool_name" not in import_block:
        # Find the closing parenthesis of the import
        for i in range(import_line_idx, import_block_end + 1):
            if lines[i].strip() == ")":
                # Insert before the closing parenthesis
                indent = "    "
                new_import = f"{indent}# tool_converter (validation)\n{indent}validate_tool_name,\n"
                lines.insert(i, new_import)
                changes_made.append(f"Added import for validate_tool_name at line {i + 1}")
                break
    else:
        print("[OK] validate_tool_name already imported")

    # ============================================================
    # FIX 2: Enhance validate_tool_call function
    # ============================================================

    # Find the validate_tool_call function
    func_start_idx = -1
    for i, line in enumerate(lines):
        if "def validate_tool_call(" in line:
            func_start_idx = i
            break

    if func_start_idx < 0:
        print("[ERROR] Cannot find validate_tool_call function!")
        return False

    # Find the line that validates the name (simple check)
    name_check_idx = -1
    for i in range(func_start_idx, min(func_start_idx + 50, len(lines))):
        if "if not name:" in lines[i]:
            name_check_idx = i
            break

    if name_check_idx < 0:
        print("[ERROR] Cannot find name validation in validate_tool_call!")
        return False

    # Check if already enhanced
    if "validate_tool_name" in "".join(lines[func_start_idx:name_check_idx + 10]):
        print("[OK] validate_tool_call already enhanced with validate_tool_name")
    else:
        # Get indentation
        indent = "    "

        # Find where to insert the enhanced validation (after "name = function_call.get("name")")
        name_get_idx = -1
        for i in range(func_start_idx, name_check_idx):
            if 'name = function_call.get("name")' in lines[i]:
                name_get_idx = i
                break

        if name_get_idx >= 0:
            # Replace the simple name validation with enhanced version
            # Find the end of the simple validation block
            simple_check_end = name_check_idx + 3  # "if not name:" + return + blank line

            # New enhanced validation code
            enhanced_validation = f'''
{indent}# [ENHANCED] Use validate_tool_name from tool_converter for thorough validation
{indent}name_valid, name_error, sanitized_name = validate_tool_name(name)
{indent}if not name_valid:
{indent}    log.warning(f"[TOOL VALIDATION] Invalid tool name: {{name_error}}. Original: {{name}}")
{indent}    return False, f"Invalid tool name: {{name_error}}", None
{indent}
{indent}# Use sanitized name (may have been fixed)
{indent}if sanitized_name and sanitized_name != name:
{indent}    log.info(f"[TOOL VALIDATION] Tool name sanitized: '{{name}}' -> '{{sanitized_name}}'")
{indent}    name = sanitized_name

'''
            # Find and replace the old simple validation
            old_validation_start = -1
            old_validation_end = -1

            for i in range(name_get_idx + 1, min(name_get_idx + 15, len(lines))):
                if "# 验证名称" in lines[i] or "if not name:" in lines[i]:
                    if old_validation_start < 0:
                        old_validation_start = i
                if old_validation_start >= 0 and "# 验证参数" in lines[i]:
                    old_validation_end = i
                    break

            if old_validation_start >= 0 and old_validation_end >= 0:
                # Replace old validation with enhanced version
                lines[old_validation_start:old_validation_end] = [enhanced_validation]
                changes_made.append(f"Enhanced name validation in validate_tool_call (lines {old_validation_start + 1}-{old_validation_end})")
            else:
                print("[WARNING] Could not find exact location to replace validation, skipping enhancement")

    # Write the patched file
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print("\n[OK] Patch applied successfully!")
    print("Changes made:")
    for change in changes_made:
        print(f"  - {change}")

    if not changes_made:
        print("  - No changes needed (already up to date)")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Antigravity Router Tool Validation Enhancement Patch")
    print("=" * 60)
    result = patch_file()
    print("=" * 60)
    if result:
        print("[OK] Patch completed!")
    else:
        print("[ERROR] Patch failed!")
