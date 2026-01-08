#!/usr/bin/env python3
"""
[FIX 2026-01-08] 上下文长度阈值修复补丁

问题：
API 在约 100K tokens 时返回 "Prompt is too long" (400) 错误
但本地 CONTEXT_CRITICAL_THRESHOLD = 120000 太高，无法在 API 报错前捕获

解决方案：
降低阈值以在 API 报错前捕获：
- CONTEXT_WARNING_THRESHOLD: 80000 -> 60000
- CONTEXT_CRITICAL_THRESHOLD: 120000 -> 90000

使用方法：
    python patch_context_threshold.py
"""

import shutil
from datetime import datetime
from pathlib import Path

TARGET_FILE = Path(__file__).parent / "antigravity_router.py"
BACKUP_SUFFIX = datetime.now().strftime(".bak.%Y%m%d_%H%M%S")

# 要查找的原始代码
OLD_CODE = '''    # ✅ 新增：上下文长度阈值配置
    # 这些阈值用于主动检测和拒绝过长的上下文，避免 API 返回空响应
    # Cursor 用户可以使用 /summarize 命令来压缩对话历史
    CONTEXT_WARNING_THRESHOLD = 80000   # 80K tokens - 警告阈值，记录日志
    CONTEXT_CRITICAL_THRESHOLD = 120000  # 120K tokens - 拒绝阈值，返回错误'''

# 替换后的新代码
NEW_CODE = '''    # ✅ 新增：上下文长度阈值配置
    # 这些阈值用于主动检测和拒绝过长的上下文，避免 API 返回空响应
    # Cursor 用户可以使用 /summarize 命令来压缩对话历史
    # [FIX 2026-01-08] 降低阈值以在 API 报错 "Prompt is too long" 之前捕获
    CONTEXT_WARNING_THRESHOLD = 60000   # 60K tokens - 警告阈值，记录日志
    CONTEXT_CRITICAL_THRESHOLD = 90000  # 90K tokens - 拒绝阈值，返回错误（API 限制约 100K）'''


def main():
    if not TARGET_FILE.exists():
        print(f"[ERROR] Target file not found: {TARGET_FILE}")
        return False

    # 读取文件内容
    content = TARGET_FILE.read_text(encoding="utf-8")

    # 检查是否已经应用过补丁
    if "CONTEXT_CRITICAL_THRESHOLD = 90000" in content:
        print("[OK] Patch already applied, no action needed")
        return True

    # 检查原始代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] Target code block not found, file may have been modified")
        print("Please manually check antigravity_router.py around line 1900-1910")
        # 尝试备用匹配
        if "CONTEXT_WARNING_THRESHOLD = 80000" in content and "CONTEXT_CRITICAL_THRESHOLD = 120000" in content:
            print("[INFO] Found thresholds via fallback matching, attempting regex-based fix...")
            content = content.replace(
                "CONTEXT_WARNING_THRESHOLD = 80000",
                "CONTEXT_WARNING_THRESHOLD = 60000"
            )
            content = content.replace(
                "CONTEXT_CRITICAL_THRESHOLD = 120000",
                "CONTEXT_CRITICAL_THRESHOLD = 90000"
            )
            # 添加注释
            old_comment = "# 这些阈值用于主动检测和拒绝过长的上下文，避免 API 返回空响应"
            new_comment = "# 这些阈值用于主动检测和拒绝过长的上下文，避免 API 返回空响应\n    # [FIX 2026-01-08] 降低阈值以在 API 报错 \"Prompt is too long\" 之前捕获"
            if old_comment in content and "[FIX 2026-01-08] 降低阈值" not in content:
                content = content.replace(old_comment, new_comment)

            # 创建备份
            backup_path = TARGET_FILE.with_suffix(TARGET_FILE.suffix + BACKUP_SUFFIX)
            shutil.copy2(TARGET_FILE, backup_path)
            print(f"[BACKUP] Created: {backup_path.name}")

            # 写入修改
            TARGET_FILE.write_text(content, encoding="utf-8")
            print("[SUCCESS] Patch applied via fallback matching!")
            print("   - CONTEXT_WARNING_THRESHOLD: 80000 -> 60000")
            print("   - CONTEXT_CRITICAL_THRESHOLD: 120000 -> 90000")
            return True
        return False

    # 创建备份
    backup_path = TARGET_FILE.with_suffix(TARGET_FILE.suffix + BACKUP_SUFFIX)
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] Created: {backup_path.name}")

    # 应用补丁
    new_content = content.replace(OLD_CODE, NEW_CODE)
    TARGET_FILE.write_text(new_content, encoding="utf-8")

    print("[SUCCESS] Context threshold patch applied!")
    print("   - CONTEXT_WARNING_THRESHOLD: 80000 -> 60000")
    print("   - CONTEXT_CRITICAL_THRESHOLD: 120000 -> 90000")
    print("")
    print("This will catch 'Prompt is too long' errors before API returns them,")
    print("allowing proper error messages that suggest using /summarize command.")

    return True


if __name__ == "__main__":
    main()
