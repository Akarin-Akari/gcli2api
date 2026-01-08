#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signature Cache 补丁脚本 - message_converter.py

用于修改 message_converter.py，添加 signature 缓存读取功能。
执行前会自动备份原文件。

Author: Claude Opus 4.5 (浮浮酱)
Date: 2026-01-07
"""

import shutil
import os
from datetime import datetime

def patch_message_converter():
    """修改 message_converter.py 添加 signature 缓存读取支持"""

    file_path = os.path.join(os.path.dirname(__file__), "converters", "message_converter.py")

    # 备份原文件
    backup_path = file_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(file_path, backup_path)
    print(f"[PATCH] 已备份原文件到: {backup_path}")

    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经打过补丁
    if 'get_cached_signature' in content:
        print("[PATCH] 文件已包含 get_cached_signature，跳过补丁")
        return False

    # 补丁1: 添加 import
    # 找到现有的 import 区域
    import_marker = "from log import log"
    if import_marker in content:
        new_import = """from log import log
from signature_cache import get_cached_signature"""
        content = content.replace(import_marker, new_import)
        print("[PATCH] 已添加 signature_cache import")
    else:
        print("[PATCH] 警告: 未找到 import 标记")

    # 补丁2: 修改 thinking block 处理逻辑，添加缓存查找
    # 找到处理 thinking 类型的代码块
    old_thinking_block = '''                            if item_type == "thinking":
                                # 提取 thinking 内容
                                thinking_text = item.get("thinking", "")
                                signature = item.get("signature", "")
                                # 只有当 signature 有效时才添加 thinking block
                                if signature and signature.strip():
                                    content_parts.append({
                                        "text": str(thinking_text),
                                        "thought": True,
                                        "thoughtSignature": signature
                                    })
                                else:
                                    # signature 无效，将 thinking 内容作为普通文本处理
                                    if thinking_text:
                                        content_parts.append({"text": str(thinking_text)})
                                        log.debug(f"[ANTIGRAVITY] Thinking block has no valid signature, treating as regular text")'''

    new_thinking_block = '''                            if item_type == "thinking":
                                # 提取 thinking 内容
                                thinking_text = item.get("thinking", "")
                                signature = item.get("signature", "")
                                # 只有当 signature 有效时才添加 thinking block
                                if signature and signature.strip():
                                    content_parts.append({
                                        "text": str(thinking_text),
                                        "thought": True,
                                        "thoughtSignature": signature
                                    })
                                else:
                                    # signature 无效，尝试从缓存恢复
                                    if thinking_text:
                                        cached_signature = get_cached_signature(thinking_text)
                                        if cached_signature:
                                            content_parts.append({
                                                "text": str(thinking_text),
                                                "thought": True,
                                                "thoughtSignature": cached_signature
                                            })
                                            log.info(f"[SIGNATURE_CACHE] 从缓存恢复 signature: thinking_len={len(thinking_text)}")
                                        else:
                                            # 缓存未命中，将 thinking 内容作为普通文本处理
                                            content_parts.append({"text": str(thinking_text)})
                                            log.debug(f"[ANTIGRAVITY] Thinking block has no valid signature and cache miss, treating as regular text")'''

    if old_thinking_block in content:
        content = content.replace(old_thinking_block, new_thinking_block)
        print("[PATCH] 已修改 thinking block 处理逻辑")
    else:
        print("[PATCH] 警告: 未找到 thinking block 的目标代码块")

    # 补丁3: 修改 redacted_thinking block 处理逻辑
    old_redacted_block = '''                            elif item_type == "redacted_thinking":
                                # 提取 redacted_thinking 内容
                                thinking_text = item.get("thinking") or item.get("data", "")
                                signature = item.get("signature", "")
                                # 只有当 signature 有效时才添加 thinking block
                                if signature and signature.strip():
                                    content_parts.append({
                                        "text": str(thinking_text),
                                        "thought": True,
                                        "thoughtSignature": signature
                                    })
                                else:
                                    # signature 无效，将 thinking 内容作为普通文本处理
                                    if thinking_text:
                                        content_parts.append({"text": str(thinking_text)})
                                        log.debug(f"[ANTIGRAVITY] Redacted thinking block has no valid signature, treating as regular text")'''

    new_redacted_block = '''                            elif item_type == "redacted_thinking":
                                # 提取 redacted_thinking 内容
                                thinking_text = item.get("thinking") or item.get("data", "")
                                signature = item.get("signature", "")
                                # 只有当 signature 有效时才添加 thinking block
                                if signature and signature.strip():
                                    content_parts.append({
                                        "text": str(thinking_text),
                                        "thought": True,
                                        "thoughtSignature": signature
                                    })
                                else:
                                    # signature 无效，尝试从缓存恢复
                                    if thinking_text:
                                        cached_signature = get_cached_signature(thinking_text)
                                        if cached_signature:
                                            content_parts.append({
                                                "text": str(thinking_text),
                                                "thought": True,
                                                "thoughtSignature": cached_signature
                                            })
                                            log.info(f"[SIGNATURE_CACHE] 从缓存恢复 redacted signature: thinking_len={len(thinking_text)}")
                                        else:
                                            # 缓存未命中，将 thinking 内容作为普通文本处理
                                            content_parts.append({"text": str(thinking_text)})
                                            log.debug(f"[ANTIGRAVITY] Redacted thinking block has no valid signature and cache miss, treating as regular text")'''

    if old_redacted_block in content:
        content = content.replace(old_redacted_block, new_redacted_block)
        print("[PATCH] 已修改 redacted_thinking block 处理逻辑")
    else:
        print("[PATCH] 警告: 未找到 redacted_thinking block 的目标代码块")

    # 写入修改后的内容
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[PATCH] 补丁应用完成: {file_path}")
    return True


if __name__ == "__main__":
    patch_message_converter()
