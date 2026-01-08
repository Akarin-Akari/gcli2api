#!/usr/bin/env python3
"""
[FIX 2026-01-08] max_tokens 保护和日志增强补丁

功能：
1. 增强日志记录 - 添加 max_tokens 到请求日志
2. 设置最小 max_tokens 保护机制（MIN_MAX_TOKENS = 16384）

使用方法：
    python patch_max_tokens.py
"""

import shutil
from datetime import datetime
from pathlib import Path

TARGET_FILE = Path(__file__).parent / "antigravity_anthropic_router.py"
BACKUP_SUFFIX = datetime.now().strftime(".bak.%Y%m%d_%H%M%S")

# 要查找的原始代码
OLD_CODE = '''    user_agent = request.headers.get("user-agent", "")
    log.info(
        f"[ANTHROPIC] /messages 收到请求: client={client_host}:{client_port}, model={model}, "
        f"stream={stream}, messages={len(messages)}, thinking_present={thinking_present}, "
        f"thinking={thinking_summary}, ua={user_agent}"
    )'''

# 替换后的新代码
NEW_CODE = '''    user_agent = request.headers.get("user-agent", "")

    # [FIX 2026-01-08] 设置最小 max_tokens 保护
    # Cursor 等客户端可能设置较小的 max_tokens（如 4096），导致长文本生成被截断
    # 对于需要生成长文本（如研究报告）的场景，自动提升到更合理的值
    MIN_MAX_TOKENS = 16384  # 最小 max_tokens 值
    original_max_tokens = max_tokens
    if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
        max_tokens = MIN_MAX_TOKENS
        payload["max_tokens"] = max_tokens
        log.info(
            f"[ANTHROPIC] max_tokens 自动提升: {original_max_tokens} -> {max_tokens} "
            f"(MIN_MAX_TOKENS={MIN_MAX_TOKENS})"
        )

    # [FIX 2026-01-08] 增强日志记录，添加 max_tokens 信息用于诊断
    log.info(
        f"[ANTHROPIC] /messages 收到请求: client={client_host}:{client_port}, model={model}, "
        f"stream={stream}, messages={len(messages)}, max_tokens={max_tokens} (original={original_max_tokens}), "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )'''


def main():
    if not TARGET_FILE.exists():
        print(f"[ERROR] Target file not found: {TARGET_FILE}")
        return False

    # 读取文件内容
    content = TARGET_FILE.read_text(encoding="utf-8")

    # 检查是否已经应用过补丁
    if "MIN_MAX_TOKENS" in content:
        print("[OK] Patch already applied, no action needed")
        return True

    # 检查原始代码是否存在
    if OLD_CODE not in content:
        print("[ERROR] Target code block not found, file may have been modified")
        print("Please manually check antigravity_anthropic_router.py")
        return False

    # 创建备份
    backup_path = TARGET_FILE.with_suffix(TARGET_FILE.suffix + BACKUP_SUFFIX)
    shutil.copy2(TARGET_FILE, backup_path)
    print(f"[BACKUP] Created: {backup_path.name}")

    # 应用补丁
    new_content = content.replace(OLD_CODE, NEW_CODE)
    TARGET_FILE.write_text(new_content, encoding="utf-8")

    print("[SUCCESS] Patch applied!")
    print("   - Enhanced logging: added max_tokens info")
    print("   - Min max_tokens protection: MIN_MAX_TOKENS = 16384")

    return True


if __name__ == "__main__":
    main()
