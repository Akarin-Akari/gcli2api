#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signature Cache 补丁脚本 - antigravity_router.py

用于修改 antigravity_router.py，在 thinking 验证逻辑中添加缓存恢复功能。
执行前会自动备份原文件。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-08
"""

import shutil
import os
from datetime import datetime

def patch_antigravity_router():
    """修改 antigravity_router.py 添加 signature 缓存恢复支持"""

    file_path = os.path.join(os.path.dirname(__file__), "antigravity_router.py")

    # 备份原文件
    backup_path = file_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"[PATCH] 已备份原文件到: {backup_path}")

    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经打过补丁
    if 'get_cached_signature' in content and 'antigravity_router' in file_path:
        print("[PATCH] 文件已包含 get_cached_signature，跳过补丁")
        return False

    patches_applied = 0

    # 补丁1: 添加 import
    import_marker = "from log import log"
    if import_marker in content and "from signature_cache import" not in content:
        new_import = """from log import log
from signature_cache import get_cached_signature"""
        content = content.replace(import_marker, new_import)
        print("[PATCH] 已添加 signature_cache import")
        patches_applied += 1
    else:
        print("[PATCH] 警告: 未找到 import 标记或已存在 import")

    # 补丁2: 修改 thinking 验证逻辑
    # 找到原始的验证代码块
    old_validation_block = '''                            if item_type in ("thinking", "redacted_thinking"):
                                # 检查是否有 signature
                                signature = item.get("signature")
                                if signature and signature.strip():
                                    has_valid_thinking = True
                                    break'''

    new_validation_block = '''                            if item_type in ("thinking", "redacted_thinking"):
                                # 检查是否有 signature
                                signature = item.get("signature")
                                if signature and signature.strip():
                                    has_valid_thinking = True
                                    break
                                else:
                                    # signature 无效，尝试从缓存恢复
                                    thinking_text = item.get("thinking", "")
                                    if thinking_text:
                                        cached_sig = get_cached_signature(thinking_text)
                                        if cached_sig:
                                            # 缓存命中，将 signature 写回 item
                                            item["signature"] = cached_sig
                                            has_valid_thinking = True
                                            log.info(f"[ANTIGRAVITY] 从缓存恢复 signature: thinking_len={len(thinking_text)}")
                                            break'''

    if old_validation_block in content:
        content = content.replace(old_validation_block, new_validation_block)
        print("[PATCH] 已修改 thinking 验证逻辑，添加缓存恢复")
        patches_applied += 1
    else:
        print("[PATCH] 警告: 未找到 thinking 验证逻辑的目标代码块")

    # 写入修改后的内容
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[PATCH] 补丁应用完成: {file_path} (应用了 {patches_applied} 个补丁)")
    return patches_applied > 0


if __name__ == "__main__":
    patch_antigravity_router()
